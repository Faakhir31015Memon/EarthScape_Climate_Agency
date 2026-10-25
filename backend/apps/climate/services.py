"""Climate analytics services.

Every value produced here is read out of MongoDB. Two mechanisms are used
deliberately:

* **aggregation pipelines** (``$match`` / ``$group`` / ``$facet`` / ``$project`` over
  stored fields) for set-wide aggregates such as per-region statistics, per-station
  counts, trend buckets and coverage;
* the shared :mod:`common.analytics` kernel as the fallback for any grouping or
  frequency the pipelines do not model (correlations, anomaly detection), so the API
  and the PySpark/MapReduce outputs stay numerically identical.

Pipelines only use stages supported by both Atlas and the local document store,
and they only touch *stored* fields - ``observation_date`` is a ``YYYY-MM-DD``
string, so month bucketing uses ``$substr``/``year``/``month`` instead of date
operators, and weekly or ordered buckets are folded and sorted in Python because the
shared tier cannot sort tens of thousands of keys on the server. Empty collections
produce empty lists plus a ``reason``; nothing is invented.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from common import analytics, regions, repositories, validators
from common.analytics import ADDITIVE_METRICS, METRIC_LABELS, METRIC_UNITS, InsufficientData
from common.api import ApiError
from common.repositories import (
    alerts,
    analytics_summaries,
    audit_logs,
    climate_observations,
    datasets as datasets_repository,
    ingestion_jobs,
    model_registry,
    notifications,
    predictions,
    processing_jobs,
    regions as regions_repository,
    saved_analyses,
    support_tickets,
    utcnow,
)
from config import env

logger = logging.getLogger(__name__)

from apps.climate import explorer  # noqa: E402  (module-level keeps imports explicit)

# --------------------------------------------------------------------- bounds
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50
ANALYTICS_ROW_LIMIT = env.get_int("CLIMATE_ANALYTICS_MAX_ROWS", 60000)
QUALITY_SAMPLE_LIMIT = env.get_int("CLIMATE_QUALITY_MAX_ROWS", 40000)
DASHBOARD_ROW_LIMIT = env.get_int("DASHBOARD_ANALYTICS_MAX_ROWS", 30000)
MAX_EXPORT_ROWS = env.get_int("MAX_EXPORT_ROWS", 200000)
MAX_ALERTS_PERSISTED = env.get_int("CLIMATE_MAX_ALERTS_PERSISTED", 500)
CLUSTER_POINT_THRESHOLD = 300
CLUSTER_COORD_PRECISION = 2
SERIES_PAGE_LIMIT = 2000
DASHBOARD_HISTORY_LIMIT = 30
SUMMARY_WINDOW_DAYS = env.get_int("DASHBOARD_QUALITY_WINDOW_DAYS", 31)

DEFAULT_MAP_METRICS = ("temperature_c", "precipitation_mm", "relative_humidity_pct")
ANOMALY_METHODS = ("statistical_baseline", "threshold", "isolation_forest")
ANOMALY_DIRECTIONS = ("above", "below", "both")
FREQUENCIES = ("daily", "weekly", "monthly", "annual")
GROUP_BY_OPTIONS = ("none", "region", "location", "country")
QUALITY_OPTIONS = ("clean", "flagged", "any")
ALERT_SCAN_WORKFLOW = "climate.anomaly_scan"
ALERT_SCAN_RULE_PREFIX = "climate.anomaly"

FRESH_DAYS = 3
LAGGING_DAYS = 14
FRESHNESS_LABELS = {
    "fresh": f"Newest stored observation is within {FRESH_DAYS} days.",
    "lagging": f"Newest stored observation is {FRESH_DAYS + 1}-{LAGGING_DAYS} days old.",
    "stalled": f"No observation for more than {LAGGING_DAYS} days.",
    "no_data": "This source has no observation documents.",
}

OBSERVATION_SORTS: Dict[str, List[Tuple[str, int]]] = {
    "-observation_date": [("observation_date", -1), ("location_code", 1)],
    "observation_date": [("observation_date", 1), ("location_code", 1)],
    "-ingested_at": [("ingested_at", -1)],
    "ingested_at": [("ingested_at", 1)],
    "-year": [("year", -1), ("month", 1), ("location_code", 1)],
    "year": [("year", 1), ("month", 1), ("location_code", 1)],
    "location_code": [("location_code", 1), ("observation_date", -1)],
    "-location_code": [("location_code", -1), ("observation_date", -1)],
    "dataset_id": [("dataset_id", 1), ("observation_date", -1)],
    "-dataset_id": [("dataset_id", -1), ("observation_date", -1)],
}
DEFAULT_OBSERVATION_SORT = "-observation_date"

LIST_PROJECTION_EXCLUSIONS = {"coordinates": 0, "provenance": 0}


# ------------------------------------------------------------------ parameters
def date_filter(request, *names: str) -> Tuple[Optional[str], Optional[str]]:
    """Read an optional ``YYYY-MM-DD`` window from two interchangeable keys."""
    from common.views import date_param

    start = None
    end = None
    for index, name in enumerate(names):
        if index == 0:
            start = date_param(request, name, start)
        else:
            end = date_param(request, name, end)
    if start and end and start > end:
        raise ApiError("validation_failed", "start must be on or before end.")
    return start, end


def first_validation_message(errors: Any) -> str:
    """The most specific message a serializer produced, for the error envelope."""
    if isinstance(errors, dict):
        for key, value in errors.items():
            if isinstance(value, dict):
                return first_validation_message(value)
            text = str(value[0] if isinstance(value, (list, tuple)) and value else value)[:240]
            return text if key in ("non_field_errors", "__all__") else f"{key}: {text}"
    if isinstance(errors, (list, tuple)) and errors:
        return str(errors[0])[:240]
    return str(errors)[:240]


def validated_body(serializer: Any) -> Dict[str, Any]:
    """Validate a request body and raise the platform's 400 envelope.

    ``raise_exception=True`` would surface DRF's generic wording; the client is
    told exactly which field failed and why.
    """
    if serializer.is_valid():
        return serializer.validated_data
    raise ApiError("validation_failed", first_validation_message(serializer.errors),
                   details=dict(serializer.errors) if isinstance(serializer.errors, dict) else None)


def default_window(*, days: int = 365, end: Optional[str] = None) -> Tuple[str, str]:
    closing = parse_date(end) or utcnow().date()
    return (closing - timedelta(days=max(1, int(days)))).isoformat(), closing.isoformat()


def regions_list(request, *, max_items: int = 50) -> List[str]:
    """Region codes from ``?regions=`` / ``?region=`` (comma tolerant, deduped)."""
    from common.views import list_param

    raw = list_param(request, "regions") + list_param(request, "region")
    return _dedupe([item for item in raw if item])[:max_items]


def _dedupe(values: Sequence[str]) -> List[str]:
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def coerce_identifier(value: Any) -> List[Any]:
    """Match a stored id whether it was persisted as a string or a BSON ObjectId."""
    text = str(value).strip()
    if not text:
        return []
    out: List[Any] = [text]
    try:
        out.append(ObjectId(text))
    except (InvalidId, TypeError):
        pass
    return out


def coerce_dataset_ids(values: Optional[Sequence[Any]]) -> List[Any]:
    out: List[Any] = []
    for value in values or ():
        if isinstance(value, (ObjectId, int, float)):
            out.append(value)
            continue
        out.extend(coerce_identifier(value))
    return _dedupe_mixed(out)[:100]


def _dedupe_mixed(values: Sequence[Any]) -> List[Any]:
    seen: set = set()
    out: List[Any] = []
    for value in values:
        marker = (type(value).__name__, str(value))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(value)
    return out


# ------------------------------------------------------------------- filters
def build_filters(*, start: Optional[str] = None, end: Optional[str] = None,
                  regions_filter: Optional[Sequence[str]] = None,
                  location_codes: Optional[Sequence[str]] = None,
                  datasets_list: Optional[Sequence[Any]] = None,
                  countries: Optional[Sequence[str]] = None,
                  metric: Optional[str] = None,
                  quality: str = "any",
                  source_names: Optional[Sequence[str]] = None,
                  year: Optional[int] = None,
                  month: Optional[int] = None,
                  limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    """MongoDB criteria for ``climate_observations`` plus the analytics row limit.

    Delegates to :func:`common.analytics.observation_query` so the API and the
    kernel always agree on scope, then adds the quality/source filters the
    kernel does not model.
    """
    criteria = analytics.observation_query(
        dataset_ids=coerce_dataset_ids(datasets_list),
        regions=regions_filter,
        location_codes=location_codes,
        start_date=start,
        end_date=end,
        countries=countries,
        metric=metric,
        exclude_flagged=(quality == "clean"),
        limit=min(max(int(limit), 1), analytics.MAX_QUERY_ROWS),
    )
    if quality == "flagged":
        criteria["quality_flags.0"] = {"$exists": True}
    if source_names:
        criteria["source_name"] = {"$in": _dedupe(source_names)[:100]}
    if year:
        criteria["year"] = int(year)
    if month:
        criteria["month"] = int(month)
    return criteria


def strip_limit(criteria: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    row_criteria = dict(criteria)
    limit = int(row_criteria.pop("_limit", analytics.MAX_QUERY_ROWS))
    return row_criteria, limit


def matching_row_count(criteria: Dict[str, Any]) -> int:
    row_criteria, limit = strip_limit(criteria)
    try:
        return min(climate_observations.count(row_criteria), limit)
    except repositories.RepositoryError as exc:
        logger.warning("Observation count failed: %s", exc)
        return 0


def fetch_rows(criteria: Dict[str, Any], *, projection: Optional[Dict[str, int]] = None,
               limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Read observation documents through the kernel so ordering/limits match."""
    row_criteria = dict(criteria)
    if limit is not None:
        row_criteria["_limit"] = min(max(int(limit), 1), analytics.MAX_QUERY_ROWS)
    return analytics.fetch_observations(row_criteria, projection=projection)


def metric_projection(metrics: Sequence[str], *, extra: Optional[Iterable[str]] = None
                      ) -> Dict[str, int]:
    """Inclusion projection carrying exactly the fields an analysis needs."""
    fields: Dict[str, int] = {
        "_id": 0, "observation_date": 1, "year": 1, "month": 1, "region_code": 1,
        "location_code": 1, "location_name": 1, "country": 1, "latitude": 1, "longitude": 1,
        "dataset_id": 1, "source_name": 1, "source_type": 1, "quality_flags": 1,
    }
    for metric in metrics:
        if metric in analytics.METRICS:
            fields[metric] = 1
    for name in extra or ():
        fields[name] = 1
    return fields


# ------------------------------------------------------- reference geography
def reference_region(code: str) -> Dict[str, Any]:
    meta = regions.REGION_META.get(code) or {}
    return {
        "code": code,
        "name": code,
        "country": meta.get("country"),
        "climate_zone": meta.get("climate_zone"),
        "centroid": meta.get("centroid"),
        "locations": [
            {
                "code": item["code"], "name": item["name"], "country": item["country"],
                "latitude": item["latitude"], "longitude": item["longitude"],
                "elevation_m": item["elevation_m"], "station_id": item["station_id"],
            }
            for item in regions.LOCATIONS if item["region_code"] == code
        ],
        "source": "reference",
    }


def stored_regions(*, limit: int = 200) -> List[Dict[str, Any]]:
    """Region documents: the ``regions`` collection when seeded, else reference data."""
    documents = regions_repository.find({"code": {"$ne": None}}, sort=[("code", 1)], limit=limit)
    if documents:
        out: List[Dict[str, Any]] = []
        for document in documents:
            serialized = repositories.serialize(document) or {}
            serialized["source"] = "regions_collection"
            out.append(serialized)
        return out
    return [{**document, "source": "reference"} for document in regions.region_documents()]


def region_index(*, limit: int = 200) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for document in stored_regions(limit=limit):
        code = document.get("code")
        if not code:
            continue
        key = str(code)
        if key not in index:
            index[key] = document
        else:
            logger.info("Duplicate region code %s ignored in favour of the first document", key)
    return index


def region_codes_present() -> List[str]:
    return _dedupe([str(code) for code in climate_observations.distinct("region_code") if code])


def location_codes_present() -> List[str]:
    return _dedupe([str(code) for code in climate_observations.distinct("location_code") if code])


def countries_present() -> List[str]:
    return _dedupe([str(name) for name in climate_observations.distinct("country") if name])


def country_options() -> List[Dict[str, Any]]:
    """Filter widget options: observed countries plus the reference catalogue."""
    counts: Dict[str, int] = {}
    pipeline = [
        {"$match": {"country": {"$ne": None}}},
        {"$group": {"_id": "$country", "observation_count": {"$sum": 1},
                    "regions": {"$addToSet": "$region_code"},
                    "last_date": {"$max": "$observation_date"}}},
        {"$sort": {"observation_count": -1}},
        {"$limit": 200},
    ]
    for row in climate_observations.aggregate(pipeline):
        if row.get("_id") in (None, ""):
            continue
        counts[str(row["_id"])] = int(row.get("observation_count") or 0)
    options = [
        {"country": name, "observation_count": count, "has_data": True}
        for name, count in counts.items()
    ]
    known = {item["country"] for item in options}
    for location in regions.LOCATIONS:
        name = str(location.get("country") or "")
        if name and name not in known:
            known.add(name)
            options.append({"country": name, "observation_count": 0, "has_data": False})
    options.sort(key=lambda item: (-item["observation_count"], item["country"]))
    return options


