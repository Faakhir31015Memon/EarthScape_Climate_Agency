"""Spark Structured Streaming processor for the spool/broker event stream.

Reads JSON events from the spool directory (checkpointed ``maxFilesPerTrigger``
file source), validates each observation against the canonical schema, then:

* deduplicates by ``event_id`` (idempotent processing),
* applies an event-time watermark (``WATERMARK_DELAY_MINUTES``) - events that
  arrive after their window closed are routed to the late-arrivals file instead
  of silently altering aggregates,
* records that fail validation go to a dead-letter file (append-only, with the
  reason), never dropped,
* ``foreachBatch`` writes Parquet into the data lake (validated layer), upserts
  monthly aggregates into ``analytics_summaries`` (``processing_mode="streaming"``),
  and persists live counters - events processed, accepted, rejected, late,
  duplicates, estimated lag seconds, last batch time - into ``processing_jobs``
  so the UI can show honest stream status.

Run modes on this machine (verified): ``--once`` processes everything currently
in the spool and exits; ``--duration N`` keeps a trigger interval going for
``N`` seconds. With Spark unavailable the script reports the real failure.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BACKEND = ROOT / "backend"
for path in (str(BACKEND), str(ROOT / "data-pipeline"), str(ROOT / "data-pipeline" / "spark")):
    if path not in sys.path:
        sys.path.insert(0, path)

logger = logging.getLogger("streaming")

WATERMARK_DELAY_MINUTES = 30
MINUTE = 60000


def _scratch_dir() -> Path:
    from config import env

    base = Path(env.get_str("PROCESSING_SCRATCH_DIR", str(BACKEND / ".data" / "processing")))
    return base / "streaming"


def _paths() -> Dict[str, Path]:
    base = _scratch_dir()
    for name in ("spool", "dead-letters", "late-arrivals", "checkpoints"):
        (base / name).mkdir(parents=True, exist_ok=True)
    return {"spool": base / "spool", "dead_letters": base / "dead-letters",
            "late": base / "late-arrivals", "checkpoint": base / "checkpoints" / "obs-v1"}


def _parse_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    """The streaming schema keeps ``payload`` as a raw JSON string so one
    malformed observation can never break a micro-batch; decode it here."""
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return {}
    return payload if isinstance(payload, dict) else {}


def validate_event(event: Dict[str, Any]):
    """Validate one event against the canonical contract. Returns (observation, issues)."""
    from common import validators

    observation = _parse_payload(event)
    issues = []
    if not event.get("event_id"):
        issues.append("event_id_missing")
    if not event.get("event_time"):
        issues.append("event_time_missing")
    moment, date_issues = validators.parse_timestamp(observation.get("observation_date"))
    issues.extend(issue.to_dict() for issue in date_issues)
    for field in ("temperature_c", "precipitation_mm"):
        value, metric_issues = validators.validate_measurement(field, observation.get(field),
                                                               observation.get("source_unit"))
        if metric_issues:
            issues.extend(metric.to_dict() for metric in metric_issues)
    if moment is None:
        return None, issues
    return observation, issues


def _append_jsonl(directory: Path, records, reason_key: str = "issues"):
    if not records:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = directory / f"{stamp}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, default=str) + "\n")
    return len(records)


def persist_stream_status(metrics: Dict[str, Any], *, status: str = "running",
                          error: str | None = None) -> None:
    """Upsert a single processing_jobs document that represents the live stream."""
    from common.repositories import processing_jobs, utcnow

    doc = {
        "workflow": "streaming.observation_processor",
        "engine": "streaming",
        "status": status,
        "submitted_at": metrics.setdefault("started_at", utcnow()),
        "started_at": metrics.setdefault("started_at", utcnow()),
        "finished_at": utcnow() if status in ("completed", "failed", "stopped") else None,
        "parameters": {"source": "spool-directory"},
        "metrics": metrics,
        "log_lines": metrics.get("log_lines", [])[-50:],
        "error_message": error,
        "attempt": 1,
        "dataset_id": None,
        "is_stream_status": True,
    }
    processing_jobs.update_one({"workflow": "streaming.observation_processor",
                                "is_stream_status": True},
                               {"$set": doc}, upsert=True)
    try:
        from common.database import get_database

        get_database().flush_if_dirty()
    except Exception:  # noqa: BLE001
        pass


def _event_moment(event: Dict[str, Any]):
    raw = event.get("event_time")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def make_foreach_batch(paths: Dict[str, Path], counters: Dict[str, Any]):
    seen_ids: set = set()

    def foreach_batch(batch_df, batch_id):
        from common import validators
        from common.data_lake import get_data_lake

        events = []
        for raw in batch_df.toJSON().collect():
            events.append(json.loads(raw))
        if len(events) > 50000:
            counters["log_lines"].append(f"batch {batch_id}: capped processing at 50000 events")
            events = events[:50000]

        # Late-arrival gate: an event whose event_time is older than the running
        # watermark (highest event time seen minus WATERMARK_DELAY_MINUTES) is
        # routed to the late-arrivals file instead of silently patching aggregates.
        watermark_floor = None
        known_max = counters.get("max_event_time")
        if known_max:
            watermark_floor = datetime.fromisoformat(known_max) - timedelta(
                minutes=WATERMARK_DELAY_MINUTES)
        accepted, dead, late = [], 0, 0
        for event in events:
            event_id = event.get("event_id")
            if event_id and event_id in seen_ids:
                # dropDuplicates upstream should make this impossible; count it
                # honestly if it ever happens (e.g. state expired).
                counters["duplicates"] = counters.get("duplicates", 0) + 1
                continue
            if event_id:
                seen_ids.add(event_id)
            moment = _event_moment(event)
            if moment and watermark_floor and moment < watermark_floor:
                _append_jsonl(paths["late"], [{"event_id": event.get("event_id"),
                                               "event_time": event.get("event_time"),
                                               "late_by_seconds": round(
                                                   (watermark_floor - moment).total_seconds(), 1),
                                               "payload": event.get("payload")}])
                late += 1
                continue
            observation, issues = validate_event(event)
            if observation is None:
                _append_jsonl(paths["dead_letters"], [
                    {"event": event, "issues": issues,
                     "dead_lettered_at": datetime.now(timezone.utc).isoformat()}])
                dead += 1
                continue
            observation = dict(observation)
            observation["schema_version"] = validators.SCHEMA_VERSION
            observation["ingested_at"] = datetime.now(timezone.utc)
            observation["event_id"] = event.get("event_id")
            accepted.append(observation)
            if moment:
                current = counters.get("max_event_time")
                iso = moment.astimezone(timezone.utc).isoformat()
                if current is None or iso > current:
                    counters["max_event_time"] = iso

        if accepted:
            import pandas as pd

            frame = pd.DataFrame(accepted)
            for column in ("year", "month"):
                if column not in frame:
                    date = pd.to_datetime(frame["observation_date"])
                    frame[column] = date.dt.year.astype("int32") if column == "year" \
                        else date.dt.month.astype("int32")
            lake = get_data_lake()
            key = f"dataset=stream/batch-{batch_id}/events.parquet"
            local = paths["checkpoint"].parent / f"batch-{batch_id}.parquet"
            frame.to_parquet(local, index=False)
            lake.upload(str(local), f"stream-batch-{batch_id}.parquet", layer="validated",
                        partition={"dataset_id": "stream"})
            local.unlink(missing_ok=True)
            _store_stream_summaries(frame)

        counters["events_processed"] += len(events)
        counters["accepted"] += len(accepted)
        counters["rejected"] += dead
        counters["late"] += late
        counters["last_batch_id"] = batch_id
        counters["last_batch_at"] = datetime.now(timezone.utc).isoformat()
        if not events:
            return
        lag = _estimate_lag(events)
        counters["lag_seconds_or_null"] = lag
        counters["log_lines"].append(
            f"batch {batch_id}: events={len(events)} accepted={len(accepted)} "
            f"dead={dead} late={late} lag={lag}s")
        persist_stream_status(counters)

    return foreach_batch


def _estimate_lag(events) -> float:
    now = datetime.now(timezone.utc)
    latest = 0.0
    for event in events[-500:]:
        try:
            moment = datetime.fromisoformat(str(event.get("event_time")).replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            latest = max(latest, (now - moment).total_seconds())
        except ValueError:
            continue
    return round(latest, 2)


def _store_stream_summaries(frame) -> None:
    """Small monthly aggregates from this batch into analytics_summaries."""
    try:
        from common.analytics import store_summary
        from common.database import get_database

        numeric = frame.copy()
        for column in ("temperature_c", "precipitation_mm"):
            if column in numeric:
                numeric[column] = numeric[column].astype("float64")
        for (region, year, month), group in numeric.groupby(
                ["region_code", "year", "month"], dropna=True):
            payload = {
                "kind": "streaming_monthly_patch",
                "mean_temperature_c": None if "temperature_c" not in group or
                group["temperature_c"].dropna().empty
                else round(float(group["temperature_c"].mean()), 4),
                "precipitation_sum_mm": None if "precipitation_mm" not in group or
                group["precipitation_mm"].dropna().empty
                else round(float(group["precipitation_mm"].sum()), 4),
                "observation_count": int(len(group)),
            }
            store_summary(metric="streaming_monthly_climate",
                          scope={"region_code": str(region), "year": int(year),
                                 "month": int(month)},
                          payload=payload, source_datasets=["stream"],
                          workflow="streaming.observation_processor",
                          processing_mode="streaming")
        get_database().flush_if_dirty()
    except Exception as exc:  # noqa: BLE001 - the stream must keep flowing
        logger.warning("stream summary persistence failed: %s", str(exc)[:150])


def run(once: bool = False, duration_seconds: int = 0) -> int:
    paths = _paths()
    from spark_session import SparkUnavailable, build_spark_session

    if sys.platform == "win32":
        # winutils.exe/hadoop.dll are absent here and binary downloads are not
        # permitted, so every Structured Streaming file discovery would crash on
        # NativeIO$Windows.access0. build_spark_session registers the pure-Java shim
        # for the local scheme; runs stay labelled "streaming-local-spool", never cluster.
        from pathlib import Path as _P

        shim = _P(__file__).resolve().parents[1] / "streaming" / "jars" / "earthscape-localfs.jar"
        if not shim.exists():
            print(json.dumps({"ok": False,
                              "error": f"local FS shim missing: {shim}; run "
                                       "data-pipeline/streaming/java/build_localfs.ps1"}))
            return 1

    try:
        spark = build_spark_session("earthscape-stream-processor")
    except SparkUnavailable as exc:
        persist_stream_status({"events_processed": 0, "accepted": 0, "rejected": 0,
                               "duplicates": 0, "late": 0, "lag_seconds_or_null": None,
                               "log_lines": [str(exc), f"cause: {(exc.cause or '')[:300]}"]},
                              status="failed", error=f"Spark unavailable: {exc}")
        print(json.dumps({"ok": False, "error": str(exc), "cause": exc.cause}))
        return 1

    counters = {"events_processed": 0, "accepted": 0, "rejected": 0, "duplicates": 0,
                "late": 0, "lag_seconds_or_null": None, "last_batch_id": None,
                "last_batch_at": None, "engine_version": spark.sparkContext.version,
                "processing_mode": "streaming-local-spool", "log_lines": []}
    persist_stream_status(counters)

    from pyspark.sql import functions as F  # noqa: F401  (kept for future batch ops)

    try:
        reader = (spark.readStream
                  .format("json")
                  .option("maxFilesPerTrigger", "10")
                  .schema("event_id STRING, event_time TIMESTAMP, source STRING, "
                          "topic STRING, payload STRING"))
        stream = (reader.load(str(paths["spool"]))
                  .withWatermark("event_time", f"{WATERMARK_DELAY_MINUTES} minutes")
                  .dropDuplicates(["event_id"]))

        writer_base = (stream.writeStream
                       .foreachBatch(make_foreach_batch(paths, counters))
                       .option("checkpointLocation", str(paths["checkpoint"]))
                       .queryName("observation-stream-processor"))
        if once:
            writer = writer_base.trigger(availableNow=True)
        else:
            writer = writer_base.trigger(processingTime="10 seconds")
        query = writer.start()
        query.awaitTermination(timeout=240 if once else (duration_seconds or 60))
        query.stop()
        counters["log_lines"].append("stream query terminated cleanly")
        persist_stream_status(counters, status="completed")
        print(json.dumps({"ok": True, "counters": {k: v for k, v in counters.items()
                                                  if k != "log_lines"}}, default=str))
        return 0
    except Exception as exc:  # noqa: BLE001 - surface the real Spark error
        counters["log_lines"].append(f"stream failure: {str(exc)[:300]}")
        persist_stream_status(counters, status="failed", error=str(exc)[:400])
        print(json.dumps({"ok": False, "error": str(exc)[:1500]}))
        return 1
    finally:
        spark.stop()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="process the spool then exit")
    parser.add_argument("--duration", type=int, default=0,
                        help="keep the trigger loop alive for N seconds (continuous mode)")
    args = parser.parse_args()
    return run(once=args.once, duration_seconds=args.duration)


if __name__ == "__main__":
    raise SystemExit(main())
