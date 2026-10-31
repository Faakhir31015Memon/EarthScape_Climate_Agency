"""Report generators plus CSV/PDF/JSON writers.

Generators compute only from stored documents. When the data is missing they
return an insufficient_data artefact saying what was looked for.
"""
from __future__ import annotations

import csv
import json
import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common import analytics  # noqa: E402
from common.collections import SCHEMA_VERSION  # noqa: E402
from common.database import get_database  # noqa: E402
from common.repositories import (  # noqa: E402
    audit_logs,
    datasets as datasets_repo,
    ingestion_jobs,
    model_registry,
    predictions as predictions_repo,
    processing_jobs,
)
from common.validators import to_float  # noqa: E402

logger = logging.getLogger(__name__)

MAX_TABLE_ROWS = 1000
FREQUENCIES = ("daily", "weekly", "monthly", "annual")

REPORT_TYPES: Dict[str, Dict[str, Any]] = {
    "climate_trend": {
        "label": "Climate trend report",
        "description": "Aggregated period series for one measured variable, with counts, "
                       "min/max/stddev and the trend slope.",
        "required_data": "climate_observations for the selected variable and window",
        "collections": ["climate_observations"],
        "default_filters": {"metric": "temperature_c", "frequency": "monthly"},
    },
    "regional_comparison": {
        "label": "Regional comparison",
        "description": "Side-by-side distribution statistics per region for one variable.",
        "required_data": "climate_observations covering at least two regions",
        "collections": ["climate_observations"],
        "default_filters": {"metric": "temperature_c"},
    },
    "dataset_quality": {
        "label": "Dataset quality",
        "description": "Completeness, quality flags and coverage window per stored dataset.",
        "required_data": "datasets plus their climate_observations",
        "collections": ["datasets", "climate_observations"],
        "default_filters": {},
    },
    "anomaly": {
        "label": "Anomaly report",
        "description": "Statistical deviations measured against the climatology computed from "
                       "the stored data.",
        "required_data": "enough climate_observations to build a baseline (>= 8 periods)",
        "collections": ["climate_observations"],
        "default_filters": {"metric": "temperature_c", "sigma": 2.5},
    },
    "forecast": {
        "label": "Forecast report",
        "description": "Stored model forecasts with the model, version and evaluation metadata "
                       "that produced them.",
        "required_data": "predictions documents (train and forecast a model first)",
        "collections": ["predictions", "model_registry"],
        "default_filters": {},
    },
    "processing_performance": {
        "label": "Processing performance",
        "description": "Duration, engine, row counts and failures of recorded processing jobs.",
        "required_data": "processing_jobs",
        "collections": ["processing_jobs"],
        "default_filters": {},
    },
    "ingestion_history": {
        "label": "Ingestion history",
        "description": "Source-by-source ingestion outcomes with accepted/rejected/skipped counts.",
        "required_data": "ingestion_jobs",
        "collections": ["ingestion_jobs"],
        "default_filters": {},
    },
    "audit_summary": {
        "label": "Audit summary",
        "description": "Administrative action counts, outcomes and the most recent recorded events.",
        "required_data": "audit_logs",
        "collections": ["audit_logs"],
        "default_filters": {},
        "administrator_only": True,
    },
}


class ReportDataError(ValueError):
    """Raised when a requested report cannot be computed from stored data."""


