"""MongoDB persistence for the ML surface: ``model_registry``, ``predictions``, anomaly rows.

Reads and writes only go through ``common.db`` helpers with sanitised values, so a client can
never steer an operator into a query. Lifecycle status (candidate/active/retired) lives in
MongoDB; the artifact facts live on disk and are synced in, never overwritten by a client.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from common.db import collection, count, find, find_one, insert_one, now_utc, oid, update_one
from common.exceptions import Conflict, NotFound, ValidationError
from common.security import sanitize_text
from common.serialization import document_to_api, parse_datetime

REGISTRY = "model_registry"
PREDICTIONS = "predictions"
EVENTS = "alert_events"
SUMMARIES = "analytics_summaries"
JOBS = "processing_jobs"

MODEL_STATUSES = ("candidate", "active", "retired")
JOB_KINDS = ("model.train", "prediction.forecast", "anomaly.scan")
JOB_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
ANOMALY_SOURCE = "machine_learning"

_STATUS_ORDER = {"severe": 0, "warning": 1, "info": 2}


def _window(*, date_from: Any = None, date_to: Any = None, field: str = "generated_at") -> dict:
    window: dict[str, Any] = {}
    start = parse_datetime(date_from) if date_from else None
    end = parse_datetime(date_to) if date_to else None
    if start and end and end < start:
        raise ValidationError("The 'to' date must not precede the 'from' date.")
    if start:
        window["$gte"] = start
    if end:
        window["$lte"] = end
    return {field: window} if window else {}


def _regex(value: str, max_length: int = 80) -> dict[str, str]:
    return {"$regex": re.escape(sanitize_text(value, max_length=max_length)), "$options": "i"}


def _int_filter(value: Any, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"'{name}' must be an integer.") from None


# --------------------------------------------------------------------------- model registry
_SYNC_FIELDS = ("model_name", "model_version", "algorithm", "target_variable", "granularity",
                "horizon_steps", "region", "training_period", "test_period", "rows_used",
                "metrics", "registry_status", "artifact_uri", "feature_definition", "backtest",
                "limitations", "data_source_files", "created_at", "last_synced_at")


def sync_registry(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Upsert the on-disk registry into Mongo; Mongo-side lifecycle status is preserved."""
    inserted = updated = unchanged = 0
    refs: list[str] = []
    for document in documents:
        ref = document["model_ref"]
        ref = sanitize_text(ref, max_length=130)
        refs.append(ref)
        existing = find_one(REGISTRY, {"model_ref": ref}, {"_id": 1, "status": 1, "region": 1})
        fields = {key: document.get(key) for key in _SYNC_FIELDS}
        if existing is None:
            payload = dict(document)
            payload["status"] = "candidate"
            payload["status_origin"] = "ml_package_disk"
            already_active = count(REGISTRY, {
                "target_variable": payload.get("target_variable"),
                "region": payload.get("region") or "",
                "granularity": payload.get("granularity"),
                "status": "active"})
            # WHY: the package marks a model 'registered' when it beat the baseline; the first
            # such model for a target+region+granularity becomes the active one to forecast from.
            if payload.get("registry_status") == "registered" and already_active == 0:
                payload["status"] = "active"
                payload["status_origin"] = "auto_active_first_registered"
                payload["promoted_at"] = now_utc()
                payload["promoted_by"] = "system:registry_sync"
            payload["first_seen_at"] = payload.get("created_at") or now_utc()
            insert_one(REGISTRY, payload)
            inserted += 1
            continue
        result = collection(REGISTRY).update_one({"_id": existing["_id"]},
                                                 {"$set": fields,
                                                  "$currentDate": {"last_synced_at": True}})
        if result.modified_count:
            updated += 1
        else:
            unchanged += 1
    missing = collection(REGISTRY).update_many(
        {"model_ref": {"$nin": refs}},
        {"$set": {"artifact_present_on_disk": False, "last_checked_at": now_utc()}})
    return {"inserted": inserted, "updated": updated, "unchanged": unchanged,
            "marked_artifact_missing": missing.modified_count, "models_on_disk": len(refs)}


