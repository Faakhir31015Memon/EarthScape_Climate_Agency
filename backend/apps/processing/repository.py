"""Reads for the processing workspace: `processing_jobs` and `analytics_summaries`.

Every returned figure carries the engine that produced it, so the UI can label analytics with
the truth instead of an assumed Spark/Hadoop claim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from apps.processing import catalogue
from common.db import aggregate, count, find, find_one, oid
from common.exceptions import NotFound
from common.serialization import document_to_api

LOG_CAP = 120
PROCESSING_KIND = "processing.run"

JOB_PROJECTION = {
    "_id": 1, "job_ref": 1, "kind": 1, "label": 1, "status": 1, "engine": 1,
    "workflow": 1, "engine_used": 1, "engine_attempted": 1, "spark_error": 1,
    "storage_engine": 1, "rows_out": 1, "outputs": 1, "summary_id": 1,
    "attempt": 1, "max_attempts": 1, "created_by": 1, "queued_at": 1, "started_at": 1,
    "finished_at": 1, "created_at": 1, "updated_at": 1, "error": 1, "metrics": 1,
}


def _seconds(later: Optional[datetime], earlier: Optional[datetime]) -> Optional[float]:
    if isinstance(later, datetime) and isinstance(earlier, datetime):
        return round((later - earlier).total_seconds(), 3)
    return None


def _query(status: str = "", workflow: str = "", dataset_id: str = "") -> dict:
    query: dict[str, Any] = {"kind": PROCESSING_KIND}
    if status:
        query["status"] = status
    if workflow:
        query["workflow"] = workflow
    if dataset_id:
        parsed = oid(dataset_id)
        if parsed is not None:
            query["payload.dataset_id"] = str(parsed)
    return query


def list_jobs(*, status: str = "", workflow: str = "", dataset_id: str = "",
              page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    query = _query(status, workflow, dataset_id)
    total = count("processing_jobs", query)
    rows = find("processing_jobs", query, sort=[("created_at", -1)], skip=(page - 1) * page_size,
                limit=page_size, projection=JOB_PROJECTION)
    return [document_to_api(row) for row in rows], total


def status_totals() -> dict:
    rows = aggregate("processing_jobs", [
        {"$match": {"kind": PROCESSING_KIND}},
        {"$group": {"_id": "$status", "jobs": {"$sum": 1}, "rows_out": {"$sum": {"$ifNull": ["$rows_out", 0]}}}},
        {"$sort": {"_id": 1}},
    ])
    by_status = {str(row["_id"] or "unknown"): {"jobs": row["jobs"], "rows_out": row["rows_out"]}
                 for row in rows}
    return {
        "by_status": by_status,
        "total_jobs": sum(row["jobs"] for row in by_status.values()),
        "total_rows_out": sum(row["rows_out"] for row in by_status.values()),
    }


def job_detail(job_id: str) -> dict:
    parsed = oid(job_id)
    document = find_one("processing_jobs", {"_id": parsed}) if parsed else None
    if not document or document.get("kind") != PROCESSING_KIND:
        raise NotFound("No processing job with that id exists in this workspace.")
    payload = document_to_api(document)
    logs = payload.get("logs") or []
    payload["logs"] = logs[-LOG_CAP:]
    payload["log_count"] = len(logs)
    payload["logs_truncated"] = max(0, len(logs) - min(len(logs), LOG_CAP))
    payload["timings"] = {
        "queued_at": payload.get("queued_at"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "queue_seconds": _seconds(document.get("started_at"), document.get("queued_at")),
        "run_seconds": _seconds(document.get("finished_at"), document.get("started_at")),
        "total_seconds": _seconds(document.get("finished_at"), document.get("queued_at")),
    }
    payload["retries"] = {
        "attempt": int(document.get("attempt") or 0),
        "max_attempts": int(document.get("max_attempts") or 3),
        "retry_available": (document.get("status") in ("failed", "cancelled")
                            and int(document.get("attempt") or 0) < int(document.get("max_attempts") or 3)),
    }
    job_ref = payload.get("id")
    payload["summaries"] = summaries_for_job(job_ref)["items"] if job_ref else []
    return payload


def summaries_for_job(job_id: str) -> dict:
    query = {"processing_job_id": oid(job_id)}
    rows = find(catalogue.summary_collection(), query, sort=[("created_at", -1)], limit=20)
    items = [document_to_api(row) for row in rows]
    return {"items": items, "total": len(items)}


def list_summaries(*, workflow: str = "", engine: str = "", dataset: str = "",
                   job_id: str = "", page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    collection_name = catalogue.summary_collection()
    query: dict[str, Any] = {}
    if workflow:
        query["workflow"] = workflow
    if engine:
        query["engine_used"] = engine
    if dataset:
        query["dataset"] = catalogue.dataset_slug_for(dataset)
    if job_id:
        parsed = oid(job_id)
        if parsed is not None:
            query["processing_job_id"] = parsed
    total = count(collection_name, query)
    rows = find(collection_name, query, sort=[("created_at", -1)], skip=(page - 1) * page_size,
                limit=page_size)
    return [document_to_api(row) for row in rows], total


def summary_totals() -> dict:
    collection_name = catalogue.summary_collection()
    by_engine = aggregate(collection_name, [
        {"$group": {"_id": "$engine_used", "summaries": {"$sum": 1},
                    "rows_out": {"$sum": {"$ifNull": ["$rows_out", 0]}}}},
        {"$sort": {"summaries": -1}},
    ])
    by_workflow = aggregate(collection_name, [
        {"$group": {"_id": "$workflow", "summaries": {"$sum": 1},
                    "rows_out": {"$sum": {"$ifNull": ["$rows_out", 0]}}}},
        {"$sort": {"_id": 1}},
    ])
    return {
        "by_engine": [{"engine_used": str(row["_id"] or "unreported"),
                       "summaries": row["summaries"], "rows_out": row["rows_out"]}
                      for row in by_engine],
        "by_workflow": [{"workflow": str(row["_id"] or "unknown"),
                         "summaries": row["summaries"], "rows_out": row["rows_out"]}
                        for row in by_workflow],
        "total_summaries": count(collection_name, {}),
    }


def last_run_per_workflow() -> dict:
    rows = aggregate(catalogue.summary_collection(), [
        {"$sort": {"created_at": -1}},
        {"$group": {"_id": "$workflow",
                    "summary_id": {"$first": "$_id"},
                    "at": {"$first": "$created_at"},
                    "engine_used": {"$first": "$engine_used"},
                    "engine_attempted": {"$first": "$engine_attempted"},
                    "spark_error": {"$first": "$spark_error"},
                    "rows_out": {"$first": "$rows_out"},
                    "status": {"$first": "$status"},
                    "outputs": {"$first": "$outputs"},
                    "processing_job_id": {"$first": "$processing_job_id"}}},
    ])
    latest: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("_id") or "")
        if key:
            latest[key] = document_to_api(row)
    return latest


def latest_summary_for_workflow(workflow_id: str) -> Optional[dict]:
    rows = find(catalogue.summary_collection(), {"workflow": workflow_id},
                sort=[("created_at", -1)], limit=1)
    return document_to_api(rows[0]) if rows else None


def stream_checkpoints(*, limit: int = 20) -> dict:
    rows = find("stream_checkpoints", {}, sort=[("updated_at", -1)], limit=max(1, min(limit, 100)))
    items = [document_to_api(row) for row in rows]
    return {
        "items": items,
        "recorded": len(items),
        "note": None if items else (
            "The 'stream_checkpoints' collection is empty: no consumer or poller has written a "
            "checkpoint from this application database yet."),
    }


def recent_errors(*, limit: int = 5) -> list[dict]:
    rows = find("processing_jobs", {"kind": PROCESSING_KIND, "status": "failed"},
                sort=[("finished_at", -1)], limit=max(1, min(limit, 20)),
                projection={"workflow": 1, "engine_used": 1, "error": 1, "finished_at": 1})
    return [document_to_api(row) for row in rows]
