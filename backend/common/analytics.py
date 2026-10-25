"""Analytics kernel.

Every number returned by the climate, dashboard and report APIs is computed here
from documents that actually exist in ``climate_observations``. When the required
observations are absent the functions return empty series and an explicit
``insufficient_data`` explanation rather than a fabricated value.

The same code path is used for MongoDB-derived data and for PySpark/MapReduce
outputs, so a dashboard cannot tell the difference between batch and streaming
sources (they share the observation schema).
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from itertools import islice
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

from common import validators
from common.repositories import (HARD_RESULT_LIMIT, analytics_summaries,
                                 climate_observations, utcnow)

METRICS = list(validators.RANGE_LIMITS)

METRIC_LABELS: Dict[str, str] = {
    "temperature_c": "Mean 2-metre temperature",
    "temperature_min_c": "Minimum 2-metre temperature",
    "temperature_max_c": "Maximum 2-metre temperature",
    "precipitation_mm": "Total precipitation",
    "relative_humidity_pct": "Relative humidity",
    "wind_speed_ms": "Mean wind speed",
    "wind_direction_deg": "Wind direction",
    "surface_pressure_hpa": "Surface pressure",
    "cloud_coverage_pct": "Cloud cover",
    "solar_radiation_kwh_m2": "Solar radiation",
}

METRIC_UNITS: Dict[str, str] = {
    "temperature_c": "°C",
    "temperature_min_c": "°C",
    "temperature_max_c": "°C",
    "precipitation_mm": "mm/day",
    "relative_humidity_pct": "%",
    "wind_speed_ms": "m/s",
    "wind_direction_deg": "°",
    "surface_pressure_hpa": "hPa",
    "cloud_coverage_pct": "%",
    "solar_radiation_kwh_m2": "kWh/m²",
}

# Precipitation is additive; everything else is an average over the period.
ADDITIVE_METRICS = {"precipitation_mm"}

MIN_POINTS_FOR_BASELINE = 8
MAX_QUERY_ROWS = 120000


class InsufficientData(Exception):
    def __init__(self, message: str, *, available: int = 0, required: int = 0):
        super().__init__(message)
        self.message = message
        self.available = available
        self.required = required


def observation_query(*, dataset_ids: Optional[Sequence[str]] = None,
                      regions: Optional[Sequence[str]] = None,
                      location_codes: Optional[Sequence[str]] = None,
                      start_date: Optional[str] = None,
                      end_date: Optional[str] = None,
                      countries: Optional[Sequence[str]] = None,
                      metric: Optional[str] = None,
                      exclude_flagged: bool = False,
                      limit: int = MAX_QUERY_ROWS) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if dataset_ids:
        criteria["dataset_id"] = {"$in": list(dataset_ids)}
    if regions:
        criteria["region_code"] = {"$in": list(regions)}
    if location_codes:
        criteria["location_code"] = {"$in": [code.upper() for code in location_codes]}
    if countries:
        criteria["country"] = {"$in": list(countries)}
    if start_date or end_date:
        window: Dict[str, Any] = {}
        if start_date:
            window["$gte"] = f"{start_date}"
        if end_date:
            window["$lte"] = f"{end_date}"
        criteria["observation_date"] = window
    if metric:
        if metric not in METRICS:
            raise InsufficientData(f"Unknown metric '{metric}'.")
        criteria[metric] = {"$ne": None}
    if exclude_flagged:
        criteria["quality_flags"] = {"$size": 0}
    criteria.setdefault("$and", [])
    criteria.pop("$and", None)
    criteria["_limit"] = limit
    return criteria


def _strip_limit(criteria: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    limit = int(criteria.pop("_limit", MAX_QUERY_ROWS))
    return criteria, limit


def fetch_observations(criteria: Dict[str, Any],
                      projection: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    query, limit = _strip_limit(dict(criteria))
    fields = {"_id": 0, "observation_date": 1, "year": 1, "month": 1, "region_code": 1,
              "location_code": 1, "location_name": 1, "country": 1, "latitude": 1, "longitude": 1,
              "dataset_id": 1, "source_name": 1}
    fields.update({metric: 1 for metric in METRICS})
    if projection:
        fields = projection
    sort = [("observation_date", 1)]
    if limit > HARD_RESULT_LIMIT:
        # Training and export windows ask for everything they hold. ``find()`` caps at
        # HARD_RESULT_LIMIT, which with an ascending sort would silently drop the newest
        # days - the exact rows a forecast needs - so a wide window streams instead.
        return list(islice(climate_observations.stream(query, projection=fields, sort=sort), limit))
    return climate_observations.find(query, projection=fields,
                                     sort=sort, limit=limit)


def to_frame(observations: List[Dict[str, Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(observations)
    if frame.empty:
        return frame
    if "observation_date" in frame:
        frame["date"] = pd.to_datetime(frame["observation_date"], format="%Y-%m-%d", errors="coerce")
    return frame


# --------------------------------------------------------------------- series
def period_label(row: pd.Series, frequency: str, index: pd.Timestamp) -> str:
    if frequency == "daily":
        return index.strftime("%Y-%m-%d")
    if frequency == "monthly":
        return index.strftime("%Y-%m")
    if frequency == "annual":
        return str(index.year)
    if frequency == "weekly":
        iso = index.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"
    raise ValueError(f"Unsupported frequency '{frequency}'.")


def trend_series(observations: List[Dict[str, Any]], metric: str, *,
                 frequency: str = "monthly", group_by: Optional[str] = None,
                 require_baseline: bool = False) -> Dict[str, Any]:
    """Aggregate one metric over time, optionally split by region or location."""
    if metric not in METRICS:
        raise InsufficientData(f"Metric '{metric}' is not supported.")
    frame = to_frame(observations)
    if frame.empty or metric not in frame:
        return _empty_series(metric, frequency, "No observations are stored for this metric yet.")

    frame = frame.dropna(subset=[metric])
    if frame.empty:
        return _empty_series(metric, frequency, "All stored values for this metric are null.")

    frame = frame.sort_values("date")
    grouper = pd.Grouper(key="date", freq={"daily": "D", "monthly": "MS", "annual": "YS",
                                           "weekly": "W-SUN"}[frequency])
    keys: List[Any] = [grouper]
    if group_by == "region":
        keys.insert(0, "region_code")
    elif group_by == "location":
        keys.insert(0, "location_code")
    elif group_by == "country":
        keys.insert(0, "country")

    agg = "sum" if metric in ADDITIVE_METRICS else "mean"
    grouped = frame.groupby(keys, dropna=True)[metric].agg([agg, "count", "min", "max", "std"])
    grouped = grouped.reset_index()

    series: List[Dict[str, Any]] = []
    for _, row in grouped.iterrows():
        bucket = row["date"]
        if pd.isna(bucket):
            continue
        entry = {
            "period": period_label(row, frequency, bucket),
            "value": _round(row[agg]),
            "observation_count": int(row["count"]),
            "min": _round(row["min"]),
            "max": _round(row["max"]),
            "std": _round(row["std"]) if not pd.isna(row["std"]) else None,
            "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
        }
        if group_by == "region":
            entry["region_code"] = str(row["region_code"])
        elif group_by == "location":
            entry["location_code"] = str(row["location_code"])
        elif group_by == "country":
            entry["country"] = str(row["country"])
        series.append(entry)

    payload: Dict[str, Any] = {
        "metric": metric,
        "label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "frequency": frequency,
        "group_by": group_by,
        "series": series,
        "point_count": len(series),
        "coverage": coverage_report(observations),
        "first_period": series[0]["period"] if series else None,
        "last_period": series[-1]["period"] if series else None,
    }
    if require_baseline and len(series) < MIN_POINTS_FOR_BASELINE:
        raise InsufficientData(
            f"Only {len(series)} periods are available; at least {MIN_POINTS_FOR_BASELINE} are "
            "needed before a trend or anomaly baseline is scientifically meaningful.",
            available=len(series), required=MIN_POINTS_FOR_BASELINE,
        )
    return payload


def compare_regions(observations: List[Dict[str, Any]], metric: str, *,
                    frequency: str = "monthly") -> Dict[str, Any]:
    frame = to_frame(observations)
    if frame.empty or metric not in frame:
        return {"metric": metric, "regions": [], "comparison": []}
    frame = frame.dropna(subset=[metric, "region_code"])
    agg = "sum" if metric in ADDITIVE_METRICS else "mean"
    grouped = frame.groupby("region_code")[metric].agg(["mean", "median", "min", "max", "std", "count"])
    totals = frame.groupby("region_code").size()
    regions = [
        {
            "region_code": str(name),
            "mean": _round(row["mean"]),
            "median": _round(row["median"]),
            "min": _round(row["min"]),
            "max": _round(row["max"]),
            "std": _round(row["std"]) if not pd.isna(row["std"]) else None,
            "observation_count": int(row["count"]),
            "record_days": int(totals.get(name, 0)),
            "aggregation": agg,
        }
        for name, row in grouped.iterrows()
    ]
    regions.sort(key=lambda item: item["mean"] if item["mean"] is not None else -math.inf, reverse=True)
    return {
        "metric": metric,
        "label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "frequency": frequency,
        "regions": regions,
        "spread": {
            "highest_region": regions[0]["region_code"] if regions else None,
            "lowest_region": regions[-1]["region_code"] if regions else None,
            "difference": _round((regions[0]["mean"] or 0) - (regions[-1]["mean"] or 0))
            if len(regions) > 1 else None,
        },
    }


def correlation_matrix(observations: List[Dict[str, Any]],
                       metrics: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    selected = [metric for metric in (metrics or METRICS) if metric in METRICS]
    frame = to_frame(observations)
    if frame.empty:
        return {"cells": [], "metrics": selected, "note": "No observations available."}
    available = [metric for metric in selected if metric in frame]
    if len(available) < 2:
        return {"cells": [], "metrics": available,
                "note": "Correlation needs at least two populated metrics."}
    sample = frame[available].dropna()
    if len(sample) < MIN_POINTS_FOR_BASELINE:
        return {"cells": [], "metrics": available,
                "note": f"Only {len(sample)} complete rows; {MIN_POINTS_FOR_BASELINE} required.",
                "sample_size": len(sample)}
    matrix = sample.corr(method="pearson")
    cells = []
    for row_name in matrix.index:
        for column_name in matrix.columns:
            value = matrix.loc[row_name, column_name]
            if pd.isna(value):
                continue
            cells.append({
                "x": str(column_name),
                "y": str(row_name),
                "coefficient": _round(value, 4),
                "sample_size": int(len(sample)),
                "strength": _strength(abs(float(value))),
            })
    return {
        "metrics": available,
        "labels": {metric: METRIC_LABELS[metric] for metric in available},
        "units": {metric: METRIC_UNITS[metric] for metric in available},
        "sample_size": int(len(sample)),
        "cells": cells,
        "method": "pearson",
        "caveat": "Correlation describes co-variation only. It is not evidence of climate causation.",
    }


def _strength(coefficient: float) -> str:
    if coefficient >= 0.8:
        return "strong"
    if coefficient >= 0.5:
        return "moderate"
    if coefficient >= 0.3:
        return "weak"
    return "negligible"


# ------------------------------------------------------------------- anomalies
def seasonal_baselines(observations: List[Dict[str, Any]], metric: str) -> Dict[str, Dict[str, Any]]:
    """Per (region, month) climatology computed from the stored record itself."""
    frame = to_frame(observations)
    if frame.empty or metric not in frame:
        return {}
    frame = frame.dropna(subset=[metric])
    baselines: Dict[str, Dict[str, Any]] = {}
    keys = ["region_code", "month"] if "region_code" in frame else ["month"]
    for name, group in frame.groupby(keys, dropna=True):
        if len(group) < MIN_POINTS_FOR_BASELINE:
            continue
        if isinstance(name, tuple):
            region, month = str(name[0]), int(name[1])
        else:
            region, month = "GLOBAL", int(name)
        mean = float(group[metric].mean())
        std = float(group[metric].std(ddof=1)) if len(group) > 1 else 0.0
        baselines[f"{region}|{month}"] = {
            "region_code": region,
            "month": month,
            "mean": _round(mean),
            "std": _round(std),
            "sample_size": int(len(group)),
            "p05": _round(float(group[metric].quantile(0.05))),
            "p95": _round(float(group[metric].quantile(0.95))),
            "metric": metric,
        }
    return baselines


def detect_anomalies(observations: List[Dict[str, Any]], metric: str, *,
                     method: str = "statistical_baseline",
                     threshold_sigma: float = 2.5,
                     absolute_threshold: Optional[float] = None,
                     direction: str = "both",
                     region_code: Optional[str] = None,
                     location_code: Optional[str] = None) -> Dict[str, Any]:
    """Threshold, statistical-baseline or isolation-forest anomaly detection.

    Returns real deviations measured against a baseline derived from the same
    stored data. ``explanation`` is built from the observable deviation only.
    """
    if metric not in METRICS:
        raise InsufficientData(f"Metric '{metric}' is not supported.")

    frame = to_frame(observations)
    if frame.empty or metric not in frame:
        return {"method": method, "anomalies": [], "baseline": None,
                "note": "No observations are stored for this metric.", "metric": metric}
    frame = frame.dropna(subset=[metric])
    if region_code:
        frame = frame[frame.get("region_code") == region_code]
    if location_code:
        frame = frame[frame.get("location_code") == location_code.upper()]
    if frame.empty:
        return {"method": method, "anomalies": [], "baseline": None,
                "note": "No observations match the selected scope.", "metric": metric}

    if method == "threshold":
        if absolute_threshold is None:
            raise InsufficientData("Threshold detection requires an absolute_threshold value.")
        candidates = frame[frame[metric] >= absolute_threshold] if direction in ("both", "above") \
            else frame[frame[metric] <= -absolute_threshold] if direction == "below" \
            else frame[(frame[metric] - absolute_threshold).abs() <= 1e-9]
        anomalies = _rows_to_anomalies(candidates, metric, method="threshold",
                                       reference=absolute_threshold, deviation_kind="absolute")
        return _anomaly_payload(anomalies, metric, method, None, frame, direction=direction)

    if method == "isolation_forest":
        return _isolation_forest_anomalies(frame, metric, region_code=region_code,
                                           location_code=location_code)

    if method != "statistical_baseline":
        raise InsufficientData(f"Unknown anomaly method '{method}'.")

    baselines = seasonal_baselines(observations, metric)
    if region_code or location_code:
        scoped_key_prefix = str(region_code or location_code or "")
        baselines = {
            key: value for key, value in baselines.items()
            if not scoped_key_prefix or value["region_code"] in (scoped_key_prefix, "GLOBAL")
        }
    if not baselines:
        global_mean = float(frame[metric].mean())
        global_std = float(frame[metric].std(ddof=1)) if len(frame) > 1 else 0.0
        if global_std <= 0:
            return {"method": method, "anomalies": [], "metric": metric,
                    "note": "Values do not vary, so no deviation can be measured."}
        z_scores = (frame[metric] - global_mean) / global_std
        flagged = frame[z_scores.abs() >= threshold_sigma]
        anomalies = _rows_to_anomalies(flagged, metric, method="statistical_baseline",
                                       reference=global_mean, deviation_kind="z_score")
        return _anomaly_payload(anomalies, metric, method,
                                {"scope": "global", "mean": _round(global_mean),
                                 "std": _round(global_std), "sample_size": int(len(frame))},
                                frame, direction=direction)

    anomalies = []
    for _, row in frame.iterrows():
        region = str(row.get("region_code") or "GLOBAL")
        key = f"{region}|{int(row['month'])}"
        baseline = baselines.get(key) or baselines.get(f"GLOBAL|{int(row['month'])}")
        if baseline is None or not baseline["std"]:
            continue
        value = float(row[metric])
        deviation = (value - baseline["mean"]) / baseline["std"]
        if abs(deviation) < threshold_sigma:
            continue
        if direction == "above" and deviation < 0:
            continue
        if direction == "below" and deviation > 0:
            continue
        anomalies.append(_anomaly_document(row, metric, deviation, baseline, "statistical_baseline"))

    anomalies.sort(key=lambda item: abs(item["deviation_score"]), reverse=True)
    return _anomaly_payload(anomalies, metric, method,
                            {"kind": "seasonal_regional_climatology",
                             "baseline_groups": len(baselines),
                             "threshold_sigma": threshold_sigma},
                            frame, direction=direction)


def _isolation_forest_anomalies(frame: pd.DataFrame, metric: str, *,
                                region_code: Optional[str], location_code: Optional[str]) -> Dict[str, Any]:
    from sklearn.ensemble import IsolationForest

    feature_frame = frame.copy()
    features = [metric] + [column for column in ("relative_humidity_pct", "precipitation_mm",
                                                 "wind_speed_ms", "surface_pressure_hpa",
                                                 "cloud_coverage_pct", "solar_radiation_kwh_m2")
                           if column in feature_frame and column != metric]
    feature_frame = feature_frame.dropna(subset=features)
    if len(feature_frame) < 30:
        return {"method": "isolation_forest", "anomalies": [], "metric": metric,
                "note": f"Isolation Forest needs at least 30 complete rows; only "
                        f"{len(feature_frame)} are available.",
                "required": 30, "available": int(len(feature_frame))}

    numeric = feature_frame[features].astype(float)
    model = IsolationForest(n_estimators=200, contamination="auto", random_state=7)
    scores = model.fit_predict(numeric)
    confidence = model.score_samples(numeric)
    feature_frame = feature_frame.assign(_label=scores, _score=confidence)
    flagged = feature_frame[feature_frame["_label"] == -1]
    mean = float(feature_frame[metric].mean())
    std = float(feature_frame[metric].std(ddof=1)) or 1.0
    anomalies = []
    for _, row in flagged.iterrows():
        deviation = (float(row[metric]) - mean) / std
        anomalies.append(_anomaly_document(row, metric, deviation,
                                           {"scope": "isolation_forest_multivariate",
                                            "features": features,
                                            "outlier_score": _round(float(row["_score"]), 4)},
                                           "isolation_forest"))
    anomalies.sort(key=lambda item: abs(item["deviation_score"]), reverse=True)
    return _anomaly_payload(anomalies, metric, "isolation_forest",
                            {"contamination": "auto", "estimators": 200,
                             "features": features, "rows_evaluated": int(len(feature_frame))},
                            frame)


def _anomaly_document(row: pd.Series, metric: str, deviation: float, baseline: Dict[str, Any],
                      method: str) -> Dict[str, Any]:
    value = float(row[metric])
    severity = "critical" if abs(deviation) >= 4 else "high" if abs(deviation) >= 3 \
        else "medium" if abs(deviation) >= 2.5 else "low"
    reference = baseline.get("mean")
    explanation = (
        f"{METRIC_LABELS[metric]} measured {value:.1f}{METRIC_UNITS[metric]} at "
        f"{row.get('location_name') or row.get('location_code') or 'the observation point'} on "
        f"{row.get('observation_date')}, versus a {method.replace('_', ' ')} reference of "
        f"{_round(reference)}{METRIC_UNITS[metric]} for "
        f"{row.get('region_code') or 'the scope'} in month {int(row.get('month') or 0)} "
        f"(baseline sample size {baseline.get('sample_size', baseline.get('rows_evaluated', 'n/a'))}). "
        f"The deviation is {deviation:+.2f} standard deviations."
    )
    return {
        "observation_date": row.get("observation_date"),
        "metric": metric,
        "metric_label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "value": _round(value),
        "deviation_score": _round(deviation, 3),
        "direction": "above" if deviation > 0 else "below",
        "severity": severity,
        "method": method,
        "baseline": baseline,
        "region_code": row.get("region_code"),
        "location_code": row.get("location_code"),
        "location_name": row.get("location_name"),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "dataset_id": str(row.get("dataset_id")) if row.get("dataset_id") else None,
        "source_name": row.get("source_name"),
        "explanation": explanation,
    }


def _rows_to_anomalies(rows: pd.DataFrame, metric: str, *, method: str, reference: Any,
                       deviation_kind: str) -> List[Dict[str, Any]]:
    out = []
    for _, row in rows.iterrows():
        value = float(row[metric])
        std = 1.0
        if deviation_kind == "z_score":
            deviation = (value - float(reference)) / (std or 1.0)
        else:
            deviation = value - float(reference)
        out.append(_anomaly_document(row, metric, deviation,
                                     {"reference": _round(float(reference)), "kind": deviation_kind},
                                     method))
    return out


def _anomaly_payload(anomalies: List[Dict[str, Any]], metric: str, method: str,
                     baseline: Optional[Dict[str, Any]], frame: pd.DataFrame, *,
                     direction: str) -> Dict[str, Any]:
    counts = defaultdict(int)
    for item in anomalies:
        counts[item["severity"]] += 1
    return {
        "metric": metric,
        "metric_label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "method": method,
        "direction": direction,
        "baseline": baseline,
        "evaluated_observations": int(len(frame)),
        "date_range": {
            "start": str(frame["observation_date"].min()) if "observation_date" in frame else None,
            "end": str(frame["observation_date"].max()) if "observation_date" in frame else None,
        },
        "anomaly_count": len(anomalies),
        "severity_counts": dict(counts),
        "anomalies": anomalies[:500],
        "truncated": len(anomalies) > 500,
        "caveat": "An anomaly is a statistical deviation from the stored reference period. It is not, "
                  "by itself, evidence that an event is scientifically exceptional.",
    }


# -------------------------------------------------------------------- quality
def coverage_report(observations: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not observations:
        return {"observation_count": 0, "completeness": {}, "flagged": {}, "date_range": None,
                "locations": 0, "regions": 0, "datasets": 0, "sources": []}
    frame = to_frame(observations)
    completeness: Dict[str, Any] = {}
    for metric in METRICS:
        if metric not in frame:
            completeness[metric] = {"populated": 0, "missing": len(frame), "pct": 0.0}
            continue
        populated = int(frame[metric].notna().sum())
        completeness[metric] = {
            "populated": populated,
            "missing": int(len(frame) - populated),
            "pct": _round(populated / len(frame) * 100, 1),
        }
    flags: Dict[str, int] = defaultdict(int)
    flagged_rows = 0
    for observation in observations:
        names = observation.get("quality_flags") or []
        if names:
            flagged_rows += 1
        for name in names:
            flags[str(name)] += 1
    dates = [item.get("observation_date") for item in observations if item.get("observation_date")]
    return {
        "observation_count": len(observations),
        "completeness": completeness,
        "flagged": dict(flags),
        "flagged_row_count": flagged_rows,
        "flagged_row_pct": _round(flagged_rows / len(observations) * 100, 1),
        "date_range": {"start": min(dates), "end": max(dates)} if dates else None,
        "locations": len({item.get("location_code") for item in observations}),
        "regions": len({item.get("region_code") for item in observations}),
        "datasets": len({str(item.get("dataset_id")) for item in observations}),
        "sources": sorted({str(item.get("source_name")) for item in observations if item.get("source_name")}),
    }


def summary_key(prefix: str, **parts) -> str:
    ordered = "|".join(f"{key}={parts[key]}" for key in sorted(parts))
    return f"{prefix}|{ordered}" if ordered else prefix


def store_summary(*, metric: str, scope: Dict[str, Any], payload: Dict[str, Any],
                  source_datasets: Sequence[str], workflow: str,
                  processing_mode: str = "mongodb") -> str:
    key = summary_key(metric, **scope)
    analytics_summaries.update_one(
        {"summary_key": key},
        {"$set": {
            "summary_key": key,
            "metric": metric,
            "scope": scope,
            "payload": payload,
            "source_datasets": list(source_datasets),
            "source_dataset_versions": {dataset: utcnow().isoformat() for dataset in source_datasets},
            "computed_at": utcnow(),
            "workflow": workflow,
            "processing_mode": processing_mode,
            "schema_version": validators.SCHEMA_VERSION,
        }},
        upsert=True,
    )
    return key


def read_summaries(*, metric: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    criteria: Dict[str, Any] = {}
    if metric:
        criteria["metric"] = metric
    return analytics_summaries.find(criteria, sort=[("computed_at", -1)], limit=limit)


def trend_slope(points: Sequence[Tuple[str, float]]) -> Dict[str, Any]:
    """Ordinary least squares slope per year - used for decadal trend reporting."""
    if len(points) < 3:
        return {"available": False, "note": "Fewer than three periods are available."}
    xs = list(range(len(points)))
    ys = [float(value) for _, value in points]
    frame = pd.DataFrame({"x": xs, "y": ys})
    slope = float(frame["x"].corr(frame["y"]) * frame["y"].std() / frame["x"].std()) \
        if frame["x"].std() else 0.0
    mean_year_length = 1.0
    return {
        "available": True,
        "slope_per_period": _round(slope, 4),
        "periods": len(points),
        "first_value": _round(ys[0]),
        "last_value": _round(ys[-1]),
        "total_change": _round(ys[-1] - ys[0]),
        "method": "least-squares on the aggregated period series",
        "period_labels": [label for label, _ in points],
        "mean_year_length": mean_year_length,
    }


def _round(value: Any, digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


def _empty_series(metric: str, frequency: str, note: str) -> Dict[str, Any]:
    return {
        "metric": metric,
        "label": METRIC_LABELS[metric],
        "unit": METRIC_UNITS[metric],
        "frequency": frequency,
        "series": [],
        "point_count": 0,
        "coverage": coverage_report([]),
        "note": note,
    }


def as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return []


def parse_period(moment: datetime) -> Dict[str, int]:
    return {"year": moment.year, "month": moment.month, "day": moment.day}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