def upsert_registry_document(document: dict[str, Any]) -> dict[str, Any]:
    """Insert or refresh one registry row (no disk-wide artifact sweep)."""
    ref = sanitize_text(document["model_ref"], max_length=130)
    moment = now_utc()
    existing = find_one(REGISTRY, {"model_ref": ref}, {"_id": 1, "status": 1})
    if existing is None:
        payload = dict(document)
        payload.setdefault("status", "candidate")
        payload.setdefault("status_origin", "training_job")
        payload["first_seen_at"] = payload.get("created_at") or moment
        payload["last_checked_at"] = moment
        insert_one(REGISTRY, payload)
        return registry_row(ref)
    fields = {key: document.get(key) for key in _SYNC_FIELDS}
    fields["artifact_present_on_disk"] = True
    collection(REGISTRY).update_one({"_id": existing["_id"]},
                                    {"$set": fields, "$currentDate": {"last_synced_at": True}})
    return registry_row(ref)


def require_row(model_ref: str) -> dict[str, Any]:
    """Fetch a registry row by its ref key, or explain that it is not registered."""
    ref = sanitize_text(model_ref, max_length=130)
    row = find_one(REGISTRY, {"model_ref": ref})
    if not row:
        raise NotFound(f"'{ref}' is not in the model registry.",
                       details=[f"{count(REGISTRY, {})} model(s) are registered; "
                                "GET /api/v1/models/ lists them."])
    return row


_LIST_PROJECTION = {"feature_definition": 0, "backtest": 0, "evaluations": 0,
                    "data_source_files": 0}


