"""Configurable hard physical/plausibility limits per variable.

These are guardrails for sensor faults and impossible values, not climate
statements. Limits are plain defaults; pass ``limits`` (or set the
ANOMALY_LIMITS_JSON env var to a JSON object) to override per deployment.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pandas as pd

from .. import mlconfig

# units follow the canonical schema's sample units
DEFAULT_LIMITS: dict[str, dict[str, Optional[float]]] = {
    "temperature":      {"min": -40.0, "max": 55.0, "unit": "degC"},
    "temperature_max":  {"min": -40.0, "max": 57.0, "unit": "degC"},
    "temperature_min":  {"min": -50.0, "max": 50.0, "unit": "degC"},
    "precipitation":    {"min": -0.1, "max": 500.0, "unit": "mm/day"},
    "humidity":         {"min": 0.0, "max": 100.0, "unit": "%"},
    "wind_speed":       {"min": 0.0, "max": 200.0, "unit": "km/h"},
    "wind_direction":   {"min": 0.0, "max": 360.0, "unit": "deg"},
    "pressure":         {"min": 850.0, "max": 1100.0, "unit": "hPa"},
    "cloud_cover":      {"min": 0.0, "max": 100.0, "unit": "%"},
    "solar_radiation":  {"min": -1.0, "max": 14.0, "unit": "MJ/m2/day"},
}


def load_limits(variable: str, limits: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    if limits and variable in limits:
        return dict(limits[variable])
    env_raw = mlconfig.ENV.raw("ANOMALY_LIMITS_JSON")
    if env_raw:
        try:
            env_limits = json.loads(env_raw)
            if variable in env_limits:
                return dict(env_limits[variable])
        except json.JSONDecodeError:
            pass
    return dict(DEFAULT_LIMITS[variable]) if variable in DEFAULT_LIMITS else None


def check_thresholds(series: pd.Series, variable: str,
                     limits: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Return threshold violations (values outside [min, max]) with severities."""
    rule = load_limits(variable, limits)
    if rule is None:
        return {"available": False,
                "reason": f"no configured limits for variable '{variable}'; refusing to invent any",
                "detections": []}
    lo, hi = rule.get("min"), rule.get("max")
    values = series.dropna()
    detections: list[dict[str, Any]] = []
    for ts, v in values.items():
        breach: Optional[str] = None
        if lo is not None and v < lo:
            breach = "below_min"
        if hi is not None and v > hi:
            breach = "above_max"
        if breach:
            margin = (v - hi) if breach == "above_max" else (lo - v)
            span = max((hi or 0) - (lo or 0), 1e-9)
            detections.append({
                "timestamp": str(ts), "value": float(v), "variable": variable,
                "rule": {"min": lo, "max": hi, "unit": rule.get("unit", "1")},
                "breach": breach, "margin_beyond_limit": round(float(margin), 4),
                # >25% of the allowed span beyond a limit is 'extreme'
                "breach_level": "extreme" if margin / span > 0.25 else "marginal",
                "method": "threshold",
            })
    return {"available": True, "variable": variable, "limits": rule,
            "checked": int(len(values)), "violations": len(detections), "detections": detections}
