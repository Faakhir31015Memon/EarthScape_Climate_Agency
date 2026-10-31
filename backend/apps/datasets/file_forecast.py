"""Year-ahead projection from an uploaded table.

The uploaded file is the whole training set: annual means per numeric column, fitted with
a least-squares trend plus a monthly climatology. Leave-one-year-out error is measured
against the years actually present, so a forecast that cannot beat "use the average" is
reported as such instead of dressing up as a model.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

MIN_YEARS = 3
MIN_YEAR_COVERAGE = 6
FORECAST_MEASURES = 3
MONTHS = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12")


class ForecastUnavailable(Exception):
    """Raised when the file cannot support an honest projection."""


def build_forecast(samples: Sequence[Tuple[datetime, float]], target_year: int, column: str) -> Dict[str, Any]:
    yearly = _yearly_means(samples)
    usable = [entry for entry in yearly if entry["months"] >= MIN_YEAR_COVERAGE]
    if len(usable) < MIN_YEARS:
        raise ForecastUnavailable(
            f"{column} has {len(usable)} usable year(s); at least {MIN_YEARS} are needed to project a future year."
        )

    years = [entry["year"] for entry in usable]
    means = [entry["mean"] for entry in usable]
    slope, intercept = _least_squares(years, means)
    seasonal = _monthly_offsets(usable)
    residual = _residual_sd(years, means, slope, intercept)
    loyo = _leave_one_year_out(usable)

    fitted = [intercept + slope * year for year in years]
    projected_year = intercept + slope * target_year
    months: List[Dict[str, Any]] = []
    for index, month in enumerate(MONTHS):
        value = projected_year + seasonal.get(index + 1, 0.0)
        months.append({
            "period": f"{target_year}-{month}",
            "value": _num(value),
            "low": _num(value - 1.96 * residual),
            "high": _num(value + 1.96 * residual),
        })

    return {
        "column": column,
        "target_year": target_year,
        "basis_years": {"first": years[0], "last": years[-1]},
        "observed": [{"year": year, "value": _num(value)} for year, value in zip(years, means)],
        "fitted": [{"year": year, "value": _num(value)} for year, value in zip(years, fitted)],
        "monthly": months,
        "annual": _num(projected_year),
        "annual_low": _num(projected_year - 1.96 * residual),
        "annual_high": _num(projected_year + 1.96 * residual),
        "trend_per_year": _num(slope),
        "uncertainty": _num(residual),
        "metrics": _metrics(means, fitted, loyo),
        "extrapolated": target_year > years[-1] or target_year < years[0],
    }


def _yearly_means(samples: Sequence[Tuple[datetime, float]]) -> List[Dict[str, Any]]:
    per_year: Dict[int, List[float]] = {}
    per_month: Dict[int, Dict[int, List[float]]] = {}
    for moment, value in samples:
        per_year.setdefault(moment.year, []).append(value)
        per_month.setdefault(moment.year, {}).setdefault(moment.month, []).append(value)
    return [
        {"year": year, "mean": statistics.fmean(values), "by_month": per_month[year],
         "months": len(per_month[year])}
        for year, values in sorted(per_year.items())
    ]


def _monthly_offsets(usable: List[Dict[str, Any]]) -> Dict[int, float]:
    offsets: Dict[int, List[float]] = {}
    for entry in usable:
        for month, values in entry["by_month"].items():
            offsets.setdefault(month, []).append(statistics.fmean(values) - entry["mean"])
    return {month: statistics.fmean(deltas) for month, deltas in offsets.items()}


def _least_squares(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    count = len(xs)
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    if spread <= 0:
        return 0.0, mean_y
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / spread
    return slope, mean_y - slope * mean_x


def _residual_sd(years: Sequence[int], means: Sequence[float], slope: float, intercept: float) -> float:
    residuals = [value - (intercept + slope * year) for year, value in zip(years, means)]
    if len(residuals) < 2:
        return 0.0
    return statistics.pstdev(residuals)


def _leave_one_year_out(usable: List[Dict[str, Any]]) -> List[float]:
    errors: List[float] = []
    for held_out in usable:
        others = [entry for entry in usable if entry is not held_out]
        if len(others) < MIN_YEARS:
            continue
        slope, intercept = _least_squares([entry["year"] for entry in others],
                                          [entry["mean"] for entry in others])
        errors.append(abs(held_out["mean"] - (intercept + slope * held_out["year"])))
    return errors


def _metrics(means: Sequence[float], fitted: Sequence[float], loyo: Sequence[float]) -> Dict[str, Any]:
    in_sample = [observed - predicted for observed, predicted in zip(means, fitted)]
    ss_total = sum((value - statistics.fmean(means)) ** 2 for value in means)
    r2 = 1.0 - sum(value * value for value in in_sample) / ss_total if ss_total > 0 else None
    climatology = statistics.fmean(means[:-1]) if len(means) > 1 else means[0]
    baseline = [abs(value - climatology) for value in means[1:]]
    model = [abs(value) for value in loyo] if loyo else []
    mae = statistics.fmean(model) if model else None
    baseline_mae = statistics.fmean(baseline) if baseline else None
    skill = None
    if mae is not None and baseline_mae not in (None, 0):
        skill = round(1 - mae / baseline_mae, 3)
    return {
        "mae": _num(mae),
        "rmse": _num(math.sqrt(statistics.fmean(value * value for value in model)) if model else None),
        "r2": _num(r2),
        "baseline_mae": _num(baseline_mae),
        "skill_vs_average": skill,
        "years_tested": len(model),
    }


def _num(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, 4 if abs(number) >= 1 else 8)