def location_options(*, limit: int = 500,
                     regions_filter: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """Station filter options merged from observations and the region catalogue."""
    counts = station_aggregates(build_filters(regions_filter=regions_filter,
                                              limit=analytics.MAX_QUERY_ROWS), limit=limit)
    index = region_index()
    options: List[Dict[str, Any]] = []
    seen: set = set()
    for code, stat in counts.items():
        seen.add(code.upper())
        options.append({
            "code": code,
            "name": stat.get("location_name") or code,
            "region_code": stat.get("region_code"),
            "country": stat.get("country"),
            "observation_count": int(stat.get("observation_count") or 0),
            "last_observation_date": stat.get("last_date"),
            "has_data": True,
        })
    for region_code, region in index.items():
        for location in region.get("locations") or []:
            code = str(location.get("code") or "")
            if not code or code.upper() in seen:
                continue
            seen.add(code.upper())
            options.append({
                "code": code,
                "name": location.get("name") or code,
                "region_code": region_code,
                "country": location.get("country") or region.get("country"),
                "observation_count": 0,
                "last_observation_date": None,
                "has_data": False,
            })
    options.sort(key=lambda item: (-item["observation_count"], str(item["region_code"] or ""),
                                   item["code"]))
    return options[:limit]


def filter_options(*, regions_filter: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Everything the explorer's filter bar needs, in one call."""
    stored = stored_regions()
    return {
        "metrics": metric_catalogue(with_counts=True),
        "regions": [
            {"code": region.get("code"), "name": region.get("name") or region.get("code"),
             "country": region.get("country"), "climate_zone": region.get("climate_zone"),
             "location_count": len(region.get("locations") or []),
             "source": region.get("source", "reference")}
            for region in stored
        ],
        "region_codes_observed": region_codes_present(),
        "locations": location_options(limit=200, regions_filter=regions_filter),
        "countries": country_options(),
        "datasets": [
            {"id": str(dataset.get("id") or dataset.get("_id")),
             "name": dataset.get("name"), "source_type": dataset.get("source_type"),
             "status": dataset.get("status"), "format": dataset.get("format"),
             "row_count": dataset.get("row_count"),
             "created_at": _iso(dataset.get("created_at"))}
            for dataset in datasets_repository.find({}, sort=[("created_at", -1)], limit=100)
        ],
        "sources": sorted({str(name) for name in climate_observations.distinct("source_name")
                           if name}),
        "frequencies": list(FREQUENCIES),
        "group_by_options": list(GROUP_BY_OPTIONS),
        "quality_options": list(QUALITY_OPTIONS),
        "anomaly_methods": list(ANOMALY_METHODS),
        "sorts": sorted(OBSERVATION_SORTS),
        "observation_window": {"newest_date": newest_observation_date(),
                               "total_observations": _count(climate_observations)},
        "limits": {"max_page_size": MAX_PAGE_SIZE, "max_export_rows": MAX_EXPORT_ROWS,
                   "analytics_row_limit": ANALYTICS_ROW_LIMIT},
        "generated_at": utcnow().isoformat(),
    }


# ------------------------------------------------------- aggregation pipelines
def _match_stage(criteria: Dict[str, Any]) -> Dict[str, Any]:
    return {"$match": strip_limit(criteria)[0]}


def regional_aggregates(criteria: Dict[str, Any],
                        metrics: Sequence[str] = ("temperature_c", "precipitation_mm"),
                        *, limit: int = 200) -> Dict[str, Dict[str, Any]]:
    """Per-region observation count, date range and mean/max/min per metric."""
    scope_pipeline = [
        _match_stage(criteria),
        {"$group": {
            "_id": "$region_code",
            "observation_count": {"$sum": 1},
            "first_date": {"$min": "$observation_date"},
            "last_date": {"$max": "$observation_date"},
            "first_year": {"$min": "$year"},
            "last_year": {"$max": "$year"},
            "locations": {"$addToSet": "$location_code"},
            "sources": {"$addToSet": "$source_name"},
        }},
        {"$sort": {"observation_count": -1}},
        {"$limit": int(limit)},
    ]
    out: Dict[str, Dict[str, Any]] = {}
    for row in climate_observations.aggregate(scope_pipeline):
        code = row.get("_id")
        if code in (None, ""):
            continue
        location_codes = sorted(str(item) for item in (row.get("locations") or []) if item)
        out[str(code)] = {
            "observation_count": int(row.get("observation_count") or 0),
            "first_date": row.get("first_date"),
            "last_date": row.get("last_date"),
            "first_year": row.get("first_year"),
            "last_year": row.get("last_year"),
            "location_count": len(location_codes),
            "location_codes": location_codes,
            "source_count": len([item for item in (row.get("sources") or []) if item]),
            "metrics": {},
        }

    for metric in metrics:
        if metric not in analytics.METRICS or not out:
            continue
        metric_criteria = dict(criteria)
        metric_criteria[metric] = {"$ne": None}
        metric_pipeline = [
            _match_stage(metric_criteria),
            {"$group": {
                "_id": "$region_code",
                "populated": {"$sum": 1},
                "mean": {"$avg": f"${metric}"},
                "max": {"$max": f"${metric}"},
                "min": {"$min": f"${metric}"},
            }},
            {"$sort": {"populated": -1}},
            {"$limit": int(limit)},
        ]
        for row in climate_observations.aggregate(metric_pipeline):
            code = row.get("_id")
            if code in (None, "") or str(code) not in out:
                continue
            out[str(code)]["metrics"][metric] = {
                "label": METRIC_LABELS.get(metric, metric),
                "unit": METRIC_UNITS.get(metric, metric),
                "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
                "populated": int(row.get("populated") or 0),
                "mean": analytics._round(row.get("mean")),
                "max": analytics._round(row.get("max")),
                "min": analytics._round(row.get("min")),
            }
    return out


def station_aggregates(criteria: Dict[str, Any], *, limit: int = 500) -> Dict[str, Dict[str, Any]]:
    """Observation count plus first/newest stored date per monitored location."""
    pipeline = [
        _match_stage(criteria),
        {"$group": {
            "_id": "$location_code",
            "observation_count": {"$sum": 1},
            "first_date": {"$min": "$observation_date"},
            "last_date": {"$max": "$observation_date"},
            "region_code": {"$addToSet": "$region_code"},
            "location_name": {"$addToSet": "$location_name"},
            "countries": {"$addToSet": "$country"},
        }},
        {"$sort": {"last_date": -1}},
        {"$limit": int(limit)},
    ]
    out: Dict[str, Dict[str, Any]] = {}
    for row in climate_observations.aggregate(pipeline):
        code = row.get("_id")
        if code in (None, ""):
            continue
        out[str(code)] = {
            "observation_count": int(row.get("observation_count") or 0),
            "first_date": row.get("first_date"),
            "last_date": row.get("last_date"),
            "region_code": _first_value(row.get("region_code")),
            "location_name": _first_value(row.get("location_name")),
            "country": _first_value(row.get("countries")),
        }
    return out


def monthly_totals(criteria: Dict[str, Any], metrics: Sequence[str], *,
                   limit: int = 400) -> List[Dict[str, Any]]:
    """MongoDB-side per-month aggregates: totals, means and populated counts."""
    group: Dict[str, Any] = {
        "_id": {"year": "$year", "month": "$month",
                "period": {"$substr": ["$observation_date", 0, 7]}},
        "observation_count": {"$sum": 1},
        "regions": {"$addToSet": "$region_code"},
    }
    for metric in metrics:
        if metric not in analytics.METRICS:
            continue
        group[f"{metric}_populated"] = {"$sum": {"$cond": [
            {"$gt": [f"${metric}", -1e30]}, 1, 0]}}
        group[f"{metric}_total"] = {"$sum": f"${metric}"}
        group[f"{metric}_mean"] = {"$avg": f"${metric}"}
        group[f"{metric}_max"] = {"$max": f"${metric}"}
        group[f"{metric}_min"] = {"$min": f"${metric}"}
    pipeline = [
        _match_stage(criteria),
        {"$group": group},
        {"$sort": {"_id.period": 1}},
        {"$limit": int(limit)},
    ]
    out: List[Dict[str, Any]] = []
    for row in climate_observations.aggregate(pipeline):
        identifier = row.get("_id") or {}
        entry: Dict[str, Any] = {
            "period": identifier.get("period"),
            "year": identifier.get("year"),
            "month": identifier.get("month"),
            "observation_count": int(row.get("observation_count") or 0),
            "region_count": len([item for item in (row.get("regions") or []) if item]),
            "metrics": {},
        }
        for metric in metrics:
            if metric not in analytics.METRICS:
                continue
            populated = int(row.get(f"{metric}_populated") or 0)
            total = row.get(f"{metric}_total")
            entry["metrics"][metric] = {
                "populated": populated,
                "total": analytics._round(total) if isinstance(total, (int, float)) else None,
                "mean": analytics._round(row.get(f"{metric}_mean")),
                "max": analytics._round(row.get(f"{metric}_max")),
                "min": analytics._round(row.get(f"{metric}_min")),
                "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
                "unit": METRIC_UNITS.get(metric),
            }
        out.append(entry)
    return out


def yearly_region_normals(criteria: Dict[str, Any], metric: str, *,
                          limit: int = 2000) -> List[Dict[str, Any]]:
    """Per (region, year) means/sums computed inside MongoDB."""
    scoped = dict(criteria)
    scoped[metric] = {"$ne": None}
    pipeline = [
        _match_stage(scoped),
        {"$group": {
            "_id": {"region_code": "$region_code", "year": "$year"},
            "observation_count": {"$sum": 1},
            "value": {"$avg": f"${metric}"},
            "total": {"$sum": f"${metric}"},
            "max": {"$max": f"${metric}"},
            "min": {"$min": f"${metric}"},
        }},
        {"$sort": {"_id.year": 1}},
        {"$limit": int(limit)},
    ]
    rows = climate_observations.aggregate(pipeline)
    out: List[Dict[str, Any]] = []
    for row in rows:
        identifier = row.get("_id") or {}
        if not identifier.get("region_code") or not identifier.get("year"):
            continue
        out.append({
            "region_code": str(identifier["region_code"]),
            "year": int(identifier["year"]),
            "observation_count": int(row.get("observation_count") or 0),
            "mean": analytics._round(row.get("value")),
            "total": analytics._round(row.get("total")),
            "max": analytics._round(row.get("max")),
            "min": analytics._round(row.get("min")),
            "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
            "unit": METRIC_UNITS.get(metric),
        })
    return out


ANNUAL_GROUP_FIELDS = {"none": None, "region": "region_code", "location": "location_code",
                       "country": "country"}


def annual_group_totals(criteria: Dict[str, Any], metric: str, *,
                        group_by: str = "none") -> List[Dict[str, Any]]:
    """One row per (group, year) with the count, total, mean, min and max.

    The historical explorer used to receive every daily document to build these
    numbers; grouping on the server keeps a platform-wide window at one row per
    station-year instead of the whole observation collection.
    """
    field = ANNUAL_GROUP_FIELDS.get(group_by, "region_code")
    scoped = dict(criteria)
    scoped[metric] = {"$ne": None}
    identifier: Dict[str, Any] = {"year": "$year"}
    if field:
        identifier["group"] = f"${field}"
    pipeline = [
        _match_stage(scoped),
        {"$group": {
            "_id": identifier,
            "observation_count": {"$sum": 1},
            "total": {"$sum": f"${metric}"},
            "mean": {"$avg": f"${metric}"},
            "min": {"$min": f"${metric}"},
            "max": {"$max": f"${metric}"},
        }},
    ]
    out: List[Dict[str, Any]] = []
    for row in climate_observations.aggregate(pipeline, allow_disk_use=True):
        key = row.get("_id") or {}
        year = key.get("year")
        if not isinstance(year, int) or year <= 0:
            continue
        value = key.get("group")
        out.append({
            "group": str(value) if value else "GLOBAL",
            "year": year,
            "observation_count": int(row.get("observation_count") or 0),
            "total": row.get("total"),
            "mean": row.get("mean"),
            "min": row.get("min"),
            "max": row.get("max"),
        })
    return out


def status_breakdown(repository: repositories.Repository,
                     criteria: Optional[Dict[str, Any]] = None, *,
                     field: str = "status") -> Dict[str, int]:
    """Group a collection by one stored field with a single aggregation pass."""
    if not field.replace("_", "").isalnum():
        raise ApiError("validation_failed", "Illegal field name.")
    pipeline = [
        {"$match": criteria or {}},
        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 50},
    ]
    out: Dict[str, int] = {}
    try:
        for row in repository.aggregate(pipeline):
            key = str(row.get("_id")) if row.get("_id") not in (None, "") else "unspecified"
            out[key] = int(row.get("count") or 0)
    except (repositories.RepositoryError, ApiError) as exc:
        logger.warning("Status breakdown unavailable for %s: %s", repository.collection_name, exc)
    return out


# ---------------------------------------------------------------- observation list
def observation_list(*, start: Optional[str], end: Optional[str],
                     regions_filter: Optional[Sequence[str]],
                     location_codes: Optional[Sequence[str]],
                     datasets_list: Optional[Sequence[Any]],
                     countries: Optional[Sequence[str]],
                     metric: Optional[str], quality: str, sort: str,
                     page: int, page_size: int,
                     include_provenance: bool = False) -> Dict[str, Any]:
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, datasets_list=datasets_list,
                             countries=countries, metric=metric, quality=quality)
    projection = dict(LIST_PROJECTION_EXCLUSIONS)
    if not include_provenance:
        projection["source_units"] = 0
    # ``_limit`` is a private kernel key, never a Mongo field: strip it first.
    row_criteria, _ = strip_limit(criteria)
    envelope = climate_observations.paginate(row_criteria, page=page, page_size=page_size,
                                            sort=OBSERVATION_SORTS.get(sort),
                                            projection=projection)
    envelope["items"] = [_present_observation(item) for item in envelope["items"]]
    if not envelope["items"] and envelope["total"] == 0:
        envelope["empty_state"] = {
            "available": False,
            "reason": "No observation documents match these filters. The collection may still be "
                      "empty - ingest a dataset to populate it.",
            "matched": 0,
        }
    envelope["sort"] = sort
    envelope["applied_filters"] = {
        "start": start, "end": end, "regions": list(regions_filter or []),
        "location_codes": [str(item) for item in (location_codes or [])],
        "datasets": [str(item) for item in (datasets_list or [])],
        "countries": list(countries or []), "metric": metric, "quality": quality,
    }
    envelope["available_sorts"] = sorted(OBSERVATION_SORTS)
    return envelope


