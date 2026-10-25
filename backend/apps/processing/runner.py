"""Registered processing task bodies dispatched through :mod:`common.tasks`.

Rules:

* Every task is registered under a stable name from
  :data:`apps.processing.workflows.WORKFLOWS`; the API can only dispatch by
  that registry key - never by a client-supplied module, callable or path.
* Subprocess invocations use an *argument list* with ``shell=False`` and never
  interpolate a client-controlled token into the executable or script path.
* Before executing, the runner re-checks the engine via
  :func:`apps.processing.engines.workflow_availability`; when the engine is
  genuinely unavailable the job transitions to ``failed`` with the specific
  reason rather than faking success.
* Any exception escaping the executor becomes a sanitised ``error_message`` on
  the job document (no tracebacks, no connection strings).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.processing import engines, jobs, workflows  # noqa: E402
from common import audit, tasks  # noqa: E402
from common.repositories import datasets as datasets_repo, maybe_object_id  # noqa: E402
from config import env  # noqa: E402


logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SPARK_TIMEOUT = int(env.get_int("PROCESSING_SPARK_TIMEOUT_SECONDS", 1800))
DEFAULT_MR_TIMEOUT = int(env.get_int("PROCESSING_MAPREDUCE_TIMEOUT_SECONDS", 1800))
DEFAULT_STREAMING_TIMEOUT = int(env.get_int("PROCESSING_STREAMING_TIMEOUT_SECONDS", 600))


# --------------------------------------------------------------------- subprocesses
def _run_subprocess(command: List[str], *, cwd: Path, timeout: int, env_extra: Optional[Dict[str, str]] = None
                    ) -> Dict[str, Any]:
    """Run a whitelisted command list. Never ``shell=True``; env extras only come
    from server-side constants (dataset ids validated upstream)."""
    merged_env = dict(__import__("os").environ)
    merged_env["PYTHONPATH"] = str(BACKEND_DIR) + (
        __import__("os").pathsep + merged_env.get("PYTHONPATH", "") if merged_env.get("PYTHONPATH") else "")
    if env_extra:
        merged_env.update({str(k): str(v) for k, v in env_extra.items()})
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            cwd=str(cwd), env=merged_env, shell=False)
    duration = round(time.monotonic() - started, 3)
    return {"return_code": result.returncode, "stdout": result.stdout,
            "stderr": result.stderr, "duration_seconds": duration, "command": command}


# ------------------------------------------------------------------------ dispatch
def _claim_or_skip(job_id: str) -> Optional[Dict[str, Any]]:
    claim = jobs.mark_running(job_id)
    if claim is not None:
        return claim
    current = jobs.processing_jobs.find_one({"_id": maybe_object_id(job_id)},
                                             {"status": 1, "workflow": 1})
    if current is None:
        logger.warning("Processing job %s no longer exists", job_id)
        return None
    logger.info("Processing job %s already in status '%s'; not re-running",
                job_id, current.get("status"))
    return None


def _guard_engine(job_id: str, entry: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    available, reason = engines.workflow_availability(entry["engine"], entry)
    if available:
        return True, None
    jobs.append_log(job_id, "error",
                    f"engine '{entry['engine']}' is unavailable: {reason}")
    jobs.fail_job(job_id, f"Engine unavailable: {reason}")
    return False, reason


# ------------------------------------------------------------------------ mapreduce
def _execute_mapreduce(job_id: str, entry: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    script = BACKEND_DIR / entry["script"]
    if not script.is_file():
        raise FileNotFoundError(f"processing script missing: {script.name}")
    dataset_id = str(parameters.get("dataset_id") or "") or None
    limit = int(parameters.get("limit") or 200000)
    command: List[str] = [sys.executable, str(script), *entry.get("script_args", []),
                          "--json", "--limit", str(max(1, min(limit, 1_000_000)))]
    if dataset_id:
        command += ["--dataset-id", dataset_id]
    if parameters.get("force_local"):
        command.append("--force-local")
    result = _run_subprocess(command, cwd=BACKEND_DIR, timeout=DEFAULT_MR_TIMEOUT)
    stdout = (result["stdout"] or "").strip()
    summary: Optional[Dict[str, Any]] = None
    if stdout:
        try:
            summary = json.loads(stdout)
        except ValueError:
            summary = None
    if result["return_code"] != 0 or summary is None:
        tail = (result["stderr"] or "")[-400:]
        raise RuntimeError(f"MapReduce orchestrator exited {result['return_code']}: {tail}")

    jobs.extend_logs(job_id, [{"level": "info", "message": line} for line in
                              (summary.get("log_lines") or [])])
    metrics = {
        "rows_in": int(summary.get("rows_in") or 0),
        "rows_out": int(summary.get("rows_out") or 0),
        "processing_mode": summary.get("processing_mode"),
        "engine_version": summary.get("engine"),
        "duration_seconds": float(summary.get("duration_seconds") or result["duration_seconds"]),
    }
    lake_info = summary.get("data_lake") or {}
    output = {
        "data_lake_uris": [str(k) for k in (lake_info.get("output_keys") or [])][:200],
        "parquet_paths": [],
        "summary_keys": [],
    }
    final_status = "completed" if summary.get("success") else "failed"
    error_message = None
    if final_status == "failed":
        error_message = (f"MapReduce run reported success=false (mode="
                          f"{summary.get('processing_mode')}, input_source="
                          f"{summary.get('input_source')})")
    return {"status": final_status, "metrics": metrics, "output": output,
            "error_message": error_message, "run_id": summary.get("run_id")}


# -------------------------------------------------------------------------- spark
def _execute_spark(job_id: str, entry: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    sys.path.insert(0, str(BACKEND_DIR / "data-pipeline"))
    sys.path.insert(0, str(BACKEND_DIR / "data-pipeline" / "spark"))
    import importlib

    jobs_module = importlib.import_module("jobs")
    session_module = importlib.import_module("spark_session")
    build_spark_session = session_module.build_spark_session
    SparkUnavailable = session_module.SparkUnavailable

    try:
        build_spark_session.__module__  # touch to keep linters happy
    except Exception:  # noqa: BLE001 - never raised in practice
        pass

    spark_job_name = entry["spark_job"]
    job_func = jobs_module.JOBS.get(spark_job_name)
    if job_func is None:
        raise RuntimeError(f"Spark job '{spark_job_name}' is not registered in spark/jobs.py")

    try:
        spark = build_spark_session(f"earthscape-processing-{spark_job_name}")
    except SparkUnavailable as exc:
        jobs.append_log(job_id, "error", f"Spark session unavailable: {exc}")
        raise RuntimeError(f"Spark session unavailable: {str(exc)[:200]}") from exc

    try:
        options: Dict[str, Any] = {"dataset_id": parameters.get("dataset_id")}
        for key in ("input_path", "metric", "lags", "mapreduce_output_path", "persist_summaries"):
            if key in parameters:
                options[key] = parameters[key]
        if not options.get("input_path"):
            options.pop("input_path", None)
        jobs.append_log(job_id, "info", f"starting spark job {spark_job_name}")
        started = time.monotonic()
        summary, out_path = job_func(spark, options)
        duration = round(time.monotonic() - started, 3)
        jobs.append_log(job_id, "info", f"{spark_job_name} finished in {duration}s")
    finally:
        try:
            spark.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Spark stop failed: %s", type(exc).__name__)

    metrics = {
        "rows_in": int(summary.get("rows_in") or summary.get("input_rows") or 0),
        "rows_out": int(summary.get("rows_out") or summary.get("files_written") or 0),
        "duplicates": int(summary.get("duplicates_dropped") or 0),
        "rejected": int(summary.get("rejected_missing_date_or_region") or 0),
        "duration_seconds": float(summary.get("duration_seconds") or duration),
        "processing_mode": summary.get("processing_mode") or f"pyspark",
        "engine_version": summary.get("spark_version") or None,
    }
    files_written = int(summary.get("files_written") or 0)
    lake_uris: List[str] = [str(uri) for uri in (summary.get("data_lake_uris") or [])[:200]]
    if out_path and not lake_uris:
        lake_uris.append(str(out_path))
    output = {
        "data_lake_uris": lake_uris,
        "parquet_paths": [str(k) for k in summary.get("parquet_paths", [])][:200],
        "summary_keys": [str(k) for k in summary.get("summary_keys", [])][:200],
    }
    return {"status": "completed" if files_written >= 0 else "failed",
            "metrics": metrics, "output": output,
            "run_id": summary.get("run_id")}


# ----------------------------------------------------------------------- streaming
def _execute_streaming(job_id: str, entry: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    script = BACKEND_DIR / entry["script"]
    if not script.is_file():
        raise FileNotFoundError(f"streaming script missing: {script.name}")
    command: List[str] = [sys.executable, str(script), "--once"]
    duration = int(parameters.get("duration_seconds") or 0)
    if duration > 0:
        command = [sys.executable, str(script), "--duration", str(max(1, min(duration, 300)))]
    result = _run_subprocess(command, cwd=BACKEND_DIR, timeout=DEFAULT_STREAMING_TIMEOUT)
    stdout = (result["stdout"] or "").strip()
    payload: Optional[Dict[str, Any]] = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except ValueError:
            payload = None
    if payload is None:
        tail = (result["stderr"] or "")[-400:]
        raise RuntimeError(f"Streaming processor exited {result['return_code']} without JSON: {tail}")
    counters = payload.get("counters") or {}
    ok = bool(payload.get("ok"))
    metrics = {
        "rows_in": int(counters.get("events_processed") or 0),
        "accepted": int(counters.get("accepted") or 0),
        "rejected": int(counters.get("rejected") or 0),
        "duplicates": int(counters.get("duplicates") or 0),
        "duration_seconds": float(result["duration_seconds"]),
        "processing_mode": counters.get("processing_mode") or "streaming",
        "engine_version": counters.get("engine_version"),
    }
    output = {"data_lake_uris": [], "parquet_paths": [], "summary_keys": []}
    if not ok:
        raise RuntimeError(f"streaming reported failure: {str(payload.get('error'))[:200]}")
    return {"status": "completed", "metrics": metrics, "output": output,
            "log_tail": (payload.get("log_lines") or [])[-20:]}


# -------------------------------------------------------- mongodb analytics rebuild
def _execute_mongodb_rebuild(job_id: str, entry: Dict[str, Any], parameters: Dict[str, Any]) -> Dict[str, Any]:
    from common import analytics as _analytics
    from common.database import get_database
    from common.repositories import climate_observations

    metric = str(parameters.get("metric") or "temperature_c")
    if metric not in _analytics.METRICS:
        raise RuntimeError(f"metric '{metric}' is not a canonical analytics metric")
    limit = int(parameters.get("limit") or 50000)
    dataset_id = parameters.get("dataset_id") or None
    query = {"dataset_id": dataset_id} if dataset_id else {}
    rows: List[Dict[str, Any]] = []
    cursor = climate_observations.stream(query, projection={"_id": 0},
                                          sort=[("observation_date", 1)], batch_size=2000)
    for row in cursor:
        if len(rows) >= limit:
            break
        rows.append(row)
    if not rows:
        jobs.append_log(job_id, "warn", "no observations matched the query; summaries were not rewritten")
        return {"status": "completed",
                "metrics": {"rows_in": 0, "rows_out": 0, "processing_mode": "mongodb"},
                "output": {"data_lake_uris": [], "parquet_paths": [], "summary_keys": []}}

    stored_keys: List[str] = []
    coverage = _analytics.coverage_report(rows)
    stored_keys.append(_analytics.store_summary(
        metric=f"coverage_{metric}", scope={"kind": "dataset" if dataset_id else "platform"},
        payload={"kind": "coverage_report", "metric": metric, **coverage},
        source_datasets=[str(dataset_id or "all")],
        workflow="analytics.summary_rebuild", processing_mode="mongodb"))

    baselines = _analytics.seasonal_baselines(rows, metric)
    for key, payload in baselines.items():
        stored_keys.append(_analytics.store_summary(
            metric=f"seasonal_baseline_{metric}",
            scope={"baseline_key": key, "region_code": payload.get("region_code"),
                   "month": payload.get("month")},
            payload={"kind": "seasonal_baseline", **payload},
            source_datasets=[str(dataset_id or "all")],
            workflow="analytics.summary_rebuild", processing_mode="mongodb"))

    trend = _analytics.trend_series(rows, metric, frequency="monthly", group_by="region")
    for series in (trend.get("series") or [])[:200]:
        stored_keys.append(_analytics.store_summary(
            metric=f"trend_monthly_{metric}",
            scope={"region_code": series.get("region_code") or "global"},
            payload={"kind": "trend_series", "frequency": "monthly", **series},
            source_datasets=[str(dataset_id or "all")],
            workflow="analytics.summary_rebuild", processing_mode="mongodb"))

    get_database().flush_if_dirty()
    return {"status": "completed",
            "metrics": {"rows_in": len(rows), "rows_out": len(stored_keys),
                        "processing_mode": "mongodb"},
            "output": {"data_lake_uris": [], "parquet_paths": [],
                        "summary_keys": [str(k)[:400] for k in stored_keys][:200]}}


# ------------------------------------------------------------------------ executor
def run_workflow(job_id: str, workflow_id: str) -> Dict[str, Any]:
    """Common execution pipeline used by every registered task name."""
    entry = workflows.get_workflow(workflow_id)
    if entry is None:
        jobs.fail_job(job_id, f"workflow '{workflow_id}' is not registered")
        return {"status": "failed", "reason": "unknown_workflow"}

    document = jobs.processing_jobs.find_one({"_id": maybe_object_id(job_id)})
    if document is None:
        logger.warning("Processing job %s no longer exists", job_id)
        return {"status": "missing"}
    if document.get("status") != "running":
        claim = _claim_or_skip(job_id)
        if claim is None:
            return {"status": document.get("status"), "skipped": True}
    else:
        claim = document

    parameters = dict(claim.get("parameters") or {})
    jobs.append_log(job_id, "info", f"workflow={workflow_id} engine={entry['engine']}")

    ok, reason = _guard_engine(job_id, entry)
    if not ok:
        audit.record(action="processing.job.engine_unavailable", resource_type="processing_job",
                     resource_id=job_id, outcome="failure",
                     metadata={"workflow": workflow_id, "reason": (reason or "")[:180]})
        return {"status": "failed", "reason": reason}

    executors: Dict[str, Any] = {
        "mapreduce": _execute_mapreduce,
        "pyspark": _execute_spark,
        "streaming": _execute_streaming,
        "mongodb": _execute_mongodb_rebuild,
    }
    executor = executors.get(entry["engine"])
    if executor is None:
        jobs.fail_job(job_id, f"no executor is implemented for engine '{entry['engine']}'")
        return {"status": "failed", "reason": "no_executor"}

    try:
        result = executor(job_id, entry, parameters)
    except subprocess.TimeoutExpired as exc:
        message = f"executor exceeded the configured timeout ({type(exc).__name__})"
        jobs.fail_job(job_id, message)
        return {"status": "failed", "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001 - every workflow failure surfaces here
        message = f"{type(exc).__name__}: {str(exc)[:280]}"
        logger.error("Processing job %s failed: %s", job_id, message)
        jobs.fail_job(job_id, message)
        audit.record(action="processing.job.failed", resource_type="processing_job",
                     resource_id=job_id, outcome="failure",
                     metadata={"workflow": workflow_id, "reason": message[:180]})
        return {"status": "failed", "reason": message}

    status = result.get("status", "completed")
    metrics = result.get("metrics") or {}
    output = result.get("output") or {}
    error_message = result.get("error_message")
    if status == "completed":
        updated = jobs.finish_job(job_id, status="completed", metrics=metrics, output=output)
        audit.record(action="processing.job.completed", resource_type="processing_job",
                     resource_id=job_id, outcome="success",
                     metadata={"workflow": workflow_id,
                               "rows_in": metrics.get("rows_in"),
                               "rows_out": metrics.get("rows_out"),
                               "duration_seconds": metrics.get("duration_seconds")})
        return {"status": "completed", "job_id": job_id,
                "duration_seconds": (updated or {}).get("duration_seconds")}
    updated = jobs.finish_job(job_id, status="failed", metrics=metrics,
                               output=output, error_message=error_message or "run reported failure")
    audit.record(action="processing.job.failed", resource_type="processing_job",
                 resource_id=job_id, outcome="failure",
                 metadata={"workflow": workflow_id, "reason": (error_message or "")[:180]})
    return {"status": "failed", "job_id": job_id}


# --------------------------------------------------------- registry entry per task
def _register(name: str, workflow_id: str):
    def body(*, job_id: str, **_: Any) -> Dict[str, Any]:  # matches tasks.submit kwargs contract
        return run_workflow(job_id, workflow_id)
    body.__name__ = f"task_{name.replace('.', '_')}"
    tasks.register(name)(body)
    return body


for _workflow_id, _entry in workflows.WORKFLOWS.items():
    _register(_entry["task_name"], _workflow_id)


def submit(workflow_id: str, job_id: str, parameters: Dict[str, Any]) -> str:
    """Queue the workflow through ``common.tasks``.

    Celery is used only when its broker is reachable (see :func:`common.tasks.backend`);
    otherwise the in-process thread pool executes the same registered callable.
    """
    entry = workflows.require_workflow(workflow_id)
    return tasks.submit(entry["task_name"], job_id=job_id, job_type="processing",
                        payload={"workflow": workflow_id, "parameters": parameters})
