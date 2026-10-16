"""Background task execution with an honest engine choice.

Celery+Redis is used when `CELERY_ENABLED=true` and a broker is reachable; otherwise a
bounded in-process thread pool runs the same task functions. Task status is always
written to MongoDB so a restart never loses the record of what was attempted.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, Callable

from common.db import collection, find_one, now_utc, update_one
from common.env import ENV
from common.logging_config import get_logger
from common.serialization import jsonable

logger = get_logger("earthscape.tasks")

_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
MAX_WORKERS = ENV.integer("BACKGROUND_WORKERS", 4)

TASK_COLLECTION = "processing_jobs"


def engine() -> str:
    if ENV.flag("CELERY_ENABLED", False) and ENV.exists("REDIS_URL"):
        try:  # pragma: no cover - exercised only when a broker exists
            from celery import Celery  # noqa: F401

            return "celery"
        except Exception:
            return "threaded"
    return "threaded"


def pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=max(1, int(MAX_WORKERS)), thread_name_prefix="task")
        return _POOL


def create_job(kind: str, *, payload: dict | None, created_by: str | None,
               label: str | None = None, collection_name: str = TASK_COLLECTION) -> dict:
    document = {
        "job_ref": f"job_{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "label": label or kind,
        "status": "queued",
        "engine": engine(),
        "payload": payload or {},
        "result": None,
        "logs": [],
        "metrics": {},
        "attempt": 0,
        "max_attempts": 3,
        "created_by": created_by,
        "queued_at": now_utc(),
        "started_at": None,
        "finished_at": None,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    result = collection(collection_name).insert_one(document)
    document["_id"] = result.inserted_id
    return document


def append_log(job_id: Any, level: str, message: str, *, collection_name: str = TASK_COLLECTION) -> None:
    entry = {"at": now_utc(), "level": level, "message": str(message)[:2000]}
    collection(collection_name).update_one(
        {"_id": job_id},
        {"$push": {"logs": {"$each": [entry], "$slice": -200}}, "$set": {"updated_at": now_utc()}},
    )


def set_status(job_id: Any, status: str, *, result: Any = None, metrics: dict | None = None,
               error: str | None = None, collection_name: str = TASK_COLLECTION) -> None:
    update: dict[str, Any] = {"status": status, "updated_at": now_utc()}
    if status == "running":
        update["started_at"] = now_utc()
    if status in ("completed", "failed", "cancelled"):
        update["finished_at"] = now_utc()
        update["duration_seconds"] = None
    if result is not None:
        update["result"] = result
    if metrics is not None:
        update["metrics"] = metrics
    if error is not None:
        update["error"] = error[:500]
    collection(collection_name).update_one({"_id": job_id}, {"$set": update})


def _sanitise_failure(text: str) -> str:
    """Keep the exception type and a short message; never expose a full traceback."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "Unknown failure"
    return f"{lines[-1][:300]}"


def submit(kind: str, func: Callable[..., dict], *, args: tuple = (), kwargs: dict | None = None,
            created_by: str | None = None, payload: dict | None = None, label: str | None = None,
            collection_name: str = TASK_COLLECTION) -> dict:
    """Persist a job record, then execute it in the background and return immediately."""
    job = create_job(kind, payload=payload, created_by=created_by, label=label,
                     collection_name=collection_name)
    _run(job["_id"], func, args, kwargs or {}, collection_name)
    return jsonable(job)


def _run(job_id, func: Callable[..., dict], args: tuple, kwargs: dict,
         collection_name: str) -> None:
    def work() -> None:
        set_status(job_id, "running", collection_name=collection_name)
        append_log(job_id, "info", f"started by engine={engine()}", collection_name=collection_name)
        try:
            outcome = func(*args, **kwargs)
            if not isinstance(outcome, dict):
                outcome = {"output": outcome}
            set_status(job_id, "completed", result=outcome, metrics=outcome.get("metrics"),
                       collection_name=collection_name)
            append_log(job_id, "info", "completed", collection_name=collection_name)
        except Exception as exc:
            logger.error("background task failed: %s", exc, exc_info=True)
            collection(collection_name).update_one(
                {"_id": job_id}, {"$inc": {"attempt": 1}}
            )
            set_status(job_id, "failed", error=_sanitise_failure(traceback.format_exc()),
                       collection_name=collection_name)
            append_log(job_id, "error", _sanitise_failure(traceback.format_exc()),
                       collection_name=collection_name)

    pool().submit(work)


def retry(job_id, func: Callable[..., dict], *, args: tuple = (), kwargs: dict | None = None,
          collection_name: str = TASK_COLLECTION) -> dict | None:
    job = find_one(collection_name, {"_id": job_id})
    if not job:
        return None
    if job.get("status") not in ("failed", "cancelled"):
        return {"retryable": False, "reason": f"job status is '{job.get('status')}', only failed jobs can retry"}
    if int(job.get("attempt", 0)) >= int(job.get("max_attempts", 3)):
        return {"retryable": False, "reason": "attempt limit reached"}
    collection(collection_name).update_one(
        {"_id": job_id}, {"$set": {"status": "queued", "queued_at": now_utc(), "error": None,
                                   "updated_at": now_utc()}}
    )
    _run(job_id, func, args, kwargs or {}, collection_name)
    return {"retryable": True, "status": "queued"}


def sweep_stale_jobs(max_age_minutes: int = 120, *, collection_name: str = TASK_COLLECTION) -> int:
    """Mark jobs left 'running' by a process crash as failed so the UI is honest."""
    cutoff = now_utc() - timedelta(minutes=max_age_minutes)
    result = collection(collection_name).update_many(
        {"status": {"$in": ["running", "queued"]}, "updated_at": {"$lt": cutoff}},
        {"$set": {"status": "failed", "error": "stale: worker no longer reporting; retry the job",
                  "finished_at": now_utc(), "updated_at": now_utc()}},
    )
    return result.modified_count
