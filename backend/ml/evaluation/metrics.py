"""Forecast-quality metrics computed on chronological holdouts only."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _finite_pairs(pairs: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    return [(float(actual), float(predicted)) for actual, predicted in pairs
            if actual is not None and predicted is not None
            and math.isfinite(actual) and math.isfinite(predicted)]


def mae(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    data = _finite_pairs(pairs)
    if not data:
        return None
    return round(sum(abs(a - p) for a, p in data) / len(data), 4)


def rmse(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    data = _finite_pairs(pairs)
    if not data:
        return None
    return round(math.sqrt(sum((a - p) ** 2 for a, p in data) / len(data)), 4)


def r2(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    data = _finite_pairs(pairs)
    if len(data) < 3:
        return None
    mean = sum(a for a, _ in data) / len(data)
    ss_total = sum((a - mean) ** 2 for a, _ in data)
    ss_resid = sum((a - p) ** 2 for a, p in data)
    if ss_total <= 0:
        return None
    return round(1.0 - ss_resid / ss_total, 4)


def mape(pairs: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Mean absolute percentage error - only when every actual is strictly positive."""
    data = _finite_pairs(pairs)
    if len(data) < 3 or any(a <= 0 for a, _ in data):
        return None
    return round(sum(abs(a - p) / a for a, p in data) / len(data) * 100.0, 3)


def skill_vs_baseline(model_mae: Optional[float], baseline_mae: Optional[float]) -> Optional[float]:
    """Fraction of naive-baseline error removed. Negative means the model is worse."""
    if model_mae is None or baseline_mae is None or baseline_mae <= 0:
        return None
    return round(1.0 - model_mae / baseline_mae, 4)


# Coarse lead-time axis. A recursive forecast is only trusted as far as it demonstrably
# removes error against the climatology anchor, and that crossover is read off these
# buckets (see ``skillful_lead_days`` and ml.inference.predictor.forecast).
LEAD_BUCKETS: Tuple[Tuple[str, int], ...] = (
    ("1-7d", 7), ("8-14d", 14), ("15-30d", 30), ("31-60d", 60),
    ("61-90d", 90), ("91-180d", 180), ("181-365d", 365), ("366d+", 36500),
)


def mae_by_lead(step_pairs: Sequence[Tuple[int, float, float]],
                buckets: Sequence[Tuple[str, int]] = LEAD_BUCKETS) -> Dict[str, Optional[float]]:
    """MAE of a forecaster inside each lead-time bucket; buckets with no points stay None."""
    groups: Dict[str, List[Tuple[float, float]]] = {}
    for step, actual, predicted in step_pairs:
        for name, upper in buckets:
            if int(step) <= upper:
                groups.setdefault(name, []).append((actual, predicted))
                break
    return {name: (mae(groups[name]) if name in groups else None) for name, _ in buckets}


def skillful_lead_days(model_by_lead: Dict[str, Optional[float]],
                       anchor_by_lead: Dict[str, Optional[float]],
                       horizon: int,
                       buckets: Sequence[Tuple[str, int]] = LEAD_BUCKETS) -> int:
    """Last lead the model still wins, counting from day 1 and stopping at the first loss."""
    trusted = 0
    for name, upper in buckets:
        model_mae_value = model_by_lead.get(name)
        anchor_mae_value = anchor_by_lead.get(name)
        if model_mae_value is None or anchor_mae_value is None:
            break
        if model_mae_value > anchor_mae_value:
            break
        trusted = min(upper, horizon) if horizon > 0 else upper
    return trusted


def residual_quantiles(pairs: Sequence[Tuple[float, float]],
                       level: float = 0.95) -> Tuple[Optional[float], Optional[float]]:
    data = _finite_pairs(pairs)
    if len(data) < 8:
        return None, None
    residuals = sorted(a - p for a, p in data)
    alpha = (1.0 - level) / 2.0
    lo = residuals[max(0, math.floor(alpha * len(residuals)))]
    hi = residuals[min(len(residuals) - 1, math.ceil((1.0 - alpha) * len(residuals)) - 1)]
    return round(lo, 4), round(hi, 4)


