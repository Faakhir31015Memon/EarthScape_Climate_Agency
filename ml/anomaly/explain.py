"""Explanation records for anomaly detections.

Every detection carries the OBSERVABLE deviation that produced it — value vs
baseline, sigma distance, season, region, method — and nothing else. No
attribution of causes: the wording is deliberately restricted to what the
sensors and the climatology actually show.
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd


def describe_season(timestamp: pd.Timestamp) -> dict[str, Any]:
    doy = int(timestamp.dayofyear)
    return {
        "day_of_year": doy,
        "month": int(timestamp.month),
        "date": str(timestamp.date()) if hasattr(timestamp, "date") else str(timestamp),
        "climatology_window": f"±{SEASON_WINDOW_DAYS} days around day-of-year {doy}",
    }


SEASON_WINDOW_DAYS = 7


def explain_detection(value: float, baseline: Optional[float], sigma: Optional[float],
                      timestamp: pd.Timestamp, region: str, variable: str, unit: str,
                      method: str, severity: str,
                      evidence: Optional[list[str]] = None) -> dict[str, Any]:
    """One human+machine readable explanation object for a single detection."""
    deviation = None if baseline is None else round(float(value - baseline), 4)
    tail = None
    if sigma is not None:
        tail = "above" if sigma >= 0 else "below"
    lines = []
    if baseline is not None and sigma is not None:
        lines.append(f"{value:.2f} {unit} vs seasonal baseline {baseline:.2f} {unit} "
                     f"({abs(sigma):.1f} robust sigma {tail})")
    elif baseline is not None:
        lines.append(f"{value:.2f} {unit} vs baseline {baseline:.2f} {unit}")
    if evidence:
        lines.extend(evidence)
    return {
        "timestamp": str(timestamp),
        "region": region,
        "variable": variable,
        "unit": unit,
        "observed_value": round(float(value), 4),
        "baseline_value": None if baseline is None else round(float(baseline), 4),
        "deviation_units": deviation,
        "robust_sigma": None if sigma is None else round(float(sigma), 3),
        "direction": tail,
        "season": describe_season(timestamp),
        "method": method,
        "severity": severity,
        "observable_deviation": "; ".join(lines) or "threshold violation without a fitted baseline",
        "note": ("Severity describes deviation from this location's own recorded climatology. "
                 "It is not a causal attribution and not a statement about climate change."),
    }
