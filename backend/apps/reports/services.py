"""Report generation over real MongoDB documents - no placeholder numbers.

Each report type is an aggregation question:

===========================  =====================================================
``climate_trends``           monthly stats per region from ``climate_observations``
``regional_comparison``      per-region stats for one variable, spread vs global
``dataset_quality``          quality-status mix, coverage and completeness per dataset
``anomalies``                z-score outliers derived from real observation statistics
``forecast_results``         stored ``predictions`` plus ``model_registry`` metadata
``processing_performance``   ``processing_jobs`` durations, outcomes and queue depth
``ingestion_history``        ``ingestion_jobs`` outcomes and record counts
===========================  =====================================================

Guarantees enforced here:

* Every figure is aggregated from stored documents. ``$match`` carries the caller's
  filters, pipelines are bounded by ``max_time_ms`` plus a documented ``$limit``, and
  ``MAX_SOURCE_DOCS`` caps the documents a single report may read.
* Where a report type reads a collection owned by another app (``predictions``,
  ``ingestion_jobs``) the documents are fetched inside the same bounded window and
  aggregated in Python, so an unrecognised field layout yields "no readable value"
  instead of a fabricated number.
* If there is no underlying data the report completes with ``empty: true`` and the real
  reason (empty collection, filter matched nothing, insufficient statistics).
* Generation runs through ``common.tasks`` (job documents live in ``processing_jobs``),
  so queued -> running -> completed/failed is visible on the job record, which the report
  document links through ``job_id`` / ``job_ref``.
"""

from __future__ import annotations

import math
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, get_args

from pymongo.errors import OperationFailure, PyMongoError

from apps.authentication.permissions import role_allows
from apps.reports import renderers
from common import tasks
from common.db import aggregate, collection, find, find_one, insert_one, now_utc, oid, update_one
from common.env import ENV, report_root
from common.exceptions import Forbidden, NotFound, ServiceUnavailableConfig, ValidationError
from common.security import sanitize_text
from common.serialization import document_to_api, parse_datetime
from common.validators import Variable

MAX_SOURCE_DOCS = ENV.integer("REPORT_MAX_SOURCE_DOCS", 100_000)
MAX_ROWS = ENV.integer("REPORT_MAX_ROWS", 500)
MAX_TIME_MS = ENV.integer("REPORT_MAX_TIME_MS", 15_000)
ANOMALY_SCOPES = ENV.integer("REPORT_ANOMALY_SCOPES", 20)
ANOMALY_MIN_SAMPLES = ENV.integer("REPORT_ANOMALY_MIN_SAMPLES", 30)

REPORT_TYPES = ("climate_trends", "regional_comparison", "dataset_quality", "anomalies",
                "forecast_results", "processing_performance", "ingestion_history")
ALLOWED_VARIABLES = frozenset(str(item) for item in get_args(Variable))
FORMATS = ("csv", "pdf", "json")
STATUSES = ("queued", "running", "completed", "failed")
MEDIA_TYPES = {"csv": "text/csv", "pdf": "application/pdf", "json": "application/json"}

LIMITS = {
    "max_source_documents": MAX_SOURCE_DOCS,
    "max_rows_per_section": MAX_ROWS,
    "max_time_ms": MAX_TIME_MS,
    "pdf_rows_per_table": renderers.MAX_PDF_ROWS,
}


# --------------------------------------------------------------------------- filters

_FILTERS_SPEC = {
    "region": str, "variable": str, "dataset_id": str, "station_id": str,
    "date_from": str, "date_to": str, "granularity": str, "provider": str,
    "status": str, "model_ref": str, "sigma": float, "window_days": int,
    "max_regions": int,
}


def normalize_filters(raw: Any) -> dict[str, Any]:
    """Whitelist + coerce the caller's filter dict. Unknown keys are rejected, not ignored."""
    source = raw if isinstance(raw, dict) else {}
    clean: dict[str, Any] = {}
    for key, value in source.items():
        if key not in _FILTERS_SPEC:
            raise ValidationError(f"Unsupported report filter '{key}'. Allowed: "
                                  f"{', '.join(sorted(_FILTERS_SPEC))}.")
        kind = _FILTERS_SPEC[key]
        if value in (None, ""):
            continue
        if kind is str:
            clean[key] = sanitize_text(value, max_length=80)
        elif kind is float:
            clean[key] = max(1.0, min(float(value), 12.0))
        else:
            clean[key] = int(value)
    if "variable" in clean and clean["variable"] not in ALLOWED_VARIABLES:
        raise ValidationError(f"Unknown variable '{clean['variable']}'. Allowed: "
                              f"{', '.join(sorted(ALLOWED_VARIABLES))}.")
    if "formats" in source:
        raise ValidationError("'formats' belongs in the request body, not in filters.")
    return clean


def _bounds(filters: dict[str, Any], now: datetime, default_days: int) -> tuple[datetime, datetime]:
    since = parse_datetime(filters.get("date_from")) or (now - timedelta(days=default_days))
    until = parse_datetime(filters.get("date_to")) or now
    if since > until:
        raise ValidationError("filters.date_from must not be after filters.date_to.")
    return since, until


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return None


def _round(value: Any, places: int = 4) -> Any:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, places)


