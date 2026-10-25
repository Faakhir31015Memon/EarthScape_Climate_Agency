"""Periodic polling ingestion — the honest development path for "streaming".

This is **periodic polling**, not a continuous sensor stream: on each cycle it
calls the real Open-Meteo forecast/archive endpoints, validates the payload
through the shared connector + validation pipeline, dedupes by ``dedupe_key``,
and appends JSONL batches to ``artifacts/datalake/raw/stream/``.  A checkpoint
(``artifacts/checkpoints/poller.json``) stores ``last_polled_at`` and the seen
keys so restarts resume exactly where the last successful poll ended.  If the
provider is unreachable the cycle reports ``unavailable`` — no fabricated
observations.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from connectors.registry import build_connector
from pipeline_config import checkpoints_root, layer_path
from streaming.schema_compat import assert_canonical
from validation.dedupe import DeduplicationIndex

CHECKPOINT_NAME = "poller.json"
SEEN_NAME = "poller_seen_keys.txt"

#: default development cities (province regions for the target audience)
DEFAULT_SITES = [
    {"location_name": "Karachi", "region": "Sindh", "latitude": 24.8607, "longitude": 67.0011, "country": "PK"},
    {"location_name": "Lahore", "region": "Punjab", "latitude": 31.5204, "longitude": 74.3587, "country": "PK"},
    {"location_name": "Islamabad", "region": "Islamabad Capital Territory", "latitude": 33.6844, "longitude": 73.0479, "country": "PK"},
    {"location_name": "Peshawar", "region": "Khyber Pakhtunkhwa", "latitude": 34.0084, "longitude": 71.5785, "country": "PK"},
    {"location_name": "Quetta", "region": "Balochistan", "latitude": 30.1798, "longitude": 66.9750, "country": "PK"},
    {"location_name": "Multan", "region": "Punjab", "latitude": 30.1520, "longitude": 71.4109, "country": "PK"},
]


def _checkpoint_path() -> Path:
    return checkpoints_root() / CHECKPOINT_NAME


def load_checkpoint() -> dict:
    path = _checkpoint_path()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_polled_at": None, "batches_written": 0, "records_written": 0, "status": "fresh"}


def save_checkpoint(cp: dict) -> None:
    _checkpoint_path().write_text(json.dumps(cp, indent=2), encoding="utf-8")


def _seen_index() -> tuple:
    seen_file = checkpoints_root() / SEEN_NAME
    seen = set()
    if seen_file.is_file():
        seen = set(seen_file.read_text(encoding="utf-8").split())
    return DeduplicationIndex(seen), seen_file


def poll_once(sites=None, mode="forecast", max_age_hours: int = 72) -> dict:
    """One polling cycle: fetch -> validate -> dedupe -> write batch -> checkpoint."""
    sites = sites or DEFAULT_SITES
    cp = load_checkpoint()
    index, seen_file = _seen_index()
    batch: list = []
    cycle_errors: list = []
    for site in sites:
        cfg = dict(site)
        cfg["mode"] = mode
        cfg["granularity"] = "hourly" if mode == "forecast" else "daily"
        cfg["dataset_id"] = f"stream_{mode}_{site['location_name'].lower()}"
        try:
            connector = build_connector("open_meteo_live" if mode == "forecast" else "open_meteo", cfg)
        except Exception as exc:  # e.g. ConnectorNotConfigured — honest report
            cycle_errors.append({"site": site["location_name"], "error": str(exc)[:200]})
            continue
        result = connector.run()
        if result.status == "unavailable":
            cycle_errors.append({"site": site["location_name"], "error": "provider unavailable",
                                 "details": result.errors[:3]})
            continue
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for rec in result.records:
            if mode == "forecast":
                age_ok = True
                try:
                    ts = datetime.strptime(rec["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                    age_ok = 0 <= (datetime.utcnow() - ts).total_seconds() <= max_age_hours * 3600
                except ValueError:
                    age_ok = False
                if not age_ok:
                    continue
            if index.accept(rec):
                assert_canonical(rec, context="polling")
                batch.append(rec)
    summary = {"polled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "previous_last_polled_at": cp.get("last_polled_at"),
               "records": len(batch), "sites": len(sites), "errors": cycle_errors}
    if batch:
        stream_dir = layer_path("raw", "stream")
        batch_file = stream_dir / f"poll-batch-{summary['polled_at'].replace(':', '').replace('-', '')}.jsonl"
        with open(batch_file, "w", encoding="utf-8") as fh:
            for rec in batch:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(seen_file, "a", encoding="utf-8") as fh:
            fh.write("\n".join(r["dedupe_key"] for r in batch) + "\n")
        summary["batch_file"] = str(batch_file)
        cp["batches_written"] = cp.get("batches_written", 0) + 1
        cp["records_written"] = cp.get("records_written", 0) + len(batch)
    cp["last_polled_at"] = summary["polled_at"]
    cp["status"] = "ok" if not cycle_errors else ("partial" if batch else "unavailable")
    cp["last_cycle"] = summary
    save_checkpoint(cp)
    return summary


def poll_loop(interval_seconds: int, iterations: int = 0, sites=None, mode="forecast") -> None:
    n = 0
    while True:
        summary = poll_once(sites=sites, mode=mode)
        print(f"[poll {n}] records={summary['records']} status={load_checkpoint()['status']} "
              f"errors={len(summary['errors'])}")
        n += 1
        if iterations and n >= iterations:
            return
        time.sleep(interval_seconds)


def main(argv=None) -> int:
    from pipeline_config import ENV

    parser = argparse.ArgumentParser(description="Periodic polling ingest (NOT a continuous sensor stream)")
    parser.add_argument("--once", action="store_true", help="single polling cycle")
    parser.add_argument("--loop", action="store_true", help="poll forever on the interval")
    parser.add_argument("--iterations", type=int, default=0, help="stop after N cycles (0 = never)")
    parser.add_argument("--interval", type=int, default=int(ENV.raw("POLL_INTERVAL_SECONDS", "900")))
    parser.add_argument("--mode", default="forecast", choices=["forecast", "archive"])
    args = parser.parse_args(argv)
    if args.loop:
        poll_loop(args.interval, args.iterations, mode=args.mode)
        return 0
    summary = poll_once(mode=args.mode)
    print(json.dumps(summary, indent=2))
    return 0 if summary["errors"] == [] else 1


if __name__ == "__main__":
    raise SystemExit(main())
