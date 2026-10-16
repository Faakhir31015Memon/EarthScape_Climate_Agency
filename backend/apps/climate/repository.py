"""MongoDB aggregation layer for observations, analytics summaries and saved analyses.

Every function reads only stored documents; absent measurements are never coerced to
zero. Numeric metric predicates use ``$type`` so a missing ``metrics.<var>.value`` is
skipped rather than averaged as 0.
"""

from __future__ import annotations

from typing import Any, Optional

from common.db import (
    aggregate,
    collection,
    count,
    find,
    find_one,
    insert_one,
    now_utc,
    oid,
    update_one,
)
from common.exceptions import Forbidden, NotFound, ValidationError
from common.security import guard_query, sanitize_text
from common.serialization import document_to_api, parse_datetime

VARIABLES = (
    "temperature", "temperature_max", "temperature_min", "precipitation", "humidity",
    "wind_speed", "wind_direction", "pressure", "cloud_cover", "solar_radiation",
)
_VARIABLE_SET = set(VARIABLES)
GRANULARITIES = ("instantaneous", "hourly", "daily", "monthly")
QUALITY_STATUSES = ("valid", "suspect", "rejected")
_NUMERIC = ["double", "int", "decimal", "long"]

# Explorer sorts are whitelisted so a client cannot steer an arbitrary index scan.
_OBS_SORTS = {
    "timestamp": ("timestamp", -1),
    "timestamp_asc": ("timestamp", 1),
    "region": ("location.region", 1),
    "region_desc": ("location.region", -1),
    "ingested": ("provenance.ingested_at", -1),
}


def metric_value_field(variable: str) -> str:
    """Return the dotted value path for a whitelisted variable, rejecting anything else."""
    name = sanitize_text(variable, max_length=32)
    if name not in _VARIABLE_SET:
        raise ValidationError(f"Unknown variable '{name}'. Allowed: {', '.join(VARIABLES)}.")
    return f"metrics.{name}.value"


def _numeric_match(field: str) -> dict:
    return {field: {"$type": _NUMERIC}}


def _window(from_dt: Any, to_dt: Any) -> dict:
    window: dict[str, Any] = {}
    start = parse_datetime(from_dt) if from_dt else None
    end = parse_datetime(to_dt) if to_dt else None
    if start:
        window["$gte"] = start
    if end:
        window["$lte"] = end
    return window


def _bbox(params: dict[str, str]) -> Optional[dict]:
    try:
        min_lat = params.get("min_lat")
        max_lat = params.get("max_lat")
        min_lon = params.get("min_lon")
        max_lon = params.get("max_lon")
        if None in (min_lat, max_lat, min_lon, max_lon):
            return None
        box = {
            "min_lat": float(min_lat), "max_lat": float(max_lat),
            "min_lon": float(min_lon), "max_lon": float(max_lon),
        }
    except (TypeError, ValueError):
        raise ValidationError("Bounding box values must be numeric.") from None
    if not (-90 <= box["min_lat"] <= 90 and -90 <= box["max_lat"] <= 90
            and -180 <= box["min_lon"] <= 180 and -180 <= box["max_lon"] <= 180
            and box["min_lat"] <= box["max_lat"] and box["min_lon"] <= box["max_lon"]):
        raise ValidationError("Bounding box is out of range or inverted.")
    return box


def build_observation_match(params: dict[str, Any], variables: Optional[list[str]] = None) -> dict:
    """Translate guarded query params into a Mongo ``$match``; raises when nothing filters."""
    match: dict[str, Any] = {}
    if params.get("dataset_id"):
        match["dataset_id"] = sanitize_text(params["dataset_id"], max_length=40)
    if params.get("region"):
        match["location.region"] = {"$regex": _escape_regex(params["region"]), "$options": "i"}
    if params.get("station"):
        station = sanitize_text(params["station"], max_length=64)
        match["$or"] = [{"location.station_id": station}, {"source.station_id": station}]
    if params.get("granularity"):
        granularity = sanitize_text(params["granularity"], max_length=20)
        if granularity not in GRANULARITIES:
            raise ValidationError(f"Unknown granularity '{granularity}'.")
        match["granularity"] = granularity
    if params.get("quality"):
        quality = sanitize_text(params["quality"], max_length=16)
        if quality not in QUALITY_STATUSES:
            raise ValidationError(f"Unknown quality status '{quality}'.")
        match["quality.status"] = quality
    box = _bbox(params)
    if box:
        match["location.latitude"] = {"$gte": box["min_lat"], "$lte": box["max_lat"]}
        match["location.longitude"] = {"$gte": box["min_lon"], "$lte": box["max_lon"]}
    lat, lon = params.get("latitude"), params.get("longitude")
    if lat and lon:
        try:
            match["location.latitude"] = {"$gte": float(lat) - 0.5, "$lte": float(lat) + 0.5}
            match["location.longitude"] = {"$gte": float(lon) - 0.5, "$lte": float(lon) + 0.5}
        except (TypeError, ValueError):
            raise ValidationError("latitude/longitude must be numeric.") from None
    window = _window(params.get("date_from"), params.get("date_to"))
    if window:
        match["timestamp"] = window
    wanted = [sanitize_text(v, max_length=32) for v in (variables or []) if v]
    if params.get("variable"):
        wanted.append(sanitize_text(params["variable"], max_length=32))
    if wanted:
        fields = [metric_value_field(v) for v in dict.fromkeys(wanted)]
        if len(fields) == 1:
            match[fields[0]] = {"$type": _NUMERIC}
        else:
            match["$and"] = [_numeric_match(f) for f in fields]
    if not match:
        raise ValidationError(
            "At least one filter is required (dataset_id, region, station, variable, "
            "granularity, quality or a date/bbox window)."
        )
    return match


