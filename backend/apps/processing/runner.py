"""Executes catalogued workflows by importing the shipped pipeline modules, nothing else.

The only code that can run is the module the pipeline package registers for a catalogue id,
called with parameters that already passed `catalogue.validate`. The engine reported back is
whatever the module actually used (`pyspark`, `pandas`, `python-validation`), including the
fallback reason when a Spark attempt failed - this module never upgrades a fallback into a
Spark claim.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from common.datalake import storage_root
from common.db import insert_one, now_utc
from common.exceptions import ServiceUnavailableConfig, ValidationError
from common.paths import ensure_paths
from common.serialization import jsonable
from apps.processing import catalogue

PREVIEW_ROW_CAP = 25

LogFn = Callable[[str, str], None]


def _silent(_level: str, _message: str) -> None:
    return None


def entry_point(workflow_id: str) -> Callable[[dict], dict]:
    """Resolve a catalogue id to its registered module entry point."""
    modules = catalogue.registered_modules()
    module_path = modules.get(str(workflow_id))
    if not module_path:
        raise ValidationError(
            f"Workflow '{workflow_id}' has no pipeline module registered in this build.",
            details=[f"Registered workflows: {', '.join(sorted(modules)) or 'none'}."])
    ensure_paths()
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise ServiceUnavailableConfig(
            f"The pipeline module for '{workflow_id}' could not be imported.",
            details=[f"{type(exc).__name__}"]) from exc
    run = getattr(module, "run", None)
    if not callable(run):
        raise ServiceUnavailableConfig(
            f"The pipeline module for '{workflow_id}' exposes no run(params) entry point.")
    return run


def execute(workflow_id: str, params: Any, *, on_log: Optional[LogFn] = None) -> dict:
    """Validate against the catalogue, call the module, report the engine that ran."""
    log = on_log or _silent
    item = catalogue.resolve_workflow(workflow_id)
    normalized = catalogue.validate(item, params)
    run = entry_point(item["id"])
    planned, note = catalogue.planned_engine(item)

    log("info", f"catalogue {item['id']} -> module '{catalogue.registered_modules().get(item['id'])}'")
    log("info", f"planned engine '{planned}': {note}")
    log("info", f"validated parameters: {json.dumps(jsonable(normalized), sort_keys=True)[:500]}")

    started_at = now_utc()
    clock = time.monotonic()
    summary = run(dict(normalized))
    duration = round(time.monotonic() - clock, 3)
    finished_at = now_utc()

    if not isinstance(summary, dict):
        raise ServiceUnavailableConfig(
            f"Workflow '{item['id']}' returned no summary, so its result cannot be reported.")
    engine_used = str(summary.get("engine_used") or "unreported")
    status = str(summary.get("status") or "unknown")
    rows_out = int(summary.get("rows_out") or 0)
    outputs = [str(path) for path in summary.get("outputs", []) or []]
    for line in ("engine_note", "spark_error", "reason", "storage_engine", "data_quality_score"):
        if summary.get(line):
            log("info", f"{line}: {str(summary[line])[:400]}")
    log("info", f"engine_used={engine_used} status={status} rows_out={rows_out} "
               f"outputs={len(outputs)} seconds={duration}")

    return {
        "workflow": item["id"],
        "title": item.get("title"),
        "params": normalized,
        "engine_used": engine_used,
        "engine_attempted": summary.get("engine_attempted"),
        "spark_error": summary.get("spark_error"),
        "engine_note": summary.get("engine_note"),
        "storage_engine": summary.get("storage_engine"),
        "execution_status": status,
        "rows_out": rows_out,
        "outputs": outputs,
        "summary": jsonable(summary),
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": duration,
    }


def record_summary(outcome: dict, *, processing_job_id: Any, created_by: Any = None) -> str:
    """Persist the produced summary so every figure can be traced to its engine and job."""
    document = {
        "workflow": outcome["workflow"],
        "job_id": outcome["workflow"],
        "processing_job_id": processing_job_id,
        "created_by": created_by,
        "title": outcome.get("title"),
        "engine_used": outcome["engine_used"],
        "engine_attempted": outcome.get("engine_attempted"),
        "spark_error": outcome.get("spark_error"),
        "engine_note": outcome.get("engine_note"),
        "storage_engine": outcome.get("storage_engine"),
        "status": outcome["execution_status"],
        "dataset": (outcome.get("params") or {}).get("dataset"),
        "params": jsonable(outcome.get("params") or {}),
        "rows_out": outcome["rows_out"],
        "outputs": outcome["outputs"],
        "summary": outcome.get("summary"),
        "started_at": outcome["started_at"],
        "finished_at": outcome["finished_at"],
        "duration_seconds": outcome["duration_seconds"],
        "created_at": now_utc(),
    }
    return insert_one(catalogue.summary_collection(), document)


def _inside_lake(candidate: Path) -> tuple[bool, Path]:
    root = storage_root().resolve()
    resolved = candidate if candidate.is_absolute() else (root / str(candidate))
    try:
        resolved = resolved.resolve()
    except OSError:
        return False, candidate
    return root in resolved.parents, resolved


def output_preview(path_text: str, *, limit: int = 10) -> dict:
    """Bounded read-back of a produced output file, so the UI can show real rows."""
    limit = max(1, min(int(limit or 10), PREVIEW_ROW_CAP))
    candidate = Path(str(path_text or ""))
    inside, resolved = _inside_lake(candidate)
    if not inside:
        return {"available": False, "reason": "the recorded output is outside the data lake root"}
    if not resolved.is_file():
        return {"available": False, "path": str(resolved),
                "reason": "the recorded output file is not present in this data lake"}
    suffix = resolved.suffix.lower()
    try:
        if suffix == ".parquet":
            import pyarrow.parquet as parquet

            table = parquet.read_table(str(resolved)).slice(0, limit)
            rows = jsonable(table.to_pylist())
            reader = "pyarrow.parquet"
            total = table.num_rows
        elif suffix in (".gz", ".csv"):
            import pandas as pd

            frame = pd.read_csv(str(resolved), nrows=limit)
            rows = jsonable(frame.to_dict("records"))
            reader = "pandas.csv" + (".gz" if str(resolved).endswith(".gz") else "")
            total = len(rows)
        elif suffix in (".jsonl", ".json", ".tsv"):
            delimiter = "\t" if suffix == ".tsv" else None
            collected: list[Any] = []
            with open(resolved, "r", encoding="utf-8") as handle:
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    if delimiter:
                        collected.append(text.split(delimiter))
                    else:
                        try:
                            collected.append(json.loads(text))
                        except ValueError:
                            continue
                    if len(collected) >= limit:
                        break
            rows = jsonable(collected)
            reader = "tsv" if delimiter else "jsonl"
            total = len(rows)
        else:
            return {"available": False, "path": str(resolved),
                    "reason": f"no preview reader for '{suffix}' files"}
    except Exception as exc:
        return {"available": False, "path": str(resolved),
                "reason": f"preview failed: {type(exc).__name__}"}
    return {"available": True, "path": str(resolved), "reader": reader,
            "rows": rows, "rows_returned": len(rows), "row_limit": limit}
