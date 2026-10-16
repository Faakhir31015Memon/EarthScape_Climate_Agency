"""Analytics computations (trends, regions, correlations) built from stored observations.

Linear slope is ordinary least squares (numpy.polyfit degree 1) over the bucket index;
correlation is Pearson (pandas DataFrame.corr). Nothing here invents data: when a
variable has no stored readings the result is an explicit empty state, never a series of
placeholders.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from apps.climate import repository


def _empty(variable: str, reason_suffix: str) -> dict:
    return {"available": False, "variable": variable,
            "reason": f"no {variable} measurements {reason_suffix}"}


def trends(variable: str, *, region: Optional[str] = None, bucket: str = "month",
           granularity: Optional[str] = None, date_from: Any = None,
           date_to: Any = None) -> dict:
    """Monthly/annual means with year-over-year deltas and an OLS slope over the buckets."""
    rows = repository.variable_series(
        variable, region=region, granularity=granularity,
        date_from=date_from, date_to=date_to, bucket=bucket,
    )
    if not rows:
        result = _empty(variable, "stored in the selected window")
        result["region"] = region
        result["bucket"] = bucket
        return result

    frame = pd.DataFrame(rows)
    frame["mean"] = frame["mean"].astype(float)
    sample_size = int(frame["sample_size"].sum())

    deltas = _year_over_year(frame, bucket)
    slope, intercept = _linear_fit(frame["mean"].to_numpy(dtype=float))
    total_change = float(slope * (len(frame) - 1)) if len(frame) > 1 else 0.0

    series = [
        {"period": str(row["period"]), "sample_size": int(row["sample_size"]),
         "mean": round(float(row["mean"]), 3),
         "minimum": round(float(row["minimum"]), 3) if row["minimum"] is not None else None,
         "maximum": round(float(row["maximum"]), 3) if row["maximum"] is not None else None,
         "yoy_delta": deltas.get(str(row["period"]))}
        for row in rows
    ]
    return {
        "available": True,
        "variable": variable,
        "region": region,
        "granularity": granularity,
        "bucket": bucket,
        "method": {
            "aggregation": f"{bucket}ly mean of stored metric values",
            "trend": "numpy.polyfit linear slope (least squares) over bucket index",
            "delta": "difference vs the same period one year earlier" if bucket == "month"
                     else "difference vs the previous period",
        },
        "period_covered": {"start": series[0]["period"], "end": series[-1]["period"]},
        "sample_size": sample_size,
        "observations_in_window": len(series),
        "slope_per_bucket": round(float(slope), 5),
        "intercept": round(float(intercept), 5),
        "net_change_over_window": round(total_change, 3),
        "direction": _direction(float(slope)),
        "overall_mean": round(float(frame["mean"].mean()), 3),
        "series": series,
    }


def _linear_fit(values: np.ndarray) -> tuple[float, float]:
    """OLS slope/intercept over the bucket index; honest no-trend result when undetermined."""
    if values.size < 2 or np.allclose(np.diff(values), 0.0):
        return 0.0, float(values[0]) if values.size else 0.0
    x = np.arange(values.size, dtype=float)
    try:
        slope, intercept = np.polyfit(x, values, 1)
    except np.linalg.LinAlgError:
        return 0.0, float(np.mean(values))
    if np.isnan(slope) or np.isnan(intercept):
        return 0.0, float(np.mean(values))
    return float(slope), float(intercept)


def _year_over_year(frame: pd.DataFrame, bucket: str) -> dict[str, float]:
    means = {str(row["period"]): float(row["mean"]) for _, row in frame.iterrows()}
    deltas: dict[str, float] = {}
    if bucket != "month":
        periods = list(means.keys())
        for index in range(1, len(periods)):
            deltas[periods[index]] = round(means[periods[index]] - means[periods[index - 1]], 3)
        return deltas
    for period, value in means.items():
        try:
            year, month = period.split("-")
            previous = f"{int(year) - 1}-{month}"
        except (ValueError, AttributeError):
            continue
        if previous in means:
            deltas[period] = round(value - means[previous], 3)
    return deltas


def _direction(slope: float) -> str:
    if abs(slope) < 1e-6:
        return "flat"
    return "increasing" if slope > 0 else "decreasing"


def regions_summary() -> dict:
    """Region rows that drive the map and region filters, from stored locations."""
    rows = repository.region_rows()
    items = []
    for row in rows:
        items.append({
            "region": row["_id"],
            "country": row.get("country"),
            "station_id": row.get("station_id"),
            "observations": int(row.get("observations") or 0),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "bounding_box": {"min_lat": row.get("min_lat"), "max_lat": row.get("max_lat"),
                              "min_lon": row.get("min_lon"), "max_lon": row.get("max_lon")},
            "latest_timestamp": row.get("latest_timestamp"),
            "latest_values": {
                "temperature": row.get("latest_temperature"),
                "precipitation": row.get("latest_precipitation"),
            },
            "averages": {
                "temperature": _round(row.get("avg_temperature")),
                "temperature_max": _round(row.get("avg_temperature_max")),
                "temperature_min": _round(row.get("avg_temperature_min")),
                "precipitation": _round(row.get("avg_precipitation")),
            },
            "suspect_observations": int(row.get("suspect") or 0),
        })
    return {
        "available": bool(items),
        "region_count": len(items),
        "reason": None if items else "no observations with a location.region have been ingested yet",
        "regions": items,
    }


def map_points(mode: str = "markers", cap: int = 500) -> dict:
    """Compact per-region points for React-Leaflet; anomaly_count from stored alert events."""
    mode = mode if mode in ("markers", "heatmap", "choropleth") else "markers"
    regions = repository.region_rows()
    alerts = repository.alerts_overview(limit=0)
    by_region = alerts.get("by_region", {})
    points = []
    for row in regions[:cap]:
        region = row["_id"]
        temperature = row.get("latest_temperature")
        if temperature is None:
            temperature = row.get("avg_temperature")
        points.append({
            "region": region,
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "temperature": _round(temperature),
            "precipitation": _round(row.get("latest_precipitation", row.get("avg_precipitation"))),
            "anomaly_count": int(by_region.get(region, 0)),
            "observations": int(row.get("observations") or 0),
        })
    return {
        "mode": mode,
        "available": bool(points),
        "reason": None if points else "no geolocated observations ingested yet",
        "cap": cap,
        "count": len(points),
        "points": points,
    }


def correlations(variable_a: str, variable_b: str, *, region: Optional[str] = None,
                 date_from: Any = None, date_to: Any = None, min_samples: int = 8) -> dict:
    """Pearson correlation between two variables over rows that carry both as numbers."""
    rows = repository.variable_pairs(variable_a, variable_b, region=region,
                                     date_from=date_from, date_to=date_to)
    field_a = repository.metric_value_field(variable_a)
    field_b = repository.metric_value_field(variable_b)
    n = len(rows)
    window = {
        "from": rows[0]["timestamp"].isoformat() if rows else None,
        "to": rows[-1]["timestamp"].isoformat() if rows else None,
    }
    base = {"pair": [variable_a, variable_b], "region": region, "window": window, "n": n,
            "method": "pearson"}
    if n == 0:
        return {**base, "status": "insufficient_data",
                "reason": f"no stored observations carry both {variable_a} and {variable_b}",
                "correlation": None}
    if n < min_samples:
        return {**base, "status": "insufficient_data",
                "reason": f"need at least {min_samples} shared samples, found {n}",
                "correlation": None}

    series_a = [float(_dig(r, field_a)) for r in rows]
    series_b = [float(_dig(r, field_b)) for r in rows]
    frame = pd.DataFrame({variable_a: series_a, variable_b: series_b})
    matrix = frame.corr(method="pearson")
    coefficient = float(matrix.loc[variable_a, variable_b])
    if np.isnan(coefficient):
        return {**base, "status": "insufficient_data",
                "reason": "one variable is constant across the sample, correlation undefined",
                "correlation": None}
    slope, intercept = np.array([0.0, 0.0])
    try:
        coef = np.polyfit(np.array(series_a, dtype=float), np.array(series_b, dtype=float), 1)
        slope, intercept = float(coef[0]), float(coef[1])
    except (np.linalg.LinAlgError, ValueError, TypeError):
        slope = intercept = float("nan")
    return {
        **base,
        "status": "ok",
        "correlation": round(coefficient, 4),
        "strength": _strength(coefficient),
        "sign": "positive" if coefficient > 0 else "negative" if coefficient < 0 else "none",
        "regression": {"slope": round(slope, 5) if slope == slope else None,
                       "intercept": round(intercept, 4) if intercept == intercept else None},
        "summary": {
            variable_a: {"mean": round(float(frame[variable_a].mean()), 3),
                         "std": round(float(frame[variable_a].std(ddof=1)), 3)},
            variable_b: {"mean": round(float(frame[variable_b].mean()), 3),
                         "std": round(float(frame[variable_b].std(ddof=1)), 3)},
        },
        "note": "Correlation is a statistical association computed from stored samples; "
                "it is not evidence of causation.",
    }


def _strength(value: float) -> str:
    magnitude = abs(value)
    if magnitude >= 0.8:
        return "very strong"
    if magnitude >= 0.6:
        return "strong"
    if magnitude >= 0.4:
        return "moderate"
    if magnitude >= 0.2:
        return "weak"
    return "negligible"


def _dig(row: dict, dotted: str):
    value = row
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _round(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 3)