def normalise_filters(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    filters = dict(raw or {})
    metric = str(filters.get("metric") or "temperature_c").strip()
    if metric not in analytics.METRICS:
        raise ReportDataError(f"Unsupported metric '{metric}'. Choose one of: "
                              f"{', '.join(analytics.METRICS)}.")
    frequency = str(filters.get("frequency") or "monthly").strip()
    if frequency not in FREQUENCIES:
        raise ReportDataError(f"frequency must be one of: {', '.join(FREQUENCIES)}.")
    start_date = _date(filters.get("start_date"), "start_date")
    end_date = _date(filters.get("end_date"), "end_date")
    sigma = to_float(filters.get("sigma"))
    return {
        "metric": metric,
        "frequency": frequency,
        "start_date": start_date,
        "end_date": end_date,
        "regions": analytics.as_list(filters.get("regions"))[:40],
        "location_codes": [code.upper() for code in analytics.as_list(filters.get("location_codes"))][:40],
        "countries": analytics.as_list(filters.get("countries"))[:40],
        "datasets": analytics.as_list(filters.get("datasets"))[:40],
        "sigma": sigma if sigma and sigma > 0 else 2.5,
        "limit": max(1, min(int(filters.get("limit") or 200), 1000)),
    }


def _date(value: Any, label: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ReportDataError(f"{label} must use the YYYY-MM-DD format.")
    return text


def _observations(filters: Dict[str, Any], metric: Optional[str] = None) -> List[Dict[str, Any]]:
    criteria = analytics.observation_query(
        dataset_ids=filters.get("datasets") or None,
        regions=filters.get("regions") or None,
        location_codes=filters.get("location_codes") or None,
        countries=filters.get("countries") or None,
        start_date=filters.get("start_date"),
        end_date=filters.get("end_date"),
        metric=metric or filters.get("metric"),
    )
    return analytics.fetch_observations(criteria)


def _provenance(filters: Dict[str, Any], collections: List[str], *, method: str,
                counts: Optional[Dict[str, int]] = None,
                model_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    database = get_database()
    return {
        "filters": filters,
        "computed_from": [
            {"collection": name,
             "documents": (counts or {}).get(name)}
            for name in collections
        ],
        "method": method,
        "database_mode": database.mode,
        "database_name": database.database_name,
        "schema_version": SCHEMA_VERSION,
        "model_metadata": model_metadata or None,
        "statement": "All figures are computed from stored data.",
    }


def _artifact(*, report_type: str, filters: Dict[str, Any], title: str,
              columns: List[str], rows: List[List[Any]], metadata: Dict[str, Any],
              provenance: Dict[str, Any], chart: Optional[Dict[str, Any]] = None,
              reason: Optional[str] = None,
              insufficient: bool = False) -> Dict[str, Any]:
    """Marks the artefact insufficient_data when rows is empty or insufficient is
    set; row_count stays 0 so the explanatory note row is never counted as data."""
    insufficient = bool(insufficient) or not rows
    return {
        "report_type": report_type,
        "label": REPORT_TYPES[report_type]["label"],
        "title": title,
        "ok": not insufficient,
        "insufficient_data": bool(insufficient),
        "reason": reason if rows else (reason or "No rows were computed from the stored data."),
        "columns": columns,
        "rows": rows,
        "row_count": 0 if insufficient else len(rows),
        "metadata": metadata,
        "provenance": provenance,
        "chart": chart,
        "generated_at": datetime.now(timezone.utc),
    }


def _metadata(filters: Dict[str, Any], *, data_source: str, units: Optional[str],
              date_range: Optional[Dict[str, Any]], row_count: int,
              model_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "generation_timestamp": datetime.now(timezone.utc).isoformat(),
        "data_source": data_source,
        "date_range": date_range,
        "units": units,
        "metric_label": analytics.METRIC_LABELS.get(filters.get("metric") or ""),
        "frequency": filters.get("frequency"),
        "row_count": row_count,
        "model_metadata": model_metadata,
        "generated_by": "EarthScape Climate Agency report generators",
    }


def _insufficient(report_type: str, filters: Dict[str, Any], reason: str, *,
                  collections: Optional[List[str]] = None) -> Dict[str, Any]:
    spec = REPORT_TYPES[report_type]
    names = collections or spec["collections"]
    return _artifact(
        report_type=report_type, filters=filters, title=spec["label"],
        columns=["message"], rows=[[reason]],
        metadata=_metadata(filters, data_source=_source_label(names), units=None,
                           date_range={"start": filters.get("start_date"),
                                       "end": filters.get("end_date")}, row_count=0),
        provenance=_provenance(filters, names, method="none (insufficient stored data)"),
        chart=None, reason=reason, insufficient=True,
    )


def _source_label(collections: List[str]) -> str:
    database = get_database()
    joined = ", ".join(f"{database.database_name}.{name}" for name in collections)
    return f"MongoDB ({database.mode}) - {joined}"


def generate_climate_trend(filters: Dict[str, Any]) -> Dict[str, Any]:
    metric = filters["metric"]
    observations = _observations(filters, metric)
    if not observations:
        return _insufficient("climate_trend", filters,
                             "No stored observations match this metric, scope and date window.")
    try:
        series = analytics.trend_series(observations, metric, frequency=filters["frequency"],
                                        group_by=filters.get("group_by"))
    except analytics.InsufficientData as exc:
        return _insufficient("climate_trend", filters, str(exc))
    points = series.get("series") or []
    if not points:
        return _insufficient("climate_trend", filters, series.get("note")
                             or "The metric column exists but holds no values.")
    columns = ["period", "value", "unit", "observation_count", "min", "max", "std", "aggregation"]
    rows = [[point.get("period"), point.get("value"), series.get("unit"),
             point.get("observation_count"), point.get("min"), point.get("max"),
             point.get("std"), point.get("aggregation")] for point in points]
    slope = analytics.trend_slope([(point["period"], point["value"]) for point in points
                                   if point.get("value") is not None])
    coverage = series.get("coverage") or {}
    metadata = _metadata(filters, data_source=_source_label(["climate_observations"]),
                         units=series.get("unit"), date_range=coverage.get("date_range"),
                         row_count=len(rows))
    metadata["trend_slope"] = slope
    metadata["coverage"] = {key: coverage.get(key) for key in
                           ("observation_count", "locations", "regions", "datasets", "sources")}
    provenance = _provenance({**filters, "group_by": series.get("group_by")},
                             ["climate_observations"],
                             method=f"common.analytics.trend_series ({filters['frequency']}, "
                                    f"{'sum' if metric in analytics.ADDITIVE_METRICS else 'mean'})",
                             counts={"climate_observations": len(observations)})
    chart = _chart_from_pairs([(index, point.get("value")) for index, point in enumerate(points)],
                              title=f"{series.get('label')} by {filters['frequency']}",
                              x_label=f"{filters['frequency']} period",
                              y_label=f"{series.get('label')} ({series.get('unit')})",
                              categories=[point.get("period") for point in points])
    return _artifact(report_type="climate_trend", filters=filters,
                     title=f"{series.get('label')} - {filters['frequency']} series",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


def generate_regional_comparison(filters: Dict[str, Any]) -> Dict[str, Any]:
    metric = filters["metric"]
    observations = _observations(filters, metric)
    if not observations:
        return _insufficient("regional_comparison", filters,
                             "No stored observations match this metric and window.")
    comparison = analytics.compare_regions(observations, metric, frequency=filters["frequency"])
    regions = comparison.get("regions") or []
    if len(regions) < 2:
        return _insufficient("regional_comparison", filters,
                             f"Regional comparison needs at least two regions with values; "
                             f"the stored data covers {len(regions)}.")
    columns = ["region_code", "mean", "median", "min", "max", "std", "observation_count",
               "record_days", "aggregation"]
    rows = [[region.get("region_code"), region.get("mean"), region.get("median"),
             region.get("min"), region.get("max"), region.get("std"),
             region.get("observation_count"), region.get("record_days"),
             region.get("aggregation")] for region in regions]
    metadata = _metadata(filters, data_source=_source_label(["climate_observations"]),
                         units=comparison.get("unit"),
                         date_range=_date_range(observations), row_count=len(rows))
    metadata["spread"] = comparison.get("spread")
    provenance = _provenance(filters, ["climate_observations"],
                             method="common.analytics.compare_regions",
                             counts={"climate_observations": len(observations)})
    chart = _chart_from_pairs([(index, region.get("mean")) for index, region in enumerate(regions)],
                              title=f"{comparison.get('label')} mean by region",
                              x_label="region", y_label=f"mean ({comparison.get('unit')})",
                              categories=[region.get("region_code") for region in regions])
    return _artifact(report_type="regional_comparison", filters=filters,
                     title=f"{comparison.get('label')} - regional comparison",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


def generate_dataset_quality(filters: Dict[str, Any]) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if filters.get("datasets"):
        from common.repositories import maybe_object_id

        criteria["_id"] = {"$in": [maybe_object_id(item) for item in filters["datasets"]]}
    documents = datasets_repo.find(criteria, sort=[("created_at", -1)],
                                  limit=min(filters["limit"], 50))
    if not documents:
        return _insufficient("dataset_quality", filters, "No datasets are stored yet.")
    columns = ["dataset", "source_type", "format", "status", "rows", "populated_metric_pct",
               "flagged_rows", "flagged_pct", "locations", "regions", "first_date", "last_date",
               "quality_flags"]
    rows: List[List[Any]] = []
    inspected = 0
    for document in documents:
        dataset_id = str(document["_id"])
        observations = analytics.fetch_observations(analytics.observation_query(
            dataset_ids=[dataset_id], limit=20000))
        inspected += len(observations)
        coverage = analytics.coverage_report(observations)
        completeness = (coverage.get("completeness") or {}).get(filters["metric"]) or {}
        flags = coverage.get("flagged") or {}
        rows.append([
            document.get("name"), document.get("source_type"), document.get("format"),
            document.get("status"), coverage.get("observation_count"),
            completeness.get("pct"), coverage.get("flagged_row_count"),
            coverage.get("flagged_row_pct"), coverage.get("locations"), coverage.get("regions"),
            (coverage.get("date_range") or {}).get("start"),
            (coverage.get("date_range") or {}).get("end"),
            ", ".join(f"{name}:{count}" for name, count in sorted(flags.items())[:8]) or "none",
        ])
    if not any(row[4] for row in rows):
        return _insufficient("dataset_quality", filters,
                             "Datasets exist but no observations have been ingested for them yet.",
                             collections=["datasets", "climate_observations"])
    metadata = _metadata(filters, data_source=_source_label(["datasets", "climate_observations"]),
                         units=analytics.METRIC_UNITS.get(filters["metric"]),
                         date_range=None, row_count=len(rows))
    metadata["observations_inspected"] = inspected
    provenance = _provenance(filters, ["datasets", "climate_observations"],
                             method="common.analytics.coverage_report per dataset",
                             counts={"datasets": len(documents),
                                     "climate_observations": inspected})
    return _artifact(report_type="dataset_quality", filters=filters,
                     title="Dataset quality", columns=columns, rows=rows,
                     metadata=metadata, provenance=provenance, chart=None)


def generate_anomaly(filters: Dict[str, Any]) -> Dict[str, Any]:
    metric = filters["metric"]
    observations = _observations(filters, metric)
    if not observations:
        return _insufficient("anomaly", filters,
                             "No stored observations match this metric, scope and date window.")
    result = analytics.detect_anomalies(observations, metric, method="statistical_baseline",
                                        threshold_sigma=float(filters["sigma"]))
    anomalies = result.get("anomalies") or []
    if not anomalies:
        reason = result.get("note") or (
            f"No observation in the scanned {result.get('evaluated_observations')} rows deviates "
            f"by {filters['sigma']} standard deviations or more from the stored climatology.")
        return _insufficient("anomaly", filters, reason)
    columns = ["observation_date", "location_code", "location_name", "region_code", "value",
               "unit", "deviation_score", "direction", "severity", "method", "baseline_mean",
               "baseline_sample_size", "explanation"]
    rows = []
    for anomaly in anomalies:
        baseline = anomaly.get("baseline") or {}
        rows.append([anomaly.get("observation_date"), anomaly.get("location_code"),
                     anomaly.get("location_name"), anomaly.get("region_code"),
                     anomaly.get("value"), anomaly.get("unit"), anomaly.get("deviation_score"),
                     anomaly.get("direction"), anomaly.get("severity"), anomaly.get("method"),
                     baseline.get("mean"), baseline.get("sample_size",
                                                       baseline.get("rows_evaluated")),
                     anomaly.get("explanation")])
    metadata = _metadata(filters, data_source=_source_label(["climate_observations"]),
                         units=result.get("unit"), date_range=result.get("date_range"),
                         row_count=len(rows))
    metadata["evaluated_observations"] = result.get("evaluated_observations")
    metadata["severity_counts"] = result.get("severity_counts")
    metadata["baseline"] = result.get("baseline")
    metadata["caveat"] = result.get("caveat")
    provenance = _provenance(filters, ["climate_observations"],
                             method="common.analytics.detect_anomalies (statistical_baseline)",
                             counts={"climate_observations": len(observations)})
    chart = _chart_from_pairs([(index, anomaly.get("deviation_score"))
                              for index, anomaly in enumerate(anomalies[:120])],
                              title=f"Deviation score (sigma >= {filters['sigma']})",
                              x_label="ranked anomaly", y_label="deviation (standard deviations)",
                              categories=[str(anomaly.get("observation_date"))
                                          for anomaly in anomalies[:120]])
    return _artifact(report_type="anomaly", filters=filters,
                     title=f"Anomalies - {result.get('metric_label')}",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


def generate_forecast(filters: Dict[str, Any]) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if filters.get("regions"):
        criteria["region_code"] = {"$in": filters["regions"]}
    if filters.get("location_codes"):
        criteria["location_code"] = {"$in": filters["location_codes"]}
    if filters.get("start_date") or filters.get("end_date"):
        window: Dict[str, Any] = {}
        if filters.get("start_date"):
            window["$gte"] = filters["start_date"]
        if filters.get("end_date"):
            window["$lte"] = filters["end_date"]
        # the window has to select on the series element; there is no
        # document-level forecast_date to filter on
        criteria["values.forecast_date"] = window
    documents = predictions_repo.find(criteria, sort=[("generated_at", -1)],
                                     limit=min(filters["limit"], 1000))
    if not documents:
        return _insufficient("forecast", filters,
                             "No stored forecast documents match this scope. Train a model and "
                             "run a prediction first.",
                             collections=["predictions", "model_registry"])
    models = {str(item["_id"]): item for item in model_registry.find({}, limit=200)}
    columns = ["forecast_date", "target_variable", "region_code", "location_code",
               "location_name", "predicted_value", "unit", "lower_bound", "upper_bound",
               "horizon_days", "model", "model_version", "algorithm", "generated_at"]
    rows: List[List[Any]] = []
    used_models: Dict[str, Any] = {}
    for document in documents:
        model_id = str(document.get("model_id")) if document.get("model_id") else None
        model = models.get(model_id or "", {})
        if model:
            used_models[model_id] = {
                "name": model.get("name"), "version": model.get("version"),
                "algorithm": model.get("algorithm"), "status": model.get("status"),
                "evaluation_metrics": model.get("evaluation_metrics"),
                "trained_on": model.get("trained_on") or model.get("training_window"),
            }
        unit = document.get("unit") or document.get("units") or analytics.METRIC_UNITS.get(
            str(document.get("target_variable") or ""), None)
        series = document.get("series") or []
        if series and isinstance(series[0], dict):
            for point in series:
                rows.append([
                    _iso_day(point.get("timestamp") or document.get("forecast_date")),
                    document.get("target_variable") or document.get("metric"),
                    document.get("region_code") or document.get("region"), document.get("location_code"),
                    document.get("location_name"),
                    to_float(point.get("value")), unit,
                    to_float(point.get("lower")), to_float(point.get("upper")),
                    point.get("horizon_days") or document.get("horizon_days") or document.get("horizon_steps"),
                    model.get("name") or document.get("model_name"),
                    model.get("version") or document.get("model_version"),
                    model.get("algorithm") or document.get("algorithm"),
                    _iso_day(document.get("generated_at"), with_time=True),
                ])
        else:
            for point in document.get("values") or []:
                if isinstance(point, dict):
                    rows.append([
                        _iso_day(point.get("forecast_date") or document.get("forecast_date")),
                        document.get("target_variable") or document.get("metric"),
                        document.get("region_code") or document.get("region"), document.get("location_code"),
                        document.get("location_name"),
                        to_float(point.get("value")), unit,
                        to_float(point.get("lower_bound")), to_float(point.get("upper_bound")),
                        point.get("horizon_days") or document.get("horizon_days") or document.get("horizon_steps"),
                        model.get("name") or document.get("model_name"),
                        model.get("version") or document.get("model_version"),
                        model.get("algorithm") or document.get("algorithm"),
                        _iso_day(document.get("generated_at"), with_time=True),
                    ])
    if not any(row[5] is not None for row in rows):
        return _insufficient("forecast", filters,
                             "The stored forecast documents carry no numeric series values, so "
                             "there is nothing to report.",
                             collections=["predictions", "model_registry"])
    metadata = _metadata(filters, data_source=_source_label(["predictions", "model_registry"]),
                         units=None, date_range=None, row_count=len(rows))
    metadata["model_metadata"] = list(used_models.values())
    metadata["value_field_used"] = "values[].value"
    provenance = _provenance(filters, ["predictions", "model_registry"],
                            method="stored prediction documents joined to model_registry",
                            counts={"predictions": len(documents), "model_registry": len(models)},
                            model_metadata=list(used_models.values()))
    ordered = [row for row in rows if row[5] is not None]
    chart = _chart_from_pairs([(index, row[5]) for index, row in enumerate(ordered[:180])],
                              title="Stored forecast values", x_label="forecast",
                              y_label="predicted value",
                              categories=[str(row[0]) for row in ordered[:180]])
    return _artifact(report_type="forecast", filters=filters, title="Forecast report",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


def generate_processing_performance(filters: Dict[str, Any]) -> Dict[str, Any]:
    documents = processing_jobs.find({}, sort=[("submitted_at", -1)],
                                    limit=min(filters["limit"], 1000))
    if not documents:
        return _insufficient("processing_performance", filters,
                             "No processing jobs have been recorded yet.")
    columns = ["workflow", "engine", "status", "submitted_at", "started_at", "finished_at",
               "duration_seconds", "rows_in", "rows_out", "error"]
    rows: List[List[Any]] = []
    durations: List[float] = []
    for document in documents:
        duration = _seconds_between(document.get("started_at"), document.get("finished_at"))
        if duration is not None:
            durations.append(duration)
        rows.append([document.get("workflow"), document.get("engine") or document.get("mode"),
                     document.get("status"), _iso_day(document.get("submitted_at"), with_time=True),
                     _iso_day(document.get("started_at"), with_time=True),
                     _iso_day(document.get("finished_at"), with_time=True), duration,
                     document.get("rows_in", document.get("records_processed")),
                     document.get("rows_out", document.get("records_written")),
                     (document.get("error_message") or document.get("error"))
                     if isinstance(document.get("error_message") or document.get("error"), str)
                     else None])
    metadata = _metadata(filters, data_source=_source_label(["processing_jobs"]), units="seconds",
                         date_range=None, row_count=len(rows))
    metadata["duration_summary"] = {
        "jobs_measured": len(durations),
        "median_seconds": round(statistics.median(durations), 2) if durations else None,
        "mean_seconds": round(statistics.fmean(durations), 2) if durations else None,
        "max_seconds": round(max(durations), 2) if durations else None,
    }
    provenance = _provenance(filters, ["processing_jobs"],
                            method="processing_jobs durations computed from stored timestamps",
                            counts={"processing_jobs": len(documents)})
    chart = _chart_from_pairs([(index, row[6]) for index, row in enumerate(rows[:180])
                               if row[6] is not None],
                              title="Job duration (seconds)", x_label="job (most recent first)",
                              y_label="seconds",
                              categories=[str(row[0]) for row in rows[:180] if row[6] is not None])
    return _artifact(report_type="processing_performance", filters=filters,
                     title="Processing performance", columns=columns, rows=rows,
                     metadata=metadata, provenance=provenance, chart=chart)


def generate_ingestion_history(filters: Dict[str, Any]) -> Dict[str, Any]:
    documents = ingestion_jobs.find({}, sort=[("started_at", -1)],
                                    limit=min(filters["limit"], 1000))
    if not documents:
        return _insufficient("ingestion_history", filters,
                             "No ingestion jobs have been recorded yet.")
    columns = ["source", "status", "started_at", "completed_at", "duration_seconds",
               "records_fetched", "records_accepted", "records_rejected", "records_skipped",
               "dataset", "error"]
    rows = []
    for document in documents:
        rows.append([document.get("source_name"), document.get("status"),
                     _iso_day(document.get("started_at"), with_time=True),
                     _iso_day(document.get("completed_at"), with_time=True),
                     _seconds_between(document.get("started_at"), document.get("completed_at")),
                     document.get("records_fetched"), document.get("records_accepted"),
                     document.get("records_rejected"), document.get("records_skipped"),
                     str(document.get("dataset_id")) if document.get("dataset_id") else None,
                     document.get("error_message")])
    totals = {
        "fetched": sum(int(row[5] or 0) for row in rows),
        "accepted": sum(int(row[6] or 0) for row in rows),
        "rejected": sum(int(row[7] or 0) for row in rows),
        "skipped": sum(int(row[8] or 0) for row in rows),
    }
    metadata = _metadata(filters, data_source=_source_label(["ingestion_jobs"]), units="records",
                         date_range=None, row_count=len(rows))
    metadata["totals"] = totals
    provenance = _provenance(filters, ["ingestion_jobs"],
                            method="ingestion_jobs as recorded by the ingestion pipeline",
                            counts={"ingestion_jobs": len(documents)})
    chart = _chart_from_pairs([(index, row[6]) for index, row in enumerate(rows[:180])],
                              title="Accepted records per ingestion job",
                              x_label="job (most recent first)", y_label="records accepted",
                              categories=[str(row[0]) for row in rows[:180]])
    return _artifact(report_type="ingestion_history", filters=filters, title="Ingestion history",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


def generate_audit_summary(filters: Dict[str, Any]) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    window: Dict[str, Any] = {}
    if filters.get("start_date"):
        window["$gte"] = _to_datetime(filters["start_date"])
    if filters.get("end_date"):
        window["$lte"] = _to_datetime(filters["end_date"], end_of_day=True)
    if window:
        criteria["created_at"] = window
    total = audit_logs.count(criteria)
    if not total:
        return _insufficient("audit_summary", filters,
                             "No audit events are recorded for this window.")
    documents = audit_logs.find(criteria, sort=[("created_at", -1)],
                                limit=min(filters["limit"] * 10, 5000))
    counts: Dict[Tuple[str, str], int] = {}
    actors: Dict[str, int] = {}
    for document in documents:
        key = (str(document.get("action")), str(document.get("outcome")))
        counts[key] = counts.get(key, 0) + 1
        actor = str(document.get("actor_role") or "anonymous")
        actors[actor] = actors.get(actor, 0) + 1
    columns = ["action", "outcome", "events"]
    rows = [[action, outcome, count] for (action, outcome), count in
            sorted(counts.items(), key=lambda item: item[1], reverse=True)][:300]
    metadata = _metadata(filters, data_source=_source_label(["audit_logs"]), units="events",
                         date_range=None, row_count=len(rows))
    metadata["events_in_window"] = total
    metadata["events_grouped"] = len(documents)
    metadata["by_actor_role"] = actors
    metadata["truncated_note"] = (
        f"The newest {len(documents)} of {total} events in this window were grouped; "
        "older events are counted but not grouped.")
    provenance = _provenance(filters, ["audit_logs"],
                            method="audit_logs grouped by action and outcome",
                            counts={"audit_logs": total})
    chart = _chart_from_pairs([(index, row[2]) for index, row in enumerate(rows[:40])],
                              title="Most frequent audit actions", x_label="action",
                              y_label="events", categories=[str(row[0])[:28] for row in rows[:40]])
    return _artifact(report_type="audit_summary", filters=filters, title="Audit summary",
                     columns=columns, rows=rows, metadata=metadata, provenance=provenance,
                     chart=chart)


GENERATORS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "climate_trend": generate_climate_trend,
    "regional_comparison": generate_regional_comparison,
    "dataset_quality": generate_dataset_quality,
    "anomaly": generate_anomaly,
    "forecast": generate_forecast,
    "processing_performance": generate_processing_performance,
    "ingestion_history": generate_ingestion_history,
    "audit_summary": generate_audit_summary,
}


def generate(report_type: str, raw_filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if report_type not in GENERATORS:
        raise ReportDataError(f"Unknown report type '{report_type}'.")
    merged = {**(REPORT_TYPES[report_type].get("default_filters") or {}),
              **(raw_filters or {})}
    filters = normalise_filters(merged)
    return GENERATORS[report_type](filters)


def computable_now(report_type: str) -> Dict[str, Any]:
    """Counts raw documents only; says nothing about whether filters match rows."""
    spec = REPORT_TYPES[report_type]
    counts: Dict[str, int] = {}
    for name in spec["collections"]:
        try:
            counts[name] = _count_collection(name)
        except Exception as exc:  # noqa: BLE001 - a probe never fails the catalogue
            counts[name] = -1
            logger.warning("Report probe failed for %s: %s", name, type(exc).__name__)
    available = all(value > 0 for value in counts.values()) if counts else False
    return {"report_type": report_type, "available": available, "documents": counts}


def _count_collection(name: str) -> int:
    from common.repositories import ALL

    return ALL[name].count({})


def catalogue() -> List[Dict[str, Any]]:
    entries = []
    for report_type, spec in REPORT_TYPES.items():
        probe = computable_now(report_type)
        missing = sorted(name for name, count in probe["documents"].items() if count <= 0)
        entries.append({
            "report_type": report_type,
            "label": spec["label"],
            "description": spec["description"],
            "required_data": spec["required_data"],
            "formats": ["csv", "pdf", "json"],
            "administrator_only": bool(spec.get("administrator_only")),
            "default_filters": spec.get("default_filters"),
            "computable_now": probe["available"],
            "documents_available": probe["documents"],
            "reason": None if probe["available"] else (
                f"No stored documents in {', '.join(missing)} for this report to read."
                if missing else "This report has no data source collected yet."),
        })
    return entries


def write_csv(artifact: Dict[str, Any], path: str) -> Dict[str, Any]:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    written_rows = 0
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        for line in _provenance_lines(artifact):
            writer.writerow([line])
        writer.writerow([])
        writer.writerow(artifact["columns"])
        for row in artifact["rows"]:
            writer.writerow([_scalar(value) for value in row])
            written_rows += 1
        handle.flush()
    return {"path": str(target), "file_size_bytes": target.stat().st_size,
            "row_count": written_rows}


def _provenance_lines(artifact: Dict[str, Any]) -> List[str]:
    metadata = artifact.get("metadata") or {}
    provenance = artifact.get("provenance") or {}
    lines = [
        f"# EarthScape Climate Agency - {artifact.get('label')}",
        f"# report_type: {artifact.get('report_type')}",
        f"# generation_timestamp: {metadata.get('generation_timestamp')}",
        f"# data_source: {metadata.get('data_source')}",
        f"# date_range: {json.dumps(metadata.get('date_range'), default=str)}",
        f"# units: {metadata.get('units')}",
        f"# method: {provenance.get('method')}",
        f"# filters: {json.dumps(provenance.get('filters'), default=str, sort_keys=True)}",
        f"# computed_from: {json.dumps(provenance.get('computed_from'), default=str)}",
        f"# database_mode: {provenance.get('database_mode')}",
        f"# schema_version: {provenance.get('schema_version')}",
        f"# model_metadata: {json.dumps(metadata.get('model_metadata'), default=str)}",
        f"# status: {'insufficient_data' if artifact.get('insufficient_data') else 'complete'}",
    ]
    if artifact.get("reason"):
        lines.append(f"# reason: {artifact['reason']}")
    lines.append("# All figures are computed from stored observations and stored job records.")
    return lines


def write_json(artifact: Dict[str, Any], path: str) -> Dict[str, Any]:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(artifact)
    payload["rows"] = [[_scalar(value) for value in row] for row in artifact["rows"]]
    payload["footer"] = "Computed from stored observations and stored job records."
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, default=str, indent=2, sort_keys=False)
        handle.flush()
    return {"path": str(target), "file_size_bytes": target.stat().st_size,
            "row_count": len(artifact["rows"])}


def write_pdf(artifact: Dict[str, Any], path: str) -> Dict[str, Any]:
    from pathlib import Path

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    small = ParagraphStyle("footnote", parent=styles["Normal"], fontSize=7.5,
                           leading=9.5, textColor=colors.HexColor("#4a4a4a"))
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8.5, leading=11)

    page_size = landscape(A4)
    written_rows = 0
    document = SimpleDocTemplate(
        str(target), pagesize=page_size, leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=16 * mm, bottomMargin=18 * mm,
        title=str(artifact.get("title"))[:120],
        author="EarthScape Climate Agency",
        subject=f"{artifact.get('report_type')} report",
    )
    metadata = artifact.get("metadata") or {}
    provenance = artifact.get("provenance") or {}

    story = [Paragraph(str(artifact.get("title"))[:160], styles["Title"]),
             Paragraph(f"{artifact.get('label')}", styles["Heading3"]),
             Spacer(1, 4)]
    facts = (
        f"Generated: {metadata.get('generation_timestamp')} &nbsp;|&nbsp; "
        f"Data source: {metadata.get('data_source')}<br/>"
        f"Date range: {_range_text(metadata.get('date_range'))} &nbsp;|&nbsp; "
        f"Units: {metadata.get('units') or 'n/a'} &nbsp;|&nbsp; "
        f"Frequency: {metadata.get('frequency') or 'n/a'}<br/>"
        f"Method: {provenance.get('method')}<br/>"
        f"Filters: {_compact(provenance.get('filters'))}<br/>"
        f"Computed from: {_compact(provenance.get('computed_from'))}<br/>"
        f"Model metadata: {_compact(metadata.get('model_metadata'))}"
    )
    story.append(Paragraph(facts, meta))
    if artifact.get("insufficient_data"):
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"<b>Insufficient data:</b> {artifact.get('reason') or 'No rows were computed.'}",
            styles["Normal"]))
    story.append(Spacer(1, 8))

    chart = artifact.get("chart")
    if chart and chart.get("points"):
        story.append(_chart_flowable(chart))
        story.append(Spacer(1, 8))

    columns = [str(column) for column in artifact["columns"]]
    table_data = [[Paragraph(f"<b>{column}</b>", small) for column in columns]]
    for row in artifact["rows"][:MAX_TABLE_ROWS]:
        table_data.append([Paragraph(_text(value), small) for value in row])
        written_rows += 1
    if artifact["row_count"] > MAX_TABLE_ROWS:
        table_data.append([Paragraph(
            f"… {artifact['row_count'] - MAX_TABLE_ROWS} further rows omitted from the printed "
            f"table; the CSV/JSON artefacts contain the full set.", small)]
            + [""] * (len(columns) - 1))

    if len(table_data) > 1:
        available = page_size[0] - 28 * mm
        widths = _column_widths(columns, artifact["rows"][:MAX_TABLE_ROWS], available)
        table = Table(table_data, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c8c8c8")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3f7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        story.append(table)
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        f"Computed from stored observations - {provenance.get('statement', '')} "
        f"Database mode: {provenance.get('database_mode')} "
        f"({provenance.get('database_name')}), schema v{provenance.get('schema_version')}.",
        small))

    def _decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#5a5a5a"))
        canvas.drawString(14 * mm, 10 * mm,
                          "EarthScape Climate Agency - computed from stored observations")
        canvas.drawRightString(page_size[0] - 14 * mm, 10 * mm,
                               f"Page {doc.page}")
        canvas.setStrokeColor(colors.HexColor("#d8d8d8"))
        canvas.line(14 * mm, 13 * mm, page_size[0] - 14 * mm, 13 * mm)
        canvas.restoreState()

    document.build(story, onFirstPage=_decorate, onLaterPages=_decorate)
    return {"path": str(target), "file_size_bytes": target.stat().st_size,
            "row_count": written_rows}


def _chart_flowable(chart: Dict[str, Any]) -> Any:
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.graphics.charts.lineplots import LinePlot

    points = [pair for pair in (chart.get("points") or []) if pair[1] is not None]
    drawing = Drawing(160 * mm, 55 * mm)
    if not points:
        drawing.add(String(0, 25, "No numeric series was available to chart.",
                           fontName="Helvetica", fontSize=8))
        return drawing
    plot = LinePlot()
    plot.x = 0
    plot.y = 12 * mm
    plot.width = 155 * mm
    plot.height = 38 * mm
    plot.data = [[tuple(pair) for pair in points]]
    plot.lines[0].strokeColor = colors.HexColor("#1f6f8b")
    plot.strokeWidth = 1.2
    plot.xValueAxis.labels.fontName = "Helvetica"
    plot.xValueAxis.labels.fontSize = 6
    plot.yValueAxis.labels.fontName = "Helvetica"
    plot.yValueAxis.labels.fontSize = 6
    x_low = min(pair[0] for pair in points)
    x_high = max(pair[0] for pair in points)
    y_low = min(pair[1] for pair in points)
    y_high = max(pair[1] for pair in points)
    # a single point or flat series leaves reportlab no room for ticks;
    # widen the bounds by one unit
    plot.xValueAxis.valueMin = x_low
    plot.xValueAxis.valueMax = x_high if x_high > x_low else x_low + 1
    plot.yValueAxis.valueMin = y_low
    plot.yValueAxis.valueMax = y_high if y_high > y_low else y_low + 1
    drawing.add(plot)
    drawing.add(String(0, 50 * mm, str(chart.get("title") or "")[:110],
                       fontName="Helvetica-Bold", fontSize=8.5))
    drawing.add(String(0, 0, f"{chart.get('x_label') or ''}  /  {chart.get('y_label') or ''}",
                       fontName="Helvetica", fontSize=6.5))
    return drawing


def _chart_from_pairs(pairs: List[Tuple[int, Any]], *, title: str, x_label: str, y_label: str,
                     categories: Optional[List[Any]] = None) -> Dict[str, Any]:
    points: List[Tuple[float, float]] = []
    for index, value in pairs:
        number = to_float(value)
        if number is None:
            continue
        points.append((float(index), number))
    return {"title": title, "x_label": x_label, "y_label": y_label,
            "points": points[:400],
            "categories": [str(item) for item in (categories or [])][:400],
            "note": None if points else "No numeric values available for the chart."}


def _column_widths(columns: List[str], rows: List[List[Any]], available: float) -> List[float]:
    """Proportional widths from the raw cell text, never from Paragraph objects."""
    from reportlab.lib.units import mm

    longest = []
    for index in range(len(columns)):
        width = len(columns[index])
        for row in rows:
            if index < len(row):
                width = max(width, min(len(_text(row[index])), 60))
        longest.append(max(4, width))
    total = sum(longest) or 1
    widths = [max(14 * mm, available * (value / total)) for value in longest]
    scale = available / sum(widths)
    return [width * scale for width in widths]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)[:120]
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")[:400]


def _scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    if isinstance(value, (int, float, bool, str)) or value is None:
        return value
    return str(value)


def _compact(value: Any) -> str:
    if value in (None, {}, []):
        return "n/a"
    return json.dumps(value, default=str, sort_keys=True)[:600]


def _range_text(value: Any) -> str:
    if not value:
        return "not bounded by the stored data"
    if isinstance(value, dict):
        return f"{value.get('start')} to {value.get('end')}"
    return str(value)


def _first_numeric(document: Dict[str, Any], fields: Tuple[str, ...]) -> Tuple[Optional[float],
                                                                              Optional[str]]:
    for field in fields:
        if field in document:
            number = to_float(document.get(field))
            if number is not None:
                return number, field
    return None, None


def _seconds_between(start: Any, end: Any) -> Optional[float]:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return round((end - start).total_seconds(), 2)


def _iso_day(value: Any, *, with_time: bool = False) -> Optional[str]:
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.isoformat() if with_time else moment.date().isoformat()
    if isinstance(value, str):
        return value if with_time else value[:10]
    return None


def _to_datetime(text: str, *, end_of_day: bool = False) -> datetime:
    moment = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return moment.replace(hour=23, minute=59, second=59) if end_of_day else moment


def _date_range(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    dates = [item.get("observation_date") for item in observations if item.get("observation_date")]
    return {"start": min(dates), "end": max(dates)} if dates else None


WRITERS: Dict[str, Callable[[Dict[str, Any], str], Dict[str, Any]]] = {
    "csv": write_csv,
    "pdf": write_pdf,
    "json": write_json,
}


def write(artifact: Dict[str, Any], path: str, fmt: str) -> Dict[str, Any]:
    if fmt not in WRITERS:
        raise ReportDataError(f"Unsupported report format '{fmt}'.")
    return WRITERS[fmt](artifact, path)