def _escape_regex(text: str) -> str:
    import re as _re

    return _re.escape(sanitize_text(text, max_length=80))


def list_observations(match: dict, *, sort: str = "timestamp", skip: int = 0,
                      limit: int = 25) -> tuple[list[dict], int]:
    field, direction = _OBS_SORTS.get(sort, ("timestamp", -1))
    projection = {
        "timestamp": 1, "granularity": 1, "location": 1, "metrics": 1,
        "quality": 1, "source": 1, "dataset_id": 1, "dedupe_key": 1, "provenance": 1,
    }
    total = count("climate_observations", match)
    rows = find("climate_observations", match, sort=[(field, direction), ("_id", 1)],
                skip=skip, limit=min(limit, 200), projection=projection)
    return rows, total


def get_observation(observation_id: str) -> dict:
    parsed = oid(observation_id)
    document = find_one("climate_observations", {"_id": parsed}) if parsed else None
    if not document:
        raise NotFound("Observation not found.")
    return document


def observation_count(match: Optional[dict] = None) -> int:
    return count("climate_observations", match or {})


def observed_range() -> dict:
    rows = aggregate("climate_observations", [
        {"$group": {"_id": None, "first": {"$min": "$timestamp"}, "last": {"$max": "$timestamp"},
                    "granularities": {"$addToSet": "$granularity"}}},
    ])
    row = rows[0] if rows else {}
    if not row:
        return {"available": False, "reason": "no observations ingested yet",
                "from": None, "to": None, "granularities": []}
    return {"available": True, "from": row.get("first"), "to": row.get("last"),
            "granularities": sorted(str(g) for g in (row.get("granularities") or []) if g)}


def count_by_quality() -> dict[str, int]:
    rows = aggregate("climate_observations", [
        {"$group": {"_id": "$quality.status", "count": {"$sum": 1}}},
    ])
    counts = {status: 0 for status in QUALITY_STATUSES}
    for row in rows:
        counts[str(row["_id"] or "unassessed")] = row["count"]
    return counts


def region_rows() -> list[dict]:
    """Latest reading + bounding box per region, ordered by observation volume."""
    return aggregate("climate_observations", [
        {"$match": {"location.region": {"$nin": [None, ""]}}},
        {"$sort": {"timestamp": -1}},
        {"$group": {
            "_id": "$location.region",
            "observations": {"$sum": 1},
            "latitude": {"$first": "$location.latitude"},
            "longitude": {"$first": "$location.longitude"},
            "min_lat": {"$min": "$location.latitude"},
            "max_lat": {"$max": "$location.latitude"},
            "min_lon": {"$min": "$location.longitude"},
            "max_lon": {"$max": "$location.longitude"},
            "country": {"$first": "$location.country"},
            "station_id": {"$first": "$location.station_id"},
            "latest_timestamp": {"$first": "$timestamp"},
            "latest_temperature": {"$first": "$metrics.temperature.value"},
            "avg_temperature": {"$avg": "$metrics.temperature.value"},
            "avg_temperature_max": {"$avg": "$metrics.temperature_max.value"},
            "avg_temperature_min": {"$avg": "$metrics.temperature_min.value"},
            "latest_precipitation": {"$first": "$metrics.precipitation.value"},
            "avg_precipitation": {"$avg": "$metrics.precipitation.value"},
            "suspect": {"$sum": {"$cond": [{"$eq": ["$quality.status", "suspect"]}, 1, 0]}},
        }},
        {"$sort": {"observations": -1}},
        {"$limit": 200},
    ])


