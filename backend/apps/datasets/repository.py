"""Dataset metadata repository (MongoDB `datasets` collection)."""

from __future__ import annotations

from typing import Any, Optional

from common.db import aggregate, collection, find, find_one, insert_one, now_utc, oid, update_one
from common.exceptions import Conflict, NotFound, ValidationError
from common.security import sanitize_text

STATUS_CHOICES = ("draft", "uploaded", "validating", "validated", "ingesting", "ready",
                  "partial", "failed", "archived")


def _base_document(*, name: str, description: str | None, source_type: str, source_name: str,
                   source_url: str | None, fmt: str, uploaded_by: str, file_name: str | None,
                   storage_uri: str | None, geographic_coverage: dict | None,
                   temporal_coverage: dict | None, schema_version: str, tags: list[str]) -> dict:
    now = now_utc()
    return {
        "name": sanitize_text(name, max_length=160),
        "description": sanitize_text(description, max_length=2000) if description else "",
        "source_type": source_type,
        "source_name": sanitize_text(source_name, max_length=120),
        "source_url": sanitize_text(source_url, max_length=500) if source_url else None,
        "file_name": sanitize_text(file_name, max_length=200) if file_name else None,
        "storage_uri": storage_uri,
        "format": fmt,
        "schema_version": schema_version,
        "geographic_coverage": geographic_coverage or {},
        "temporal_coverage": temporal_coverage or {},
        "record_count": 0,
        "rejected_count": 0,
        "duplicate_count": 0,
        "quality_score": None,
        "status": "draft",
        "tags": [sanitize_text(t, max_length=32) for t in (tags or [])[:20]],
        "column_map": {},
        "units_hint": {},
        "granularity": "daily",
        "default_location": {},
        "uploaded_by": uploaded_by,
        "created_at": now,
        "updated_at": now,
        "last_ingestion_at": None,
        "archived_at": None,
    }


def create(*, name: str, source_type: str, source_name: str, fmt: str, uploaded_by: str,
           description: str = "", source_url: str = "", file_name: str = "",
           storage_uri: str = "", geographic_coverage: dict | None = None,
           temporal_coverage: dict | None = None, schema_version: str = "1.0.0",
           tags: list[str] | None = None) -> dict:
    clean_name = sanitize_text(name, max_length=160)
    if len(clean_name) < 2:
        raise ValidationError("Dataset name must contain at least 2 characters.")
    existing = find_one("datasets", {"name": clean_name,
                                     "source_name": sanitize_text(source_name, max_length=120)})
    if existing:
        raise Conflict(f"A dataset named '{clean_name}' from {source_name} already exists "
                       f"(id {existing['_id']}). Upload a new version or reuse it.")
    document = _base_document(name=clean_name, description=description, source_type=source_type,
                              source_name=source_name, source_url=source_url or None, fmt=fmt,
                              uploaded_by=uploaded_by, file_name=file_name or None,
                              storage_uri=storage_uri or None,
                              geographic_coverage=geographic_coverage,
                              temporal_coverage=temporal_coverage,
                              schema_version=schema_version, tags=tags or [])
    document["_id"] = insert_one("datasets", document)
    return document


def find_by_name(name: str, source_name: str) -> Optional[dict]:
    return find_one("datasets", {"name": sanitize_text(name, max_length=160),
                                 "source_name": sanitize_text(source_name, max_length=120)})


def get(dataset_id: str) -> dict:
    parsed = oid(dataset_id)
    if not parsed:
        raise NotFound("Dataset not found.")
    document = collection("datasets").find_one({"_id": parsed})
    if not document:
        raise NotFound("Dataset not found.")
    return document


def get_optional(dataset_id: str) -> Optional[dict]:
    parsed = oid(dataset_id)
    if not parsed:
        return None
    return collection("datasets").find_one({"_id": parsed})


