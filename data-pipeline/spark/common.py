"""Shared Spark/pandas engine plumbing for the ``spark/`` jobs.

Every job exposes the same name, parameters and output schema regardless of
engine.  If ``pyspark`` is importable and ``SPARK_ENGINE_PREFER=true`` (or
``SPARK_MASTER`` is explicitly set), the Spark implementation runs; otherwise —
or if the Spark attempt fails for any reason — the pandas fallback runs and the
summary records ``engine_used`` plus ``spark_error`` honestly.  Jobs never
collect full datasets: aggregation happens in the engine, bounded ``take`` /
``toPandas`` is only applied to already-grouped frames.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from pipeline_config import ENV, datalake_root, write_tabular

FLAT_METRIC_FIELDS = [
    "temperature", "temperature_max", "temperature_min", "precipitation", "humidity",
    "wind_speed", "wind_direction", "pressure", "cloud_cover", "solar_radiation",
]


class SparkUnavailable(Exception):
    """Raised when a real Spark session cannot be created (import, JVM, master)."""


def spark_importable() -> bool:
    try:
        import pyspark  # noqa: F401

        return True
    except Exception:
        return False


_SESSION = None


def get_session():
    """Create/reuse a SparkSession: master from SPARK_MASTER (local[*] or yarn://)."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    if not spark_importable():
        try:
            import findspark

            findspark.init(ENV.raw("SPARK_HOME") or None)
        except Exception:
            pass
    try:
        import pyspark  # noqa: F401
        from pyspark.sql import SparkSession
    except Exception as exc:  # ImportError or broken JVM path
        raise SparkUnavailable(f"pyspark not usable here: {type(exc).__name__}: {exc}") from exc
    master = ENV.raw("SPARK_MASTER", "local[*]")
    try:
        builder = (SparkSession.builder.appName("earthscape-climate-pipeline")
                   .master(master)
                   .config("spark.sql.session.timeZone", "UTC"))
        if int(ENV.integer("SPARK_DRIVER_MEMORY_MB", 0) or 0) > 0:
            builder = builder.config("spark.driver.memory", f"{ENV.integer('SPARK_DRIVER_MEMORY_MB', 0)}m")
        _SESSION = builder.getOrCreate()
        return _SESSION
    except Exception as exc:
        raise SparkUnavailable(f"could not start SparkSession on master {master!r}: {exc}") from exc


def stop_session():
    global _SESSION
    if _SESSION is not None:
        try:
            _SESSION.stop()
        finally:
            _SESSION = None


def prefer_spark() -> bool:
    if not spark_importable():
        return False
    if ENV.exists("SPARK_MASTER"):
        return True
    return ENV.flag("SPARK_ENGINE_PREFER", False)


def execute_job(job_id: str, params: dict,
                spark_fn: Optional[Callable], pandas_fn: Callable) -> dict:
    """Run ``spark_fn`` when Spark is genuinely usable, else ``pandas_fn``.

    Returns the summary dict from the chosen engine (both engines stamp
    ``engine_used``); a failed Spark attempt degrades to pandas with the reason
    recorded — never a silent pretending.
    """
    if prefer_spark() and spark_fn is not None:
        try:
            return spark_fn(params)
        except SparkUnavailable as exc:
            summary = pandas_fn(params)
            summary["spark_error"] = str(exc)
            summary["engine_attempted"] = "pyspark"
            return summary
        except Exception as exc:
            summary = pandas_fn(params)
            summary["spark_error"] = f"spark path failed: {type(exc).__name__}: {str(exc)[:200]}"
            summary["engine_attempted"] = "pyspark"
            return summary
    summary = pandas_fn(params)
    if not spark_importable():
        summary.setdefault("engine_note", "pyspark not installed; pandas fallback engine (identical job/output schema)")
    return summary


# ---------------------------------------------------------------- load/flatten
def flatten_record(rec: dict) -> dict:
    loc = rec.get("location") or {}
    ts = rec.get("timestamp", "")
    row = {
        "dedupe_key": rec.get("dedupe_key", ""),
        "dataset_id": rec.get("dataset_id", ""),
        "timestamp": ts,
        "year": int(ts[:4]) if len(ts) >= 4 and ts[:4].isdigit() else None,
        "month": int(ts[5:7]) if len(ts) >= 7 and ts[5:7].isdigit() else None,
        "day": int(ts[8:10]) if len(ts) >= 10 and ts[8:10].isdigit() else None,
        "granularity": rec.get("granularity", ""),
        "region": loc.get("region") or loc.get("name") or "unassigned",
        "station_id": loc.get("station_id") or (rec.get("source") or {}).get("station_id") or "",
        "latitude": loc.get("latitude"),
        "longitude": loc.get("longitude"),
        "quality_status": (rec.get("quality") or {}).get("status", ""),
    }
    metrics = rec.get("metrics") or {}
    for m in FLAT_METRIC_FIELDS:
        meas = metrics.get(m)
        row[m] = float(meas["value"]) if isinstance(meas, dict) and meas.get("value") is not None else None
    prov = rec.get("provenance") or {}
    row["transformations"] = "; ".join(prov.get("transformations", []))
    return row


def iter_jsonl(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def find_input(dataset: Optional[str] = None, layers=("validated", "raw")) -> list:
    """Locate JSONL inputs in the data lake for a dataset (or all)."""
    root = datalake_root()
    files: list = []
    for layer in layers:
        base = root / layer
        if not base.is_dir():
            continue
        for ds_dir in sorted(base.iterdir()):
            if ds_dir.is_dir() and (dataset is None or ds_dir.name == f"dataset={dataset}"):
                files.extend(sorted(p for p in ds_dir.rglob("*.jsonl")))
    return files


def load_rows(dataset: Optional[str] = None, paths: Optional[list] = None,
              region: Optional[str] = None, year_from: Optional[int] = None,
              year_to: Optional[int] = None) -> list:
    files = [Path(p) for p in paths] if paths else find_input(dataset)
    rows = []
    for f in files:
        for rec in iter_jsonl(f):
            row = rec if _is_flat(rec) else flatten_record(rec)
            if region and row.get("region") != region:
                continue
            y = row.get("year")
            if year_from is not None and (y is None or y < year_from):
                continue
            if year_to is not None and (y is None or y > year_to):
                continue
            rows.append(row)
    return rows


def _is_flat(row: dict) -> bool:
    return "timestamp" in row and "year" in row and "region" in row


def new_summary(job_id: str, params: dict, engine: str) -> dict:
    return {
        "job_id": job_id,
        "engine_used": engine,
        "params": {k: v for k, v in params.items() if v is not None},
        "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rows_out": 0,
        "outputs": [],
        "status": "ok",
    }


def finish_summary(summary: dict) -> dict:
    summary["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return summary


def write_partitioned_rows(rows: list, dataset: str, base_name: str, columns: list,
                           layer: str = "analytics") -> list:
    """Write rows under layer/dataset=<name>/year=/month=/ using the parquet-or-csv.gz engine."""
    from pipeline_config import partition_dir

    manifests = []
    grouped: dict = {}
    for r in rows:
        key = (r.get("year") or 1970, r.get("month") or 1)
        grouped.setdefault(key, []).append(r)
    if not grouped:
        grouped[(None, None)] = rows
    for (year, month), bucket in sorted(grouped.items(), key=lambda kv: (kv[0][0] is None, kv[0])):
        if year is None:
            directory = layer_path(layer, f"dataset={dataset}")
        else:
            directory = partition_dir(layer, dataset, year, month)
        manifests.append(write_tabular(bucket, directory, base_name, columns=columns))
    return manifests


def layer_path(layer: str, *parts) -> Path:
    from pipeline_config import layer_path as _lp

    return _lp(layer, *parts)


def safe_div(a, b):
    return None if not b else a / b


def round_or_none(v, nd=4):
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return round(float(v), nd)
