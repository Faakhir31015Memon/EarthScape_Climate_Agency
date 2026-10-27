"""Background execution.

A bounded
in-process thread pool runs the same registered callables. Only names present in
REGISTRY can be executed.
"""
from __future__ import annotations

import logging
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

from config import env

logger = logging.getLogger(__name__)

REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {}
_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()
_ACTIVE: Dict[str, Dict[str, Any]] = {}
_ACTIVE_LOCK = threading.Lock()


class BackendUnavailable(RuntimeError):
    pass


def register(name: str):
    def decorator(func: Callable[..., Dict[str, Any]]):
        REGISTRY[name] = func
        return func
    return decorator


def pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=env.get_int("BACKGROUND_WORKERS", 4),
                thread_name_prefix="earthscape-worker",
            )
        return _pool


def backend() -> str:
    return "in-process-thread-pool"


JOB_REPOSITORIES: Dict[str, str] = {
    "ingestion": "ingestion_jobs",
    "processing": "processing_jobs",
    "report": "reports",
}


def submit(task_name: str, *, job_id: str, job_type: str, payload: Dict[str, Any]) -> str:
    """Queue a registered task. Returns the executor that accepted it."""
    if task_name not in REGISTRY:
        raise BackendUnavailable(f"Task '{task_name}' is not registered.")
    if job_type not in JOB_REPOSITORIES:
        raise BackendUnavailable(f"Unknown job type '{job_type}'.")

    with _ACTIVE_LOCK:
        _ACTIVE[job_id] = {"task": task_name, "job_type": job_type}
    pool().submit(_execute, task_name, job_id, job_type, payload)
    return "in-process-thread-pool"


def _execute(task_name: str, job_id: str, job_type: str, payload: Dict[str, Any]) -> None:
    from common import repositories

    repository = getattr(repositories, JOB_REPOSITORIES[job_type])
    try:
        REGISTRY[task_name](job_id=job_id, **payload)
    except Exception as exc:  # noqa: BLE001 - worker boundary
        logger.error("Background task %s (job %s) failed: %s", task_name, job_id, exc)
        logger.debug("%s", traceback.format_exc())
        try:
            repository.update_by_id(job_id, {
                "status": "failed",
                "error_message": str(exc)[:500],
                "finished_at": repositories.utcnow(),
            })
        except Exception as mark_exc:  # noqa: BLE001
            logger.error("Could not mark job %s failed: %s", job_id, mark_exc)
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(job_id, None)
        try:
            from common.database import get_database

            get_database().flush_if_dirty()
        except Exception as exc:  # noqa: BLE001
            logger.error("Post-task flush failed: %s", exc)


def active_jobs() -> Dict[str, Dict[str, Any]]:
    with _ACTIVE_LOCK:
        return dict(_ACTIVE)


def active_job_count() -> int:
    with _ACTIVE_LOCK:
        return len(_ACTIVE)


def shutdown(wait: bool = True) -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=wait)
            _pool = None