def percentile(values: list[float], fraction: float) -> Optional[float]:
    """Linear-interpolated percentile over the real durations found in the window."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def columns(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in pairs]


def section(key: str, title: str, cols: list[dict[str, str]], rows: list[dict[str, Any]],
            *, reason: str | None = None, notes: Iterable[str] = (),
            row_limit: int = MAX_ROWS) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "truncated": len(rows) >= row_limit,
        "row_limit": row_limit,
        "reason": reason,
        "notes": [note for note in notes if note],
    }


def _empty_reason_for(collection_name: str, filters: dict[str, Any]) -> str:
    scope = []
    if filters.get("region"):
        scope.append(f"region '{filters['region']}'")
    if filters.get("dataset_id"):
        scope.append(f"dataset '{filters['dataset_id']}'")
    if filters.get("variable"):
        scope.append(f"variable '{filters['variable']}'")
    where = f" for {', '.join(scope)}" if scope else ""
    return f"no documents in {collection_name}{where} inside the requested date range"


# ------------------------------------------------------------------ observation match


def _observation_match(filters: dict[str, Any], variable: str, since: datetime,
                       until: datetime, *, require_value: bool = True) -> dict[str, Any]:
    """Indexed window first (``timestamp``), then the caller's scope filters.

    Alternatives (for example a station id that may live in ``location`` or ``source``)
    are collected under ``$and`` so callers can add their own ``$or`` without clobbering
    one another.
    """
    match: dict[str, Any] = {"timestamp": {"$gte": since, "$lte": until}}
    clauses: list[dict[str, Any]] = []
    if require_value:
        match[f"metrics.{variable}.value"] = {"$type": ["double", "int", "decimal"]}
    if filters.get("region"):
        match["location.region"] = filters["region"]
    if filters.get("dataset_id"):
        match["dataset_id"] = filters["dataset_id"]
    if filters.get("granularity"):
        match["granularity"] = filters["granularity"]
    if filters.get("provider"):
        match["source.provider"] = filters["provider"]
    if filters.get("station_id"):
        clauses.append({"$or": [{"location.station_id": filters["station_id"]},
                                {"source.station_id": filters["station_id"]}]})
    if clauses:
        match["$and"] = clauses
    return match


def _stats_pipeline(filters: dict[str, Any], variable: str, since: datetime, until: datetime,
                    group_key: dict[str, Any]) -> list[dict]:
    match = _observation_match(filters, variable, since, until)
    return [
        {"$match": match},
        {"$limit": MAX_SOURCE_DOCS},
        {"$project": {
            "scope": group_key,
            "region": {"$ifNull": ["$location.region", "unspecified"]},
            "value": f"$metrics.{variable}.value",
            "unit": {"$ifNull": [f"$metrics.{variable}.unit", "unknown"]},
            "timestamp": 1,
        }},
        {"$group": {
            "_id": {"scope": "$scope", "unit": "$unit"},
            "samples": {"$sum": 1},
            "mean": {"$avg": "$value"},
            "minimum": {"$min": "$value"},
            "maximum": {"$max": "$value"},
            "stddev": {"$stdDevPop": "$value"},
            "first_seen": {"$min": "$timestamp"},
            "last_seen": {"$max": "$timestamp"},
        }},
        {"$sort": {"samples": -1}},
        {"$limit": MAX_ROWS},
    ]


def _stats_rows(rows: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        key = row.get("_id") or {}
        out.append({
            "scope": key.get("scope"), "unit": key.get("unit"),
            "samples": row.get("samples"), "mean": _round(row.get("mean")),
            "minimum": _round(row.get("minimum")), "maximum": _round(row.get("maximum")),
            "stddev": _round(row.get("stddev")),
            "first_seen": _iso(row.get("first_seen")), "last_seen": _iso(row.get("last_seen")),
        })
    return out


# --------------------------------------------------------------------- report builders

_STATS_COLS = columns(
    ("scope", "Scope"), ("unit", "Unit"), ("samples", "Samples"), ("mean", "Mean"),
    ("minimum", "Min"), ("maximum", "Max"), ("stddev", "Std dev"),
    ("first_seen", "First seen"), ("last_seen", "Last seen"),
)


def _climate_trends(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    variable = filters.get("variable", "temperature")
    since, until = _bounds(filters, now, default_days=365)
    period_key = {"$dateToString": {"format": "%Y-%m", "date": "$timestamp", "timezone": "UTC"}}
    match = _observation_match(filters, variable, since, until)
    pipeline = [
        {"$match": match},
        {"$limit": MAX_SOURCE_DOCS},
        {"$project": {
            "period": period_key,
            "region": {"$ifNull": ["$location.region", "unspecified"]},
            "value": f"$metrics.{variable}.value",
            "unit": {"$ifNull": [f"$metrics.{variable}.unit", "unknown"]},
        }},
        {"$group": {
            "_id": {"period": "$period", "region": "$region", "unit": "$unit"},
            "samples": {"$sum": 1}, "mean": {"$avg": "$value"}, "minimum": {"$min": "$value"},
            "maximum": {"$max": "$value"}, "stddev": {"$stdDevPop": "$value"},
        }},
        {"$sort": {"_id.period": -1, "samples": -1}},
        {"$limit": MAX_ROWS},
    ]
    rows = aggregate("climate_observations", pipeline, max_time_ms=MAX_TIME_MS)
    monthly = [{
        "period": (r.get("_id") or {}).get("period"), "region": (r.get("_id") or {}).get("region"),
        "unit": (r.get("_id") or {}).get("unit"), "samples": r.get("samples"),
        "mean": _round(r.get("mean")), "minimum": _round(r.get("minimum")),
        "maximum": _round(r.get("maximum")), "stddev": _round(r.get("stddev")),
    } for r in rows]

    overall = aggregate("climate_observations", _stats_pipeline(
        filters, variable, since, until, {"$literal": "all regions"}), max_time_ms=MAX_TIME_MS)
    sections = [
        section("monthly_stats", f"{variable} by month", columns(
            ("period", "Month (UTC)"), ("region", "Region"), ("unit", "Unit"),
            ("samples", "Samples"), ("mean", "Mean"), ("minimum", "Min"),
            ("maximum", "Max"), ("stddev", "Std dev")), monthly,
            reason=None if monthly else _empty_reason_for("climate_observations", filters)),
        section("scope_stats", f"{variable} overall by scope", _STATS_COLS, _stats_rows(overall)),
    ]
    return {
        "sections": sections,
        "data_source": f"climate_observations (variable={variable}, "
                       f"provider={filters.get('provider', 'all')})",
        "observed_range": _observed_range(overall),
        "units": {variable: sorted({str(row['unit']) for row in monthly if row.get('unit')}) or
                              sorted({str(row['unit']) for row in _stats_rows(overall) if row.get('unit')})},
        "metrics": {"periods": len(monthly), "scope_groups": len(overall)},
        "empty": not monthly and not overall,
        "reason": None if (monthly or overall) else _empty_reason_for("climate_observations", filters),
    }


def _observed_range(stat_rows: list[dict]) -> dict[str, Any]:
    stamps = [row.get("first_seen") for row in _stats_rows_of(stat_rows) if row.get("first_seen")]
    ends = [row.get("last_seen") for row in _stats_rows_of(stat_rows) if row.get("last_seen")]
    return {"observed_from": min(stamps) if stamps else None,
            "observed_to": max(ends) if ends else None}


def _stats_rows_of(rows: list[dict]) -> list[dict[str, Any]]:
    """Normalise raw ``$group`` output or already-mapped rows for range derivation."""
    prepared: list[dict[str, Any]] = []
    for row in rows:
        if "first_seen" in row and isinstance(row.get("first_seen"), str):
            prepared.append(row)
            continue
        prepared.append({"first_seen": _iso(row.get("first_seen")), "last_seen": _iso(row.get("last_seen"))})
    return prepared


def _regional_comparison(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    variable = filters.get("variable", "temperature")
    since, until = _bounds(filters, now, default_days=365)
    rows = aggregate("climate_observations", _stats_pipeline(
        filters, variable, since, until, "$location.region"), max_time_ms=MAX_TIME_MS)
    mapped = _stats_rows(rows)
    totals = [row for row in mapped if row.get("samples")]
    global_mean = None
    if totals:
        weighted = sum((row["mean"] or 0) * row["samples"] for row in totals if row.get("mean") is not None)
        samples = sum(row["samples"] for row in totals)
        global_mean = round(weighted / samples, 4) if samples else None
    notes: list[str] = []
    if len(mapped) < 2:
        notes.append("fewer than two regions carry data in this window, so the comparison is "
                     "a single-region picture rather than a contrast")
    for row in mapped:
        row["delta_vs_global"] = (_round((row["mean"] or 0) - global_mean)
                                  if row.get("mean") is not None and global_mean is not None else None)
        row.pop("first_seen", None)
        row.pop("last_seen", None)
    cols = columns(("scope", "Region"), ("unit", "Unit"), ("samples", "Samples"), ("mean", "Mean"),
                   ("minimum", "Min"), ("maximum", "Max"), ("stddev", "Std dev"),
                   ("delta_vs_global", "Delta vs global mean"))
    sections = [section("regional_stats", f"{variable} by region", cols, mapped,
                        reason=None if mapped else _empty_reason_for("climate_observations", filters),
                        notes=notes)]
    return {
        "sections": sections,
        "data_source": f"climate_observations (variable={variable}, grouped by location.region)",
        "observed_range": _observed_range(rows),
        "units": {variable: sorted({str(row.get("unit")) for row in mapped if row.get("unit")})},
        "metrics": {"regions": len(mapped), "global_mean": global_mean},
        "empty": not mapped,
        "reason": None if mapped else _empty_reason_for("climate_observations", filters),
    }


_QUALITY_COLS = columns(
    ("dataset_id", "Dataset"), ("dataset_name", "Name"), ("source_name", "Source"),
    ("status", "Dataset status"), ("total", "Observations"), ("valid", "Valid"),
    ("suspect", "Suspect"), ("rejected", "Rejected"), ("unclassified", "Unclassified"),
    ("coverage_from", "Coverage from"), ("coverage_to", "Coverage to"),
    ("regions", "Regions"), ("completeness_pct", "Metric completeness %"),
)


def _dataset_quality(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    variable = filters.get("variable")
    since, until = _bounds(filters, now, default_days=365)
    match: dict[str, Any] = {"timestamp": {"$gte": since, "$lte": until}}
    if filters.get("dataset_id"):
        match["dataset_id"] = filters["dataset_id"]
    if filters.get("provider"):
        match["source.provider"] = filters["provider"]

    projection: dict[str, Any] = {
        "dataset_id": 1,
        "timestamp": 1,
        "region": "$location.region",
        "quality": {"$ifNull": ["$quality.status", "unclassified"]},
    }
    if variable:
        projection["has_metric"] = {"$isNumber": f"$metrics.{variable}.value"}
    group_stage: dict[str, Any] = {
        "_id": "$dataset_id",
        "total": {"$sum": 1},
        "valid": {"$sum": {"$cond": [{"$eq": ["$quality", "valid"]}, 1, 0]}},
        "suspect": {"$sum": {"$cond": [{"$eq": ["$quality", "suspect"]}, 1, 0]}},
        "rejected": {"$sum": {"$cond": [{"$eq": ["$quality", "rejected"]}, 1, 0]}},
        "unclassified": {"$sum": {"$cond": [{"$eq": ["$quality", "unclassified"]}, 1, 0]}},
        "coverage_from": {"$min": "$timestamp"},
        "coverage_to": {"$max": "$timestamp"},
        "regions": {"$addToSet": "$region"},
    }
    if variable:
        group_stage["with_metric"] = {"$sum": {"$cond": [{"$eq": ["$has_metric", True]}, 1, 0]}}

    pipeline = [
        {"$match": match},
        {"$limit": MAX_SOURCE_DOCS},
        {"$project": projection},
        {"$group": group_stage},
        {"$sort": {"total": -1}},
        {"$limit": MAX_ROWS},
    ]
    rows = aggregate("climate_observations", pipeline, max_time_ms=MAX_TIME_MS)

    dataset_names: dict[str, dict[str, Any]] = {}
    for dataset in find("datasets", {}, projection={"name": 1, "source_name": 1, "status": 1,
                                                    "source_type": 1}, limit=MAX_ROWS):
        dataset_names[str(dataset.get("_id"))] = dataset

    notes = [
        "completeness % = valid documents / total documents",
        "quality.status is written by the ingestion validator; 'unclassified' means the "
        "document carries no quality block at all",
    ]
    if variable:
        notes.insert(1, f"metric presence for '{variable}' is counted separately in the notes below")

    mapped: list[dict[str, Any]] = []
    for row in rows:
        dataset_id = str(row.get("_id") or "unknown")
        meta = dataset_names.get(dataset_id, {})
        regions = [str(item) for item in (row.get("regions") or []) if item][:20]
        total = row.get("total") or 0
        mapped.append({
            "dataset_id": dataset_id,
            "dataset_name": meta.get("name"),
            "source_name": meta.get("source_name"),
            "status": meta.get("status"),
            "total": total,
            "valid": row.get("valid"), "suspect": row.get("suspect"),
            "rejected": row.get("rejected"), "unclassified": row.get("unclassified"),
            "coverage_from": _iso(row.get("coverage_from")), "coverage_to": _iso(row.get("coverage_to")),
            "regions": regions,
            "completeness_pct": _round(100.0 * (row.get("valid") or 0) / total, 2) if total else None,
            "variable_presence_pct": (_round(100.0 * (row.get("with_metric") or 0) / total, 2)
                                      if variable and total else None),
        })
    if variable:
        notes.append(f"variable_presence_pct is the share of documents that carry "
                     f"metrics.{variable}.value")
    cols = _QUALITY_COLS + ([columns(("variable_presence_pct", f"{variable} presence %"))[0]]
                            if variable else [])
    sections = [
        section("dataset_quality", "Quality mix and coverage per dataset", cols, mapped,
                reason=None if mapped else _empty_reason_for("climate_observations", filters),
                notes=notes),
    ]
    known_datasets = collection("datasets").estimated_document_count()
    return {
        "sections": sections,
        "data_source": "climate_observations grouped by dataset_id, joined to the datasets registry",
        "observed_range": {"observed_from": min((row["coverage_from"] for row in mapped
                                                 if row["coverage_from"]), default=None),
                           "observed_to": max((row["coverage_to"] for row in mapped
                                               if row["coverage_to"]), default=None)},
        "units": {},
        "metrics": {"datasets_with_observations": len(mapped), "datasets_registered": known_datasets},
        "empty": not mapped,
        "reason": None if mapped else _empty_reason_for("climate_observations", filters),
    }


def _anomalies(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    variable = filters.get("variable", "temperature")
    sigma = float(filters.get("sigma", 3.0))
    window_days = int(filters.get("window_days", 365))
    since, until = _bounds(filters, now, default_days=window_days)
    stats = aggregate("climate_observations", _stats_pipeline(
        filters, variable, since, until,
        {"$ifNull": ["$location.region", "$dataset_id"]}), max_time_ms=MAX_TIME_MS)

    outliers: list[dict[str, Any]] = []
    skipped: list[str] = []
    examined = 0
    for row in stats:
        if examined >= ANOMALY_SCOPES:
            skipped.append(f"stopped after {ANOMALY_SCOPES} scopes (REPORT_ANOMALY_SCOPES)")
            break
        key = row.get("_id") or {}
        scope, unit = key.get("scope"), key.get("unit")
        mean, stddev, samples = row.get("mean"), row.get("stddev"), row.get("samples") or 0
        if not isinstance(mean, (int, float)) or not isinstance(stddev, (int, float)) or stddev <= 0:
            skipped.append(f"scope '{scope}': insufficient statistics "
                           f"(samples={samples}, stddev={stddev})")
            continue
        if samples < ANOMALY_MIN_SAMPLES:
            skipped.append(f"scope '{scope}': only {samples} samples, "
                           f"{ANOMALY_MIN_SAMPLES} required for a z-score")
            continue
        examined += 1
        low = mean - sigma * stddev
        high = mean + sigma * stddev
        projection = {"timestamp": 1, "location.region": 1, "dataset_id": 1,
                      "dedupe_key": 1, "granularity": 1,
                      f"metrics.{variable}.value": 1, f"metrics.{variable}.unit": 1,
                      "quality.status": 1}
        candidates: list[dict[str, Any]] = []
        for comparison in ({"$gt": high}, {"$lt": low}):
            candidate_match = _observation_match(filters, variable, since, until)
            candidate_match[f"metrics.{variable}.value"] = comparison
            candidate_match.setdefault("$and", []).append(
                {"$or": [{"location.region": scope}, {"dataset_id": scope}]})
            candidates.extend(find("climate_observations", candidate_match,
                                   sort=[("timestamp", -1)], limit=50, projection=projection))
        for document in candidates:
            metrics = (document.get("metrics") or {}).get(variable) or {}
            value = metrics.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            outliers.append({
                "observation_id": str(document.get("_id")),
                "timestamp": _iso(document.get("timestamp")),
                "scope": (document.get("location") or {}).get("region") or document.get("dataset_id"),
                "value": _round(value), "unit": unit or metrics.get("unit") or "unknown",
                "mean": _round(mean), "stddev": _round(stddev),
                "z_score": _round((value - mean) / stddev, 3),
                "deviation": _round(value - mean),
                "threshold_band": f"[{_round(low, 3)}, {_round(high, 3)}]",
                "quality_status": (document.get("quality") or {}).get("status"),
                "granularity": document.get("granularity"),
            })

    outliers.sort(key=lambda item: abs(item.get("z_score") or 0), reverse=True)
    outliers = outliers[:MAX_ROWS]

    published: list[dict[str, Any]] = []
    for summary in find("analytics_summaries", {"summary_type": "anomaly_features"},
                        sort=[("computed_at", -1)], limit=50):
        published.append({
            "computed_at": _iso(summary.get("computed_at")),
            "scope": summary.get("scope_label") or summary.get("region") or summary.get("dataset_id"),
            "variable": summary.get("variable"),
            "method": summary.get("method"),
            "detail": sanitize_text(str({key: value for key, value in summary.items()
                                         if key not in ("_id",)}), max_length=600),
        })

    cols = columns(("timestamp", "Timestamp"), ("scope", "Scope"), ("value", "Value"),
                   ("unit", "Unit"), ("mean", "Scope mean"), ("stddev", "Std dev"),
                   ("z_score", "Z score"), ("deviation", "Deviation"),
                   ("threshold_band", "Accepted band"), ("quality_status", "Quality"),
                   ("granularity", "Granularity"), ("observation_id", "Observation id"))
    sections = [
        section("statistical_outliers",
                f"Observations beyond {sigma:g} standard deviations from the scope mean",
                cols, outliers,
                reason=None if outliers else (
                    _empty_reason_for("climate_observations", filters) if not stats else
                    f"no observation exceeds {sigma:g} sigma; scopes examined: {examined}"),
                notes=[*skipped[:20],
                       f"scopes examined: {examined} of {len(stats)} (cap {ANOMALY_SCOPES}; "
                       f"minimum {ANOMALY_MIN_SAMPLES} samples for a usable baseline)"]),
        section("baseline_scopes", "Per-scope statistics used as the baseline", _STATS_COLS,
                _stats_rows(stats), row_limit=MAX_ROWS),
        section("ml_anomaly_summaries", "anomaly_features summaries published by the ML app",
                columns(("computed_at", "Computed at"), ("scope", "Scope"), ("variable", "Variable"),
                        ("method", "Method"), ("detail", "Summary")),
                published,
                reason=None if published else
                "the ML app has not stored analytics_summaries documents with "
                "summary_type='anomaly_features' yet"),
    ]
    return {
        "sections": sections,
        "data_source": f"climate_observations (variable={variable}, sigma={sigma:g}, "
                       f"window_days={window_days})",
        "observed_range": _observed_range(stats),
        "units": {variable: sorted({str(row.get("unit")) for row in _stats_rows(stats)
                                    if row.get("unit")})},
        "metrics": {"scopes_with_baseline": examined, "outliers": len(outliers),
                    "sigma": sigma, "min_samples_for_baseline": ANOMALY_MIN_SAMPLES},
        "empty": not outliers and not stats,
        "reason": None if (outliers or stats) else _empty_reason_for("climate_observations", filters),
    }


def _forecast_value(doc: dict) -> tuple[Optional[float], Optional[str], Optional[str]]:
    steps = doc.get("steps") or doc.get("series") or doc.get("forecast")
    unit = doc.get("unit") or doc.get("units")
    label = str(doc.get("horizon_step") or "") or None
    if isinstance(steps, list):
        for step in reversed(steps):
            if isinstance(step, dict):
                for key in ("value", "forecast_value", "mean", "prediction"):
                    candidate = step.get(key)
                    if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                        return float(candidate), str(step.get("unit") or unit or "unknown"), (
                            str(step.get("step") or step.get("horizon") or step.get("index") or "")
                            or label)
                break
    for key in ("forecast_value", "value", "prediction", "mean"):
        candidate = doc.get(key)
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return float(candidate), str(unit or "unknown"), label
    return None, None, None


def _forecast_results(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    variable = filters.get("variable")
    match: dict[str, Any] = {}
    if variable:
        match["target_variable"] = variable
    if filters.get("region"):
        match["geographic_scope.region"] = filters["region"]
    if filters.get("model_ref"):
        match["$or"] = [{"model_ref": filters["model_ref"]},
                        {"model_version_id": filters["model_ref"]}]
    since, until = _bounds(filters, now, default_days=90)
    window: dict[str, Any] = {"$gte": since}
    if filters.get("date_from") or filters.get("date_to"):
        window = {"$gte": since, "$lte": until}
    match["generated_at"] = window
    documents = find("predictions", match, sort=[("generated_at", -1)], limit=MAX_ROWS)

    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    unreadable = 0
    for doc in documents:
        value, unit, horizon = _forecast_value(doc)
        model = str(doc.get("model_ref") or doc.get("model_version_id") or "unknown")
        region = str((doc.get("geographic_scope") or {}).get("region")
                     or doc.get("region") or "unspecified")
        target = str(doc.get("target_variable") or variable or "unspecified")
        if value is None:
            unreadable += 1
            continue
        key = (model, target, region, str(unit))
        bucket = grouped.setdefault(key, {"model": model, "target_variable": target, "region": region,
                                          "unit": unit, "values": [], "latest": None})
        bucket["values"].append(value)
        generated = _iso(doc.get("generated_at"))
        if generated and (bucket["latest"] is None or generated > bucket["latest"]):
            bucket["latest"] = generated
        rows.append({
            "prediction_id": str(doc.get("_id")), "generated_at": generated, "model": model,
            "target_variable": target, "region": region, "horizon_step": horizon,
            "value": _round(value), "unit": unit,
            "actual": _round(doc.get("actual_value")) if doc.get("actual_value") is not None else None,
        })

    summary = []
    for bucket in grouped.values():
        values = bucket["values"]
        summary.append({
            "model": bucket["model"], "target_variable": bucket["target_variable"],
            "region": bucket["region"], "unit": bucket["unit"], "forecasts": len(values),
            "mean": _round(sum(values) / len(values)) if values else None,
            "minimum": _round(min(values)) if values else None,
            "maximum": _round(max(values)) if values else None,
            "latest_generated_at": bucket["latest"],
        })
    summary.sort(key=lambda item: item["forecasts"] or 0, reverse=True)

    model_metadata: list[dict[str, Any]] = []
    for model_key in sorted({str(item["model"]) for item in summary}):
        parsed = oid(model_key)
        record = find_one("model_registry", {"_id": parsed}) if parsed else None
        if not record:
            record = find_one("model_registry", {"model_ref": model_key})
        if not record:
            record = find_one("model_registry", {"model_name": model_key})
        if record:
            model_metadata.append({
                "model_ref": model_key,
                "model_name": record.get("model_name"),
                "model_version": record.get("model_version"),
                "status": record.get("status"),
                "algorithm": record.get("algorithm") or record.get("model_type"),
                "target_variable": record.get("target_variable"),
                "granularity": record.get("granularity"),
                "trained_at": _iso(record.get("trained_at") or record.get("created_at")),
                "metrics": record.get("metrics") or record.get("evaluation") or {},
            })
        else:
            model_metadata.append({"model_ref": model_key,
                                   "note": "no model_registry document matches this reference"})

    rows.sort(key=lambda item: item.get("generated_at") or "", reverse=True)
    sections = [
        section("forecast_summary", "Stored forecasts grouped by model, variable and region",
                columns(("model", "Model"), ("target_variable", "Variable"), ("region", "Region"),
                        ("unit", "Unit"), ("forecasts", "Forecasts"), ("mean", "Mean value"),
                        ("minimum", "Min"), ("maximum", "Max"),
                        ("latest_generated_at", "Latest generated")),
                summary,
                reason=None if summary else _empty_reason_for("predictions", filters)),
        section("forecast_rows", "Most recent stored forecast documents",
                columns(("generated_at", "Generated at"), ("region", "Region"),
                        ("target_variable", "Variable"), ("model", "Model"),
                        ("horizon_step", "Horizon"), ("value", "Predicted value"), ("unit", "Unit"),
                        ("actual", "Observed actual"), ("prediction_id", "Prediction id")),
                rows[:MAX_ROWS]),
    ]
    return {
        "sections": sections,
        "data_source": "predictions joined to model_registry metadata",
        "observed_range": {"observed_from": min((row["generated_at"] for row in rows
                                                 if row["generated_at"]), default=None),
                           "observed_to": max((row["generated_at"] for row in rows
                                               if row["generated_at"]), default=None)},
        "units": {variable or "forecast": sorted({str(row.get("unit")) for row in rows
                                                  if row.get("unit")})},
        "metrics": {"prediction_documents": len(documents), "readable_values": len(rows),
                    "documents_without_a_readable_value": unreadable},
        "model_metadata": model_metadata,
        "empty": not rows,
        "reason": None if rows else _empty_reason_for("predictions", filters),
    }


_PERF_COLS = columns(
    ("kind", "Job kind"), ("runs", "Runs"), ("completed", "Completed"), ("failed", "Failed"),
    ("other", "Other statuses"), ("avg_seconds", "Avg duration s"),
    ("p50_seconds", "P50 s"), ("p95_seconds", "P95 s"), ("max_seconds", "Max s"),
    ("last_finished_at", "Last finished"),
)


def _processing_performance(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    since, until = _bounds(filters, now, default_days=90)
    match: dict[str, Any] = {"created_at": {"$gte": since, "$lte": until}}
    if filters.get("status"):
        match["status"] = filters["status"]
    duration = {
        "$cond": [
            {"$and": [{"$ne": ["$started_at", None]}, {"$ne": ["$finished_at", None]}]},
            {"$divide": [{"$subtract": ["$finished_at", "$started_at"]}, 1000]},
            None,
        ]
    }
    pipeline = [
        {"$match": match},
        {"$limit": MAX_SOURCE_DOCS},
        {"$project": {"kind": 1, "status": 1, "created_at": 1, "finished_at": 1,
                      "started_at": 1, "duration_seconds": duration, "label": 1}},
        {"$group": {
            "_id": "$kind",
            "runs": {"$sum": 1},
            "completed": {"$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}},
            "failed": {"$sum": {"$cond": [{"$eq": ["$status", "failed"]}, 1, 0]}},
            "avg_seconds": {"$avg": "$duration_seconds"},
            "max_seconds": {"$max": "$duration_seconds"},
            "last_finished_at": {"$max": "$finished_at"},
        }},
        {"$sort": {"runs": -1}},
        {"$limit": MAX_ROWS},
    ]
    try:
        rows = aggregate("processing_jobs", pipeline, max_time_ms=MAX_TIME_MS)
    except PyMongoError as exc:
        raise ServiceUnavailableConfig(
            "processing_jobs could not be aggregated for this window.",
            details=[f"{type(exc).__name__}: {str(exc)[:160]}"]) from exc

    durations = aggregate("processing_jobs", [
        {"$match": match}, {"$limit": MAX_SOURCE_DOCS},
        {"$project": {"kind": 1, "duration_seconds": duration}},
        {"$match": {"duration_seconds": {"$gt": 0}}},
        {"$limit": 5000},
    ], max_time_ms=MAX_TIME_MS)

    by_kind: dict[str, list[float]] = {}
    overall: list[float] = []
    for row in durations:
        value = row.get("duration_seconds")
        if isinstance(value, (int, float)) and value > 0:
            by_kind.setdefault(str(row.get("kind")), []).append(float(value))
            overall.append(float(value))

    mapped = []
    for row in rows:
        kind = str(row.get("_id"))
        samples = by_kind.get(kind, [])
        mapped.append({
            "kind": kind, "runs": row.get("runs"), "completed": row.get("completed"),
            "failed": row.get("failed"),
            "other": (row.get("runs") or 0) - (row.get("completed") or 0) - (row.get("failed") or 0),
            "avg_seconds": _round(row.get("avg_seconds"), 2),
            "p50_seconds": _round(percentile(samples, 0.50), 2),
            "p95_seconds": _round(percentile(samples, 0.95), 2),
            "max_seconds": _round(row.get("max_seconds"), 2),
            "last_finished_at": _iso(row.get("last_finished_at")),
        })

    queue_depth = {
        "queued": collection("processing_jobs").count_documents({"status": "queued"}),
        "running": collection("processing_jobs").count_documents({"status": "running"}),
        "succeeded": collection("processing_jobs").count_documents({"status": "completed"}),
        "failed": collection("processing_jobs").count_documents({"status": "failed"}),
    }
    finished_in_window = sum((item["completed"] or 0) for item in mapped)
    failed_in_window = sum((item["failed"] or 0) for item in mapped)
    failure_rate = (_round(100.0 * failed_in_window / (finished_in_window + failed_in_window), 2)
                    if (finished_in_window + failed_in_window) else None)
    sections = [
        section("performance_by_kind", "Background job durations by kind", _PERF_COLS, mapped,
                reason=None if mapped else _empty_reason_for("processing_jobs", filters),
                notes=["percentiles are computed from the durations of up to 5000 jobs inside the "
                       "window; avg/max come from the aggregation itself"]),
        section("overall_timing", "Whole-window timing",
                columns(("label", "Measure"), ("value", "Value")),
                [{"label": "jobs with a measurable duration", "value": len(overall)},
                 {"label": "median duration (s)", "value": _round(percentile(overall, 0.5), 2)},
                 {"label": "p90 duration (s)", "value": _round(percentile(overall, 0.9), 2)},
                 {"label": "p95 duration (s)", "value": _round(percentile(overall, 0.95), 2)},
                 {"label": "background engine", "value": tasks.engine()}] if overall else
                [{"label": "jobs with a measurable duration", "value": 0},
                 {"label": "note", "value": "no job in this window recorded both a start and a "
                                            "finish timestamp"}]),
    ]
    return {
        "sections": sections,
        "data_source": "processing_jobs (common.tasks job records)",
        "observed_range": {"observed_from": _iso(since), "observed_to": _iso(until)},
        "units": {"duration": "seconds"},
        "metrics": {"kinds": len(mapped), "queue_depth_whole_collection": queue_depth,
                    "completed_in_window": finished_in_window, "failed_in_window": failed_in_window,
                    "failure_rate_percent": failure_rate},
        "empty": not mapped,
        "reason": None if mapped else _empty_reason_for("processing_jobs", filters),
    }


def _pick(document: dict, *names: str) -> Optional[Any]:
    for name in names:
        value = document.get(name)
        if value is not None and value != "":
            return value
    return None


def _int_value(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


_INGEST_COLS = columns(
    ("day", "Day (UTC)"), ("provider", "Provider"), ("jobs", "Jobs"), ("completed", "Completed"),
    ("failed", "Failed"), ("other", "Other"), ("records_accepted", "Records accepted"),
    ("records_rejected", "Records rejected"),
)


def _ingestion_history(filters: dict[str, Any], now: datetime) -> dict[str, Any]:
    since, until = _bounds(filters, now, default_days=90)
    match: dict[str, Any] = {}
    if filters.get("status"):
        match["status"] = filters["status"]
    if filters.get("dataset_id"):
        match["dataset_id"] = filters["dataset_id"]
    documents = find("ingestion_jobs", match, sort=[("created_at", -1)], limit=MAX_ROWS,
                     projection={"job_ref": 1, "status": 1, "kind": 1, "provider": 1, "source": 1,
                                 "dataset_id": 1, "dataset_name": 1, "records_accepted": 1,
                                 "records_rejected": 1, "accepted": 1, "rejected": 1, "error": 1,
                                 "created_at": 1, "started_at": 1, "finished_at": 1,
                                 "granularity": 1, "created_by": 1})

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    recent: list[dict[str, Any]] = []
    for doc in documents:
        created = doc.get("created_at") or doc.get("started_at")
        if not isinstance(created, datetime):
            continue
        if created < since or created > until:
            continue
        provider_raw = _pick(doc, "provider", "source") or "unknown"
        provider = str(provider_raw.get("provider")) if isinstance(provider_raw, dict) else str(provider_raw)
        if filters.get("provider") and provider != filters["provider"]:
            continue
        day = created.astimezone(timezone.utc).strftime("%Y-%m-%d")
        bucket = buckets.setdefault((day, provider), {
            "day": day, "provider": provider, "jobs": 0, "completed": 0, "failed": 0, "other": 0,
            "records_accepted": 0, "records_rejected": 0})
        bucket["jobs"] += 1
        status = str(doc.get("status") or "unknown")
        if status == "completed":
            bucket["completed"] += 1
        elif status == "failed":
            bucket["failed"] += 1
        else:
            bucket["other"] += 1
        bucket["records_accepted"] += _int_value(_pick(doc, "records_accepted", "accepted")) or 0
        bucket["records_rejected"] += _int_value(_pick(doc, "records_rejected", "rejected")) or 0
        recent.append({
            "job_ref": doc.get("job_ref"), "day": day, "provider": provider, "status": status,
            "dataset_name": _pick(doc, "dataset_name"), "dataset_id": doc.get("dataset_id"),
            "records_accepted": _int_value(_pick(doc, "records_accepted", "accepted")),
            "records_rejected": _int_value(_pick(doc, "records_rejected", "rejected")),
            "created_at": _iso(created), "started_at": _iso(doc.get("started_at")),
            "finished_at": _iso(doc.get("finished_at")),
            "error": sanitize_text(doc.get("error"), max_length=200) if doc.get("error") else None,
        })

    grouped = sorted(buckets.values(), key=lambda item: (item["day"], item["provider"]),
                     reverse=True)[:MAX_ROWS]
    recent.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    note = ("ingestion job documents are read within a bounded window (up to "
            f"{MAX_ROWS} most recent jobs); the provider field is taken from 'provider' or 'source'")
    sections = [
        section("daily_ingestion", "Ingestion outcomes per day and provider", _INGEST_COLS, grouped,
                reason=None if grouped else _empty_reason_for("ingestion_jobs", filters),
                notes=[note]),
        section("recent_jobs", "Most recent ingestion jobs",
                columns(("created_at", "Created at"), ("provider", "Provider"),
                        ("dataset_name", "Dataset"), ("status", "Status"),
                        ("records_accepted", "Accepted"), ("records_rejected", "Rejected"),
                        ("started_at", "Started"), ("finished_at", "Finished"),
                        ("job_ref", "Job ref"), ("error", "Error")),
                recent[:MAX_ROWS]),
    ]
    return {
        "sections": sections,
        "data_source": "ingestion_jobs",
        "observed_range": {"observed_from": _iso(since), "observed_to": _iso(until)},
        "units": {"records": "count"},
        "metrics": {"jobs_read": len(documents), "day_provider_groups": len(grouped)},
        "empty": not grouped,
        "reason": None if grouped else _empty_reason_for("ingestion_jobs", filters),
    }


BUILDERS = {
    "climate_trends": _climate_trends,
    "regional_comparison": _regional_comparison,
    "dataset_quality": _dataset_quality,
    "anomalies": _anomalies,
    "forecast_results": _forecast_results,
    "processing_performance": _processing_performance,
    "ingestion_history": _ingestion_history,
}


# --------------------------------------------------------------------- payload + files


def build_payload(report_type: str, filters: dict[str, Any], *, title: str,
                  requested_by_label: str, generated_at: Optional[datetime] = None) -> dict[str, Any]:
    builder = BUILDERS.get(report_type)
    if builder is None:
        raise ValidationError(f"Unknown report type '{report_type}'. Allowed: "
                              f"{', '.join(REPORT_TYPES)}.")
    now = generated_at or now_utc()
    built = builder(filters, now)
    since, until = _bounds(filters, now, default_days=365)
    payload: dict[str, Any] = {
        "report_type": report_type,
        "title": title or f"{report_type.replace('_', ' ').title()} report",
        "status": "completed",
        "generated_at": now,
        "requested_by_label": requested_by_label,
        "filters": filters,
        "date_range": {
            "requested_from": _iso(since) if (filters.get("date_from")) else None,
            "requested_to": _iso(until) if (filters.get("date_to")) else None,
            **built.get("observed_range", {}),
            "note": "window defaults to the last 365 days unless filters set an explicit range",
        },
        "data_source": built["data_source"],
        "units": built.get("units") or {},
        "metrics": built.get("metrics") or {},
        "sections": built["sections"],
        "model_metadata": built.get("model_metadata") or [],
        "empty": bool(built.get("empty")),
        "reason": built.get("reason"),
        "limits": LIMITS,
    }
    return payload


def write_outputs(report_id: str, payload: dict[str, Any], formats: Iterable[str]) -> dict[str, Any]:
    root = report_root()
    folder = root / str(report_id)
    folder.mkdir(parents=True, exist_ok=True)
    stored: dict[str, Any] = {}
    for fmt in formats:
        renderer = renderers.RENDERERS.get(fmt)
        if renderer is None:
            continue
        content = renderer(payload)
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        name = f"{payload.get('report_type', 'report')}-{uuid.uuid4().hex[:12]}.{fmt}"
        (folder / name).write_bytes(raw)
        stored[fmt] = {
            "path": f"{report_id}/{name}",
            "filename": name,
            "bytes": len(raw),
            "media_type": MEDIA_TYPES.get(fmt, "application/octet-stream"),
            "written_at": now_utc(),
        }
    return stored


def _safe_relative(entry: Any) -> Path:
    relative = entry.get("path") if isinstance(entry, dict) else str(entry)
    text = sanitize_text(relative, max_length=300)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise Forbidden("The stored report path is not a safe relative path.")
    return path


def resolve_output(report: dict, fmt: str) -> tuple[Path, dict[str, Any]]:
    """Return the on-disk file for a stored export, validating against the allowlist."""
    stored = report.get("output_files") or {}
    if fmt not in stored:
        raise NotFound(f"This report has no '{fmt}' export. Formats produced: "
                       f"{', '.join(sorted(stored)) or 'none'}.")
    entry = stored[fmt]
    relative = _safe_relative(entry)
    root = report_root().resolve()
    target = (root / relative).resolve()
    if root not in target.parents and target != root:
        raise Forbidden("Refusing to serve a file outside the report storage root.")
    if not target.is_file():
        raise NotFound("The stored report file is missing; regenerate the report.")
    return target, entry if isinstance(entry, dict) else {}


# ------------------------------------------------------------------------- CRUD layer


def _requested_by_label(identity) -> str:
    return f"{getattr(identity, 'email', '') or 'unknown'} ({getattr(identity, 'role', 'user')})"


def create_report(payload: Any, identity) -> dict[str, Any]:
    """Persist the report document, then generate it through ``common.tasks``."""
    report_type = payload.report_type
    filters = normalize_filters(payload.filters)
    formats = list(dict.fromkeys(fmt for fmt in (payload.formats or ["csv"]) if fmt in FORMATS))
    if not formats:
        raise ValidationError("Pick at least one export format from csv, pdf, json.")
    now = now_utc()
    document: dict[str, Any] = {
        "report_type": report_type,
        "title": sanitize_text(payload.title, max_length=160) or f"{report_type.replace('_', ' ').title()} report",
        "filters": filters,
        "formats": formats,
        "status": "queued",
        "empty": False,
        "reason": None,
        "sections": [],
        "output_files": {},
        "metrics": {},
        "model_metadata": [],
        "data_source": None,
        "date_range": None,
        "units": {},
        "limits": LIMITS,
        "requested_by": str(identity.user_id),
        "requested_by_label": _requested_by_label(identity),
        "requested_role": identity.role,
        "job_id": None,
        "job_ref": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
    }
    document["_id"] = oid(insert_one("reports", document))
    job = tasks.submit(
        "report.generate", generate, args=(str(document["_id"]),),
        created_by=str(identity.user_id),
        payload={"report_type": report_type, "filters": filters, "formats": formats},
        label=f"report:{document['title'][:60]}",
        collection_name="processing_jobs",
    )
    update_one("reports", {"_id": document["_id"]}, {"$set": {
        "job_id": job.get("_id"), "job_ref": job.get("job_ref"), "updated_at": now_utc()}})
    document["job_id"] = job.get("_id")
    document["job_ref"] = job.get("job_ref")
    return document


def generate(report_id: str) -> dict[str, Any]:
    """Task body: aggregate, render, persist. Raises so the job record shows failures."""
    report = find_one("reports", {"_id": oid(report_id)})
    if not report:
        raise NotFound("Report document no longer exists.")
    started = now_utc()
    update_one("reports", {"_id": report["_id"]},
               {"$set": {"status": "running", "started_at": started, "updated_at": started}})
    try:
        payload = build_payload(
            report["report_type"], report.get("filters") or {},
            title=report.get("title") or "", requested_by_label=report.get("requested_by_label", ""),
            generated_at=started,
        )
        stored = write_outputs(str(report["_id"]), payload, report.get("formats") or ["csv"])
        finished = now_utc()
        update_one("reports", {"_id": report["_id"]}, {"$set": {
            "status": "completed", "empty": payload["empty"], "reason": payload["reason"],
            "sections": payload["sections"], "metrics": payload["metrics"],
            "model_metadata": payload["model_metadata"], "data_source": payload["data_source"],
            "date_range": payload["date_range"], "units": payload["units"],
            "output_files": stored, "completed_at": finished, "updated_at": finished,
            "error": None,
            "row_counts": {section["key"]: section["row_count"] for section in payload["sections"]},
        }})
        return {
            "report_id": str(report["_id"]),
            "empty": payload["empty"],
            "reason": payload["reason"],
            "files": {fmt: entry["path"] for fmt, entry in stored.items()},
            "file_sizes": {fmt: entry["bytes"] for fmt, entry in stored.items()},
            "metrics": {"report_metrics": payload["metrics"],
                        "sections": len(payload["sections"]),
                        "total_rows": sum(section["row_count"] for section in payload["sections"])},
        }
    except Exception as exc:
        failed = now_utc()
        update_one("reports", {"_id": report["_id"]}, {"$set": {
            "status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:400]}",
            "completed_at": failed, "updated_at": failed}})
        raise


def _require(report_id: str) -> dict:
    parsed = oid(report_id)
    report = find_one("reports", {"_id": parsed}) if parsed else None
    if not report:
        raise NotFound("Report not found.")
    return report


def can_view(report: dict, identity) -> bool:
    if str(report.get("requested_by")) == str(identity.user_id):
        return True
    return role_allows(identity.role, "report.view_any")


def summarise(report: dict) -> dict[str, Any]:
    payload = document_to_api(report) or {}
    payload["files_available"] = sorted((report.get("output_files") or {}).keys())
    payload.pop("sections", None)
    return payload


def get_report(report_id: str, identity, *, include_sections: bool = True) -> dict[str, Any]:
    report = _require(report_id)
    if not can_view(report, identity):
        raise Forbidden("You can only read reports you requested, unless you hold report.view_any.")
    payload = document_to_api(report) or {}
    if not include_sections:
        payload.pop("sections", None)
    payload["files_available"] = sorted((report.get("output_files") or {}).keys())
    job = find_one("processing_jobs", {"_id": oid(report.get("job_id"))}) if report.get("job_id") else None
    payload["job"] = document_to_api(job) if job else None
    return payload


def list_reports(identity, params: dict[str, str], *, page: int = 1,
                 page_size: int = 25) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if not role_allows(identity.role, "report.view_any"):
        query["requested_by"] = str(identity.user_id)
    elif params.get("requested_by"):
        query["requested_by"] = str(params["requested_by"])
    if params.get("report_type"):
        query["report_type"] = params["report_type"]
    if params.get("status"):
        query["status"] = params["status"]
    total = collection("reports").count_documents(query)
    rows = find("reports", query, sort=[("created_at", -1)], skip=(page - 1) * page_size,
                limit=page_size, projection={"sections": 0})
    return [summarise(row) for row in rows], total


def storage_state() -> dict[str, Any]:
    root = report_root()
    usage = shutil.disk_usage(str(root))
    return {"root_relative": "artifacts/reports", "free_bytes": usage.free,
            "total_bytes": usage.total, "reports": collection("reports").estimated_document_count()}