def variable_series(variable: str, *, region: Optional[str] = None,
                    granularity: Optional[str] = None, date_from: Any = None,
                    date_to: Any = None, bucket: str = "month",
                    limit: int = 600) -> list[dict]:
    """Mean/min/max/count per calendar bucket for one variable, from stored readings only."""
    field = metric_value_field(variable)
    match: dict[str, Any] = _numeric_match(field)
    if region:
        match["location.region"] = sanitize_text(region, max_length=80)
    if granularity:
        match["granularity"] = sanitize_text(granularity, max_length=20)
    window = _window(date_from, date_to)
    if window:
        match["timestamp"] = window
    fmt = "%Y" if bucket == "year" else "%Y-%m"
    value_ref = "$" + field
    rows = aggregate("climate_observations", [
        {"$match": match},
        {"$group": {
            "_id": {"$dateToString": {"format": fmt, "date": "$timestamp", "timezone": "UTC"}},
            "n": {"$sum": 1},
            "mean": {"$avg": value_ref},
            "min": {"$min": value_ref},
            "max": {"$max": value_ref},
        }},
        {"$sort": {"_id": 1}},
        {"$limit": limit},
    ])
    return [
        {"period": row["_id"], "sample_size": row["n"], "mean": row["mean"],
         "minimum": row["min"], "maximum": row["max"]}
        for row in rows
    ]


def variable_pairs(variable_a: str, variable_b: str, *, region: Optional[str] = None,
                   date_from: Any = None, date_to: Any = None,
                   limit: int = 5000) -> list[dict]:
    """Rows carrying BOTH variables as real numbers; used for correlation over shared samples."""
    field_a, field_b = metric_value_field(variable_a), metric_value_field(variable_b)
    match: dict[str, Any] = {**_numeric_match(field_a), **_numeric_match(field_b)}
    if region:
        match["location.region"] = sanitize_text(region, max_length=80)
    window = _window(date_from, date_to)
    if window:
        match["timestamp"] = window
    projection = {field_a: 1, field_b: 1, "timestamp": 1}
    cursor = collection("climate_observations").find(match, projection).sort("timestamp", 1).limit(limit)
    return list(cursor)