def per_step_residual_quantiles(step_pairs: Sequence[Tuple[int, float, float]],
                                horizon: int, level: float = 0.95
                                ) -> List[Optional[List[float]]]:
    """Empirical residual quantiles per lead-time step; NaN-free bands for the artifact.

    ``step_pairs`` items are ``(lead_step, actual, predicted)``. Steps with too few
    samples fall back to a global-std normal approximation, recorded in the
    uncertainty note.
    """
    buckets: Dict[int, List[Tuple[float, float]]] = {}
    all_pairs: List[Tuple[float, float]] = []
    for step, actual, predicted in step_pairs:
        step = max(1, min(int(step), max(1, int(horizon))))
        buckets.setdefault(step, []).append((actual, predicted))
        all_pairs.append((actual, predicted))
    global_lo, global_hi = residual_quantiles(all_pairs, level=level)
    data = _finite_pairs(all_pairs)
    std = None
    if len(data) >= 8:
        mean_err = sum(a - p for a, p in data) / len(data)
        std = math.sqrt(sum((a - p - mean_err) ** 2 for a, p in data) / (len(data) - 1))
    bands: List[Optional[List[float]]] = []
    for step in range(1, max(1, int(horizon)) + 1):
        pairs = buckets.get(step) or []
        lo, hi = residual_quantiles(pairs, level=level) if len(pairs) >= 8 else (None, None)
        if lo is None and std is not None:
            scale = math.sqrt(step)
            lo, hi = -1.96 * std * scale, 1.96 * std * scale
        if lo is None and global_lo is not None:
            lo, hi = global_lo, global_hi
        bands.append([round(lo, 4), round(hi, 4)] if lo is not None and hi is not None else None)
    return bands


def evaluate(pairs: Sequence[Tuple[float, float]],
             baseline_mae_value: Optional[float] = None) -> Dict[str, Any]:
    result = {
        "mae": mae(pairs),
        "rmse": rmse(pairs),
        "mape_or_null": mape(pairs),
        "r2": r2(pairs),
        "evaluated_points": len(_finite_pairs(pairs)),
        "baseline_mae": baseline_mae_value,
        "skill_vs_baseline": skill_vs_baseline(mae(pairs), baseline_mae_value),
    }
    return result


def holdout_comparison_table(run_results: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Comparison rows: each algorithm plus both naive baselines on the same holdout."""
    table: List[Dict[str, Any]] = []
    for entry in run_results:
        metrics = entry.get("metrics") or {}
        table.append({
            "model": entry.get("algorithm"),
            "train_size": entry.get("train_size"),
            "test_size": entry.get("test_size"),
            "mae": metrics.get("mae"),
            "rmse": metrics.get("rmse"),
            "mape_pct": metrics.get("mape_or_null"),
            "r2": metrics.get("r2"),
            "skill_vs_naive": metrics.get("skill_vs_baseline"),
        })
    for key, label in (("naive_baseline", "baseline: naive persistence"),
                       ("seasonal_baseline", "baseline: seasonal-naive (365 d)"),
                       ("climatology_baseline", "baseline: stored-record (region, month) climatology")):
        metrics = (run_results[0].get(key) or {}) if run_results else {}
        table.append({
            "model": label, "train_size": None,
            "test_size": metrics.get("evaluated_points"),
            "mae": metrics.get("mae"), "rmse": metrics.get("rmse"),
            "mape_pct": metrics.get("mape_or_null"), "r2": metrics.get("r2"),
            "skill_vs_naive": None,
        })
    return table


__all__ = ["mae", "rmse", "r2", "mape", "skill_vs_baseline", "residual_quantiles",
           "per_step_residual_quantiles", "evaluate", "holdout_comparison_table"]
