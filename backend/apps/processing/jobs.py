"""Lifecycle helpers for the ``processing_jobs`` collection.

Every state transition a client observes on ``/api/v1/processing/jobs`` flows
through this module. The rules the module enforces:

* queued → running happens exactly once (an atomic ``update_many`` guard
  prevents double dispatch);
* every log line is a structured ``{at, level, message}`` object appended to a
  bounded ``log_lines`` array (never an unbounded push);
* finished_at is written only when the job actually reaches a terminal state,
  and the timestamp comes from ``repositories.utcnow()`` not the client;
* error messages are sanitised - the request id survives, exception classes
  and internal tracebacks do not leak into the API response (Hard rule 4);
* cancellation is only honoured while the job is still ``queued``: running
  executors (subprocess, Spark session, in-line pandas rollup) are not
  interrupt-safe so the API refuses to fake a cancel;
* active-first ordering: jobs whose status is queued/running are listed above
  completed/failed ones when ``active_first=true``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common.api import ApiError  # noqa: E402
from common.repositories import (  # noqa: E402
    RepositoryError,
    maybe_object_id,
    processing_jobs,
    to_object_id,
    utcnow,
)
from config import env  # noqa: E402


logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
ACTIVE_STATUSES = ("queued", "running")
CANCEL_REQUESTABLE = ("queued",)
MAX_LOG_LINES = int(env.get_int("PROCESSING_MAX_LOG_LINES", 200))
_ACTIVE_RE = re.compile(r"mongodb(\+srv)?://[^\s\"']+", re.IGNORECASE)


# --------------------------------------------------------------------------- create
def create_job(*, workflow: str, engine: str, dataset_id: Optional[str],
               parameters: Dict[str, Any], submitted_by: Optional[str],
               submitted_by_role: Optional[str],
               workflow_label: Optional[str] = None,
               dataset_name: Optional[str] = None,
               processing_mode: Optional[str] = None,
               attempt: int = 1,
               retry_of: Optional[str] = None) -> Dict[str, Any]:
    """Insert a fresh ``processing_jobs`` document in status ``queued``."""
    moment = utcnow()
    document: Dict[str, Any] = {
        "workflow": workflow,
        "workflow_label": workflow_label,
        "engine": engine,
        "dataset_id": dataset_id or None,
        "dataset_name": dataset_name,
        "status": "queued",
        "submitted_at": moment,
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "submitted_by": submitted_by,
        "submitted_by_role": submitted_by_role,
        "parameters": dict(parameters or {}),
        "metrics": None,
        "output": None,
        "log_lines": [],
        "error_message": None,
        "processing_mode": processing_mode,
        "attempt": int(attempt),
        "retry_of": retry_of,
        "schema_version": 3,
    }
    try:
        job_id = processing_jobs.insert_one(document)
    except RepositoryError as exc:
        logger.error("Processing job could not be stored: %s", exc)
        raise ApiError("internal_error", "The processing job could not be stored.") from exc
    document["id"] = job_id
    document["_id"] = maybe_object_id(job_id)
    return document


def get_job(job_id: str) -> Dict[str, Any]:
    try:
        document = processing_jobs.find_one({"_id": to_object_id(job_id)})
    except RepositoryError as exc:
        raise ApiError("not_found", "No such processing job.") from exc
    if document is None:
        raise ApiError("not_found", "No such processing job.")
    return document


# ------------------------------------------------------------------------ transitions
def mark_running(job_id: str) -> Optional[Dict[str, Any]]:
    """Atomic queued → running claim. Returns the updated doc or ``None`` on a
    double-dispatch (the second worker must skip the job)."""
    claimed = processing_jobs.update_many(
        {"_id": to_object_id(job_id), "status": "queued"},
        {"status": "running", "started_at": utcnow()},
    )
    if not claimed:
        return None
    return processing_jobs.find_one({"_id": to_object_id(job_id)})


def set_status(job_id: str, status: str, **extra: Any) -> Dict[str, Any]:
    updates: Dict[str, Any] = {"status": status, **extra}
    if status in TERMINAL_STATUSES and "finished_at" not in updates:
        updates["finished_at"] = utcnow()
    updated = processing_jobs.update_by_id(job_id, updates)
    if updated is None:
        raise ApiError("not_found", "No such processing job.")
    return updated


# ------------------------------------------------------------------------ log lines
def append_log(job_id: str, level: str, message: str) -> None:
    """Append a structured log entry, bounded to the most recent ``MAX_LOG_LINES``.

    Log message text is passed through ``sanitize_error`` so raw stack traces and
    connection strings never reach the API response.
    """
    line = {"at": utcnow(), "level": str(level or "info").lower()[:16],
            "message": sanitize_error(str(message))}
    processing_jobs.update_by_id(job_id, {
        "$push": {"log_lines": {"$each": [line], "$slice": -MAX_LOG_LINES}},
    })


def extend_logs(job_id: str, entries: List[Dict[str, Any]]) -> None:
    """Bulk log append used after subprocess runs (each entry may be a bare string
    produced by the streaming orchestrator or a shaped dict)."""
    shaped: List[Dict[str, Any]] = []
    moment = utcnow()
    for entry in entries or []:
        if isinstance(entry, dict):
            message = entry.get("message") or entry.get("msg") or str(entry)
            level = entry.get("level") or "info"
            at = entry.get("at") or moment
        else:
            message = str(entry)
            level = "info"
            at = moment
        shaped.append({"at": at, "level": str(level).lower()[:16],
                       "message": sanitize_error(message)})
    if not shaped:
        return
    processing_jobs.update_by_id(job_id, {
        "$push": {"log_lines": {"$each": shaped, "$slice": -MAX_LOG_LINES}},
    })


# ------------------------------------------------------------------- metrics/output
def record_metrics(job_id: str, metrics: Dict[str, Any], output: Optional[Dict[str, Any]] = None) -> None:
    updates: Dict[str, Any] = {}
    if metrics:
        updates["metrics"] = _clean_metrics(metrics)
    if output:
        updates["output"] = _clean_output(output)
    if not updates:
        return
    existing = processing_jobs.find_one({"_id": to_object_id(job_id)}, {"metrics": 1, "output": 1})
    merged_metrics = {**(existing or {}).get("metrics", {}), **(updates.get("metrics") or {})}
    merged_output = {**(existing or {}).get("output", {}), **(updates.get("output") or {})}
    payload: Dict[str, Any] = {}
    if merged_metrics:
        payload["metrics"] = merged_metrics
    if merged_output:
        payload["output"] = merged_output
    if payload:
        processing_jobs.update_by_id(job_id, payload)


def finish_job(job_id: str, *, status: str, metrics: Optional[Dict[str, Any]] = None,
               output: Optional[Dict[str, Any]] = None,
               error_message: Optional[str] = None) -> Dict[str, Any]:
    if status not in TERMINAL_STATUSES:
        raise ApiError("validation_failed",
                       f"finish_job requires a terminal status (one of {TERMINAL_STATUSES}).")
    started = processing_jobs.find_one({"_id": to_object_id(job_id)}, {"started_at": 1})
    finished = utcnow()
    duration: Optional[float] = None
    raw_started = (started or {}).get("started_at")
    if isinstance(raw_started, (int, float)):
        duration = round(finished.timestamp() - float(raw_started), 3)
    elif isinstance(raw_started, datetime):
        started_dt = raw_started if raw_started.tzinfo else raw_started.replace(tzinfo=timezone.utc)
        duration = round((finished - started_dt).total_seconds(), 3)
    elif isinstance(raw_started, str):
        # mongomock in local-development round-trips datetime as an ISO string.
        try:
            parsed = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None:
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            duration = round((finished - parsed).total_seconds(), 3)
    updates: Dict[str, Any] = {"status": status, "finished_at": finished,
                                "duration_seconds": duration,
                                "error_message": sanitize_error(error_message) if error_message else None}
    if metrics:
        updates["metrics"] = _clean_metrics(metrics)
    if output:
        updates["output"] = _clean_output(output)
    updated = processing_jobs.update_by_id(job_id, updates)
    if updated is None:
        raise ApiError("not_found", "No such processing job.")
    return updated


def fail_job(job_id: str, message: str,
             metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return finish_job(job_id, status="failed", error_message=message, metrics=metrics)


# ------------------------------------------------------------------------ cancel
def cancel_job(job_id: str, actor_id: Optional[str]) -> Dict[str, Any]:
    document = get_job(job_id)
    status = document.get("status")
    if status in TERMINAL_STATUSES:
        raise ApiError("conflict",
                       f"A {status} job cannot be cancelled.", status=409)
    if status == "running":
        raise ApiError("conflict",
                       "This processing job is already running; the current executors "
                       "(subprocess, Spark session, in-line rollup) are not interrupt-safe, "
                       "so cancellation mid-run would be dishonest. Wait for completion or "
                       "check /api/v1/system/status for progress.", status=409)
    # Only queued is genuinely cancellable.
    updated = processing_jobs.find_one_and_update(
        {"_id": to_object_id(job_id), "status": "queued"},
        {"status": "cancelled", "finished_at": utcnow(),
         "error_message": "Cancelled by the submitter before the executor started.",
         "cancelled_by": actor_id},
    )
    if updated is None:
        raise ApiError("conflict",
                       "This job was claimed by a worker before the cancel could be recorded.",
                       status=409)
    return updated


# ------------------------------------------------------------------------ listing
def list_jobs(*, workflow: Optional[str] = None, status: Optional[str] = None,
              dataset_id: Optional[str] = None, submitted_by: Optional[str] = None,
              active_first: bool = False, page: int = 1, page_size: int = 50) -> Dict[str, Any]:
    base: Dict[str, Any] = {}
    if workflow:
        base["workflow"] = workflow
    if status:
        base["status"] = status
    if dataset_id:
        base["dataset_id"] = dataset_id
    if submitted_by:
        base["submitted_by"] = submitted_by
    if not active_first:
        return processing_jobs.paginate(base, page=page, page_size=page_size,
                                        sort=[("submitted_at", -1)])

    # Two bounded queries stitched together so queued/running always lead.
    active_query = dict(base)
    active_query["status"] = {"$in": list(ACTIVE_STATUSES)}
    finished_query = dict(base)
    finished_query["status"] = {"$nin": list(ACTIVE_STATUSES)}

    window_end = page * page_size
    active_docs = processing_jobs.find(active_query, sort=[("submitted_at", -1)], limit=window_end)
    remaining = max(0, window_end - len(active_docs))
    finished_docs = processing_jobs.find(finished_query, sort=[("submitted_at", -1)],
                                          limit=remaining) if remaining else []
    combined = active_docs + finished_docs
    slice_start = (page - 1) * page_size
    items = combined[slice_start:slice_start + page_size]
    active_total = processing_jobs.count(active_query)
    finished_total = processing_jobs.count(finished_query)
    total = active_total + finished_total
    pages = (total + page_size - 1) // page_size if total else 0
    from common.repositories import serialize_many

    return {
        "items": serialize_many(items),
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": page > 1,
        "ordering": "active_first",
    }


def summarise_jobs() -> Dict[str, int]:
    """Real counts by status across the collection."""
    out = {status: processing_jobs.count({"status": status})
           for status in ["queued", "running", "completed", "failed", "cancelled"]}
    out["total"] = processing_jobs.count({})
    return out


# --------------------------------------------------------------------- sanitising
def sanitize_error(raw: Any) -> str:
    """Never let a raw connection string or traceback leak to the API."""
    text = str(raw or "")
    text = _ACTIVE_RE.sub("mongodb://***", text)
    text = re.sub(r"Traceback \(most recent call last\):[\s\S]+", "internal error", text)
    text = text.strip()
    return (text[:500] or "unspecified error")


def _clean_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    allowed = ("rows_in", "rows_out", "accepted", "rejected", "duplicates",
               "duration_seconds", "peak_memory_mb", "processing_mode",
               "engine_version")
    out: Dict[str, Any] = {}
    for key in allowed:
        if key in metrics and metrics[key] is not None:
            value = metrics[key]
            out[key] = value if not isinstance(value, str) else sanitize_error(value)
    return out


def _clean_output(output: Dict[str, Any]) -> Dict[str, Any]:
    allowed = ("data_lake_uris", "summary_keys", "parquet_paths")
    out: Dict[str, Any] = {}
    for key in allowed:
        if key in output and output[key] is not None:
            value = output[key]
            if isinstance(value, (list, tuple)):
                out[key] = [str(item)[:400] for item in list(value)[:200]]
    return out


# ------------------------------------------------------------------------ helpers
Tuple2 = Tuple[Any, Any]  # small alias kept to appease strict linters


def active_count() -> int:
    return processing_jobs.count({"status": {"$in": list(ACTIVE_STATUSES)}})


def queued_count() -> int:
    return processing_jobs.count({"status": "queued"})
