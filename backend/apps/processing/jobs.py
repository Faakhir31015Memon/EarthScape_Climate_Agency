"""Background task functions run on `common.tasks`, with logs and status written to Mongo.

`submit()` cannot know its own document id before the thread starts, so each submission stores
a unique `run_token` in the job payload; the task resolves its job record through it and can
then append logs and stamp engine facts on the very document the API returns to the client.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from apps.processing import catalogue, runner
from common.db import find_one, now_utc, update_one
from common.exceptions import ValidationError
from common.logging_config import get_logger
from common.serialization import jsonable
from common.tasks import append_log, set_status

logger = get_logger("earthscape.processing")

TASK_KIND = "processing.run"


def new_run_token(workflow_id: str) -> str:
    return f"{workflow_id}:{uuid4().hex}"


def task_kwargs(*, workflow: str, params: dict, dataset_id: str, run_token: str,
                created_by: Any) -> dict:
    """The single dict used both as the job payload and as the task kwargs, so a retry
    re-runs exactly what was validated at request time."""
    return {
        "workflow": workflow,
        "params": jsonable(params or {}),
        "dataset_id": dataset_id or "",
        "run_token": run_token,
        "created_by": str(created_by or ""),
    }


def kwargs_from_payload(payload: Any) -> dict:
    """Rebuild validated task kwargs from a stored payload for a safe re-run."""
    if not isinstance(payload, dict) or not payload.get("workflow"):
        raise ValidationError("This job record does not carry the payload needed to re-run it.",
                              details=["Expected payload keys: workflow, params, dataset_id, run_token."])
    workflow = catalogue.resolve_workflow(payload.get("workflow"))
    params = payload.get("params") or {}
    return task_kwargs(workflow=workflow["id"], params=catalogue.validate(workflow, params),
                       dataset_id=str(payload.get("dataset_id") or ""),
                       run_token=str(payload.get("run_token") or ""),
                       created_by=str(payload.get("created_by") or ""))


def _job_id_for(run_token: str) -> Optional[Any]:
    if not run_token:
        return None
    document = find_one("processing_jobs", {"payload.run_token": run_token}, {"_id": 1})
    return document.get("_id") if document else None


def run_processing_job(*, workflow: str, params: Any = None, dataset_id: str = "",
                       run_token: str = "", created_by: str = "") -> dict:
    """Run one catalogued workflow and report only what the pipeline module actually did."""
    job_id = _job_id_for(run_token)

    def log(level: str, message: str) -> None:
        if job_id is not None:
            append_log(job_id, level, message)

    item = catalogue.resolve_workflow(workflow)
    log("info", f"workflow '{item['id']}' ({item.get('title')}) requested by {created_by or 'system'}")
    outcome = runner.execute(item["id"], params or {}, on_log=log)

    summary_id = None
    produced = outcome["execution_status"] == "ok" and (outcome["rows_out"] > 0 or outcome["outputs"])
    if outcome["execution_status"] == "ok" and outcome["outputs"]:
        summary_id = runner.record_summary(outcome, processing_job_id=job_id, created_by=created_by or None)
        log("info", f"summary '{summary_id}' stored in '{catalogue.summary_collection()}' "
                    f"(engine_used={outcome['engine_used']})")

    metrics = {
        "engine_used": outcome["engine_used"],
        "engine_attempted": outcome.get("engine_attempted"),
        "storage_engine": outcome.get("storage_engine"),
        "execution_status": outcome["execution_status"],
        "rows_out": outcome["rows_out"],
        "outputs_written": len(outcome["outputs"]),
        "module_seconds": outcome["duration_seconds"],
        "parameters": jsonable(outcome.get("params") or {}),
    }
    if job_id is not None:
        update_one("processing_jobs", {"_id": job_id}, {"$set": {
            "workflow": item["id"],
            "engine_used": outcome["engine_used"],
            "engine_attempted": outcome.get("engine_attempted"),
            "spark_error": outcome.get("spark_error"),
            "storage_engine": outcome.get("storage_engine"),
            "rows_out": outcome["rows_out"],
            "summary_id": summary_id,
            "outputs": outcome["outputs"],
            "metrics": metrics,
            "updated_at": now_utc(),
        }})
    else:
        logger.warning("processing job record not resolvable for run_token %s", run_token[:40])

    if not produced:
        reason = str(outcome["summary"].get("reason") or outcome["summary"].get("status")
                     or "the pipeline module reported no results")
        log("error", f"not marked complete: {reason} (engine_used={outcome['engine_used']})")
        if job_id is not None:
            set_status(job_id, "failed", metrics=metrics, error=reason[:300])
        raise RuntimeError(reason[:300])

    return {
        "workflow": item["id"],
        "dataset_id": dataset_id or None,
        "engine_used": outcome["engine_used"],
        "engine_attempted": outcome.get("engine_attempted"),
        "spark_error": outcome.get("spark_error"),
        "storage_engine": outcome.get("storage_engine"),
        "rows_out": outcome["rows_out"],
        "outputs": outcome["outputs"],
        "summary_id": summary_id,
        "parameters": jsonable(outcome.get("params") or {}),
        "execution_status": outcome["execution_status"],
        "metrics": metrics,
    }
