"""Machine-learning application services.

Training runs execute inside the bounded ``common.tasks.pool()`` thread pool and
own their ``model_registry`` status transitions
(``training -> candidate | failed``) directly; nothing here depends on the
processing/ingestion apps.
"""
from __future__ import annotations

import logging
import sys
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from common import analytics, storage_paths
from common.analytics import InsufficientData, METRIC_LABELS, METRIC_UNITS

# common/api.py and rest_framework.views import each other through DRF's
# DEFAULT_AUTHENTICATION_CLASSES; the monitoring shim breaks that cycle and has to load first.
from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common.api import ADMINISTRATOR, ApiError  # noqa: E402
from common.regions import LOCATIONS, REGION_META
from common.repositories import (RepositoryError, climate_observations, model_registry,
                                 predictions, serialize, utcnow, users)
from config import env

logger = logging.getLogger(__name__)


def _ml():
    """Import the ml package lazily so module import never depends on ML libs."""
    from ml.training import pipeline  # noqa: WPS433
    from ml.inference import predictor  # noqa: WPS433
    return pipeline, predictor


def _valid_date(value: Optional[str], field: str) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ApiError("validation_failed", f"{field} must use the YYYY-MM-DD format.")
    return str(value)[:10]


def _clean_scope(options: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    regions = [str(r).strip() for r in (options.get("regions") or []) if str(r).strip()]
    locations = [str(c).strip().upper() for c in (options.get("location_codes") or []) if str(c).strip()]
    known_regions = set(REGION_META) | {str(row.get("region_code")) for row in
                                        climate_observations.find({}, {"region_code": 1}, limit=2000)}
    for region in regions:
        if region not in known_regions:
            raise ApiError("validation_failed", f"Unknown region_code '{region}'.")
    known_locations = {str(loc["code"]) for loc in LOCATIONS}
    for code in locations:
        if code not in known_locations:
            raise ApiError("validation_failed", f"Unknown location_code '{code}'.")
    if not regions and not locations:
        raise ApiError("validation_failed", "Provide regions[] and/or location_codes[].")
    return regions[:30], locations[:60]


def validate_train_options(options: Dict[str, Any]) -> Dict[str, Any]:
    pipeline, predictor = _ml()
    target = str(options.get("target_variable") or "").strip()
    if target not in analytics.METRICS:
        raise ApiError("validation_failed",
                       f"target_variable must be one of: {', '.join(analytics.METRICS)}.")
    horizon = options.get("horizon_days")
    if horizon in (None, ""):
        horizon = predictor.max_horizon_days()
    try:
        horizon = int(horizon)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", "horizon_days must be an integer.")
    if horizon < 1 or horizon > predictor.max_horizon_days():
        raise ApiError("validation_failed",
                       f"horizon_days must be between 1 and {predictor.max_horizon_days()}.")
    algorithms = options.get("algorithms") or options.get("algorithm") or list(pipeline.DEFAULT_ALGORITHMS)
    if isinstance(algorithms, str):
        algorithms = [algorithms]
    algorithms = [str(a).strip().lower() for a in algorithms if str(a).strip()]
    unknown = [a for a in algorithms if a not in pipeline.ALGORITHMS]
    if unknown or not algorithms:
        raise ApiError("validation_failed",
                       f"Unknown algorithm(s) {unknown}. Options: {', '.join(pipeline.ALGORITHMS)}.")
    regions, locations = _clean_scope(options)
    min_rows = options.get("min_train_rows", pipeline.DEFAULT_MIN_TRAIN_ROWS)
    try:
        min_rows = int(min_rows)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", "min_train_rows must be an integer.")
    if not 50 <= min_rows <= 50000:
        raise ApiError("validation_failed", "min_train_rows must be between 50 and 50000.")
    name = str(options.get("name") or "").strip()[:64] or None
    return {
        "target_variable": target,
        "horizon_days": horizon,
        "algorithms": algorithms,
        "regions": regions,
        "location_codes": locations,
        "start": _valid_date(options.get("start"), "start"),
        "end": _valid_date(options.get("end"), "end"),
        "min_train_rows": min_rows,
        "name": name,
    }


def train_model(requested_by: str, options: Dict[str, Any]) -> Dict[str, Any]:
    """Register ``training`` placeholders, then run the pipeline in a worker thread."""
    pipeline, _ = _ml()
    clean = validate_train_options(options)
    placeholders: List[Dict[str, Any]] = []
    try:
        for algorithm in clean["algorithms"]:
            base = clean["name"] or pipeline.default_model_name(clean["target_variable"], algorithm)
            name = base if clean["name"] is None else f"{base}-{algorithm}"
            version = pipeline.next_version(name)
            document = {
                "name": name,
                "version": version,
                "algorithm": algorithm,
                "target_variable": clean["target_variable"],
                "status": "training",
                "created_at": utcnow(),
                "trained_by": requested_by,
                "requested_by": requested_by,
                "training_options": {k: v for k, v in clean.items() if k != "name"},
                "progress_stage": "queued",
                "training_started_at": utcnow(),
                "evaluation_metrics": {},
                "notes": None,
                "limitations": [],
                "error_message": None,
            }
            try:
                document["_id"] = model_registry.insert_one(document)
            except Exception as exc:  # noqa: BLE001 - version race: bump once and retry
                logger.warning("placeholder insert race (%s); bumping version", exc)
                document["version"] = pipeline.next_version(name)
                document["_id"] = model_registry.insert_one(document)
            placeholders.append({"model_id": str(document["_id"]), "name": name,
                                 "version": document["version"], "algorithm": algorithm,
                                 "created_at": document["created_at"]})

        from common import tasks

        tasks.pool().submit(_execute_training, placeholders, clean, requested_by)
    except Exception:  # noqa: BLE001 - a half-registered run must not leave rows in "training"
        for item in placeholders:
            try:
                model_registry.delete_by_id(item["model_id"])
            except RepositoryError:
                logger.warning("could not remove placeholder %s", item["model_id"])
        raise
    return {
        "status": "training",
        "model_ids": [item["model_id"] for item in placeholders],
        "models": placeholders,
        "note": ("Training runs in a background thread; poll the model detail endpoint. "
                 "If no observations are stored, the models finish with status 'failed' and an "
                 "error message rather than metrics."),
    }


def _execute_training(placeholders: List[Dict[str, Any]], clean: Dict[str, Any],
                      requested_by: str) -> None:
    pipeline, _ = _ml()
    by_algo = {item["algorithm"]: item["model_id"] for item in placeholders}

    def combined(stage: str, pct: int) -> None:
        for model_id in by_algo.values():
            try:
                model_registry.update_by_id(model_id, {"$set": {
                    "progress_stage": f"{pct}% - {stage}", "progress_at": utcnow()}})
            except RepositoryError:
                logger.warning("progress update failed for model %s", model_id)

    try:
        result = pipeline.run_training(
            target_variable=clean["target_variable"], regions=clean["regions"],
            location_codes=clean["location_codes"], start=clean["start"], end=clean["end"],
            horizon_days=clean["horizon_days"], algorithms=clean["algorithms"],
            min_train_rows=clean["min_train_rows"],
            name=clean.get("name"), trained_by=requested_by, requested_by=requested_by,
            precreated=placeholders, progress_cb=combined)
        for item in result.get("results", []):
            if item.get("status") != "trained":
                model_id = by_algo.get(item["algorithm"])
                if model_id:
                    model_registry.update_by_id(model_id, {"$set": {
                        "status": "failed",
                        "error_message": str(item.get("error"))[:500],
                        "progress_stage": "failed",
                        "finished_at": utcnow(),
                    }})
        from common.database import get_database

        get_database().flush_if_dirty()
    except Exception as exc:  # noqa: BLE001 - worker boundary: record the real failure
        logger.error("training run failed: %s", exc)
        for item in placeholders:
            try:
                model_registry.update_by_id(item["model_id"], {"$set": {
                    "status": "failed",
                    "error_message": f"{type(exc).__name__}: {exc}"[:500],
                    "progress_stage": "failed",
                    "finished_at": utcnow(),
                }})
            except RepositoryError:
                logger.exception("could not mark model %s failed", item["model_id"])
        try:
            from common.database import get_database

            get_database().flush_if_dirty()
        except Exception:  # noqa: BLE001
            logger.exception("snapshot flush failed")


def training_status() -> Dict[str, Any]:
    pipeline, _ = _ml()
    training = model_registry.find({"status": "training"}, sort=[("created_at", 1)], limit=50)
    counts: Dict[str, int] = {}
    for row in model_registry.find({}, projection={"status": 1}, limit=5000):
        counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
    now = datetime.now(timezone.utc)
    return {
        "training": [
            {
                "model_id": str(row.get("_id")),
                "name": row.get("name"),
                "algorithm": row.get("algorithm"),
                "stage": row.get("progress_stage"),
                "started_at": row.get("training_started_at"),
                "elapsed_seconds": int((now - _aware(row.get("training_started_at") or row.get("created_at"))).total_seconds())
                if row.get("training_started_at") or row.get("created_at") else None,
            }
            for row in training
        ],
        "status_counts": counts,
        "background_backend": "in-process bounded thread pool (common.tasks.pool)",
        "algorithms": {key: {k: v for k, v in spec.items()} for key, spec in pipeline.ALGORITHMS.items()},
        "xgboost_available": bool(pipeline.XGBOOST_AVAILABLE),
    }


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def list_models(*, status: Optional[str] = None, target_variable: Optional[str] = None,
                algorithm: Optional[str] = None, page: int = 1, page_size: int = 25) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if target_variable:
        query["target_variable"] = target_variable
    if algorithm:
        query["algorithm"] = algorithm
    return model_registry.paginate(query, page=page, page_size=page_size,
                                   sort=[("created_at", -1)],
                                   projection={"feature_definition": 0})


def get_model(model_id: str) -> Dict[str, Any]:
    document = _require_model(model_id)
    return serialize(document)


def _require_model(model_id: str) -> Dict[str, Any]:
    try:
        document = model_registry.find_one({"_id": _oid(model_id)})
    except RepositoryError:
        document = None
    if document is None:
        raise ApiError("not_found", f"No model with id '{model_id}'.", status=404)
    return document


def _artifact_available(document: Dict[str, Any]) -> bool:
    path = storage_paths.stored_path(document.get("artifact_uri"))
    return bool(path and path.is_file())


def _oid(value: str):
    from bson import ObjectId
    from bson.errors import InvalidId

    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        raise ApiError("not_found", f"'{value}' is not a valid model identifier.", status=404)


def patch_model(model_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    document = _require_model(model_id)
    updates: Dict[str, Any] = {"updated_at": utcnow()}
    if "name" in body:
        name = str(body.get("name") or "").strip()
        if not 3 <= len(name) <= 80:
            raise ApiError("validation_failed", "name must be 3-80 characters.")
        updates["name"] = name
    if "notes" in body:
        updates["notes"] = str(body.get("notes") or "")[:4000] or None
    action = body.get("action")
    if action == "retire":
        return retire_model(model_id)
    if action == "activate":
        return activate_model(model_id)
    if action not in (None, ""):
        raise ApiError("validation_failed", "action must be 'activate' or 'retire'.")
    model_registry.update_by_id(str(document["_id"]), {"$set": updates})
    return serialize(model_registry.find_one({"_id": document["_id"]}))


def activate_model(model_id: str) -> Dict[str, Any]:
    pipeline, _ = _ml()
    document = _require_model(model_id)
    if document.get("status") not in ("candidate", "retired"):
        raise ApiError("conflict",
                       f"Only candidate/retired models can be activated (status is "
                       f"'{document.get('status')}').", status=409)
    if not _artifact_available(document):
        raise ApiError("conflict", "This model has no persisted artifact to serve predictions from.",
                       status=409)
    retired = model_registry.update_many(
        {"target_variable": document.get("target_variable"), "status": "active",
         "_id": {"$ne": document["_id"]}},
        {"$set": {"status": "retired", "retired_at": utcnow(),
                  "notes": "Superseded by a newer active version."}})
    model_registry.update_by_id(model_id, {"$set": {
        "status": "active", "activated_at": utcnow(), "error_message": None}})
    result = serialize(model_registry.find_one({"_id": document["_id"]}) or {})
    result["retired_previous_versions"] = int(retired)
    return result


def retire_model(model_id: str) -> Dict[str, Any]:
    document = _require_model(model_id)
    if document.get("status") in ("training",):
        raise ApiError("conflict", "A model that is still training cannot be retired.", status=409)
    if document.get("status") == "retired":
        return serialize(document)
    model_registry.update_by_id(model_id, {"$set": {"status": "retired", "retired_at": utcnow()}})
    return serialize(model_registry.find_one({"_id": document["_id"]}) or {})


def retrain_model(model_id: str, requested_by: str) -> Dict[str, Any]:
    document = _require_model(model_id)
    options = document.get("training_options") or {}
    if not options:
        raise ApiError("conflict",
                       "This model has no stored training options to repeat; start a new training run.",
                       status=409)
    merged = dict(options)
    pipeline, _ = _ml()
    algorithm = document.get("algorithm")
    base_name = str(document.get("name") or "")
    if base_name == pipeline.default_model_name(document.get("target_variable"), algorithm):
        base_name = ""  # default lineage: let the pipeline bump <target>-<algo>-daily
    elif algorithm and base_name.endswith(f"-{algorithm}"):
        base_name = base_name[: -(len(algorithm) + 1)]
    merged["name"] = base_name or None
    merged["algorithms"] = [algorithm]
    outcome = train_model(requested_by, merged)
    model_registry.update_by_id(model_id, {"$set": {"retrain_of_previous": False,
                                                    "last_retrain_requested_at": utcnow()}})
    outcome["retrain_of"] = str(document["_id"])
    return outcome


def delete_model(model_id: str) -> Dict[str, Any]:
    document = _require_model(model_id)
    removed_files: List[str] = []
    for field in ("artifact_uri",):
        uri = document.get(field)
        path = storage_paths.stored_path(uri)
        if path is not None and path.is_file():
            try:
                path.unlink()
                removed_files.append(str(uri))
            except OSError:
                logger.warning("could not delete artifact %s", path)
    deleted = model_registry.delete_by_id(model_id)
    return {"deleted": int(deleted), "model_id": model_id, "removed_files": removed_files,
            "note": "Stored predictions keep their snapshot of the model's metrics."}


def _observation_stats() -> Dict[str, Dict[str, Any]]:
    stats = {metric: {"rows_with_value": 0, "locations": set(), "first": None, "last": None}
             for metric in analytics.METRICS}
    total = 0
    cursor = climate_observations.stream({}, {metric: 1 for metric in analytics.METRICS}
                                         | {"observation_date": 1, "location_code": 1})
    try:
        for row in cursor:
            total += 1
            day = str(row.get("observation_date") or "")[:10]
            for metric in analytics.METRICS:
                if row.get(metric) is not None:
                    entry = stats[metric]
                    entry["rows_with_value"] += 1
                    if row.get("location_code"):
                        entry["locations"].add(str(row["location_code"]))
                    if day:
                        entry["first"] = day if entry["first"] is None else min(entry["first"], day)
                        entry["last"] = day if entry["last"] is None else max(entry["last"], day)
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001
            pass
    return {metric: {"rows_with_value": value["rows_with_value"],
                     "locations": len(value["locations"]),
                     "first_date": value["first"], "last_date": value["last"],
                     "total_rows": total}
            for metric, value in stats.items()}


def catalogue() -> Dict[str, Any]:
    pipeline, predictor = _ml()
    stats = _observation_stats()
    pipeline_min = pipeline.DEFAULT_MIN_TRAIN_ROWS
    targets = []
    for metric in analytics.METRICS:
        info = stats.get(metric) or {"rows_with_value": 0, "locations": 0,
                                     "first_date": None, "last_date": None, "total_rows": 0}
        distinct_days = _distinct_days(metric) if info["rows_with_value"] else 0
        targets.append({
            "target_variable": metric,
            "label": METRIC_LABELS[metric],
            "unit": METRIC_UNITS[metric],
            "rows_with_value": info["rows_with_value"],
            "distinct_days": distinct_days,
            "locations": info["locations"],
            "first_date": info["first_date"],
            "last_date": info["last_date"],
            "trainable": bool(info["rows_with_value"] >= pipeline_min and distinct_days >= 60),
            "blocker": None if info["rows_with_value"] >= pipeline_min and distinct_days >= 60
            else "no training data available (need >= "
                 f"{pipeline_min} rows and 60 distinct days of stored observations)",
        })
    from ml.features.build import LAGS, WINDOWS, covariates_for

    return {
        "targets": targets,
        "default_feature_set": {
            "description": "calendar (year/month/day, doy+month sin/cos), geography, target lags, "
                           "rolling mean/std/min/max/count, region-month climatology anomaly, "
                           "missing-data indicators, lag-1 covariates",
            "lags": list(LAGS),
            "rolling_windows_days": list(WINDOWS),
            "covariates_per_target": {metric: covariates_for(metric) for metric in analytics.METRICS},
        },
        "algorithms": [
            {"name": key, **spec, "available": key != "xgboost" or pipeline.XGBOOST_AVAILABLE,
             "blocked_by": None if (key != "xgboost" or pipeline.XGBOOST_AVAILABLE)
             else "xgboost package not installed"}
            for key, spec in pipeline.ALGORITHMS.items()
        ],
        "horizon": {"max_forecast_horizon_days": predictor.max_horizon_days(),
                    "split_strategy": "chronological (train = older, test = recent; no shuffling)"},
        "database_mode": None,  # filled by the view layer from health info; kept null-safe
    }


def _distinct_days(metric: str) -> int:
    try:
        rows = climate_observations.aggregate([
            {"$match": {metric: {"$ne": None}}},
            {"$group": {"_id": "$observation_date"}},
            {"$count": "days"},
        ])
        return int(rows[0]["days"]) if rows else 0
    except RepositoryError:
        return 0


def create_prediction(requested_by: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _, predictor = _ml()
    model_id = str(body.get("model_id") or "").strip()
    document = None
    try:
        document = model_registry.find_one({"_id": _oid(model_id)})
    except ApiError:
        raise ApiError("not_found", f"No model with id '{model_id}'.", status=404)
    if document is None:
        raise ApiError("not_found", f"No model with id '{model_id}'.", status=404)
    if document.get("status") in ("training", "retired", "failed"):
        raise ApiError("conflict",
                       f"Model '{document.get('name')}' has status '{document.get('status')}' and "
                       "cannot produce forecasts; only active (or fully trained candidate) models can.",
                       status=409)
    if not _artifact_available(document):
        raise ApiError("conflict",
                       "This candidate model has no persisted artifact, so there is nothing to run.",
                       status=409)
    horizon = body.get("horizon_days") or 7
    try:
        horizon = int(horizon)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", "horizon_days must be an integer.")
    if horizon < 1 or horizon > predictor.max_horizon_days():
        raise ApiError("validation_failed",
                       f"horizon_days must be between 1 and {predictor.max_horizon_days()} "
                       "(FORECAST_MAX_HORIZON_DAYS).")
    scope = body.get("scope") or {}
    if not isinstance(scope, dict) or (not scope.get("region_code") and not scope.get("location_code")):
        raise ApiError("validation_failed", "scope.region_code or scope.location_code is required.")
    origin = _valid_date(body.get("forecast_origin_date"), "forecast_origin_date")

    observations = _scope_observations(scope)
    try:
        artifact = predictor.load_artifact(document)
        payload = predictor.forecast(
            model_document=document, observations=observations, scope=scope,
            horizon_days=horizon, forecast_origin_date=origin, requested_by=requested_by)
    except predictor.HorizonTooLong as exc:
        raise ApiError("validation_failed", str(exc))
    except InsufficientData as exc:
        raise ApiError("conflict" if "artifact" in str(exc) else "validation_failed", str(exc))
    payload["target_variable"] = document.get("target_variable")
    payload["created_at"] = utcnow()
    payload["_id"] = predictions.insert_one(payload)
    return _name_requester(serialize(payload))


def _scope_observations(scope: Dict[str, Any]) -> List[Dict[str, Any]]:
    criteria = analytics.observation_query(
        regions=[scope["region_code"]] if scope.get("region_code") else None,
        location_codes=[scope["location_code"]] if scope.get("location_code") else None,
        limit=analytics.MAX_QUERY_ROWS)
    return analytics.fetch_observations(criteria)


def list_predictions(*, model_id: Optional[str] = None, target_variable: Optional[str] = None,
                     region_code: Optional[str] = None, location_code: Optional[str] = None,
                     limit: int = 50) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {}
    if model_id:
        query["model_id"] = model_id
    if target_variable:
        query["target_variable"] = target_variable
    if region_code:
        query["region_code"] = region_code
    if location_code:
        query["location_code"] = str(location_code).upper()
    rows = predictions.find(query, sort=[("generated_at", -1)], limit=min(int(limit), 500))
    items: List[Dict[str, Any]] = []
    for document in rows:
        # The list carries the two ends of the series so a row shows a number, not a dash.
        steps = document.pop("values", None) or []
        dates = sorted((str(row.get("forecast_date") or ""), row) for row in steps if isinstance(row, dict))
        if dates:
            nearest = dates[0][1]
            furthest = dates[-1][1]
            first_val = nearest.get("value")
            last_val = furthest.get("value")
            unit = nearest.get("unit") or document.get("unit") or document.get("units")
        elif document.get("series") and isinstance(document["series"], list) and isinstance(document["series"][0], dict):
            first_val = document["series"][0].get("value")
            last_val = document["series"][-1].get("value")
            unit = document.get("units") or document.get("unit")
        else:
            first_val = steps[0] if steps and isinstance(steps[0], (int, float)) else None
            last_val = steps[-1] if steps and isinstance(steps[-1], (int, float)) else None
            unit = document.get("units") or document.get("unit")
        summary = _name_requester(serialize(document))
        summary["forecast_steps"] = len(steps) or len(document.get("series") or [])
        summary["first_value"] = first_val
        summary["last_value"] = last_val
        summary["unit"] = unit
        items.append(summary)
    return items


def _name_requester(document: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the requester's id into the name a reader recognises."""
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        return document
    requester = str(provenance.get("requested_by") or "")
    provenance.pop("requested_by", None)
    if not requester:
        return document
    try:
        account = users.find_one({"_id": _oid(requester)})
    except ApiError:
        account = None
    name = str((account or {}).get("full_name") or "").strip()
    if name:
        provenance["requested_by_name"] = name
    return document


def get_prediction(prediction_id: str) -> Dict[str, Any]:
    try:
        document = predictions.find_one({"_id": _oid(prediction_id)})
    except ApiError:
        document = None
    if document is None:
        raise ApiError("not_found", f"No prediction with id '{prediction_id}'.", status=404)
    return _name_requester(serialize(document))


def delete_prediction(prediction_id: str, actor) -> Dict[str, Any]:
    """Delete a stored forecast; only its requester or an administrator may do it.

    Ownership is read from the raw document because serialize() is a presentation view.
    """
    try:
        document = predictions.find_one({"_id": _oid(prediction_id)})
    except ApiError:
        document = None
    if document is None:
        raise ApiError("not_found", f"No prediction with id '{prediction_id}'.", status=404)
    role = getattr(actor, "role", None)
    owner = str(document.get("requested_by")
                or (document.get("provenance") or {}).get("requested_by") or "")
    if role != ADMINISTRATOR and owner != str(getattr(actor, "id", "")):
        raise ApiError("permission_denied", "This forecast belongs to another account.", status=403)
    removed = predictions.delete_by_id(prediction_id)
    return {"deleted": bool(removed), "id": prediction_id,
            "note": "The forecast document was removed; the model and its observations are untouched."}


def export_prediction_csv(prediction_id: str) -> str:
    document = get_prediction(prediction_id)
    lines = ["forecast_date,value,unit,lower_bound,upper_bound"]
    series = document.get("series") or []
    if series and isinstance(series[0], dict):
        for step in series:
            lines.append(",".join([
                str(step.get("timestamp") or ""),
                "" if step.get("value") is None else repr(step.get("value")),
                str(document.get("units") or document.get("unit") or ""),
                "" if step.get("lower") is None else repr(step.get("lower")),
                "" if step.get("upper") is None else repr(step.get("upper")),
            ]))
    else:
        for row in document.get("values") or []:
            if isinstance(row, dict):
                lines.append(",".join([
                    str(row.get("forecast_date")),
                    "" if row.get("value") is None else repr(row.get("value")),
                    str(row.get("unit") or document.get("unit") or ""),
                    "" if row.get("lower_bound") is None else repr(row.get("lower_bound")),
                    "" if row.get("upper_bound") is None else repr(row.get("upper_bound")),
                ]))
            elif isinstance(row, (int, float)):
                lines.append(f",{row},{document.get('units') or document.get('unit') or ''},,")
    header = (f"# EarthScape forecast | model {document.get('model_name')} v{document.get('model_version')} "
              f"| target {document.get('target_variable')} | origin {document.get('forecast_origin_date')} "
              f"| {document.get('method')}")
    return header + "\n" + "\n".join(lines) + "\n"


def explain(model_id: str, scope: Dict[str, Any]) -> Dict[str, Any]:
    """Training + metrics + features + a real actual-vs-forecast overlap series."""
    _, predictor = _ml()
    document = _require_model(model_id)
    region_code = scope.get("region_code")
    location_code = str(scope.get("location_code")).upper() if scope.get("location_code") else None
    query: Dict[str, Any] = {"model_id": str(document["_id"])}
    if region_code:
        query["region_code"] = region_code
    if location_code:
        query["location_code"] = location_code
    stored = predictions.find(query, sort=[("generated_at", -1)], limit=1)
    forecast_values = (stored[0].get("values") if stored else None) or []
    if not forecast_values:
        # compute on the fly without persisting, so "explain" still has values to work from
        observations = _scope_observations(scope)
        try:
            payload = predictor.forecast(model_document=document, observations=observations,
                                         scope=scope, horizon_days=7, requested_by="explain-endpoint")
            forecast_values = payload.get("values") or []
        except (InsufficientData, predictor.HorizonTooLong):
            forecast_values = []
    overlap: List[Dict[str, Any]] = []
    if forecast_values:
        dates = [str(row.get("forecast_date")) for row in forecast_values if isinstance(row, dict) and row.get("forecast_date")]
        if dates:
            criteria = analytics.observation_query(
                regions=[region_code] if region_code else None,
                location_codes=[location_code] if location_code else None,
                start_date=dates[0], end_date=dates[-1])
            rows = analytics.fetch_observations(criteria)
            target = document.get("target_variable")
            actuals: Dict[str, List[float]] = {}
            for row in rows:
                if row.get(target) is not None:
                    actuals.setdefault(str(row.get("observation_date"))[:10], []).append(float(row[target]))
            for row in forecast_values:
                if not isinstance(row, dict):
                    continue
                day = str(row.get("forecast_date"))
                series = actuals.get(day) or []
                overlap.append({
                    "date": day,
                    "predicted": row.get("value"),
                    "actual_mean_of_stations": round(sum(series) / len(series), 3) if series else None,
                    "actual_station_count": len(series),
                    "lower_bound": row.get("lower_bound"),
                    "upper_bound": row.get("upper_bound"),
                })
    return {
        "model": {
            "id": str(document["_id"]),
            "name": document.get("name"),
            "version": document.get("version"),
            "algorithm": document.get("algorithm"),
            "status": document.get("status"),
            "training_period": document.get("training_period"),
            "train_size": document.get("train_size"),
            "test_size": document.get("test_size"),
            "evaluation_metrics": document.get("evaluation_metrics"),
            "uncertainty_method": document.get("uncertainty_method"),
            "limitations": document.get("limitations"),
        },
        "features_used": (document.get("feature_definition") or {}).get("features"),
        "feature_definition": document.get("feature_definition"),
        "prediction_id": str(stored[0].get("_id")) if stored else None,
        "overlap_series": overlap,
        "note": ("Overlap compares the stored forecast with the observations recorded for those "
                 "dates; a null actual means no observation exists for that day yet. These are "
                 "statistical projections of day-to-day weather, not a climate trend."),
    }


def forecast_summary_for_scope(*, region_code: Optional[str] = None,
                               location_code: Optional[str] = None,
                               target_variable: Optional[str] = None) -> Dict[str, Any]:
    _, predictor = _ml()
    return predictor.forecast_summary_for_scope(region_code=region_code,
                                                location_code=location_code,
                                                target_variable=target_variable)


__all__ = ["train_model", "training_status", "list_models", "get_model", "patch_model",
           "activate_model", "retire_model", "retrain_model", "delete_model", "catalogue",
           "create_prediction", "list_predictions", "get_prediction", "export_prediction_csv",
           "explain", "forecast_summary_for_scope", "validate_train_options"]
