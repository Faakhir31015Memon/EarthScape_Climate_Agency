"""Celery application + beat schedule for the EarthScape backend.

Import constraints (enforced by tests in the deployment environment):

* Importing this module must not require Redis or a running broker; every
  network probe stays inside task bodies.
* ``common/tasks.py`` dispatches ``run_registered_task.delay(task_name, job_id,
  job_type, payload)`` - positional - so the signature below accepts those
  four values in exactly that order.
* The task body reuses the registry populated by :mod:`common.tasks` - it does
  NOT implement its own dispatch logic. The same callable runs whether the job
  came from Celery or from the in-process thread pool.
* Beat entries only reference task names registered in ``common.tasks.REGISTRY``
  or the ``apps.*`` modules that self-register at import time below.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from celery import Celery  # noqa: E402
from celery.schedules import crontab  # noqa: E402
from config import env  # noqa: E402


logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent
for _path in (str(BACKEND_DIR), str(REPO_ROOT / "data-pipeline"),
              str(REPO_ROOT / "data-pipeline" / "spark")):
    if _path not in sys.path:
        sys.path.insert(0, _path)


_BROKER = env.get_raw("CELERY_BROKER_URL") or env.get_raw("REDIS_URL") or "memory://"
_BACKEND = env.get_raw("CELERY_RESULT_BACKEND") or env.get_raw("REDIS_URL") or "cacheio://"


app = Celery("earthscape")
app.config_from_object({
    "broker_url": _BROKER,
    "result_backend": _BACKEND,
    "task_serializer": "json",
    "result_serializer": "json",
    "accept_content": ["json"],
    "timezone": "UTC",
    "enable_utc": True,
    "task_track_started": True,
    "worker_prefetch_multiplier": 1,
    "broker_connection_retry_on_startup": True,
    "broker_transport_options": {"visibility_timeout": 3600},
})


# ------------------------------------------------------------------------ task body
@app.task(name="earthscape.run_registered_task", bind=False)
def run_registered_task(task_name: str, job_id: str, job_type: str,
                        payload: Dict[str, Any]) -> Dict[str, Any]:
    """Celery entry point for any name already present in ``common.tasks.REGISTRY``.

    Signature matches the ``.delay(task_name, job_id, job_type, payload)`` call
    in :func:`common.tasks.submit` (four positional arguments, keyword-agnostic).
    """
    from common import tasks as common_tasks

    if task_name not in common_tasks.REGISTRY:
        raise RuntimeError(f"task '{task_name}' is not registered in common.tasks.REGISTRY")
    if job_type not in common_tasks.JOB_REPOSITORIES:
        raise RuntimeError(f"unknown job_type '{job_type}'")
    func = common_tasks.REGISTRY[task_name]
    return func(job_id=job_id, **(payload or {}))


# ----------------------------------------------------------- scheduled entry points
# Each beat entry runs one of the platform's registered tasks. When Celery is not
# in use the same names are reachable through the API endpoints that arm the
# in-process alert monitor / ingestion scheduler; the beat tasks below simply
# reuse those entry points so the code path is identical whichever backend runs.
@app.task(name="earthscape.scheduled_ingestion_tick")
def scheduled_ingestion_tick() -> Dict[str, Any]:
    from apps.ingestion import scheduler as ingestion_scheduler

    sched = ingestion_scheduler.get_scheduler()
    result = sched.run_once()
    return {"runs": len(result) if isinstance(result, list) else 0,
            "status": sched.status()}


@app.task(name="earthscape.alert_scan_tick")
def alert_scan_tick() -> Dict[str, Any]:
    from apps.alerts import monitor

    return monitor.run_once(source="celery-beat")


@app.task(name="earthscape.analytics_summary_rebuild")
def analytics_summary_rebuild(metric: str = "temperature_c") -> Dict[str, Any]:
    """Queue the analytics-summary-rebuild workflow via the whitelisted task name."""
    from apps.processing import jobs as processing_jobs_service
    from apps.processing import runner as processing_runner, workflows

    entry = workflows.WORKFLOWS["mongodb.analytics_summary_rebuild"]
    document = processing_jobs_service.create_job(
        workflow="mongodb.analytics_summary_rebuild",
        engine=entry["engine"],
        dataset_id=None,
        parameters={"metric": metric, "limit": 50000},
        submitted_by=None,
        submitted_by_role="celery-beat",
        workflow_label=entry["label"],
    )
    executor = processing_runner.submit("mongodb.analytics_summary_rebuild",
                                                 document["id"],
                                                 {"metric": metric, "limit": 50000})
    return {"job_id": document["id"], "executor": executor}


@app.task(name="earthscape.local_backup")
def local_backup() -> Dict[str, Any]:
    from apps.monitoring import views as monitoring_views

    return monitoring_views.perform_backup(created_by=None, note="celery-beat")


@app.task(name="earthscape.model_retrain_check")
def model_retrain_check() -> Dict[str, Any]:
    """Report whether any registered model looks stale.

    Uses the read-only helpers already exposed by the machine-learning service:
    ``training_status`` (last training run) and ``catalogue`` (model counts). No
    model is retrained automatically - a human still has to hit the training
    endpoint - because silently refreshing a fitted model is exactly the kind of
    fabrication the platform refuses to make.
    """
    from apps.machine_learning import services as ml_services

    training = ml_services.training_status()
    catalogue = ml_services.catalogue()
    return {"training_status": training, "catalogue": catalogue,
            "auto_retrain_performed": False,
            "note": "This beat entry only surfaces the current state; a retrain must be requested explicitly."}


@app.task(name="earthscape.notification_retry")
def notification_retry(limit: int = 50) -> Dict[str, Any]:
    from apps.alerts import notifications

    return notifications.retry_due_notifications(limit=limit)


# -------------------------------------------------------------------- beat schedule
# Intervals are chosen to be safe defaults; a deployment without a broker never
# runs these because ``common.tasks.backend()`` returns ``in-process-thread-pool``
# and the Celery worker itself is not started.
app.conf.beat_schedule = {
    "scheduled-ingestion-tick": {
        "task": "earthscape.scheduled_ingestion_tick",
        "schedule": timedelta(seconds=max(60, env.get_int("INGESTION_TICK_SECONDS", 300))),
    },
    "alert-scan-tick": {
        "task": "earthscape.alert_scan_tick",
        "schedule": timedelta(seconds=max(30, env.get_int("ALERT_EVALUATION_SECONDS", 300))),
    },
    "analytics-summary-rebuild-nightly": {
        "task": "earthscape.analytics_summary_rebuild",
        "schedule": crontab(hour=3, minute=0),
        "args": (),
    },
    "local-backup-daily": {
        "task": "earthscape.local_backup",
        "schedule": crontab(hour=2, minute=30),
    },
    "model-retrain-check-weekly": {
        "task": "earthscape.model_retrain_check",
        "schedule": crontab(day_of_week=1, hour=4, minute=15),
    },
    "notification-retry": {
        "task": "earthscape.notification_retry",
        "schedule": timedelta(seconds=90),
    },
}


def beat_schedule_summary() -> Dict[str, Any]:
    """Diagnostic helper used by ``/api/v1/system/status`` to report the schedule."""
    moment = datetime.now(timezone.utc)
    entries = []
    for name, spec in app.conf.beat_schedule.items():
        schedule = spec.get("schedule")
        entries.append({
            "name": name,
            "task": spec.get("task"),
            "schedule": str(schedule) if schedule is not None else None,
        })
    return {"entries": entries, "generated_at": moment,
            "broker_url_configured": bool(env.get_raw("CELERY_BROKER_URL")
                                          or env.get_raw("REDIS_URL"))}


__all__ = ["app", "run_registered_task", "beat_schedule_summary"]