def registry_query(params: dict[str, str]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if params.get("region"):
        query["region"] = sanitize_text(params["region"], max_length=80)
    if params.get("variable"):
        query["target_variable"] = sanitize_text(params["variable"], max_length=32)
    if params.get("granularity"):
        query["granularity"] = sanitize_text(params["granularity"], max_length=24)
    if params.get("algorithm"):
        query["algorithm"] = sanitize_text(params["algorithm"], max_length=48)
    if params.get("status"):
        status = sanitize_text(params["status"], max_length=16)
        if status not in MODEL_STATUSES:
            raise ValidationError(f"Unknown model status '{status}'. "
                                  f"Allowed: {', '.join(MODEL_STATUSES)}.")
        query["status"] = status
    if params.get("horizon"):
        query["horizon_steps"] = _int_filter(params["horizon"], "horizon")
    if params.get("artifact") == "missing":
        query["artifact_present_on_disk"] = False
    if params.get("search"):
        query["model_name"] = _regex(params["search"], 80)
    return query


def list_registry(params: dict[str, str], *, page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    query = registry_query(params)
    total = count(REGISTRY, query)
    rows = find(REGISTRY, query, sort=[("created_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size, projection=_LIST_PROJECTION)
    return [document_to_api(row) for row in rows], total


def registry_row(model_ref: str) -> dict[str, Any]:
    return document_to_api(require_row(model_ref))


def registry_status_counts() -> dict[str, Any]:
    rows = collection(REGISTRY).aggregate([{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    by_status = {status: 0 for status in MODEL_STATUSES}
    for row in rows:
        by_status[str(row["_id"] or "unknown")] = row["count"]
    variables = collection(REGISTRY).aggregate(
        [{"$group": {"_id": "$target_variable", "count": {"$sum": 1}}}])
    regions = collection(REGISTRY).aggregate(
        [{"$group": {"_id": "$region", "count": {"$sum": 1}}}])
    return {
        "total": count(REGISTRY, {}),
        "by_status": by_status,
        "by_variable": {str(row["_id"] or "unknown"): row["count"] for row in variables},
        "by_region": {str(row["_id"] or "unknown"): row["count"] for row in regions},
        "artifacts_missing_on_disk": count(REGISTRY, {"artifact_present_on_disk": False}),
    }


def active_model(*, target_variable: str, region: Optional[str],
                 granularity: Optional[str]) -> Optional[dict[str, Any]]:
    query: dict[str, Any] = {"status": "active",
                             "target_variable": sanitize_text(target_variable, max_length=32)}
    if region:
        query["region"] = sanitize_text(region, max_length=80)
    if granularity:
        query["granularity"] = sanitize_text(granularity, max_length=24)
    rows = find(REGISTRY, query, sort=[("promoted_at", -1), ("created_at", -1)], limit=1)
    return rows[0] if rows else None


def promote(model_ref: str, actor_id: str) -> dict[str, Any]:
    """Activate one model and retire the previous active model for the same slice, atomically."""
    row = require_row(model_ref)
    moment = now_utc()
    result = collection(REGISTRY).update_one(
        {"_id": row["_id"], "status": {"$in": ["candidate", "active", "retired"]}},
        {"$set": {"status": "active", "status_origin": "manual_promote", "promoted_at": moment,
                  "promoted_by": sanitize_text(actor_id, max_length=64), "retired_at": None,
                  "retired_by": None, "retire_reason": None, "updated_at": moment}},
    )
    if result.matched_count != 1:
        raise Conflict(f"'{row['model_ref']}' could not be activated (status "
                       f"'{row.get('status')}'); re-check the registry.")
    same_slice = {"target_variable": row.get("target_variable"),
                  "region": row.get("region") or "", "granularity": row.get("granularity"),
                  "status": "active", "_id": {"$ne": row["_id"]}}
    superseded = [str(doc["model_ref"]) for doc in
                  find(REGISTRY, same_slice, projection={"model_ref": 1}, limit=50)]
    retired = collection(REGISTRY).update_many(same_slice, {"$set": {
        "status": "retired", "retired_at": moment,
        "retired_by": sanitize_text(actor_id, max_length=64),
        "retire_reason": f"superseded by {row['model_ref']} on promotion",
        "updated_at": moment}})
    return {
        "model": registry_row(row["model_ref"]),
        "previous_active_retired": superseded,
        "retired_count": retired.modified_count,
        "policy": "exactly one active model per target_variable + region + granularity; the "
                  "artifact files on disk are untouched, only the registry status changed",
    }


def retire(model_ref: str, actor_id: str, reason: Optional[str]) -> dict[str, Any]:
    row = require_row(model_ref)
    if row.get("status") == "retired":
        return {"model": registry_row(row["model_ref"]), "changed": False,
                "note": f"'{row['model_ref']}' was already retired"
                        f"{' (' + str(row.get('retire_reason')) + ')' if row.get('retire_reason') else ''}."}
    moment = now_utc()
    clean_reason = sanitize_text(reason or "retired via the model registry API", max_length=400)
    result = collection(REGISTRY).update_one(
        {"_id": row["_id"], "status": {"$ne": "retired"}},
        {"$set": {"status": "retired", "retired_at": moment,
                  "retired_by": sanitize_text(actor_id, max_length=64),
                  "retire_reason": clean_reason, "updated_at": moment}},
    )
    return {
        "model": registry_row(row["model_ref"]),
        "changed": bool(result.modified_count),
        "artifacts_deleted": False,
        "note": "Retirement is a registry decision: the joblib artifact, metadata.json and the "
                "trained weights stay on disk so historic forecasts remain reproducible. "
                "Reason recorded on the registry row.",
        "reason": clean_reason,
    }


def record_evaluation(model_ref: str, evaluation: dict[str, Any], *, job_id: Optional[str] = None,
                      actor_id: Optional[str] = None) -> dict[str, Any]:
    """Append a real evaluation record to the model's registry row."""
    row = require_row(model_ref)
    moment = now_utc()
    record = {
        "evaluated_at": moment,
        "algorithm": evaluation.get("algorithm"),
        "metrics": (evaluation.get("evaluation_metrics") or {}),
        "horizon_validated_for": evaluation.get("horizon_validated_for"),
        "training_period": evaluation.get("training_period") or {},
        "test_period": evaluation.get("test_period") or {},
        "backtest_available": bool((evaluation.get("backtest") or {}).get("available")),
        "limitations": [str(item)[:400] for item in (evaluation.get("limitations") or [])][:10],
        "mode": evaluation.get("mode"),
        "job_id": str(job_id) if job_id else None,
        "requested_by": sanitize_text(actor_id, max_length=64) if actor_id else None,
    }
    collection(REGISTRY).update_one(
        {"_id": row["_id"]},
        {"$push": {"evaluations": {"$each": [record], "$slice": -20}},
         "$set": {"last_evaluation": record, "updated_at": moment}},
    )
    return {"model_ref": row["model_ref"], "evaluation": document_to_api(record),
            "evaluations_stored": count(REGISTRY, {"model_ref": row["model_ref"],
                                                   "evaluations": {"$exists": True}})}


# --------------------------------------------------------------------------- predictions
def insert_prediction(document: dict[str, Any]) -> dict[str, Any]:
    stored = dict(document)
    result = insert_one(PREDICTIONS, stored)
    stored["_id"] = result.inserted_id
    return document_to_api(stored)


def prediction_query(params: dict[str, str]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if params.get("region"):
        query["region"] = sanitize_text(params["region"], max_length=80)
    if params.get("variable") or params.get("target_variable"):
        query["target_variable"] = sanitize_text(
            params.get("variable") or params.get("target_variable"), max_length=32)
    if params.get("model"):
        model = sanitize_text(params["model"], max_length=130)
        if "@" in model:
            query["model_ref"] = model
        else:
            query["model_name"] = model
    if params.get("granularity"):
        query["granularity"] = sanitize_text(params["granularity"], max_length=24)
    if params.get("status"):
        query["status"] = sanitize_text(params["status"], max_length=24)
    if params.get("horizon"):
        query["horizon_steps"] = _int_filter(params["horizon"], "horizon")
    if params.get("min_horizon"):
        query["horizon_steps"] = {"$gte": _int_filter(params["min_horizon"], "min_horizon")}
    if params.get("max_horizon"):
        query["horizon_steps"] = {"$lte": _int_filter(params["max_horizon"], "max_horizon")}
    if params.get("uncertainty") == "true":
        query["has_uncertainty"] = True
    if params.get("created_by"):
        query["requested_by"] = sanitize_text(params["created_by"], max_length=64)
    window = _window(date_from=params.get("from") or params.get("date_from"),
                     date_to=params.get("to") or params.get("date_to"))
    query.update(window)
    return query


_LIGHT_PROJECTION = {"series": 0, "values": 0, "timestamps": 0,
                     "uncertainty.lower": 0, "uncertainty.upper": 0, "availability": 0,
                     "warnings": 0, "backtest_aggregate": 0}


def list_predictions(params: dict[str, str], *, page: int = 1,
                     page_size: int = 25) -> tuple[list[dict], int]:
    query = prediction_query(params)
    total = count(PREDICTIONS, query)
    rows = find(PREDICTIONS, query, sort=[("generated_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size, projection=_LIGHT_PROJECTION)
    return [document_to_api(row) for row in rows], total


def require_prediction(prediction_id: str) -> dict[str, Any]:
    parsed = oid(prediction_id)
    row = find_one(PREDICTIONS, {"_id": parsed}) if parsed else None
    if not row:
        raise NotFound("Prediction not found.",
                       details=["predictions are stored under their Mongo id; "
                                "GET /api/v1/predictions/ lists them."])
    return row


def get_prediction(prediction_id: str) -> dict[str, Any]:
    return document_to_api(require_prediction(prediction_id))


def latest_prediction(params: dict[str, str]) -> tuple[Optional[dict], str]:
    query = prediction_query(params)
    query["status"] = "forecast"
    rows = find(PREDICTIONS, query, sort=[("generated_at", -1), ("_id", -1)], limit=1)
    if rows:
        return document_to_api(rows[0]), ""
    if count(PREDICTIONS, {}) == 0:
        return None, ("no predictions are stored yet; POST /api/v1/predictions/forecast with a "
                      "registered model_ref")
    return None, ("no stored forecast matches these filters"
                  + (f": {sorted(query.keys())}" if query else "")
                  + " (POST /api/v1/predictions/forecast to generate one)")


def predictions_overview() -> dict[str, Any]:
    rows = collection(PREDICTIONS).aggregate([
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    with_bands = count(PREDICTIONS, {"has_uncertainty": True})
    return {"total": count(PREDICTIONS, {}),
            "by_status": {str(row["_id"] or "unknown"): row["count"] for row in rows},
            "with_uncertainty_bands": with_bands,
            "without_uncertainty_bands": count(PREDICTIONS, {"has_uncertainty": False})}


# --------------------------------------------------------------------------- anomalies
def insert_alert_events(documents: list[dict[str, Any]]) -> dict[str, Any]:
    """Store detections one by one; the unique dedupe_key makes re-scans idempotent."""
    created: list[str] = []
    deduped = 0
    for document in documents:
        try:
            created.append(str(insert_one(EVENTS, document)))
        except DuplicateKeyError:
            deduped += 1
    return {"created": created, "deduped": deduped}


def anomaly_event_query(params: dict[str, str]) -> dict[str, Any]:
    query: dict[str, Any] = {"condition": "anomaly"}
    origin = sanitize_text(params.get("origin", ""), max_length=24)
    if origin in ("ml", "machine_learning"):
        query["source"] = ANOMALY_SOURCE
    elif origin == "alerts":
        query["source"] = {"$ne": ANOMALY_SOURCE}
    elif origin not in ("", "all"):
        raise ValidationError("origin must be one of: ml, alerts, all.")
    if params.get("severity"):
        severity = sanitize_text(params["severity"], max_length=16)
        if severity not in _STATUS_ORDER:
            raise ValidationError(f"Unknown severity '{severity}'. Allowed: info, warning, severe.")
        query["severity"] = severity
    if params.get("region"):
        query["region"] = sanitize_text(params["region"], max_length=80)
    if params.get("variable"):
        query["target_variable"] = sanitize_text(params["variable"], max_length=32)
    if params.get("status"):
        status = sanitize_text(params["status"], max_length=16)
        if status not in ("open", "acknowledged", "resolved"):
            raise ValidationError(f"Unknown event status '{status}'.")
        query["status"] = status
    if params.get("method"):
        query["detector.method"] = sanitize_text(params["method"], max_length=48)
    window = _window(date_from=params.get("from") or params.get("date_from"),
                     date_to=params.get("to") or params.get("date_to"), field="detected_at")
    query.update(window)
    return query


def list_anomaly_events(params: dict[str, str], *, page: int = 1,
                        page_size: int = 25) -> tuple[list[dict], int]:
    query = anomaly_event_query(params)
    total = count(EVENTS, query)
    rows = find(EVENTS, query, sort=[("detected_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(row) for row in rows], total


def anomaly_event_counts(params: dict[str, str]) -> dict[str, Any]:
    base = anomaly_event_query(dict(params, severity=""))
    severities = collection(EVENTS).aggregate(
        [{"$match": base}, {"$group": {"_id": "$severity", "count": {"$sum": 1}}}])
    methods = collection(EVENTS).aggregate(
        [{"$match": base}, {"$group": {"_id": "$detector.method", "count": {"$sum": 1}}}])
    newest = find(EVENTS, base, sort=[("detected_at", -1)], limit=1,
                  projection={"detected_at": 1})
    return {"total": count(EVENTS, base),
            "by_severity": {str(row["_id"] or "unknown"): row["count"] for row in severities},
            "by_method": {str(row["_id"] or "unknown"): row["count"] for row in methods},
            "newest_detected_at": newest[0].get("detected_at") if newest else None}


def publish_anomaly_summary(document: dict[str, Any]) -> str:
    return insert_one(SUMMARIES, document)


def anomaly_summaries(*, region: Optional[str] = None, variable: Optional[str] = None,
                      limit: int = 10) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"summary_type": "anomaly_features"}
    if region:
        query["region"] = sanitize_text(region, max_length=80)
    if variable:
        query["variable"] = sanitize_text(variable, max_length=32)
    return [document_to_api(row) for row in
            find(SUMMARIES, query, sort=[("computed_at", -1), ("_id", -1)], limit=limit)]


# --------------------------------------------------------------------------- background jobs
def job_summary(job: dict[str, Any]) -> dict[str, Any]:
    payload = document_to_api(job) or {}
    return {
        "id": payload.get("id"),
        "job_ref": payload.get("job_ref"),
        "kind": payload.get("kind"),
        "label": payload.get("label"),
        "status": payload.get("status"),
        "engine": payload.get("engine"),
        "created_by": payload.get("created_by"),
        "queued_at": payload.get("queued_at"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
        "attempt": payload.get("attempt"),
        "max_attempts": payload.get("max_attempts"),
        "error": payload.get("error"),
        "metrics": payload.get("metrics"),
        "result": payload.get("result"),
        "logs": payload.get("logs") or [],
        "log_count": len(payload.get("logs") or []),
        "payload": payload.get("payload"),
        "poll": f"/api/v1/models/jobs/{payload.get('id')}",
    }


def require_job(job_id: str) -> dict[str, Any]:
    parsed = oid(job_id)
    job = find_one(JOBS, {"_id": parsed}) if parsed else None
    if not job:
        raise NotFound("No such background job in this deployment.",
                       details=["job ids are the Mongo id returned by POST /api/v1/models/train "
                                "(or the forecast/scan jobs)."])
    return job


def job_detail(job_id: str) -> dict[str, Any]:
    job = require_job(job_id)
    summary = job_summary(job)
    if str(job.get("kind")) == "model.train" and job.get("payload"):
        refs = [str(ref) for ref in ((job.get("payload") or {}).get("registered_refs") or [])]
        summary["registered_models"] = [
            document_to_api(row) for row in
            find(REGISTRY, {"model_ref": {"$in": refs}}, projection=_LIST_PROJECTION, limit=50)
        ] if refs else []
    return summary


def job_query(params: dict[str, str]) -> dict[str, Any]:
    kinds = [sanitize_text(params.get("kind", ""), max_length=48)] if params.get("kind") else []
    unknown = [kind for kind in kinds if kind not in JOB_KINDS]
    if unknown:
        raise ValidationError(f"Unknown job kind '{unknown[0]}'. "
                              f"Allowed: {', '.join(JOB_KINDS)}.")
    query: dict[str, Any] = {"kind": {"$in": kinds}} if kinds else {"kind": {"$in": list(JOB_KINDS)}}
    if params.get("status"):
        status = sanitize_text(params["status"], max_length=24)
        if status not in JOB_STATUSES:
            raise ValidationError(f"Unknown job status '{status}'. Allowed: {', '.join(JOB_STATUSES)}.")
        query["status"] = status
    if params.get("created_by"):
        query["created_by"] = sanitize_text(params["created_by"], max_length=64)
    window = _window(date_from=params.get("from"), date_to=params.get("to"), field="created_at")
    query.update(window)
    return query


def list_jobs(params: dict[str, str], *, page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    query = job_query(params)
    total = count(JOBS, query)
    rows = find(JOBS, query, sort=[("created_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size,
                projection={"result.registered_models": 0})
    return [job_summary(row) for row in rows], total


def job_counts() -> dict[str, Any]:
    rows = collection(JOBS).aggregate([
        {"$match": {"kind": {"$in": list(JOB_KINDS)}}},
        {"$group": {"_id": {"kind": "$kind", "status": "$status"}, "count": {"$sum": 1}}},
    ])
    by_status: dict[str, int] = {status: 0 for status in JOB_STATUSES}
    by_kind: dict[str, int] = {kind: 0 for kind in JOB_KINDS}
    for row in rows:
        key = row["_id"] or {}
        by_status[str(key.get("status") or "unknown")] = \
            by_status.get(str(key.get("status") or "unknown"), 0) + row["count"]
        by_kind[str(key.get("kind") or "unknown")] = \
            by_kind.get(str(key.get("kind") or "unknown"), 0) + row["count"]
    return {"by_status": by_status, "by_kind": by_kind, "total": sum(by_status.values())}


def register_job_refs(job_object_id: Any, refs: list[str]) -> None:
    """Attach the model refs a training job registered so the job page can link them."""
    if not refs:
        return
    update_one(JOBS, {"_id": oid(job_object_id)},
               {"$set": {"payload.registered_refs": refs, "updated_at": now_utc()}})


def job_clock() -> datetime:
    return now_utc()