def _present_observation(document: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(document)
    if out.get("dataset_id") is not None:
        out["dataset_id"] = str(out["dataset_id"])
    if out.get("ingested_at") is not None:
        out["ingested_at"] = _iso(out["ingested_at"])
    if out.get("observed_at") is not None:
        out["observed_at"] = _iso(out["observed_at"])
    flags = out.get("quality_flags")
    out["quality_flags"] = [str(flag) for flag in (flags or [])]
    out["is_flagged"] = bool(out["quality_flags"])
    return out


def _iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


# --------------------------------------------------------------------- trends
AGGREGATED_FREQUENCIES = ("daily", "weekly", "monthly", "annual")
TREND_GROUP_FIELDS = {"region": "region_code", "location": "location_code", "country": "country"}
TREND_PERIOD_FIELDS = {"daily": ("d",), "weekly": ("wy", "ww"), "monthly": ("y", "m"),
                       "annual": ("y",)}


def _coverage_zero() -> Dict[str, Any]:
    """The empty coverage report, identical to ``analytics.coverage_report([])``."""
    return {"observation_count": 0, "completeness": {}, "flagged": {}, "date_range": None,
            "locations": 0, "regions": 0, "datasets": 0, "sources": []}


def _coverage_accumulators(*, group_field: Optional[str]) -> Dict[str, Any]:
    """One ``$group`` body that measures everything ``coverage_report`` reports."""
    stage: Dict[str, Any] = {
        "_id": None if group_field is None else {"scope": f"${group_field}"},
        "observation_count": {"$sum": 1},
        "first_date": {"$min": "$observation_date"},
        "last_date": {"$max": "$observation_date"},
        "flagged_rows": {"$sum": {"$cond": [
            {"$gt": [{"$size": {"$ifNull": ["$quality_flags", []]}}, 0]}, 1, 0]}},
        "locations": {"$addToSet": "$location_code"},
        "regions": {"$addToSet": "$region_code"},
        "datasets": {"$addToSet": "$dataset_id"},
        "sources": {"$addToSet": "$source_name"},
    }
    for name in analytics.METRICS:
        stage[f"populated_{name}"] = {"$sum": {"$cond": [
            {"$ne": [f"${name}", None]}, 1, 0]}}
    return stage


def _coverage_document(row: Dict[str, Any], flags: Dict[str, int]) -> Dict[str, Any]:
    count = int(row.get("observation_count") or 0)
    if not count:
        return _coverage_zero()
    completeness: Dict[str, Any] = {}
    for name in analytics.METRICS:
        populated = int(row.get(f"populated_{name}") or 0)
        completeness[name] = {
            "populated": populated,
            "missing": count - populated,
            "pct": analytics._round(populated / count * 100, 1),
        }
    flagged_rows = int(row.get("flagged_rows") or 0)
    dates = [value for value in (row.get("first_date"), row.get("last_date")) if value]
    return {
        "observation_count": count,
        "completeness": completeness,
        "flagged": flags,
        "flagged_row_count": flagged_rows,
        "flagged_row_pct": analytics._round(flagged_rows / count * 100, 1),
        "date_range": {"start": min(dates), "end": max(dates)} if dates else None,
        "locations": len(row.get("locations") or []),
        "regions": len(row.get("regions") or []),
        "datasets": len(row.get("datasets") or []),
        "sources": sorted(str(value) for value in (row.get("sources") or []) if value),
    }


def coverage_scopes(criteria: Dict[str, Any], *, group_field: Optional[str] = None
                    ) -> List[Tuple[Optional[str], Dict[str, Any]]]:
    """Coverage measured inside MongoDB, optionally split by one field.

    The kernel's :func:`analytics.coverage_report` needs every document in the window.
    With tens of thousands of observations on the Atlas shared tier that read is the
    slowest thing the platform does, so the same numbers are aggregated on the server
    and only the summary is transferred.
    """
    flag_id: Any = ({"scope": f"${group_field}", "flag": "$quality_flags"}
                    if group_field else {"flag": "$quality_flags"})
    pipeline = [
        _match_stage(criteria),
        {"$facet": {
            "totals": [{"$group": _coverage_accumulators(group_field=group_field)}],
            "flags": [{"$unwind": "$quality_flags"},
                      {"$group": {"_id": flag_id, "count": {"$sum": 1}}}],
        }},
    ]
    face = climate_observations.aggregate(pipeline)
    if not face:
        return []
    flag_counts: Dict[str, Dict[str, int]] = {}
    for entry in face[0].get("flags") or []:
        identifier = entry.get("_id") or {}
        bucket = str(identifier.get("scope")) if group_field else ""
        flag_counts.setdefault(bucket, {})[str(identifier.get("flag"))] = int(entry.get("count") or 0)
    out: List[Tuple[Optional[str], Dict[str, Any]]] = []
    for row in face[0].get("totals") or []:
        scope = (row.get("_id") or {}).get("scope") if group_field else None
        flags = flag_counts.get(str(scope) if group_field else "", {})
        out.append((scope, _coverage_document(row, flags)))
    return out


def coverage_from_database(criteria: Dict[str, Any]) -> Dict[str, Any]:
    scopes = coverage_scopes(criteria)
    return scopes[0][1] if scopes else _coverage_zero()


def _trend_period_key(frequency: str) -> Dict[str, Any]:
    if frequency in ("daily", "weekly"):
        return {"d": "$observation_date"}
    if frequency == "monthly":
        return {"y": "$year", "m": "$month"}
    return {"y": "$year"}


def _trend_period_label(frequency: str, key: Dict[str, Any]) -> str:
    if frequency == "daily":
        return str(key.get("d"))
    if frequency == "weekly":
        return f"{int(key['wy'])}-W{int(key['ww']):02d}"
    if frequency == "monthly":
        return f"{int(key['y']):04d}-{int(key['m']):02d}"
    return str(int(key["y"]))


def _trend_sort_key(row: Dict[str, Any], *, group_field: Optional[str],
                    period_fields: Sequence[str]) -> Tuple[Any, ...]:
    scope = (str((row.get("_id") or {}).get("k")),) if group_field else ()
    key = row.get("_id") or {}
    parts: List[Any] = []
    for name in period_fields:
        value = key.get(name)
        if value is None:
            value = "" if name == "d" else 0
        parts.append(value)
    return (*scope, *parts)


def _roll_up_to_iso_weeks(buckets: List[Dict[str, Any]], *,
                          group_field: Optional[str]) -> List[Dict[str, Any]]:
    """Combine daily buckets into the weeks the kernel's ``W-SUN`` grouper builds.

    ``W-SUN`` bins end on Sunday, which is exactly a Monday-to-Sunday ISO week, so the
    week a day belongs to can be read off its own date and the daily sums added up.
    """
    merged: Dict[Tuple[Any, int, int], Dict[str, Any]] = {}
    for row in buckets:
        raw = str((row.get("_id") or {}).get("d"))
        try:
            iso = date.fromisoformat(raw).isocalendar()
        except ValueError:
            continue
        scope = (row.get("_id") or {}).get("k") if group_field else None
        bucket = merged.get((scope, iso[0], iso[1]))
        if bucket is None:
            identifier: Dict[str, Any] = {"wy": iso[0], "ww": iso[1]}
            if group_field:
                identifier = {"k": scope, **identifier}
            bucket = merged[(scope, iso[0], iso[1])] = {
                "_id": identifier, "observation_count": 0, "value_sum": 0,
                "value_sumsq": 0, "value_count": 0, "min": None, "max": None,
            }
        for name in ("observation_count", "value_sum", "value_sumsq", "value_count"):
            bucket[name] += row.get(name) or 0
        for name in ("min", "max"):
            value = row.get(name)
            if value is None:
                continue
            current = bucket[name]
            bucket[name] = value if current is None else (min(current, value) if name == "min"
                                                          else max(current, value))
    return list(merged.values())


def trend_series_from_database(criteria: Dict[str, Any], metric: str, *,
                               frequency: str, group_by: Optional[str],
                               require_baseline: bool = True) -> Dict[str, Any]:
    """:func:`analytics.trend_series` computed by MongoDB rather than in Python.

    Raises :class:`analytics.InsufficientData` with the same message the kernel raises,
    so the caller's availability contract is unchanged. ``None`` is returned when the
    frequency or grouping is outside what this path models and the caller must fall
    back to the row-based kernel.
    """
    if frequency not in AGGREGATED_FREQUENCIES:
        return None  # type: ignore[return-value]
    if group_by not in (None, *TREND_GROUP_FIELDS):
        return None  # type: ignore[return-value]
    group_field = TREND_GROUP_FIELDS.get(str(group_by)) if group_by else None
    stage_key = dict(_trend_period_key("daily" if frequency == "weekly" else frequency))
    if group_field:
        stage_key = {"k": f"${group_field}", **stage_key}
    pipeline = [_match_stage(criteria)]
    if group_field:
        pipeline.append({"$match": {group_field: {"$ne": None}}})
    has_value = {"$ne": [f"${metric}", None]}
    pipeline += [
        {"$group": {
            "_id": stage_key,
            "observation_count": {"$sum": 1},
            "value_sum": {"$sum": {"$cond": [has_value, f"${metric}", 0]}},
            "value_sumsq": {"$sum": {"$cond": [has_value,
                                               {"$multiply": [f"${metric}", f"${metric}"]}, 0]}},
            "value_count": {"$sum": {"$cond": [has_value, 1, 0]}},
            "min": {"$min": f"${metric}"},
            "max": {"$max": f"${metric}"},
        }},
    ]
    buckets = climate_observations.aggregate(pipeline, allow_disk_use=True)
    # The buckets are ordered in Python: a ``$sort`` over tens of thousands of period
    # keys exceeds the server's in-memory sort limit on the shared Atlas tier. Weekly
    # trends are ordered *before* being folded, so the accumulated sums always run in
    # the same sequence and a period cannot flip its last decimal between requests.
    buckets.sort(key=lambda row: _trend_sort_key(
        row, group_field=group_field,
        period_fields=TREND_PERIOD_FIELDS["daily" if frequency == "weekly" else frequency]))
    if frequency == "weekly":
        buckets = _roll_up_to_iso_weeks(buckets, group_field=group_field)
        buckets.sort(key=lambda row: _trend_sort_key(row, group_field=group_field,
                                                    period_fields=TREND_PERIOD_FIELDS["weekly"]))
    coverage = coverage_from_database(criteria)
    if not buckets:
        return {
            "metric": metric, "label": METRIC_LABELS[metric], "unit": METRIC_UNITS[metric],
            "frequency": frequency, "series": [], "point_count": 0, "coverage": coverage,
            "note": "No observations are stored for this metric yet.",
        }
    aggregation = "sum" if metric in analytics.ADDITIVE_METRICS else "mean"
    series: List[Dict[str, Any]] = []
    for row in buckets:
        key = row.get("_id") or {}
        count = int(row.get("observation_count") or 0)
        populated = int(row.get("value_count") or 0)
        total = float(row.get("value_sum") or 0.0)
        value = total if aggregation == "sum" else (total / populated if populated else None)
        std: Optional[float] = None
        if populated > 1:
            # Sample deviation, computed from the two moments because the fallback
            # document store has no $stdDevSamp. Variance is clamped at zero so a
            # floating-point tie cannot turn into a square root of a negative number.
            variance = max((float(row.get("value_sumsq") or 0.0)
                            - total * total / populated) / (populated - 1), 0.0)
            std = math.sqrt(variance)
        entry = {
            "period": _trend_period_label(frequency, key),
            "value": analytics._round(value),
            "observation_count": count,
            "min": analytics._round(row.get("min")),
            "max": analytics._round(row.get("max")),
            "std": analytics._round(std),
            "aggregation": aggregation,
        }
        if group_field:
            entry[TREND_GROUP_FIELDS[str(group_by)]] = str(key.get("k"))
        series.append(entry)
    payload: Dict[str, Any] = {
        "metric": metric,
        "label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "frequency": frequency,
        "group_by": group_by,
        "series": series,
        "point_count": len(series),
        "coverage": coverage,
        "first_period": series[0]["period"] if series else None,
        "last_period": series[-1]["period"] if series else None,
    }
    if require_baseline and len(series) < analytics.MIN_POINTS_FOR_BASELINE:
        raise InsufficientData(
            f"Only {len(series)} periods are available; at least "
            f"{analytics.MIN_POINTS_FOR_BASELINE} are needed before a trend or anomaly "
            "baseline is scientifically meaningful.",
            available=len(series), required=analytics.MIN_POINTS_FOR_BASELINE,
        )
    return payload


def trend_payload(*, start: Optional[str], end: Optional[str],
                  regions_filter: Optional[Sequence[str]],
                  location_codes: Optional[Sequence[str]],
                  metric: str, frequency: str, group_by: str,
                  countries: Optional[Sequence[str]] = None,
                  require_baseline: bool = True,
                  row_limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    """``analytics.trend_series`` wrapped in an honest availability contract."""
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, countries=countries,
                             metric=metric, limit=row_limit)
    empty_fields = {
        "metric": metric, "label": METRIC_LABELS.get(metric), "unit": METRIC_UNITS.get(metric),
        "frequency": frequency, "group_by": group_by, "series": [], "point_count": 0,
    }
    grouped = group_by if group_by != "none" else None
    rows: Optional[List[Dict[str, Any]]] = None
    try:
        payload = trend_series_from_database(criteria, metric, frequency=frequency,
                                            group_by=grouped, require_baseline=require_baseline)
        if payload is None:
            rows = fetch_rows(criteria, projection=metric_projection(analytics.METRICS),
                              limit=row_limit)
            payload = analytics.trend_series(rows, metric, frequency=frequency,
                                            group_by=grouped, require_baseline=require_baseline)
    except InsufficientData as exc:
        return {
            **empty_fields,
            "available": False,
            "reason": exc.message,
            "available_points": int(exc.available or 0),
            "required_points": int(exc.required or analytics.MIN_POINTS_FOR_BASELINE),
            "observations_evaluated": len(rows) if rows is not None else matching_row_count(criteria),
        }
    point_count = int(payload.get("point_count") or 0)
    evaluated = (len(rows) if rows is not None
                 else int((payload.get("coverage") or {}).get("observation_count") or 0))
    if point_count == 0:
        return {
            **empty_fields,
            "available": False,
            "reason": payload.get("note") or "No periods could be built from stored observations.",
            "available_points": 0,
            "required_points": analytics.MIN_POINTS_FOR_BASELINE,
            "coverage": payload.get("coverage"),
            "observations_evaluated": evaluated,
        }
    payload["available"] = True
    payload["observations_evaluated"] = evaluated
    payload["matched_observations"] = matching_row_count(criteria)
    payload["scope"] = {"start": start, "end": end, "regions": list(regions_filter or []),
                        "location_codes": list(location_codes or [])}
    if point_count > SERIES_PAGE_LIMIT:
        payload["series"] = payload["series"][:SERIES_PAGE_LIMIT]
        payload["truncated"] = True
    return payload


def anomaly_result(*, start: Optional[str], end: Optional[str], metric: str, method: str,
                   threshold_sigma: float, absolute_threshold: Optional[float], direction: str,
                   region_code: Optional[str], location_code: Optional[str],
                   row_limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    criteria = build_filters(start=start, end=end, regions_filter=[region_code] if region_code
                             else None, location_codes=[location_code] if location_code else None,
                             metric=metric, limit=row_limit)
    # Only the multivariate detector needs the other columns; reading all ten for a
    # single-metric baseline doubles the transfer time for nothing.
    projection = metric_projection(analytics.METRICS if method == "isolation_forest" else [metric])
    rows = fetch_rows(criteria, projection=projection, limit=row_limit)
    try:
        payload = analytics.detect_anomalies(
            rows, metric, method=method, threshold_sigma=threshold_sigma,
            absolute_threshold=absolute_threshold, direction=direction,
            region_code=region_code, location_code=location_code,
        )
    except InsufficientData as exc:
        return {
            "available": False, "metric": metric, "method": method, "anomalies": [],
            "anomaly_count": 0, "reason": exc.message,
            "available_points": int(exc.available or 0),
            "required_points": int(exc.required or analytics.MIN_POINTS_FOR_BASELINE),
            "observations_evaluated": len(rows),
        }
    except Exception as exc:  # noqa: BLE001 - detection never becomes a blank 500
        logger.exception("Anomaly detection failed (method=%s, metric=%s)", method, metric)
        return {
            "available": False, "metric": metric, "label": METRIC_LABELS.get(metric),
            "unit": METRIC_UNITS.get(metric), "method": method, "direction": direction,
            "anomalies": [], "anomaly_count": 0, "severity_counts": {},
            "observations_evaluated": len(rows),
            "reason": (f"The '{method}' detector could not complete on the stored record "
                       f"({type(exc).__name__}). Try method=statistical_baseline or "
                       f"method=threshold, which only need stored values."),
            "scope": {"start": start, "end": end, "region": region_code,
                      "location": location_code, "threshold_sigma": threshold_sigma,
                      "absolute_threshold": absolute_threshold},
        }
    payload["observations_evaluated"] = payload.get("evaluated_observations", len(rows))
    payload["available"] = bool(payload.get("anomalies")) or bool(rows)
    if payload.get("note"):
        payload["reason"] = payload["note"]
    if not payload["available"]:
        payload.setdefault("reason", "No observations are stored in this scope.")
    payload["scope"] = {"start": start, "end": end, "region": region_code,
                        "location": location_code, "method": method,
                        "threshold_sigma": threshold_sigma,
                        "absolute_threshold": absolute_threshold, "direction": direction}
    return payload


def persist_anomaly_scan(payload: Dict[str, Any], *, start: Optional[str], end: Optional[str],
                         metric: str, method: str, direction: str,
                         threshold_sigma: float, absolute_threshold: Optional[float],
                         region_code: Optional[str], location_code: Optional[str],
                         persist_limit: int, dry_run: bool,
                         actor_user_id: Optional[str], actor_role: Optional[str],
                         workflow: str = ALERT_SCAN_WORKFLOW,
                         processing_mode: str = "mongodb") -> Dict[str, Any]:
    """Store scan results as alerts-compatible candidates plus one summary record."""
    anomalies = list(payload.get("anomalies") or [])
    rows_used = int(payload.get("evaluated_observations") or 0)
    scope = {
        "workflow": workflow,
        "kind": "anomaly_scan",
        "region": region_code or "all",
        "location": location_code or "all",
        "window_start": payload.get("date_range", {}).get("start"),
        "window_end": payload.get("date_range", {}).get("end"),
        "method": method,
        "direction": direction,
        "threshold_sigma": threshold_sigma,
        "absolute_threshold": absolute_threshold,
        "requested_start": start,
        "requested_end": end,
    }
    summary_payload = {
        "metric": payload.get("metric"),
        "metric_label": payload.get("metric_label"),
        "unit": payload.get("unit"),
        "method": payload.get("method"),
        "direction": payload.get("direction"),
        "evaluated_observations": rows_used,
        "anomaly_count": payload.get("anomaly_count", len(anomalies)),
        "severity_counts": payload.get("severity_counts") or {},
        "date_range": payload.get("date_range"),
        "baseline": payload.get("baseline"),
        "truncated": payload.get("truncated", False),
        "sample": anomalies[:50],
        "persisted_limit": persist_limit,
    }
    source_datasets = sorted({str(item.get("dataset_id")) for item in anomalies[:200]
                              if item.get("dataset_id")})

    if dry_run:
        return {
            "dry_run": True,
            "anomalies_detected": len(anomalies),
            "alerts_persisted": 0,
            "alerts_already_present": 0,
            "alerts_over_limit": max(0, len(anomalies) - persist_limit),
            "would_persist": min(len(anomalies), persist_limit),
            "summary_written": False,
            "severity_counts": payload.get("severity_counts") or {},
            "workflow": workflow,
        }

    summary_key = analytics.store_summary(
        metric=str(payload.get("metric") or "unknown"),
        scope=scope,
        payload=summary_payload,
        source_datasets=source_datasets,
        workflow=workflow,
        processing_mode=processing_mode,
    )

    persisted = 0
    skipped = 0
    over_limit = 0
    stored_ids: List[str] = []
    notify = _anomaly_notifiers()
    recipients = notify["recipients"](
        {"recipients": ["role:administrator"] + ([actor_user_id] if actor_user_id else [])}
    ) if notify else []
    for anomaly in anomalies:
        if persisted >= persist_limit:
            over_limit += 1
            continue
        candidate = anomaly_alert_document(anomaly, scope=scope, workflow=workflow,
                                           actor_user_id=actor_user_id, actor_role=actor_role)
        if alerts.find_one({"dedupe_key": candidate["dedupe_key"]}, {"_id": 1}) is not None:
            skipped += 1
            continue
        alert_id = alerts.insert_one(candidate)
        stored_ids.append(alert_id)
        persisted += 1
        if notify and recipients:
            # An anomaly alert that reaches nobody is a silent dashboard entry, not an
            # alert. The rule engine already fans out; this path must match it.
            ids = notify["queue"](
                recipients,
                type="alert",
                title=f"{candidate['severity'].upper()} anomaly: {candidate['title']}",
                body=str(candidate.get("description") or candidate["title"])[:4000],
                dedupe_key=f"alert:{alert_id}",
                related_resource={"type": "alert", "id": alert_id},
                severity=candidate["severity"],
            )
            if ids:
                alerts.update_by_id(alert_id, {"notification_ids": ids})

    return {
        "dry_run": False,
        "summary_key": summary_key,
        "summary_written": True,
        "anomalies_detected": len(anomalies),
        "alerts_persisted": persisted,
        "alerts_already_present": skipped,
        "alerts_over_limit": over_limit,
        "persist_limit": persist_limit,
        "alert_ids": stored_ids[:50],
        "severity_counts": payload.get("severity_counts") or {},
        "evaluated_observations": rows_used,
        "workflow": workflow,
        "generated_at": utcnow().isoformat(),
    }


ALERT_SCAN_WORKFLOW = "climate.anomaly_scan"
ALERT_SCAN_RULE_PREFIX = "climate.anomaly"

#: The anomaly detector grades deviations as low/medium/high, while every other alert in
#: the platform - and the alerts API summary - speaks in info/warning/critical. One
#: vocabulary, mapped once at the point the alert is written.
ANOMALY_SEVERITY_MAP = {"low": "info", "medium": "warning", "high": "critical",
                        "critical": "critical", "info": "info", "warning": "warning"}


def _anomaly_notifiers():
    """Resolve the alert notification helpers, or ``None`` if they cannot load.

    ``apps.alerts`` reads climate analytics for its own rule evaluation, so importing it
    at module scope here would close a cycle. The lookup is deferred, and a failure to
    load leaves the scan itself intact - the alerts are still real, only quieter.
    """
    try:
        from apps.alerts import notifications, rules

        return {"recipients": rules.resolve_recipients,
                "queue": notifications.queue_for_recipients}
    except Exception as exc:  # noqa: BLE001 - scanning must not depend on the alert app
        logger.warning("anomaly alert notifications unavailable: %s", type(exc).__name__)
        return None


def anomaly_alert_document(anomaly: Dict[str, Any], *, scope: Dict[str, Any], workflow: str,
                           actor_user_id: Optional[str],
                           actor_role: Optional[str]) -> Dict[str, Any]:
    """An ``alerts``-compatible document for a detected anomaly."""
    observation_date = str(anomaly.get("observation_date") or "unknown")
    location_code = str(anomaly.get("location_code") or "GLOBAL")
    metric = str(anomaly.get("metric") or "unknown")
    deviation = float(anomaly.get("deviation_score") or 0.0)
    now = utcnow()
    raw_severity = str(anomaly.get("severity") or "medium")
    title = (f"{anomaly.get('metric_label') or metric} "
             f"{anomaly.get('value')}{anomaly.get('unit') or ''} at "
             f"{anomaly.get('location_name') or location_code} on {observation_date}")
    return {
        # ---- fields the alerts API filters and indexes on
        "rule_key": f"{ALERT_SCAN_RULE_PREFIX}.{metric}.{anomaly.get('method', 'baseline')}",
        "triggered_at": now,
        "status": "open",
        "severity": ANOMALY_SEVERITY_MAP.get(raw_severity, "warning"),
        "anomaly_severity": raw_severity,
        # ---- descriptive payload
        "kind": "anomaly",
        "source": "climate_anomaly_scan",
        "title": title,
        "message": title,
        "description": anomaly.get("explanation"),
        "dedupe_key": f"climate-anomaly|{metric}|{location_code}|{observation_date}"
                      f"|{round(deviation, 3)}",
        "metric": metric,
        "metric_label": anomaly.get("metric_label"),
        "unit": anomaly.get("unit"),
        "value": anomaly.get("value"),
        "deviation_score": anomaly.get("deviation_score"),
        "direction": anomaly.get("direction"),
        "method": anomaly.get("method"),
        "baseline": anomaly.get("baseline"),
        "region_code": anomaly.get("region_code"),
        "location_code": anomaly.get("location_code"),
        "location_name": anomaly.get("location_name"),
        "latitude": anomaly.get("latitude"),
        "longitude": anomaly.get("longitude"),
        "observation_date": anomaly.get("observation_date"),
        "dataset_id": anomaly.get("dataset_id"),
        "source_name": anomaly.get("source_name"),
        "explanation": anomaly.get("explanation"),
        "caveat": anomaly.get("caveat") or (
            "A statistical deviation against the stored reference period - not, by itself, "
            "evidence that an event is scientifically exceptional."),
        "scan": {"workflow": workflow, "scope": scope, "requested_by": actor_user_id,
                 "requested_role": actor_role, "processing_mode": "mongodb"},
        "acknowledged_by": None,
        "acknowledged_at": None,
        "resolved_by": None,
        "resolved_at": None,
        "notification_ids": [],
        "created_at": now,
        "updated_at": now,
        "schema_version": 1,
    }


def persisted_anomalies(*, limit: int = 20, region_code: Optional[str] = None,
                        metric: Optional[str] = None) -> List[Dict[str, Any]]:
    criteria: Dict[str, Any] = {"kind": "anomaly"}
    if region_code:
        criteria["region_code"] = region_code
    if metric:
        criteria["metric"] = metric
    return [repositories.serialize(document) or {}
            for document in alerts.find(criteria, sort=[("triggered_at", -1)], limit=limit)]


def on_the_fly_anomalies(*, window_start: Optional[str], window_end: Optional[str],
                         metric: str = "temperature_c", row_limit: int = DASHBOARD_ROW_LIMIT,
                         limit: int = 20) -> Dict[str, Any]:
    """Detection computed straight from observations when nothing is persisted yet."""
    try:
        payload = anomaly_result(start=window_start, end=window_end, metric=metric,
                                 method="statistical_baseline", threshold_sigma=2.5,
                                 absolute_threshold=None, direction="both",
                                 region_code=None, location_code=None, row_limit=row_limit)
    except Exception as exc:  # noqa: BLE001 - the dashboard must degrade, not break
        logger.warning("On-the-fly anomaly detection unavailable: %s", exc)
        return {"source": "computed", "available": False,
                "reason": "Anomaly detection could not run against the stored data.",
                "anomalies": []}
    anomalies = list(payload.get("anomalies") or [])
    if not anomalies:
        return {
            "source": "computed",
            "available": False,
            "reason": payload.get("reason") or (
                f"Fewer than {analytics.MIN_POINTS_FOR_BASELINE} comparable observations per "
                "region-month exist, so no deviation can be measured yet."),
            "metric": metric,
            "method": payload.get("method"),
            "evaluated_observations": payload.get("observations_evaluated", 0),
            "anomalies": [],
        }
    return {
        "source": "computed",
        "available": True,
        "metric": metric,
        "method": payload.get("method"),
        "baseline": payload.get("baseline"),
        "evaluated_observations": payload.get("observations_evaluated"),
        "anomaly_count": len(anomalies),
        "severity_counts": payload.get("severity_counts"),
        "caveat": payload.get("caveat"),
        "anomalies": anomalies[:limit],
    }


# -------------------------------------------------------------------- map data
LATEST_POINT_FIELDS = ("observation_date", "location_name", "region_code", "country",
                       "latitude", "longitude", "source_name", "quality_flags")


def latest_points_from_database(criteria: Dict[str, Any], metrics: Sequence[str]
                                ) -> List[Dict[str, Any]]:
    """The newest stored observation per monitored point, resolved inside MongoDB.

    ``$first`` reads each field in ``$sort`` order, so a station's values always come
    from its own most recent document. The ``$project`` comes before the sort because
    sorting whole observation documents crosses the server's in-memory sort limit.
    """
    projection: Dict[str, int] = {"_id": 0, "location_code": 1}
    projection.update({name: 1 for name in LATEST_POINT_FIELDS})
    for metric in metrics:
        if metric in analytics.METRICS:
            projection[metric] = 1
    carried = [name for name in projection if name != "_id"]
    pipeline = [
        _match_stage(criteria),
        {"$project": projection},
        {"$sort": {"observation_date": -1, "location_code": 1}},
        {"$group": {"_id": "$location_code",
                    **{name: {"$first": f"${name}"} for name in carried}}},
    ]
    return climate_observations.aggregate(pipeline, allow_disk_use=True)


def date_window_from_database(criteria: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Oldest and newest stored date in scope, without reading the rows."""
    bounds = climate_observations.aggregate([
        _match_stage(criteria),
        {"$group": {"_id": None, "first": {"$min": "$observation_date"},
                    "last": {"$max": "$observation_date"}}},
    ])
    row = bounds[0] if bounds else {}
    return {"start": row.get("first"), "end": row.get("last")}


def map_data(*, metrics: Sequence[str], start: Optional[str], end: Optional[str],
             regions_filter: Optional[Sequence[str]], location_codes: Optional[Sequence[str]],
             countries: Optional[Sequence[str]], datasets_list: Optional[Sequence[Any]],
             quality: str = "any", max_points: int = 5000) -> Dict[str, Any]:
    selected = _dedupe([metric for metric in (metrics or DEFAULT_MAP_METRICS)
                        if metric in analytics.METRICS]) or list(DEFAULT_MAP_METRICS)
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, datasets_list=datasets_list,
                             countries=countries, quality=quality,
                             limit=min(max(int(max_points), 1) * 60, ANALYTICS_ROW_LIMIT))
    latest = latest_points_from_database(criteria, selected)
    counts = station_aggregates(criteria, limit=max(len(latest) + 1, 500))

    points: List[Dict[str, Any]] = []
    for entry in latest:
        code = str(entry.get("_id") or entry.get("location_code") or "")
        latitude = _coerce_float(entry.get("latitude"))
        longitude = _coerce_float(entry.get("longitude"))
        stat = counts.get(code, {})
        measurements: Dict[str, Dict[str, Any]] = {}
        for metric in selected:
            value = entry.get(metric)
            measurements[metric] = {
                "value": analytics._round(_coerce_float(value)) if value is not None else None,
                "unit": METRIC_UNITS[metric],
            }
        points.append({
            "location_code": code,
            "location_name": entry.get("location_name"),
            "region_code": entry.get("region_code"),
            "country": entry.get("country"),
            "latitude": latitude,
            "longitude": longitude,
            "last_observation_date": entry.get("observation_date"),
            "measurements": measurements,
            "observation_count": int(stat.get("observation_count") or 0),
            "cluster_key": cluster_key(latitude, longitude),
            "source_name": entry.get("source_name"),
            "quality_flags": [str(flag) for flag in (entry.get("quality_flags") or [])][:20],
        })
    points.sort(key=lambda item: (str(item["last_observation_date"] or ""),
                                  item["location_code"]), reverse=True)
    points = points[:max(1, int(max_points))]

    clusters = cluster_summaries(points) if len(points) > CLUSTER_POINT_THRESHOLD else []
    matched = matching_row_count(criteria)
    return {
        "metrics": selected,
        "labels": {metric: METRIC_LABELS[metric] for metric in selected},
        "units": {metric: METRIC_UNITS[metric] for metric in selected},
        "points": points,
        "point_count": len(points),
        "clusters": clusters,
        "clustered": bool(clusters),
        "cluster_threshold": CLUSTER_POINT_THRESHOLD,
        "cluster_key_precision": CLUSTER_COORD_PRECISION,
        "bounds": compute_bounds(points),
        "scope": {"start": start, "end": end, "regions": list(regions_filter or []),
                  "location_codes": list(location_codes or []), "quality": quality},
        "window": date_window_from_database(criteria),
        "matched_observations": matched,
        "generated_at": utcnow().isoformat(),
        "data_note": (
            f"{len(points)} monitored point(s) built from {matched} stored observation "
            "document(s). Each value is the most recent measurement recorded for that station - "
            "not an interpolated grid cell and not a model output."
            if points else
            "No observations are stored for this filter set, so the map has no points to show. "
            "Ingest a dataset to populate it; nothing here is estimated or filled in."
        ),
    }


def cluster_key(latitude: Optional[float], longitude: Optional[float]) -> Optional[str]:
    if latitude is None or longitude is None:
        return None
    return (f"{round(latitude, CLUSTER_COORD_PRECISION):.{CLUSTER_COORD_PRECISION}f},"
            f"{round(longitude, CLUSTER_COORD_PRECISION):.{CLUSTER_COORD_PRECISION}f}")


def cluster_summaries(points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for point in points:
        key = point.get("cluster_key")
        if key:
            buckets.setdefault(key, []).append(point)
    out: List[Dict[str, Any]] = []
    for key, members in buckets.items():
        latitudes = [item["latitude"] for item in members if item["latitude"] is not None]
        longitudes = [item["longitude"] for item in members if item["longitude"] is not None]
        measurements: Dict[str, Dict[str, Any]] = {}
        for metric in analytics.METRICS:
            values = [item["measurements"].get(metric, {}).get("value") for item in members
                      if item["measurements"].get(metric, {}).get("value") is not None]
            if values:
                measurements[metric] = {
                    "mean": analytics._round(statistics.fmean(values)),
                    "min": analytics._round(min(values)),
                    "max": analytics._round(max(values)),
                    "unit": METRIC_UNITS[metric],
                    "stations": len(values),
                }
        out.append({
            "cluster_key": key,
            "point_count": len(members),
            "centroid": [analytics._round(statistics.fmean(latitudes), 4) if latitudes else None,
                         analytics._round(statistics.fmean(longitudes), 4) if longitudes else None],
            "regions": sorted({str(item["region_code"]) for item in members if item["region_code"]}),
            "last_observation_date": max((str(item["last_observation_date"] or "")
                                          for item in members), default="") or None,
            "total_observations": sum(item["observation_count"] for item in members),
            "measurements": measurements,
        })
    out.sort(key=lambda item: item["point_count"], reverse=True)
    return out[:500]


def compute_bounds(points: List[Dict[str, Any]]) -> Optional[List[float]]:
    latitudes = [item["latitude"] for item in points if item["latitude"] is not None]
    longitudes = [item["longitude"] for item in points if item["longitude"] is not None]
    if not latitudes or not longitudes:
        return None
    return [min(latitudes), min(longitudes), max(latitudes), max(longitudes)]


def window_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    dates = [str(row.get("observation_date")) for row in rows if row.get("observation_date")]
    if not dates:
        return {"start": None, "end": None}
    return {"start": min(dates), "end": max(dates)}


def newest_observation_date() -> Optional[str]:
    rows = climate_observations.find({"observation_date": {"$ne": None}},
                                     {"observation_date": 1, "_id": 0},
                                     sort=[("observation_date", -1)], limit=1)
    return rows[0].get("observation_date") if rows else None


# -------------------------------------------------------------------- quality
def source_freshness(*, criteria: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Newest stored observation date per source plus the resulting lag in days."""
    row_criteria, _ = strip_limit(criteria or {})
    sources = _dedupe([str(name) for name in climate_observations.distinct("source_name",
                                                                           row_criteria) if name])
    today = utcnow().date()
    out: List[Dict[str, Any]] = []
    for name in sources[:200]:
        scoped = dict(row_criteria)
        scoped["source_name"] = name
        newest = climate_observations.find(scoped, {"observation_date": 1, "_id": 0},
                                          sort=[("observation_date", -1)], limit=1)
        newest_date = newest[0].get("observation_date") if newest else None
        lag = lag_days(newest_date, today=today)
        out.append({
            "source_name": name,
            "newest_date": newest_date,
            "lag_days": lag,
            "status": freshness_status(lag),
            "status_note": FRESHNESS_LABELS[freshness_status(lag)],
            "records": climate_observations.count(scoped),
        })
    out.sort(key=lambda item: (item["lag_days"] if item["lag_days"] is not None else 10 ** 6,
                               item["source_name"]))
    return out


def freshness_status(lag: Optional[int]) -> str:
    if lag is None:
        return "no_data"
    if lag <= FRESH_DAYS:
        return "fresh"
    if lag <= LAGGING_DAYS:
        return "lagging"
    return "stalled"


def lag_days(value: Any, *, today: Optional[date] = None) -> Optional[int]:
    """Whole days between a stored date and "now" - never a negative lag."""
    parsed = parse_date(value)
    if parsed is None:
        return None
    reference = parse_date(today) or utcnow().date()
    return max(0, (reference - parsed).days)


def parse_date(value: Any) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_moment(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    try:
        from dateutil import parser as date_parser

        moment = date_parser.parse(str(value).strip())
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, OverflowError, ImportError):
        return None


def quality_report(*, start: Optional[str], end: Optional[str],
                   regions_filter: Optional[Sequence[str]],
                   datasets_list: Optional[Sequence[Any]],
                   countries: Optional[Sequence[str]],
                   scope_detail: bool = True) -> Dict[str, Any]:
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             datasets_list=datasets_list, countries=countries,
                             limit=QUALITY_SAMPLE_LIMIT)
    overall = coverage_from_database(criteria)
    freshness = source_freshness(criteria=criteria)
    lags = [item["lag_days"] for item in freshness if item["lag_days"] is not None]
    observations_read = int(overall["observation_count"])
    payload: Dict[str, Any] = {
        "scope": {"start": start, "end": end, "regions": list(regions_filter or []),
                  "datasets": [str(item) for item in (datasets_list or [])],
                  "countries": list(countries or [])},
        "overall": overall,
        "available": bool(observations_read),
        "observations_read": observations_read,
        "completeness_pct": overall["completeness"].get("temperature_c", {}).get("pct", 0.0),
        "flag_totals": overall["flagged"],
        "flagged_rows": overall.get("flagged_row_count", 0),
        "flagged_row_pct": overall.get("flagged_row_pct", 0.0),
        "per_source": freshness,
        "source_count": len(freshness),
        "worst_lag_days": max(lags) if lags else None,
        "median_lag_days": analytics._round(statistics.median(lags), 1) if lags else None,
        "freshness_thresholds": {"fresh_max_days": FRESH_DAYS, "lagging_max_days": LAGGING_DAYS},
        "status_counts": tally(item["status"] for item in freshness),
        "generated_at": utcnow().isoformat(),
    }
    if not observations_read:
        payload["reason"] = ("No observations match this filter set, so completeness, flag counts "
                             "and freshness cannot be computed. Ingest a dataset first.")
        payload["per_scope"] = []
        return payload

    row_criteria, limit = strip_limit(criteria)
    payload["flag_index_totals"] = {
        "clean_rows": climate_observations.count({**row_criteria, "quality_flags": {"$size": 0}}),
        "flagged_rows": climate_observations.count({**row_criteria,
                                                    "quality_flags.0": {"$exists": True}}),
        "total_rows": climate_observations.count(row_criteria),
        "limit_applied": limit,
    }
    if scope_detail:
        payload["per_scope"] = scoped_coverage(
            regions_filter=regions_filter, datasets_list=datasets_list, start=start, end=end,
            countries=countries, cap=15)
    return payload


def scoped_coverage(*, regions_filter: Optional[Sequence[str]],
                    datasets_list: Optional[Sequence[Any]], start: Optional[str],
                    end: Optional[str], countries: Optional[Sequence[str]],
                    cap: int = 15) -> List[Dict[str, Any]]:
    """Per-region and per-dataset coverage, aggregated in MongoDB.

    Each dimension keeps the scope the row-based version read: regions ignore the dataset
    filter, datasets ignore region and country filters. ``cap`` bounds how many scopes are
    reported per dimension, highest observation count first, and every scope is measured in
    full rather than from a sampled window of documents.
    """
    region_criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                                    countries=countries, limit=analytics.MAX_QUERY_ROWS)
    dataset_criteria = build_filters(start=start, end=end, datasets_list=datasets_list,
                                     limit=analytics.MAX_QUERY_ROWS)
    out: List[Dict[str, Any]] = []
    for scope_type, criteria in (("region", region_criteria), ("dataset", dataset_criteria)):
        field = "region_code" if scope_type == "region" else "dataset_id"
        scopes = [(scope, report) for scope, report in coverage_scopes(criteria, group_field=field)
                  if scope not in (None, "")]
        scopes.sort(key=lambda item: item[1]["observation_count"], reverse=True)
        for scope, report in scopes[:cap]:
            out.append({
                "scope_type": scope_type,
                "scope": str(scope),
                "observation_count": report["observation_count"],
                "date_range": report["date_range"],
                "flagged_row_count": report.get("flagged_row_count", 0),
                "flagged_row_pct": report.get("flagged_row_pct", 0.0),
                "flags": report["flagged"],
                "temperature_completeness_pct": report["completeness"]["temperature_c"]["pct"],
                "precipitation_completeness_pct": report["completeness"]["precipitation_mm"]["pct"],
                "locations": report["locations"],
                "regions": report["regions"],
                "sampled_rows": report["observation_count"],
                "note": None if report["observation_count"]
                        else "No observations stored for this scope.",
            })
    out.sort(key=lambda item: item["observation_count"], reverse=True)
    return out


# ------------------------------------------------------------------- stations
def stations(*, regions_filter: Optional[Sequence[str]] = None,
             countries: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Monitored locations from the ``regions`` collection plus live observation counts."""
    index = region_index()
    criteria = build_filters(regions_filter=regions_filter, countries=countries,
                             limit=analytics.MAX_QUERY_ROWS)
    live = station_aggregates(criteria, limit=1000)
    today = utcnow().date()

    wanted = _dedupe([str(code) for code in (regions_filter or [])] + list(index))
    items: List[Dict[str, Any]] = []
    seen: set = set()
    for code in wanted:
        region = index.get(code)
        if not region or (countries and region.get("country") not in list(countries)):
            continue
        from_reference = region.get("source") == "reference"
        for location in region.get("locations") or []:
            location_code = str(location.get("code") or "").upper()
            if not location_code or location_code in seen:
                continue
            seen.add(location_code)
            stat = live.get(location_code, {})
            lag = lag_days(stat.get("last_date"), today=today)
            items.append({
                "location_code": location_code,
                "location_name": location.get("name") or stat.get("location_name"),
                "region_code": code,
                "country": location.get("country") or region.get("country") or stat.get("country"),
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "elevation_m": location.get("elevation_m"),
                "station_id": location.get("station_id"),
                "observation_count": int(stat.get("observation_count") or 0),
                "first_observation_date": stat.get("first_date"),
                "last_observation_date": stat.get("last_date"),
                "lag_days": lag,
                "status": "monitoring" if stat.get("observation_count") else "awaiting_data",
                "status_note": ("Reporting observations from climate_observations."
                                if stat.get("observation_count")
                                else "Registered in the region catalogue but no observations yet."),
                "geography_source": "reference" if from_reference else "regions_collection",
            })

    for code, stat in live.items():
        if code.upper() in seen:
            continue
        lag = lag_days(stat.get("last_date"), today=today)
        items.append({
            "location_code": code,
            "location_name": stat.get("location_name"),
            "region_code": stat.get("region_code"),
            "country": stat.get("country"),
            "latitude": None, "longitude": None, "elevation_m": None, "station_id": None,
            "observation_count": int(stat.get("observation_count") or 0),
            "first_observation_date": stat.get("first_date"),
            "last_observation_date": stat.get("last_date"),
            "lag_days": lag,
            "status": "monitoring",
            "status_note": "Observations exist for a location that is not in the region catalogue.",
            "geography_source": "observations",
        })

    items.sort(key=lambda item: (-item["observation_count"], item["location_code"]))
    reporting = [item for item in items if item["observation_count"]]
    return {
        "stations": items,
        "station_count": len(items),
        "reporting_count": len(reporting),
        "awaiting_data_count": len(items) - len(reporting),
        "regions_source": ("regions_collection"
                           if any(region.get("source") == "regions_collection"
                                  for region in index.values()) else "reference"),
        "note": None if items else (
            "No monitored stations are available: the regions collection is empty and "
            "climate_observations holds no documents yet."),
        "generated_at": utcnow().isoformat(),
    }


def region_summary(code: str, *, start: Optional[str] = None,
                   end: Optional[str] = None) -> Dict[str, Any]:
    """One region: catalogue entry, locations, aggregates and a recent trend."""
    index = region_index()
    document = index.get(code)
    known = sorted(index)
    if document is None and code not in set(region_codes_present()):
        sample = ", ".join(known[:12]) if known else "none are catalogued yet"
        suffix = " The full list is available from GET /api/v1/climate/regions." if len(known) > 12 else "."
        raise ApiError("not_found", f"No region with code '{code}'. Catalogued regions: {sample}{suffix}")
    document = document or reference_region(code)
    criteria = build_filters(start=start, end=end, regions_filter=[code])
    aggregates = regional_aggregates(criteria, limit=5)
    stats = aggregates.get(code)
    location_criteria = build_filters(start=start, end=end, regions_filter=[code])
    live_locations = station_aggregates(location_criteria, limit=200)
    known_codes = {str(item.get("code")) for item in (document.get("locations") or [])}
    locations = []
    for location in document.get("locations") or []:
        location_code = str(location.get("code") or "")
        stat = live_locations.get(location_code, {})
        locations.append({
            **location,
            "observation_count": int(stat.get("observation_count") or 0),
            "first_observation_date": stat.get("first_date"),
            "last_observation_date": stat.get("last_date"),
            "lag_days": lag_days(stat.get("last_date")),
        })
    for code_seen, stat in live_locations.items():
        if code_seen in known_codes:
            continue
        locations.append({
            "code": code_seen, "name": stat.get("location_name"),
            "latitude": None, "longitude": None,
            "observation_count": int(stat.get("observation_count") or 0),
            "first_observation_date": stat.get("first_date"),
            "last_observation_date": stat.get("last_date"),
            "lag_days": lag_days(stat.get("last_date")),
        })

    trend = trend_payload(start=start, end=end, regions_filter=[code], location_codes=None,
                          metric="temperature_c", frequency="monthly", group_by="none",
                          require_baseline=True)
    return {
        "region": {
            "code": code,
            "name": document.get("name") or code,
            "country": document.get("country"),
            "climate_zone": document.get("climate_zone"),
            "centroid": document.get("centroid"),
            "source": document.get("source", "reference"),
        },
        "statistics": stats or {
            "observation_count": 0, "first_date": None, "last_date": None,
            "location_count": len(locations), "metrics": {},
            "reason": "No observation documents exist for this region yet.",
        },
        "location_count": len(locations),
        "locations": locations[:200],
        "temperature_trend": trend,
        "available": bool(stats and stats["observation_count"]),
        "generated_at": utcnow().isoformat(),
    }


# -------------------------------------------------------------------- metrics
def metric_catalogue(*, with_counts: bool = True) -> List[Dict[str, Any]]:
    """The measurement contract the frontend filter widgets are built from."""
    parameter_by_field = {
        value["field"]: {"parameter": key, "source_unit": value["unit"], "label": value["label"]}
        for key, value in regions.NASA_PARAMETERS.items()
    }
    total: Optional[int] = None
    catalogue: List[Dict[str, Any]] = []
    for metric in analytics.METRICS:
        low, high = validators.RANGE_LIMITS[metric]
        populated: Optional[int] = None
        if with_counts:
            if total is None:
                total = climate_observations.count({})
            populated = climate_observations.count({metric: {"$ne": None}}) if total else 0
        catalogue.append({
            "field": metric,
            "label": METRIC_LABELS[metric],
            "unit": METRIC_UNITS[metric],
            "range_min": low,
            "range_max": high,
            "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
            "missing_encoding": "null",
            "populated_count": populated,
            "total_count": total,
            "completeness_pct": (analytics._round(populated / total * 100, 1)
                                 if with_counts and total and populated is not None else 0.0),
            "has_data": bool(populated),
            "source_parameters": parameter_by_field.get(metric),
        })
    return catalogue


# ---------------------------------------------------------------- CSV export
EXPORT_COLUMNS = [
    "dedupe_key", "observation_date", "year", "month", "day", "location_code", "location_name",
    "region_code", "country", "latitude", "longitude",
    *analytics.METRICS,
    "source_name", "source_type", "dataset_name", "dataset_id", "source_units", "quality_flags",
    "ingested_at", "schema_version",
]


def export_projection() -> Dict[str, int]:
    """A pure inclusion projection: a CSV carries exactly the export columns.

    MongoDB (and the local store) reject a projection that mixes inclusions with
    exclusions, so the heavy ``coordinates``/``provenance`` sub-documents are
    simply not listed here instead of being excluded explicitly.
    """
    fields = {column: 1 for column in EXPORT_COLUMNS}
    fields["_id"] = 0
    return fields


def _csv_safe(value: Any) -> str:
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@") and not re.match(r"^-[0-9]", text):
        return "'" + text
    return text


def _csv_value(row: Dict[str, Any], column: str) -> Any:
    value = row.get(column)
    if column == "quality_flags":
        return "|".join(str(flag) for flag in (value or []))
    if column == "dataset_id":
        return str(value) if value is not None else ""
    if column == "source_units":
        if isinstance(value, dict):
            return ";".join(f"{key}={item}" for key, item in sorted(value.items()))[:300]
        return "" if value is None else str(value)[:300]
    if isinstance(value, datetime):
        return value.isoformat()
    return _csv_safe(value)


def export_plan(*, start: Optional[str], end: Optional[str],
                regions_filter: Optional[Sequence[str]], location_codes: Optional[Sequence[str]],
                datasets_list: Optional[Sequence[Any]], countries: Optional[Sequence[str]],
                metric: Optional[str], quality: str, sort: str,
                requested_rows: Optional[int], actor: Optional[Dict[str, Any]]
                ) -> Dict[str, Any]:
    """Bounded export plan: column contract, sort and a known row count."""
    max_rows = min(max(int(requested_rows or MAX_EXPORT_ROWS), 1), MAX_EXPORT_ROWS)
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, datasets_list=datasets_list,
                             countries=countries, metric=metric, quality=quality,
                             limit=analytics.MAX_QUERY_ROWS)
    sort_key = sort if sort in OBSERVATION_SORTS else DEFAULT_OBSERVATION_SORT
    pipeline = [
        {"$match": strip_limit(criteria)[0]},
        {"$limit": max_rows + 1},
        {"$group": {"_id": None, "rows": {"$sum": 1},
                    "first_date": {"$min": "$observation_date"},
                    "last_date": {"$max": "$observation_date"}}},
    ]
    rows = climate_observations.aggregate(pipeline)
    summary = rows[0] if rows else {}
    matched = int(summary.get("rows") or 0)
    scope = {"start": start, "end": end, "regions": list(regions_filter or []),
             "location_codes": list(location_codes or []),
             "datasets": [str(item) for item in (datasets_list or [])],
             "countries": list(countries or []), "metric": metric, "quality": quality,
             "sort": sort_key}
    return {
        "criteria": criteria,
        "sort": OBSERVATION_SORTS[sort_key],
        "sort_key": sort_key,
        "max_rows": max_rows,
        "scope": scope,
        "actor": actor,
        "matched_rows": matched,
        "exported_rows": min(matched, max_rows),
        "truncated": matched > max_rows,
        "first_date": summary.get("first_date"),
        "last_date": summary.get("last_date"),
        "columns": list(EXPORT_COLUMNS),
        "generated_at": utcnow(),
    }


def csv_header_lines(meta: Dict[str, Any]) -> List[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    filters = " ".join(f"{key}={_compact(value)}" for key, value in sorted(meta["scope"].items())
                       if value not in (None, [], "", {})) or "none"
    actor = meta.get("actor") or {}
    writer.writerow([f"# EarthScape Climate Agency observation export - generated "
                     f"{_iso(meta['generated_at'])}"])
    writer.writerow([f"# rows_included={meta['exported_rows']} rows_matched={meta['matched_rows']} "
                     f"rows_limit={meta['max_rows']} truncated={str(meta['truncated']).lower()}"])
    writer.writerow([f"# date_range={_csv_safe(meta.get('first_date'))}.."
                     f"{_csv_safe(meta.get('last_date'))}"])
    writer.writerow([f"# filters {filters}"])
    writer.writerow([f"# requested_by={actor.get('id') or 'system'} "
                     f"role={actor.get('role') or 'system'}"])
    writer.writerow(["# null means the source did not supply that measurement (never zero); "
                     "quality_flags are pipe-separated; coordinates/provenance are omitted"])
    writer.writerow(EXPORT_COLUMNS)
    return buffer.getvalue().splitlines(keepends=True)


def csv_data_lines(rows: Iterable[Dict[str, Any]]) -> List[str]:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_csv_value(row, column) for column in EXPORT_COLUMNS])
    return buffer.getvalue().splitlines(keepends=True)


def export_stream(meta: Dict[str, Any], *, chunk_documents: int = 1000) -> Iterable[str]:
    """Streaming CSV body: comment header (with real counts), header row, then rows.

    A server-side cursor is used so a large export never materialises in memory;
    if the active document store cannot provide one, the export still completes
    through the bounded ``find`` path.
    """
    yield from csv_header_lines(meta)
    criteria, _ = strip_limit(meta["criteria"])
    max_rows = int(meta["max_rows"])
    if not max_rows:
        return
    try:
        cursor = climate_observations.stream(criteria, projection=export_projection(),
                                            sort=meta["sort"])
    except TypeError as exc:
        logger.warning("Server-side export cursor unavailable (%s); using a bounded read", exc)
        cursor = None
    if cursor is None:
        yield from csv_data_lines(_bounded_read(criteria, meta["sort"], max_rows,
                                                chunk_documents=chunk_documents))
        return
    emitted = 0
    try:
        batch: List[Dict[str, Any]] = []
        for document in cursor:
            if emitted >= max_rows:
                break
            batch.append(document)
            if len(batch) >= chunk_documents:
                yield from csv_data_lines(batch)
                emitted += len(batch)
                batch = []
        if batch:
            yield from csv_data_lines(batch)
    finally:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001 - closing a server cursor must never break the response
            logger.info("Export cursor close skipped")


def _bounded_read(criteria: Dict[str, Any], sort: Sequence[Tuple[str, int]], max_rows: int,
                  *, chunk_documents: int) -> List[Dict[str, Any]]:
    row_criteria, _ = strip_limit(criteria)
    return climate_observations.find(row_criteria, projection=export_projection(),
                                     sort=sort, limit=min(max_rows, repositories.HARD_RESULT_LIMIT))


def compact(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(item) for item in value[:10])
    return str(value)[:60]


def _compact(value: Any) -> str:
    return compact(value)


# ------------------------------------------------------------- saved analyses
def list_saved_analyses(user_id: str, *, role: str, page: int, page_size: int,
                        search: Optional[str] = None) -> Dict[str, Any]:
    criteria: Dict[str, Any] = ({} if role == "administrator"
                                else {"$or": [{"owner_user_id": str(user_id)}, {"is_shared": True}]})
    if search:
        criteria["name"] = {"$regex": escape_regex(search), "$options": "i"}
    envelope = saved_analyses.paginate(criteria, page=page, page_size=page_size,
                                      sort=[("updated_at", -1)])
    envelope["items"] = [present_saved_analysis(item) for item in envelope["items"]]
    return envelope


def create_saved_analysis(user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = utcnow()
    document = {
        "name": str(payload["name"])[:140],
        "description": str(payload.get("description") or "")[:500],
        "owner_user_id": str(user_id),
        "filters": payload.get("filters") or {},
        "chart_config": payload.get("chart_config") or {},
        "notes": str(payload.get("notes") or "")[:2000],
        "created_at": now,
        "updated_at": now,
        "is_shared": bool(payload.get("is_shared")),
        "schema_version": 1,
    }
    document["_id"] = saved_analyses.insert_one(document)
    return present_saved_analysis(document)


def update_saved_analysis(document_id: str, payload: Dict[str, Any], *, user_id: str,
                          role: str) -> Dict[str, Any]:
    document = owned_saved_analysis(document_id, user_id=user_id, role=role)
    updates: Dict[str, Any] = {"updated_at": utcnow()}
    for field in ("name", "description", "filters", "chart_config", "notes", "is_shared"):
        if field in payload:
            updates[field] = payload[field]
    updated = saved_analyses.update_one({"_id": document["_id"]}, {"$set": updates},
                                       return_document=True)
    return present_saved_analysis(repositories.serialize(updated) or {})


def get_saved_analysis(document_id: str, *, user_id: str, role: str) -> Dict[str, Any]:
    document = owned_saved_analysis(document_id, user_id=user_id, role=role)
    return present_saved_analysis(repositories.serialize(document) or {})


def delete_saved_analysis(document_id: str, *, user_id: str, role: str) -> int:
    document = owned_saved_analysis(document_id, user_id=user_id, role=role)
    return saved_analyses.delete_many({"_id": document["_id"]})


def owned_saved_analysis(document_id: str, *, user_id: str, role: str) -> Dict[str, Any]:
    from common.repositories import to_object_id

    document = saved_analyses.find_one({"_id": to_object_id(document_id)})
    if document is None:
        raise ApiError("not_found", "No such saved analysis.")
    if role != "administrator" and str(document.get("owner_user_id")) != str(user_id):
        raise ApiError(
            "permission_denied",
            "Only the owner or an administrator may access this saved analysis.",
        )
    return document


def present_saved_analysis(document: Dict[str, Any]) -> Dict[str, Any]:
    out = repositories.serialize(document) or dict(document)
    out.pop("_id", None)
    out["id"] = str(out.get("id") or "")
    out["owner_user_id"] = str(out.get("owner_user_id") or "")
    for field in ("created_at", "updated_at"):
        out[field] = _iso(out.get(field))
    return out


def escape_regex(text: str) -> str:
    return re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", str(text))[:60]


# ------------------------------------------------------------------- analytics
def list_summaries(*, metric: Optional[str] = None, workflow: Optional[str] = None,
                   processing_mode: Optional[str] = None, prefix: Optional[str] = None,
                   limit: int = 100) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if metric:
        criteria["metric"] = metric
    if workflow:
        criteria["workflow"] = workflow
    if processing_mode:
        criteria["processing_mode"] = processing_mode
    if prefix:
        criteria["summary_key"] = {"$regex": f"^{escape_regex(prefix)}"}
    documents = analytics_summaries.find(criteria, sort=[("computed_at", -1)], limit=limit)
    items = [present_summary(document) for document in documents]
    return {
        "items": items,
        "total": len(items),
        "filters": {"metric": metric, "workflow": workflow, "processing_mode": processing_mode,
                    "prefix": prefix},
        "available_metrics": sorted({str(item.get("metric")) for item in items if item.get("metric")}),
        "available_workflows": sorted({str(item.get("workflow")) for item in items
                                       if item.get("workflow")}),
        "available_processing_modes": sorted({str(item.get("processing_mode")) for item in items
                                              if item.get("processing_mode")}),
        "note": None if items else (
            "No stored aggregates exist yet. Run POST /api/v1/analytics/recompute, or wait for "
            "a MapReduce/PySpark workflow to publish one."),
        "generated_at": utcnow().isoformat(),
    }


def present_summary(document: Dict[str, Any]) -> Dict[str, Any]:
    out = repositories.serialize(document) or dict(document)
    out.pop("_id", None)
    out["computed_at"] = _iso(out.get("computed_at"))
    payload = out.get("payload")
    if isinstance(payload, dict):
        out["payload_size"] = len(payload)
    return out


def summary_by_key(key: str) -> Dict[str, Any]:
    document = analytics_summaries.find_one({"summary_key": str(key)[:300]})
    if document is None:
        raise ApiError("not_found",
                       "No stored aggregate matches that key. List them via "
                       "GET /api/v1/analytics/summaries or recompute with "
                       "POST /api/v1/analytics/recompute.")
    return present_summary(document)


def recompute_core_aggregates(*, start: Optional[str] = None, end: Optional[str] = None,
                              metric: Optional[str] = None, limit: int = ANALYTICS_ROW_LIMIT,
                              processing_mode: str = "mongodb") -> Dict[str, Any]:
    """Recompute the core aggregates from MongoDB and store them via ``store_summary``."""
    started = utcnow()
    stored: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    scope_base = {"window_start": start, "window_end": end,
                  "metric": metric or "mixed", "origin": "analytics.recompute"}

    monthly_criteria = build_filters(start=start, end=end, limit=limit)
    monthly_rows = fetch_rows(monthly_criteria,
                              projection=metric_projection(["temperature_c", "precipitation_mm"]))
    datasets_used = _dedupe([str(row.get("dataset_id")) for row in monthly_rows
                             if row.get("dataset_id") is not None])

    if metric in (None, "temperature_c"):
        record = _recompute_global_monthly_mean(
            monthly_rows, monthly_criteria, scope=scope_base, datasets_used=datasets_used,
            processing_mode=processing_mode)
        (stored if record.get("stored") else skipped).append(record)

    if metric in (None, "precipitation_mm"):
        record = _recompute_monthly_precipitation(
            monthly_criteria, scope=scope_base, datasets_used=datasets_used,
            processing_mode=processing_mode)
        (stored if record.get("stored") else skipped).append(record)

    record = _recompute_regional_annual_normals(
        start=start, end=end, metric=metric, limit=limit, scope=scope_base,
        datasets_used=datasets_used, processing_mode=processing_mode)
    if record.get("stored"):
        stored.extend(record["stored"])
    else:
        skipped.append({"name": record["name"], "reason": record["reason"]})

    record = _recompute_station_freshness(scope=scope_base, datasets_used=datasets_used,
                                          processing_mode=processing_mode)
    (stored if record.get("stored") else skipped).append(record)

    finished = utcnow()
    return {
        "stored": stored,
        "skipped": skipped,
        "stored_count": len(stored),
        "skipped_count": len(skipped),
        "observations_in_scope": matching_row_count(build_filters(start=start, end=end,
                                                                  limit=limit)),
        "rows_read": len(monthly_rows),
        "source_datasets": datasets_used,
        "window": {"start": start, "end": end},
        "processing_mode": processing_mode,
        "duration_seconds": round((finished - started).total_seconds(), 3),
        "started_at": _iso(started),
        "finished_at": _iso(finished),
        "note": None if stored else "No aggregate could be computed from the stored data.",
    }


def _recompute_global_monthly_mean(rows: List[Dict[str, Any]], criteria: Dict[str, Any], *,
                                   scope: Dict[str, Any], datasets_used: Sequence[str],
                                   processing_mode: str) -> Dict[str, Any]:
    name = "global_monthly_mean_temperature"
    try:
        series = analytics.trend_series(rows, "temperature_c", frequency="monthly")
    except InsufficientData as exc:
        return {"stored": False, "name": name, "reason": exc.message}
    points = series.get("series") or []
    if not points:
        return {"stored": False, "name": name,
                "reason": series.get("note") or "No monthly periods are available."}
    values = [item["value"] for item in points if item["value"] is not None]
    key = analytics.store_summary(
        metric="temperature_c",
        scope={**scope, "aggregation": "global_monthly_mean"},
        payload={
            "series": points[:SERIES_PAGE_LIMIT],
            "point_count": len(points),
            "first_period": points[0]["period"],
            "last_period": points[-1]["period"],
            "period_mean": analytics._round(statistics.fmean(values)) if values else None,
            "pipeline_cross_check": [
                {"period": row["period"], "mean": row["metrics"]["temperature_c"]["mean"],
                 "populated": row["metrics"]["temperature_c"]["populated"],
                 "observation_count": row["observation_count"]}
                for row in monthly_totals(criteria, ["temperature_c"], limit=400)
                if row.get("period")][-36:],
            "source": "climate_observations",
        },
        source_datasets=list(datasets_used), workflow="analytics.recompute",
        processing_mode=processing_mode)
    return {"stored": True, "summary_key": key, "name": name, "metric": "temperature_c",
            "scope": "global_monthly_mean", "record_count": len(points)}


def _recompute_monthly_precipitation(criteria: Dict[str, Any], *, scope: Dict[str, Any],
                                     datasets_used: Sequence[str],
                                     processing_mode: str) -> Dict[str, Any]:
    name = "monthly_precipitation_totals"
    entries = [
        {"period": row["period"], "year": row["year"], "month": row["month"],
         "total_mm": row["metrics"]["precipitation_mm"]["total"],
         "mean_mm": row["metrics"]["precipitation_mm"]["mean"],
         "populated": row["metrics"]["precipitation_mm"]["populated"],
         "observation_count": row["observation_count"]}
        for row in monthly_totals(criteria, ["precipitation_mm"], limit=400) if row.get("period")
    ]
    if not entries:
        return {"stored": False, "name": name,
                "reason": "No stored observation carries a precipitation value."}
    key = analytics.store_summary(
        metric="precipitation_mm",
        scope={**scope, "aggregation": "monthly_total"},
        payload={"months": entries, "record_count": len(entries),
                 "grand_total_mm": analytics._round(
                     sum(item["total_mm"] or 0 for item in entries)),
                 "pipeline": ["$match", "$group", "$sort", "$limit"],
                 "source": "climate_observations"},
        source_datasets=list(datasets_used), workflow="analytics.recompute",
        processing_mode=processing_mode)
    return {"stored": True, "summary_key": key, "name": name, "metric": "precipitation_mm",
            "scope": "monthly_total", "record_count": len(entries)}


def _recompute_regional_annual_normals(*, start: Optional[str], end: Optional[str],
                                        metric: Optional[str], limit: int,
                                        scope: Dict[str, Any], datasets_used: Sequence[str],
                                        processing_mode: str) -> Dict[str, Any]:
    name = "regional_annual_normals"
    metrics_for_normals = [metric] if metric else list(analytics.METRICS)
    stored: List[Dict[str, Any]] = []
    for metric_name in metrics_for_normals:
        if metric_name not in analytics.METRICS:
            continue
        criteria = build_filters(start=start, end=end, limit=limit)
        rows = yearly_region_normals(criteria, metric_name, limit=2000)
        if not rows:
            continue
        per_region: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            per_region.setdefault(row["region_code"], []).append({
                "year": row["year"],
                "value": row["total"] if metric_name in ADDITIVE_METRICS else row["mean"],
                "aggregation": row["aggregation"],
                "observation_count": row["observation_count"],
                "min": row["min"],
                "max": row["max"],
            })
        complete = {region: entries for region, entries in per_region.items() if len(entries) >= 3}
        if not complete:
            continue
        payload_regions = []
        for region_code, entries in sorted(complete.items()):
            entries.sort(key=lambda item: item["year"])
            values = [item["value"] for item in entries if item["value"] is not None]
            payload_regions.append({
                "region_code": region_code,
                "years": [item["year"] for item in entries],
                "normal": analytics._round(statistics.fmean(values)) if values else None,
                "aggregation": entries[0]["aggregation"],
                "unit": METRIC_UNITS[metric_name],
                "per_year": entries[:120],
                "trend": analytics.trend_slope([(str(item["year"]), item["value"])
                                                for item in entries if item["value"] is not None]),
            })
        key = analytics.store_summary(
            metric=metric_name,
            scope={**scope, "aggregation": "regional_annual_normal"},
            payload={"regions": payload_regions, "region_count": len(payload_regions),
                     "requires_minimum_years": 3,
                     "computed_by": "MongoDB aggregation ($match/$group) + python normalisation",
                     "source": "climate_observations"},
            source_datasets=list(datasets_used), workflow="analytics.recompute",
            processing_mode=processing_mode)
        stored.append({"stored": True, "summary_key": key, "name": name, "metric": metric_name,
                       "scope": "regional_annual_normal", "record_count": len(payload_regions)})
    if stored:
        return {"stored": stored, "name": name}
    return {"stored": False, "name": name,
            "reason": ("No region has the three complete years the normals need, so an annual "
                       "normal would not be meaningful yet.")}


def _recompute_station_freshness(*, scope: Dict[str, Any], datasets_used: Sequence[str],
                                 processing_mode: str) -> Dict[str, Any]:
    name = "station_freshness"
    counts = station_aggregates(build_filters(limit=analytics.MAX_QUERY_ROWS), limit=1000)
    today = utcnow().date()
    rows = [
        {"location_code": code, "observation_count": item["observation_count"],
         "first_observation_date": item["first_date"], "last_observation_date": item["last_date"],
         "lag_days": lag_days(item["last_date"], today=today),
         "status": freshness_status(lag_days(item["last_date"], today=today))}
        for code, item in sorted(counts.items())
    ]
    if not rows:
        return {"stored": False, "name": name,
                "reason": "climate_observations holds no documents, so no station can be measured."}
    key = analytics.store_summary(
        metric="metadata",
        scope={**scope, "aggregation": "station_freshness"},
        payload={"stations": rows[:1000], "station_count": len(rows),
                 "newest_observation_date": max((row["last_observation_date"] or ""
                                                 for row in rows), default="") or None,
                 "sources": source_freshness(),
                 "status_counts": tally(row["status"] for row in rows)},
        source_datasets=list(datasets_used), workflow="analytics.recompute",
        processing_mode=processing_mode)
    return {"stored": True, "summary_key": key, "name": name, "metric": "metadata",
            "scope": "station_freshness", "record_count": len(rows)}


def trend_slope_report(*, metric: str, group_by: str, frequency: str = "annual",
                       regions_filter: Optional[Sequence[str]] = None,
                       location_codes: Optional[Sequence[str]] = None,
                       start: Optional[str] = None, end: Optional[str] = None,
                       base_from: Optional[str] = None, base_to: Optional[str] = None,
                       limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    """Per-group OLS slope over an aggregated series plus deltas against a base period."""
    # Callers may pass a year (``1991``) or a date (``1991-05-01``): compare years.
    base_from = explorer.normalise_period(base_from)
    base_to = explorer.normalise_period(base_to)
    series = trend_payload(start=start, end=end, regions_filter=regions_filter,
                           location_codes=location_codes, metric=metric, frequency=frequency,
                           group_by=group_by, require_baseline=False, row_limit=limit)
    points = series.get("series") or []
    if not series.get("available") and not points:
        return {
            "available": False, "metric": metric, "label": METRIC_LABELS.get(metric),
            "unit": METRIC_UNITS.get(metric), "frequency": frequency, "group_by": group_by,
            "slopes": [], "reason": series.get("reason") or
            "No periods could be aggregated from stored observations.",
            "required_points": series.get("required_points", analytics.MIN_POINTS_FOR_BASELINE),
            "available_points": series.get("available_points", 0),
        }

    key_field = {"region": "region_code", "location": "location_code",
                 "country": "country"}.get(group_by, "scope")
    buckets: Dict[str, List[Tuple[str, float]]] = {}
    for point in points:
        label = str(point.get(key_field) or "GLOBAL")
        if point.get("value") is None:
            continue
        buckets.setdefault(label, []).append((str(point["period"]), float(point["value"])))

    entries: List[Dict[str, Any]] = []
    for label, pairs in sorted(buckets.items()):
        pairs.sort(key=lambda item: item[0])
        slope = analytics.trend_slope(pairs)
        entry: Dict[str, Any] = {
            "group": label, "group_by": key_field,
            "periods": [period for period, _ in pairs],
            "values": [value for _, value in pairs],
            "slope": slope,
            "available": bool(slope.get("available")),
        }
        if not entry["available"]:
            entry["reason"] = slope.get("note")
        base_values = [value for period, value in pairs
                       if _in_base_window(period, base_from, base_to)]
        if base_from or base_to:
            entry["base_period"] = {"start": base_from, "end": base_to,
                                    "periods": len(base_values)}
            if base_values:
                normal = analytics._round(statistics.fmean(base_values))
                latest_period, latest_value = pairs[-1]
                entry["base_normal"] = normal
                entry["latest_period"] = latest_period
                entry["latest_value"] = analytics._round(latest_value)
                entry["delta_vs_base"] = analytics._round(latest_value - (normal or 0.0))
            else:
                entry["reason"] = ("No period of the stored series falls inside the requested "
                                   "base window, so no delta against a normal can be computed.")
        entries.append(entry)

    entries.sort(key=lambda item: (item["slope"] or {}).get("slope_per_period") or 0, reverse=True)
    return {
        "available": any(item["available"] for item in entries),
        "metric": metric,
        "label": METRIC_LABELS.get(metric),
        "unit": METRIC_UNITS.get(metric),
        "frequency": frequency,
        "group_by": group_by,
        "base_period": {"start": base_from, "end": base_to},
        "method": "least-squares slope over the aggregated period series "
                  "(common.analytics.trend_slope)",
        "slopes": entries[:200],
        "group_count": len(entries),
        "observations_evaluated": series.get("observations_evaluated", 0),
        "reason": None if entries else "No group produced an aggregated value.",
        "generated_at": utcnow().isoformat(),
    }


def _in_base_window(period: str, base_from: Optional[str], base_to: Optional[str]) -> bool:
    if not base_from and not base_to:
        return False
    year = str(period)[:4]
    if len(year) != 4 or not year.isdigit():
        return False
    if base_from and year < base_from:
        return False
    if base_to and year > base_to:
        return False
    return True


def analytics_matrix(*, metric: str = "temperature_c",
                     comparison_metric: Optional[str] = None,
                     correlation_metrics: Optional[Sequence[str]] = None,
                     frequency: str = "monthly", regions_filter: Optional[Sequence[str]] = None,
                     start: Optional[str] = None, end: Optional[str] = None,
                     limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    """One call for the analytics page: correlation + regional comparison + trend."""
    scope = build_filters(start=start, end=end, regions_filter=regions_filter, limit=limit)
    rows = fetch_rows(scope, projection=metric_projection(analytics.METRICS))
    comparison_metric = comparison_metric or metric
    trend_rows = [row for row in rows if row.get(metric) is not None]
    try:
        trend = analytics.trend_series(trend_rows, metric, frequency=frequency)
    except InsufficientData as exc:
        trend = {"available": False, "reason": exc.message, "metric": metric, "series": [],
                 "point_count": 0}
    if not trend.get("series"):
        trend["available"] = False
        trend.setdefault("reason", trend.get("note") or
                         "No periods are available for this metric.")
    else:
        trend["available"] = True
    return {
        "scope": {"metric": metric, "comparison_metric": comparison_metric,
                  "frequency": frequency, "regions": list(regions_filter or []),
                  "start": start, "end": end},
        "correlation": analytics.correlation_matrix(rows, correlation_metrics),
        "regional_comparison": analytics.compare_regions(rows, comparison_metric,
                                                        frequency=frequency),
        "trend": trend,
        "observations_evaluated": len(rows),
        "generated_at": utcnow().isoformat(),
    }


def comparison_payload(*, metric: str, frequency: str, regions_filter: Optional[Sequence[str]],
                       start: Optional[str], end: Optional[str],
                       location_codes: Optional[Sequence[str]] = None,
                       limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, metric=metric, limit=limit)
    rows = fetch_rows(criteria, projection=metric_projection([metric]), limit=limit)
    payload = analytics.compare_regions(rows, metric, frequency=frequency)
    payload["available"] = bool(payload.get("regions"))
    if not payload["available"]:
        payload["reason"] = ("No stored observation carries a value for this metric inside the "
                            "requested scope, so regions cannot be compared.")
    payload["observations_evaluated"] = len(rows)
    payload["scope"] = {"regions": list(regions_filter or []), "start": start, "end": end}
    payload["series"] = series_by_group(rows, metric, frequency, "region")
    return payload


def series_by_group(rows: List[Dict[str, Any]], metric: str, frequency: str,
                    group_by: str) -> List[Dict[str, Any]]:
    """Multi-series payload (one entry per group) for comparison charts."""
    try:
        payload = analytics.trend_series(rows, metric, frequency=frequency, group_by=group_by)
    except InsufficientData as exc:
        logger.info("Grouped series unavailable: %s", exc.message)
        return []
    return payload.get("series") or []


def correlation_payload(*, metrics: Optional[Sequence[str]], start: Optional[str],
                        end: Optional[str], regions_filter: Optional[Sequence[str]],
                        datasets_list: Optional[Sequence[Any]],
                        countries: Optional[Sequence[str]],
                        limit: int = ANALYTICS_ROW_LIMIT) -> Dict[str, Any]:
    selected = _dedupe([metric for metric in (metrics or analytics.METRICS)
                        if metric in analytics.METRICS])
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             datasets_list=datasets_list, countries=countries, limit=limit)
    rows = fetch_rows(criteria, projection=metric_projection(selected or analytics.METRICS),
                      limit=limit)
    payload = analytics.correlation_matrix(rows, selected or None)
    payload["observations_evaluated"] = len(rows)
    payload["available"] = bool(payload.get("cells"))
    if not payload["available"]:
        payload.setdefault("reason", payload.get("note") or
                           "At least two populated metrics are required for a correlation.")
    return payload


def history_payload(*, metric: str, regions_filter: Optional[Sequence[str]],
                    location_codes: Optional[Sequence[str]], countries: Optional[Sequence[str]],
                    datasets_list: Optional[Sequence[Any]], start: Optional[str],
                    end: Optional[str], base_from: str, base_to: str,
                    group_by: str) -> Dict[str, Any]:
    criteria = build_filters(start=start, end=end, regions_filter=regions_filter,
                             location_codes=location_codes, countries=countries,
                             datasets_list=datasets_list, metric=metric)
    return explorer.historical_explorer(
        (), metric=metric, base_from=base_from, base_to=base_to, group_by=group_by,
        pipeline_rows=yearly_region_normals(criteria, metric, limit=2000)
        if group_by in ("none", "region") else [],
        annual_rows=annual_group_totals(criteria, metric, group_by=group_by),
        observations_evaluated=matching_row_count(criteria),
    )


# ------------------------------------------------------------------- dashboard
def dashboard_summary(user_id: str, *, role: str, name: str = "") -> Dict[str, Any]:
    """Real computed platform state. No decorative numbers, no zero-filled guesses."""
    totals = platform_totals(user_id=user_id)
    window_start, window_end = default_window(days=SUMMARY_WINDOW_DAYS)
    newest = newest_observation_date()

    temperature_trend = trend_payload(start=None, end=None, regions_filter=None,
                                      location_codes=None, metric="temperature_c",
                                      frequency="monthly", group_by="none",
                                      require_baseline=True)
    precipitation_trend = trend_payload(start=None, end=None, regions_filter=None,
                                        location_codes=None, metric="precipitation_mm",
                                        frequency="monthly", group_by="none",
                                        require_baseline=True)

    comparison_criteria = build_filters(metric="temperature_c", limit=DASHBOARD_ROW_LIMIT)
    aggregates = regional_aggregates(comparison_criteria, ["temperature_c"], limit=40)
    ranked = sorted(
        [{"region_code": code,
          "mean_temperature_c": stats["metrics"].get("temperature_c", {}).get("mean"),
          "unit": METRIC_UNITS["temperature_c"],
          "max_temperature_c": stats["metrics"].get("temperature_c", {}).get("max"),
          "min_temperature_c": stats["metrics"].get("temperature_c", {}).get("min"),
          "observation_count": stats["observation_count"],
          "last_observation_date": stats["last_date"]}
         for code, stats in aggregates.items()
         if stats["metrics"].get("temperature_c", {}).get("mean") is not None],
        key=lambda item: item["mean_temperature_c"], reverse=True)

    alert_context = dashboard_alerts(window_start=window_start, window_end=window_end)
    current_data = current_data_state(newest)
    quality_criteria = build_filters(start=window_start, end=window_end, limit=QUALITY_SAMPLE_LIMIT)
    quality_coverage = coverage_from_database(quality_criteria)

    return {
        "totals": totals,
        "last_successful_sync": last_successful_sync(),
        "current_data": current_data,
        "temperature_trend": temperature_trend,
        "precipitation_trend": precipitation_trend,
        "regional_comparison": {
            "metric": "temperature_c",
            "label": METRIC_LABELS["temperature_c"],
            "unit": METRIC_UNITS["temperature_c"],
            "aggregation": "mean",
            "ranked_regions": ranked[:15],
            "region_count": len(ranked),
            "available": bool(ranked),
            "reason": None if ranked else (
                "No stored observation has a temperature value, so regions cannot be ranked."),
            "note": "Each value is the mean of the daily measurements actually stored for that "
                    "region - not a model output or an interpolated grid.",
        },
        "recent_anomalies": alert_context,
        "forecast_summaries": forecast_summaries(),
        "data_quality": {
            "window": {"start": window_start, "end": window_end},
            "coverage": quality_coverage,
            "observations_in_window": quality_coverage["observation_count"],
            "per_source": current_data["per_source"],
            "available": bool(quality_coverage["observation_count"]),
            "reason": None if quality_coverage["observation_count"] else (
                "No observation documents fall inside the recent window, so data quality cannot "
                "be measured."),
        },
        "pipeline_status": pipeline_status(),
        "alerts": recent_alerts(limit=10),
        "quick_actions": quick_actions(role, name=name, has_data=bool(totals["observations"])),
        "scope": {"user_id": str(user_id), "role": role},
        "generated_at": utcnow().isoformat(),
    }


def platform_totals(*, user_id: Optional[str] = None) -> Dict[str, Any]:
    observation_count = _count(climate_observations)
    stored_region_count = _count(regions_repository)
    observed_region_count = len(region_codes_present())
    region_count = stored_region_count or observed_region_count
    location_count = len(location_codes_present())
    return {
        "datasets": _count(datasets_repository),
        "datasets_by_status": status_breakdown(datasets_repository),
        "observations": observation_count,
        "locations": location_count,
        "regions": region_count,
        "regions_source": ("regions_collection" if stored_region_count
                           else "observations" if observed_region_count else "none"),
        "ingestion_jobs": status_breakdown(ingestion_jobs),
        "processing_jobs": status_breakdown(processing_jobs),
        "active_models": _count(model_registry, {"status": "active"}),
        "predictions_generated": _count(predictions),
        "open_alerts": _count(alerts, {"status": {"$in": ["open", "acknowledged", "unacknowledged"]}}),
        "unread_notifications": (_count(notifications, {"recipient_user_id": str(user_id),
                                                        "read_at": None})
                                 if user_id else 0),
        "support_tickets": status_breakdown(support_tickets),
    }


def last_successful_sync() -> Optional[Dict[str, Any]]:
    rows = ingestion_jobs.find(
        {"status": {"$in": ["completed", "completed_with_errors"]},
         "completed_at": {"$ne": None}},
        sort=[("completed_at", -1)], limit=1)
    if not rows:
        return None
    document = rows[0]
    return {
        "source_name": document.get("source_name"),
        "completed_at": _iso(document.get("completed_at")),
        "records_accepted": int(document.get("records_accepted") or 0),
        "records_rejected": int(document.get("records_rejected") or 0),
        "status": document.get("status"),
    }


def current_data_state(newest: Optional[str]) -> Dict[str, Any]:
    freshness = source_freshness()
    lag = lag_days(newest)
    return {
        "available": bool(newest),
        "newest_observation_date": newest,
        "lag_days": lag,
        "status": freshness_status(lag),
        "status_note": FRESHNESS_LABELS[freshness_status(lag)],
        "per_source": freshness,
        "source_count": len(freshness),
        "reason": None if newest else (
            "climate_observations holds no documents yet, so there is no current data. Ingest a "
            "dataset (Datasets & Ingestion) to populate the platform."),
    }


def dashboard_alerts(*, window_start: Optional[str], window_end: Optional[str]) -> Dict[str, Any]:
    persisted = persisted_anomalies(limit=12)
    if persisted:
        return {
            "source": "alerts_collection",
            "available": True,
            "count": len(persisted),
            "items": [present_alert(item) for item in persisted],
            "note": "Persisted by POST /api/v1/climate/anomalies/scan.",
        }
    computed = on_the_fly_anomalies(window_start=window_start, window_end=window_end,
                                    limit=12)
    computed["items"] = computed.pop("anomalies", [])
    return computed


def present_alert(document: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(document)
    out["triggered_at"] = _iso(out.get("triggered_at"))
    out["created_at"] = _iso(out.get("created_at"))
    return {key: out.get(key) for key in (
        "id", "rule_key", "status", "severity", "title", "message", "metric", "metric_label",
        "value", "unit", "deviation_score", "direction", "region_code", "location_code",
        "location_name", "observation_date", "triggered_at", "explanation", "method")}


def recent_alerts(*, limit: int = 10) -> List[Dict[str, Any]]:
    documents = alerts.find({}, sort=[("triggered_at", -1), ("created_at", -1)], limit=limit)
    return [present_alert(repositories.serialize(document) or {}) for document in documents]


def forecast_summaries(*, limit: int = 25) -> Dict[str, Any]:
    documents = predictions.find({}, sort=[("generated_at", -1)], limit=200)
    if not documents:
        return {
            "available": False, "items": [],
            "reason": "The predictions collection is empty - no model has produced a forecast yet.",
            "prediction_count": 0,
        }
    latest: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for document in documents:
        key = (document.get("region_code"), document.get("target_variable"),
               document.get("location_code"))
        latest.setdefault(key, document)
    items = []
    for document in list(latest.values())[:limit]:
        steps = document.get("values") or []
        # A forecast is stored as the whole horizon series, so the card shows the nearest
        # step rather than a top-level value the writer never puts there.
        first = min(steps, key=lambda row: str(row.get("forecast_date") or "~"), default={})
        items.append({
            "id": str(document.get("_id")),
            "region_code": document.get("region_code"),
            "location_code": document.get("location_code"),
            "target_variable": document.get("target_variable"),
            "origin_date": document.get("forecast_origin_date"),
            "forecast_date": first.get("forecast_date"),
            "predicted_value": first.get("value"),
            "unit": first.get("unit"),
            "confidence_low": first.get("lower_bound"),
            "confidence_high": first.get("upper_bound"),
            "forecast_steps": len(steps),
            "model_id": str(document.get("model_id")) if document.get("model_id") else None,
            "model_name": document.get("model_name"),
            "generated_at": _iso(document.get("generated_at")),
            "horizon_days": document.get("horizon_days"),
        })
    return {
        "available": bool(items),
        "items": items,
        "prediction_count": _count(predictions),
        "reason": None if items else "No prediction documents could be read.",
        "note": "Latest stored forecast per region/target/location.",
    }


def pipeline_status() -> Dict[str, Any]:
    by_status = status_breakdown(processing_jobs)
    jobs = processing_jobs.find({"status": "completed"},
                                sort=[("completed_at", -1)], limit=100)
    durations: List[float] = []
    for job in jobs:
        started = parse_moment(job.get("started_at") or job.get("submitted_at"))
        finished = parse_moment(job.get("completed_at"))
        if started and finished and finished >= started:
            durations.append((finished - started).total_seconds())
    recent_ingestion = ingestion_jobs.find(
        {"status": {"$in": ["completed", "completed_with_errors", "failed"]}},
        sort=[("completed_at", -1)], limit=25)
    ingestion_durations: List[float] = []
    for job in recent_ingestion:
        started = parse_moment(job.get("started_at"))
        finished = parse_moment(job.get("completed_at"))
        if started and finished and finished >= started:
            ingestion_durations.append((finished - started).total_seconds())

    breakdown = status_breakdown(processing_jobs, field="processing_mode")
    if not breakdown:
        breakdown = {"unspecified": 0}
    return {
        "running": int(by_status.get("running", 0)),
        "queued": int(by_status.get("queued", 0)),
        "completed": int(by_status.get("completed", 0)),
        "failed": int(by_status.get("failed", 0)),
        "by_status": by_status,
        "last_duration_seconds": analytics._round(durations[0], 2) if durations else None,
        "average_duration_seconds": analytics._round(statistics.fmean(durations), 2)
        if durations else None,
        "completed_jobs_sampled": len(jobs),
        "processing_mode_breakdown": breakdown,
        "ingestion_running": int(status_breakdown(ingestion_jobs).get("running", 0)),
        "ingestion_last_duration_seconds": analytics._round(ingestion_durations[0], 2)
        if ingestion_durations else None,
        "reason": None if (by_status or durations) else (
            "No processing jobs have been submitted yet, so pipeline duration and mode statistics "
            "are unknown rather than zero."),
    }


def quick_actions(role: str, *, name: str = "", has_data: bool = True) -> List[Dict[str, Any]]:
    viewer = [
        {"id": "import-data-file", "label": "Import a data file (CSV, Excel, ODS, Parquet, JSON)",
         "route": "/datasets/upload", "requires": "viewer", "enabled": True},
        {"id": "open-climate-explorer", "label": "Open the climate explorer",
         "route": "/climate/explorer", "requires": "viewer",
         "enabled": has_data,
         "disabled_reason": None if has_data else "No observations are stored yet."},
        {"id": "open-map", "label": "View the live observation map", "route": "/climate/map",
         "requires": "viewer", "enabled": has_data,
         "disabled_reason": None if has_data else "The map needs at least one observation."},
        {"id": "submit-ticket", "label": "Ask the platform team a question",
         "route": "/support/new", "requires": "viewer", "enabled": True},
    ]
    analyst = [
        {"id": "run-anomaly-scan", "label": "Run an anomaly scan and persist alerts",
         "route": "/climate/anomalies", "requires": "climate_analyst", "enabled": has_data,
         "disabled_reason": None if has_data else "Detection needs stored observations."},
        {"id": "recompute-aggregates", "label": "Recompute the stored aggregates",
         "route": "/analytics", "requires": "climate_analyst", "enabled": has_data},
        {"id": "export-observations", "label": "Export filtered observations (CSV)",
         "route": "/climate/observations", "requires": "climate_analyst", "enabled": has_data},
        {"id": "save-analysis", "label": "Save the current analysis view",
         "route": "/climate/explorer", "requires": "climate_analyst", "enabled": True},
    ]
    administrator = [
        {"id": "run-ingestion", "label": "Run an ingestion job", "route": "/ingestion",
         "requires": "administrator", "enabled": True},
        {"id": "upload-dataset", "label": "Upload a dataset", "route": "/datasets/upload",
         "requires": "administrator", "enabled": True},
        {"id": "train-model", "label": "Train a forecast model", "route": "/models",
         "requires": "administrator", "enabled": True},
        {"id": "view-audit", "label": "Review the audit trail", "route": "/system/audit",
         "requires": "administrator", "enabled": True},
    ]
    if role == "administrator":
        return administrator + analyst + viewer
    if role == "climate_analyst":
        return analyst + viewer
    return viewer


WIDGET_CATALOGUE: List[Dict[str, Any]] = [
    {"id": "kpi-tiles", "title": "Platform totals", "kind": "stat", "endpoint": "/dashboard/summary",
     "field": "totals", "requires": "none", "role": "viewer"},
    {"id": "temperature-trend", "title": "Temperature trend", "kind": "line-chart",
     "endpoint": "/climate/trends", "requires": "observations", "role": "viewer",
     "params": {"metric": "temperature_c", "frequency": "monthly"}},
    {"id": "precipitation-trend", "title": "Precipitation trend", "kind": "bar-chart",
     "endpoint": "/climate/trends", "requires": "observations", "role": "viewer",
     "params": {"metric": "precipitation_mm", "frequency": "monthly"}},
    {"id": "observation-map", "title": "Live observation map", "kind": "map",
     "endpoint": "/climate/map-data", "requires": "observations", "role": "viewer"},
    {"id": "regional-comparison", "title": "Regional comparison", "kind": "bar-chart",
     "endpoint": "/climate/comparison", "requires": "observations", "role": "viewer"},
    {"id": "correlation-matrix", "title": "Correlation matrix", "kind": "heatmap",
     "endpoint": "/climate/correlations", "requires": "two-metrics", "role": "viewer"},
    {"id": "anomaly-feed", "title": "Recent anomalies", "kind": "table",
     "endpoint": "/climate/anomalies", "requires": "observations", "role": "viewer"},
    {"id": "data-quality", "title": "Data quality and freshness", "kind": "table",
     "endpoint": "/climate/quality", "requires": "observations", "role": "viewer"},
    {"id": "pipeline-status", "title": "Processing pipeline", "kind": "stat",
     "endpoint": "/dashboard/summary", "field": "pipeline_status", "requires": "processing-jobs",
     "role": "viewer"},
    {"id": "forecast-summaries", "title": "Latest forecasts", "kind": "table",
     "endpoint": "/dashboard/summary", "field": "forecast_summaries", "requires": "predictions",
     "role": "viewer"},
    {"id": "activity-feed", "title": "Activity feed", "kind": "timeline",
     "endpoint": "/dashboard/activity", "requires": "audit-or-jobs", "role": "viewer"},
    {"id": "stations", "title": "Monitored stations", "kind": "table",
     "endpoint": "/climate/stations", "requires": "none", "role": "viewer"},
    {"id": "saved-analyses", "title": "Saved analyses", "kind": "list",
     "endpoint": "/climate/saved", "requires": "none", "role": "climate_analyst"},
    {"id": "export-panel", "title": "CSV export", "kind": "action",
     "endpoint": "/climate/export", "requires": "observations", "role": "climate_analyst"},
]


def widget_catalogue(user_id: str, *, role: str) -> Dict[str, Any]:
    facts = {
        "observations": _count(climate_observations),
        "predictions": _count(predictions),
        "alerts": _count(alerts),
        "audit-or-jobs": _count(audit_logs) + _count(processing_jobs) + _count(ingestion_jobs),
        "processing-jobs": _count(processing_jobs),
        "none": 1,
    }
    populated = {name: _count(climate_observations, {name: {"$ne": None}})
                 for name in ("temperature_c", "precipitation_mm")}
    facts["two-metrics"] = 1 if all(populated.values()) else 0
    saved_count = saved_analyses.count({"$or": [{"owner_user_id": str(user_id)},
                                                {"is_shared": True}]})
    facts["saved-analyses"] = saved_count
    widgets = []
    for widget in WIDGET_CATALOGUE:
        requirement = widget.get("requires", "none")
        count = int(facts.get(requirement, 0) or 0)
        computable = count > 0
        empty_reason = None if computable else _widget_reason(requirement)
        widgets.append({
            **widget,
            "computable": computable,
            "underlying_count": count,
            "empty_state": None if computable else {
                "status": "needs_data",
                "reason": empty_reason,
                "next_step": _widget_next_step(role),
            },
            "role_allowed": _role_allows(role, widget.get("role", "viewer")),
        })
    return {
        "widgets": widgets,
        "computable_count": sum(1 for item in widgets if item["computable"]),
        "total_count": len(widgets),
        "data_facts": facts,
        "generated_at": utcnow().isoformat(),
    }


def _widget_reason(requirement: str) -> str:
    return {
        "observations": "No observation documents are stored yet.",
        "two-metrics": "At least two metrics need populated values before a correlation exists.",
        "predictions": "The predictions collection is empty - no model has produced a forecast.",
        "processing-jobs": "No processing jobs have been submitted.",
        "audit-or-jobs": "No audit events or job records exist yet.",
    }.get(requirement, "Required data is not available yet.")


def _widget_next_step(role: str) -> str:
    if role in ("administrator", "climate_analyst"):
        return "Ingest a dataset, then this widget computes itself."
    return "Ask an analyst or administrator to ingest data; this widget needs stored observations."


def _role_allows(role: str, required: str) -> bool:
    order = {"viewer": 0, "climate_analyst": 1, "administrator": 2}
    return order.get(role, 0) >= order.get(required, 0)


ACTIVITY_KINDS = ("audit", "ingestion", "processing")


def activity_stream(*, page: int, page_size: int, kinds: Sequence[str],
                    outcome: Optional[str] = None, action: Optional[str] = None,
                    since: Optional[str] = None) -> Dict[str, Any]:
    """Merged audit + job activity with real totals and Python-side ordering."""
    wanted = [kind for kind in (kinds or ACTIVITY_KINDS) if kind in ACTIVITY_KINDS] or \
        list(ACTIVITY_KINDS)
    since_moment = parse_moment(since)
    fetch = min(page * page_size, 500)

    sources: List[Tuple[str, List[Dict[str, Any]]]] = []
    total = 0
    if "audit" in wanted:
        criteria: Dict[str, Any] = {}
        if outcome:
            criteria["outcome"] = outcome
        if action:
            criteria["action"] = {"$regex": escape_regex(action), "$options": "i"}
        if since_moment:
            criteria["created_at"] = {"$gte": since_moment}
        documents = audit_logs.find(criteria, sort=[("created_at", -1)], limit=fetch)
        total += _count(audit_logs, criteria)
        sources.append(("audit", [_audit_event(document) for document in documents]))
    if "ingestion" in wanted:
        criteria = _job_criteria(since_moment, action)
        documents = ingestion_jobs.find(criteria, sort=[("started_at", -1), ("created_at", -1)],
                                        limit=fetch)
        total += _count(ingestion_jobs, criteria)
        sources.append(("ingestion", [_job_event(document, "ingestion") for document in documents]))
    if "processing" in wanted:
        criteria = _job_criteria(since_moment, action)
        documents = processing_jobs.find(criteria, sort=[("submitted_at", -1), ("created_at", -1)],
                                         limit=fetch)
        total += _count(processing_jobs, criteria)
        sources.append(("processing", [_job_event(document, "processing")
                                       for document in documents]))

    merged: List[Dict[str, Any]] = [event for _, events in sources for event in events]
    merged.sort(key=lambda item: item["_sort"], reverse=True)
    start_index = (page - 1) * page_size
    window = merged[start_index:start_index + page_size]
    pages = (total + page_size - 1) // page_size if total else 0
    return {
        "items": window,
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": page > 1,
        "sources": {name: len(events) for name, events in sources},
        "note": None if merged else (
            "No audit events or job records exist yet - the activity feed fills itself once "
            "ingestion, processing or administration activity is performed."),
        "generated_at": utcnow().isoformat(),
    }


def _job_criteria(since_moment: Optional[datetime], action: Optional[str]) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if since_moment:
        criteria["$or"] = [
            {"started_at": {"$gte": since_moment}},
            {"submitted_at": {"$gte": since_moment}},
            {"completed_at": {"$gte": since_moment}},
        ]
    if action:
        criteria["workflow"] = {"$regex": escape_regex(action), "$options": "i"}
    return criteria


def _audit_event(document: Dict[str, Any]) -> Dict[str, Any]:
    moment = parse_moment(document.get("created_at")) or utcnow()
    return {
        "id": str(document.get("_id")),
        "kind": "audit",
        "action": document.get("action"),
        "resource_type": document.get("resource_type"),
        "resource_id": str(document["resource_id"]) if document.get("resource_id") else None,
        "outcome": document.get("outcome"),
        "actor_user_id": str(document["actor_user_id"]) if document.get("actor_user_id") else None,
        "actor_role": document.get("actor_role"),
        "request_id": document.get("request_id"),
        "at": moment.isoformat(),
        "metadata": document.get("metadata") or {},
        "_sort": moment,
    }


def _job_event(document: Dict[str, Any], kind: str) -> Dict[str, Any]:
    moment = (parse_moment(document.get("completed_at"))
              or parse_moment(document.get("started_at"))
              or parse_moment(document.get("submitted_at"))
              or parse_moment(document.get("created_at")) or utcnow())
    duration = None
    started = (parse_moment(document.get("started_at"))
               or parse_moment(document.get("submitted_at")))
    finished = parse_moment(document.get("completed_at"))
    if started and finished and finished >= started:
        duration = round((finished - started).total_seconds(), 2)
    return {
        "id": str(document.get("_id")),
        "kind": kind,
        "action": f"{kind}_job.{document.get('status') or 'unknown'}",
        "resource_type": kind + "_job",
        "resource_id": str(document.get("_id")),
        "outcome": _job_outcome(document.get("status")),
        "status": document.get("status"),
        "label": document.get("source_name") or document.get("name") or document.get("workflow"),
        "records_accepted": document.get("records_accepted"),
        "records_rejected": document.get("records_rejected"),
        "duration_seconds": duration,
        "at": moment.isoformat(),
        "_sort": moment,
    }


def _job_outcome(status: Any) -> str:
    text = str(status or "")
    if text in ("completed", "completed_with_errors", "succeeded"):
        return "success" if text == "completed" else "partial"
    if text in ("failed", "cancelled"):
        return "failure"
    if text in ("running", "queued"):
        return "in_progress"
    return "unknown"


def _count(repository: repositories.Repository, criteria: Optional[Dict[str, Any]] = None) -> int:
    try:
        return repository.count(criteria or {})
    except repositories.RepositoryError as exc:
        logger.warning("Count failed on %s: %s", repository.collection_name, exc)
        return 0


def tally(values: Iterable[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return out


def _first_value(values: Any) -> Optional[str]:
    if isinstance(values, (list, tuple)):
        for item in values:
            if item not in (None, ""):
                return str(item)
        return None
    if values in (None, ""):
        return None
    return str(values)


def _coerce_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


__all__ = [
    "ANOMALY_DIRECTIONS",
    "ANOMALY_METHODS",
    "DEFAULT_MAP_METRICS",
    "FREQUENCIES",
    "GROUP_BY_OPTIONS",
    "MAX_ALERTS_PERSISTED",
    "MAX_EXPORT_ROWS",
    "MAX_PAGE_SIZE",
    "OBSERVATION_SORTS",
    "QUALITY_OPTIONS",
    "analytics_matrix",
    "build_filters",
    "coerce_dataset_ids",
    "comparison_payload",
    "correlation_payload",
    "country_options",
    "create_saved_analysis",
    "dashboard_summary",
    "date_filter",
    "default_window",
    "delete_saved_analysis",
    "export_plan",
    "export_stream",
    "filter_options",
    "first_validation_message",
    "get_saved_analysis",
    "history_payload",
    "list_saved_analyses",
    "list_summaries",
    "location_options",
    "map_data",
    "matching_row_count",
    "metric_catalogue",
    "metric_projection",
    "monthly_totals",
    "observation_list",
    "on_the_fly_anomalies",
    "persist_anomaly_scan",
    "quality_report",
    "quick_actions",
    "recompute_core_aggregates",
    "region_codes_present",
    "region_summary",
    "regional_aggregates",
    "regions_list",
    "station_aggregates",
    "stations",
    "status_breakdown",
    "stored_regions",
    "strip_limit",
    "summary_by_key",
    "trend_payload",
    "trend_slope_report",
    "update_saved_analysis",
    "validated_body",
    "widget_catalogue",
    "yearly_region_normals",
]