def list_datasets(*, search: str = "", status: str = "", source_type: str = "", page: int = 1,
                  page_size: int = 25, order: str = "-created_at") -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if search:
        text = sanitize_text(search, max_length=60)
        query["$or"] = [{"name": {"$regex": text, "$options": "i"}},
                        {"description": {"$regex": text, "$options": "i"}},
                        {"tags": {"$regex": text, "$options": "i"}}]
    if status and status in STATUS_CHOICES:
        query["status"] = status
    if source_type:
        query["source_type"] = sanitize_text(source_type, max_length=20)
    total = collection("datasets").count_documents(query)
    allowed_orders = {"-created_at", "created_at", "-updated_at", "updated_at", "name", "-name",
                      "-record_count", "record_count"}
    sort_field = order if order in allowed_orders else "-created_at"
    rows = find("datasets", query, sort=[(sort_field, 1 if not sort_field.startswith("-") else -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return rows, total


def patch(dataset_id: str, changes: dict) -> dict:
    document = get(dataset_id)
    allowed = {"name", "description", "tags", "status", "column_map", "units_hint", "granularity",
               "default_location", "geographic_coverage", "temporal_coverage", "source_url",
               "file_name", "storage_uri", "schema_version"}
    update: dict[str, Any] = {"updated_at": now_utc()}
    for key, value in changes.items():
        if key not in allowed:
            continue
        if isinstance(value, str):
            update[key] = sanitize_text(value, max_length=2000 if key == "description" else 200)
        elif key == "tags":
            update[key] = [sanitize_text(t, max_length=32) for t in list(value or [])[:20]]
        elif isinstance(value, dict):
            update[key] = value
        elif key == "status" and value in STATUS_CHOICES:
            update[key] = value
        else:
            continue
    if update.get("status") == "archived":
        update["archived_at"] = now_utc()
    update_one("datasets", {"_id": document["_id"]}, {"$set": update})
    return get(dataset_id)


def record_ingestion_result(dataset_id: str, *, records_added: int, coverage: dict, status: str,
                            storage_uri: str | None = None, rejected: int = 0,
                            duplicates: int = 0, quality_score: float | None = None) -> dict:
    document = get(dataset_id)
    update: dict[str, Any] = {
        "status": status,
        "record_count": int(document.get("record_count", 0)) + int(records_added),
        "rejected_count": int(document.get("rejected_count", 0)) + int(rejected),
        "duplicate_count": int(document.get("duplicate_count", 0)) + int(duplicates),
        "last_ingestion_at": now_utc(),
        "updated_at": now_utc(),
    }
    if coverage:
        temporal = {}
        if coverage.get("temporal_start"):
            temporal["start"] = coverage.get("temporal_start")
        if coverage.get("temporal_end"):
            temporal["end"] = coverage.get("temporal_end")
        if temporal:
            previous = document.get("temporal_coverage") or {}
            merged = dict(temporal)
            if previous.get("start"):
                merged["start"] = min(str(previous["start"]), str(temporal.get("start", previous["start"])))
            if previous.get("end"):
                merged["end"] = max(str(previous["end"]), str(temporal.get("end", previous["end"])))
            update["temporal_coverage"] = merged
        geo = {"regions": coverage.get("regions", [])[:40]}
        if coverage.get("bounding_box"):
            geo["bounding_box"] = coverage["bounding_box"]
        update["geographic_coverage"] = geo
    if storage_uri:
        update["storage_uri"] = storage_uri
    if quality_score is not None:
        update["quality_score"] = round(float(quality_score), 3)
    update_one("datasets", {"_id": document["_id"]}, {"$set": update})
    return get(dataset_id)


def archive(dataset_id: str) -> dict:
    return patch(dataset_id, {"status": "archived"})


def delete(dataset_id: str, *, include_observations: bool = False) -> dict:
    document = get(dataset_id)
    removed_observations = 0
    if include_observations:
        result = collection("climate_observations").delete_many({"dataset_id": str(document["_id"])})
        removed_observations = result.deleted_count
    collection("datasets").delete_one({"_id": document["_id"]})
    collection("ingestion_jobs").update_many({"dataset_id": str(document["_id"])},
                                             {"$set": {"dataset_deleted": True}})
    return {"dataset_id": str(document["_id"]), "observations_removed": removed_observations}


def storage_stats() -> dict:
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}, "records": {"$sum": "$record_count"}}},
        {"$sort": {"count": -1}},
    ]
    rows = aggregate("datasets", pipeline)
    by_status = {row["_id"] or "unknown": row["count"] for row in rows}
    total_records = sum(row.get("records", 0) or 0 for row in rows)
    return {"by_status": by_status, "total": sum(by_status.values()), "total_records": total_records}


def summary_card(dataset_id: str) -> dict:
    """Aggregate view of one dataset built from real observation documents."""
    document = get(dataset_id)
    ident = str(document["_id"])
    stats = aggregate("climate_observations", [
        {"$match": {"dataset_id": ident}},
        {"$group": {
            "_id": None,
            "observations": {"$sum": 1},
            "first": {"$min": "$timestamp"},
            "last": {"$max": "$timestamp"},
            "regions": {"$addToSet": "$location.region"},
            "stations": {"$addToSet": "$location.station_id"},
            "suspect": {"$sum": {"$cond": [{"$eq": ["$quality.status", "suspect"]}, 1, 0]}},
            "valid": {"$sum": {"$cond": [{"$eq": ["$quality.status", "valid"]}, 1, 0]}},
            "avg_temperature": {"$avg": "$metrics.temperature.value"},
            "total_precipitation": {"$sum": {"$ifNull": ["$metrics.precipitation.value", 0]}},
            "avg_humidity": {"$avg": "$metrics.humidity.value"},
        }},
    ])
    row = stats[0] if stats else {}
    variables_present = aggregate("climate_observations", [
        {"$match": {"dataset_id": ident}},
        {"$project": {"keys": {"$objectToArray": "$metrics"}}},
        {"$unwind": "$keys"},
        {"$group": {"_id": "$keys.k", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
    ])
    observations = int(row.get("observations") or 0)
    quality_score = (row.get("valid", 0) / observations) if observations else None
    return {
        "dataset": document,
        "observations": observations,
        "first_timestamp": row.get("first"),
        "last_timestamp": row.get("last"),
        "regions": sorted([r for r in (row.get("regions") or []) if r])[:40],
        "station_count": len([s for s in (row.get("stations") or []) if s]),
        "quality": {"valid": row.get("valid", 0), "suspect": row.get("suspect", 0),
                    "score": round(quality_score, 3) if quality_score is not None else None},
        "aggregates": {
            "average_temperature_c": round(row["avg_temperature"], 2) if row.get("avg_temperature") else None,
            "total_precipitation_mm": round(row["total_precipitation"], 2) if row.get("total_precipitation") else None,
            "average_humidity_pct": round(row["avg_humidity"], 2) if row.get("avg_humidity") else None,
        },
        "variables": [{"name": item["_id"], "observations": item["count"]} for item in variables_present],
        "declared_record_count": document.get("record_count", 0),
        "stored_observations_match_declared": observations == int(document.get("record_count") or 0),
    }


def distinct_regions() -> list[str]:
    rows = aggregate("climate_observations", [
        {"$match": {"location.region": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$location.region", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 200},
    ])
    return [row["_id"] for row in rows]
