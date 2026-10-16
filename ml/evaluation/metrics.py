"""Prediction error metrics.

Every number returned here must originate from a real executed train/eval run;
MAPE is deliberately skipped (with a reason) when actuals are ~zero, which is
the norm for precipitation.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

MAPE_ZERO_TOLERANCE = 1e-9


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(np.asarray(y_true) - np.asarray(y_pred))))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((np.asarray(y_true) - np.asarray(y_pred)) ** 2)))


def mape(y_true: np.ndarray, y_pred: np.ndarray,
         zero_tolerance: float = MAPE_ZERO_TOLERANCE) -> tuple[Optional[float], Optional[str]]:
    """Return (mape, None) or (None, reason). Skipped when any actual is ~zero."""
    actual = np.asarray(y_true, dtype="float64")
    if actual.size == 0:
        return None, "no evaluation samples"
    near_zero = int(np.sum(np.abs(actual) <= zero_tolerance))
    if near_zero:
        return None, (f"skipped: {near_zero}/{actual.size} actual values are ~0; MAPE is undefined/inflated "
                      f"near zero (typical for dry-day precipitation)")
    pred = np.asarray(y_pred, dtype="float64")
    return float(np.mean(np.abs((actual - pred) / actual)) * 100.0), None


def r2(y_true: np.ndarray, y_pred: np.ndarray) -> Optional[float]:
    actual = np.asarray(y_true, dtype="float64")
    pred = np.asarray(y_pred, dtype="float64")
    ss_res = float(np.sum((actual - pred) ** 2))
    ss_tot = float(np.sum((actual - np.mean(actual)) ** 2))
    if ss_tot <= 1e-12:
        return None
    return 1.0 - ss_res / ss_tot


def skill_vs_baseline(model_error: float, baseline_error: float) -> Optional[float]:
    """Fractional improvement over the baseline: (baseline - model) / baseline.

    <= 0 means the model does not beat the baseline at all.
    """
    if baseline_error <= 0:
        return None
    return float((baseline_error - model_error) / baseline_error)


def evaluate_forecast(y_true: Any, y_pred: Any) -> dict[str, Any]:
    """Full metric bundle with honest MAPE/R2 skip reasons."""
    y_true_arr = np.asarray(y_true, dtype="float64")
    y_pred_arr = np.asarray(y_pred, dtype="float64")
    mape_value, mape_reason = mape(y_true_arr, y_pred_arr)
    r2_value = r2(y_true_arr, y_pred_arr)
    out: dict[str, Any] = {
        "n": int(y_true_arr.size),
        "mae": mae(y_true_arr, y_pred_arr),
        "rmse": rmse(y_true_arr, y_pred_arr),
        "mape": mape_value,
        "r2": r2_value,
    }
    if mape_value is None:
        out["mape_skipped_reason"] = mape_reason
    if r2_value is None:
        out["r2_skipped_reason"] = "actual variance ~0 on the evaluation window"
    return out