def distinct_variable_counts() -> list[dict]:
    rows = aggregate("climate_observations", [
        {"$project": {"keys": {"$objectToArray": "$metrics"}}},
        {"$unwind": "$keys"},
        {"$group": {"_id": "$keys.k", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])
    return [{"variable": row["_id"], "observations": row["count"]} for row in rows]


def quality_report() -> dict:
    """Per-dataset completeness, quality mix, provenance transformations and duplicate check."""
    per_field = {}
    for variable in VARIABLES:
        field = f"metrics.{variable}.value"
        per_field[variable] = {
            "$sum": {"$cond": [{"$ne": [{"$type": "$" + field}, "missing"]}, 1, 0]}
        }
    rows = aggregate("climate_observations", [
        {"$group": {
            "_id": "$dataset_id",
            "observations": {"$sum": 1},
            "valid": {"$sum": {"$cond": [{"$eq": ["$quality.status", "valid"]}, 1, 0]}},
            "suspect": {"$sum": {"$cond": [{"$eq": ["$quality.status", "suspect"]}, 1, 0]}},
            "rejected": {"$sum": {"$cond": [{"$eq": ["$quality.status", "rejected"]}, 1, 0]}},
            "distinct_keys": {"$addToSet": "$dedupe_key"},
            **per_field,
        }},
        {"$sort": {"observations": -1}},
        {"$limit": 200},
    ])
    transformations = aggregate("climate_observations", [
        {"$match": {"provenance.transformations": {"$exists": True}}},
        {"$unwind": "$provenance.transformations"},
        {"$group": {"_id": "$provenance.transformations", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 100},
    ])
    datasets = []
    for row in rows:
        total = int(row.get("observations") or 0)
        completeness = {
            variable: {
                "present": int(row.get(variable, 0)),
                "coverage": round((int(row.get(variable, 0)) / total), 4) if total else None,
            }
            for variable in VARIABLES
        }
        distinct = len([k for k in (row.get("distinct_keys") or []) if k])
        datasets.append({
            "dataset_id": str(row["_id"]) if row["_id"] else None,
            "observations": total,
            "quality": {"valid": row.get("valid", 0), "suspect": row.get("suspect", 0),
                        "rejected": row.get("rejected", 0)},
            "completeness": completeness,
            "duplicates": max(total - distinct, 0),
        })
    return {
        "datasets": datasets,
        "transformations_applied": [
            {"transformation": t["_id"], "count": t["count"]} for t in transformations
        ],
    }


def analytics_summaries(params: dict[str, str], *, page: int = 1,
                        page_size: int = 25) -> tuple[list[dict], int]:
    conditions: list[dict[str, Any]] = []
    if params.get("job"):
        job = sanitize_text(params["job"], max_length=80)
        conditions.append({"$or": [{"job_id": job}, {"job": job}]})
    if params.get("summary_type"):
        conditions.append({"summary_type": sanitize_text(params["summary_type"], max_length=60)})
    if params.get("engine"):
        engine = sanitize_text(params["engine"], max_length=40)
        conditions.append({"$or": [{"engine_used": engine}, {"engine": engine}]})
    window = _window(params.get("date_from"), params.get("date_to"))
    if window:
        conditions.append({"$or": [{"generated_at": window}, {"computed_at": window},
                                   {"finished_at": window}]})
    query: dict[str, Any] = conditions[0] if len(conditions) == 1 else (
        {"$and": conditions} if conditions else {})
    total = count("analytics_summaries", query)
    rows = find("analytics_summaries", query, sort=[("computed_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(row) for row in rows], total


def analytics_summary_overview() -> dict:
    rows = aggregate("analytics_summaries", [
        {"$group": {"_id": {"$ifNull": ["$engine_used", "$engine"]}, "count": {"$sum": 1}}},
    ])
    return {"total": count("analytics_summaries", {}),
            "by_engine": {str(r["_id"] or "unknown"): r["count"] for r in rows}}


# --------------------------------------------------------------------------- jobs/predictions
def ingestion_overview() -> dict:
    status_rows = aggregate("ingestion_jobs", [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    by_status = {str(r["_id"] or "unknown"): r["count"] for r in status_rows}
    accepted = aggregate("ingestion_jobs", [
        {"$group": {"_id": None, "accepted": {"$sum": "$records_accepted"},
                    "rejected": {"$sum": "$records_rejected"},
                    "duplicates": {"$sum": "$records_duplicate"}}},
    ])
    totals = accepted[0] if accepted else {}
    synced = find("ingestion_jobs", {"status": "completed"},
                  sort=[("completed_at", -1), ("updated_at", -1)], limit=1)
    last_sync = synced[0] if synced else None
    return {
        "by_status": by_status,
        "success": by_status.get("completed", 0),
        "failed": by_status.get("failed", 0),
        "records_accepted": int(totals.get("accepted") or 0),
        "records_rejected": int(totals.get("rejected") or 0),
        "records_duplicate": int(totals.get("duplicates") or 0),
        "last_successful_sync": document_to_api(last_sync) if last_sync else None,
    }


def processing_overview() -> dict:
    status_rows = aggregate("processing_jobs", [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    engine_rows = aggregate("processing_jobs", [
        {"$group": {"_id": "$engine", "count": {"$sum": 1}}},
    ])
    recent = find("processing_jobs", {}, sort=[("created_at", -1)], limit=5)
    return {
        "by_status": {str(r["_id"] or "unknown"): r["count"] for r in status_rows},
        "by_engine": {str(r["_id"] or "unknown"): r["count"] for r in engine_rows},
        "recent": [document_to_api(row) for row in recent],
    }


def recent_alerts(limit: int = 10) -> list[dict]:
    rows = find("alert_events", {}, sort=[("detected_at", -1), ("_id", -1)], limit=limit)
    return [document_to_api(row) for row in rows]


def alerts_overview(limit: int = 10) -> dict:
    status_rows = aggregate("alert_events", [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ])
    region_rows = aggregate("alert_events", [
        {"$match": {"region": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$region", "count": {"$sum": 1}}},
    ])
    return {
        "by_status": {str(r["_id"] or "unknown"): r["count"] for r in status_rows},
        "by_region": {str(r["_id"]): r["count"] for r in region_rows},
        "recent": recent_alerts(limit),
    }


def anomaly_summary(limit: int = 20) -> dict:
    events = aggregate("alert_events", [
        {"$group": {"_id": {"$ifNull": ["$target_variable", "$variable"]}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])
    summaries = find("analytics_summaries", {"summary_type": "anomaly_features"},
                     sort=[("computed_at", -1), ("_id", -1)], limit=limit)
    return {
        "events_by_variable": {str(r["_id"] or "unknown"): r["count"] for r in events},
        "event_total": count("alert_events", {}),
        "recent_events": recent_alerts(limit),
        "anomaly_feature_summaries": [document_to_api(row) for row in summaries],
    }


def forecast_overview(limit: int = 10) -> dict:
    rows = find("predictions", {}, sort=[("generated_at", -1), ("_id", -1)], limit=limit)
    grouped: dict[str, dict] = {}
    for row in rows:
        key = f"{row.get('model_ref') or row.get('model')}|{row.get('target_variable')}|{row.get('region')}"
        value = row.get("value")
        bucket = grouped.setdefault(key, {"model": row.get("model_ref") or row.get("model"),
                                          "target_variable": row.get("target_variable"),
                                          "region": row.get("region"), "unit": row.get("unit"),
                                          "forecasts": 0, "latest_generated_at": None, "values": []})
        bucket["forecasts"] += 1
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            bucket["values"].append(float(value))
        generated = row.get("generated_at")
        if generated and (bucket["latest_generated_at"] is None
                          or str(generated) > str(bucket["latest_generated_at"])):
            bucket["latest_generated_at"] = generated
    summaries = []
    for bucket in grouped.values():
        values = bucket.pop("values")
        bucket["mean"] = round(sum(values) / len(values), 3) if values else None
        summaries.append(bucket)
    summaries.sort(key=lambda item: item["forecasts"], reverse=True)
    return {"total": count("predictions", {}), "latest": summaries[:limit],
            "recent": [document_to_api(row) for row in rows]}


def dataset_count() -> int:
    return count("datasets", {})


# --------------------------------------------------------------------------- saved analyses
def _shared_flag(view_state: dict) -> bool:
    return bool(view_state.get("shared"))


def _safe_view_state(view_state: Any) -> dict:
    if not isinstance(view_state, dict):
        return {}
    try:
        return guard_query(view_state)
    except ValueError as exc:
        raise ValidationError("view_state contains a disallowed key.") from exc


def create_analysis(*, name: str, description: str, view_state: dict, owner_id: str) -> dict:
    clean_state = _safe_view_state(view_state)
    now = now_utc()
    document = {
        "name": sanitize_text(name, max_length=140),
        "description": sanitize_text(description, max_length=1000) if description else "",
        "view_state": clean_state,
        "shared": _shared_flag(clean_state),
        "owner": str(owner_id),
        "created_at": now,
        "updated_at": now,
        "last_modified_by": str(owner_id),
    }
    document["_id"] = oid(insert_one("saved_analyses", document))
    return document


def get_analysis(analysis_id: str) -> dict:
    parsed = oid(analysis_id)
    document = find_one("saved_analyses", {"_id": parsed}) if parsed else None
    if not document:
        raise NotFound("Saved analysis not found.")
    return document


def list_analyses(user_id: str, *, scope: str = "mine", page: int = 1,
                  page_size: int = 25) -> tuple[list[dict], int]:
    if scope == "all":
        query = {"$or": [{"owner": str(user_id)}, {"shared": True}]}
    else:
        query = {"owner": str(user_id)}
    total = count("saved_analyses", query)
    rows = find("saved_analyses", query, sort=[("updated_at", -1), ("_id", -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(row) for row in rows], total


def patch_analysis(analysis_id: str, identity, changes: dict) -> dict:
    document = get_analysis(analysis_id)
    _assert_owner(document, identity)
    update: dict[str, Any] = {"updated_at": now_utc(),
                              "last_modified_by": str(identity.user_id)}
    if "name" in changes:
        update["name"] = sanitize_text(changes["name"], max_length=140)
    if "description" in changes:
        update["description"] = sanitize_text(changes["description"], max_length=1000)
    if "view_state" in changes:
        clean_state = _safe_view_state(changes["view_state"])
        update["view_state"] = clean_state
        update["shared"] = _shared_flag(clean_state)
    update_one("saved_analyses", {"_id": document["_id"]}, {"$set": update})
    return document_to_api(get_analysis(analysis_id))


def delete_analysis(analysis_id: str, identity) -> dict:
    document = get_analysis(analysis_id)
    _assert_owner(document, identity)
    collection("saved_analyses").delete_one({"_id": document["_id"]})
    return {"deleted": True, "id": str(document["_id"])}


def can_read(document: dict, identity) -> bool:
    if str(document.get("owner")) == str(identity.user_id):
        return True
    shared = bool(document.get("shared")) or bool((document.get("view_state") or {}).get("shared"))
    return shared


def _assert_owner(document: dict, identity) -> None:
    if str(document.get("owner")) != str(identity.user_id):
        raise Forbidden("Only the analysis owner may modify or delete it.")
