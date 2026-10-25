"""Season-aware statistical anomaly detection.

Baseline = day-of-year climatology (median over ±window days across all
recorded years, leave-one-out) plus a robust scale (1.4826 * MAD). A slot is
only eligible when its baseline sample reaches MIN_BASELINE_SAMPLE; with a
thin baseline the detector REFUSES to call anything exceptional and says so —
two years of data cannot support a 4-sigma claim.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from .. import mlconfig
from . import severity as S
from .explain import explain_detection
from .threshold import check_thresholds

MAD_SCALE = 1.4826


def _dayofyear_window(all_values: pd.Series, ts: pd.Timestamp, window_days: int) -> pd.Series:
    doy = ts.dayofyear
    offsets = np.arange(-window_days, window_days + 1)
    days = ((doy - 1 + offsets) % 366) + 1
    mask = all_values.index.dayofyear.isin(days)
    sample = all_values[mask]
    return sample.drop(index=ts, errors="ignore")  # leave-one-out


def statistical_anomalies(series: pd.Series, variable: str, region: str,
                          unit: str = "1", origin: Optional[str] = None,
                          window_days: int = 7, min_baseline: Optional[int] = None,
                          z_info: float = S.Z_INFO, limits: Optional[dict[str, Any]] = None,
                          max_detections: int = 500) -> dict[str, Any]:
    observed = series.dropna()
    if origin:
        cutoff = pd.to_datetime(origin, utc=True)
        observed = observed[observed.index <= cutoff]
    if observed.empty:
        return {"status": "no_data", "detections": [],
                "reason": f"no observed {variable} values for {region}"}

    min_baseline = mlconfig.min_baseline_sample() if min_baseline is None else int(min_baseline)
    threshold_result = check_thresholds(observed, variable, limits)
    breach_by_ts = {d["timestamp"]: d["breach_level"] for d in threshold_result.get("detections", [])}

    detections: list[dict[str, Any]] = []
    insufficient = 0
    degenerate = 0
    for ts, value in observed.items():
        sample = _dayofyear_window(observed, ts, window_days)
        if len(sample) < min_baseline:
            insufficient += 1
            continue  # thin baseline: never declare "exceptional"
        med = float(np.median(sample))
        mad = float(np.median(np.abs(sample - med))) * MAD_SCALE
        if mad <= 1e-12:
            std = float(np.std(sample, ddof=1)) if len(sample) > 1 else 0.0
            if std <= 1e-12:
                degenerate += 1
                sigma = None
            else:
                sigma = (float(value) - med) / std
        else:
            sigma = (float(value) - med) / mad
        breach_level = breach_by_ts.get(str(ts))
        if sigma is None and breach_level is None:
            continue
        sev = S.severity_for(sigma, breach_level)
        if sev == "none":
            continue
        if sigma is not None and abs(sigma) < z_info and breach_level is None:
            continue
        evidence = []
        if breach_level:
            evidence.append(f"outside physical/plausibility limits ({breach_level} breach)")
        if sigma is None:
            evidence.append("robust sigma undefined (constant ±{}d climatology); severity from "
                            "threshold breach only".format(window_days))
        detections.append(explain_detection(
            value=value, baseline=med, sigma=sigma, timestamp=ts, region=region,
            variable=variable, unit=unit, method="statistical_doy_climatology_mad",
            severity=sev, evidence=evidence))

    detections.sort(key=lambda d: (S.rank(d["severity"]), abs(d.get("robust_sigma") or 0.0)),
                    reverse=True)
    status = "ok" if detections or insufficient < len(observed) else "insufficient_baseline"
    notes = []
    if insufficient:
        notes.append(f"{insufficient}/{len(observed)} slots skipped: day-of-year baseline had fewer than "
                     f"{min_baseline} comparable observations (MIN_BASELINE_SAMPLE) — no anomaly claimed "
                     f"for them")
    if degenerate:
        notes.append(f"{degenerate} slots had a constant climatology window (MAD=0): z undefined, "
                     f"thresholds only")
    return {
        "status": status,
        "region": region, "variable": variable, "unit": unit,
        "params": {"window_days": window_days, "min_baseline_sample": min_baseline,
                   "z_info": z_info, "leave_one_out": True},
        "checked": int(len(observed)),
        "flagged": len(detections),
        "insufficient_baseline_slots": insufficient,
        "degenerate_baseline_slots": degenerate,
        "detections": detections[:max_detections],
        "truncated": max(0, len(detections) - max_detections),
        "threshold_summary": {k: threshold_result.get(k) for k in ("available", "violations", "reason")},
        "notes": notes,
        "note": "Deviations are measured against this location's own recorded seasonality. "
                "They are observations, not causal attributions.",
    }
