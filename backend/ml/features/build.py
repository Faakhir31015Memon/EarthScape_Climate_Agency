"""Feature engineering for climate time series.

A feature for a row dated d uses only values strictly before d, so training rows and
recursive inference share the code without leakage. Missing measurements stay NaN with
an indicator column instead of being filled with zero, and every fit (climatology,
imputation median) comes from the training window and is frozen into the artifact.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from common import analytics
from common.analytics import InsufficientData, seasonal_baselines

LAGS: Tuple[int, ...] = (1, 7, 14, 30)
WINDOWS: Tuple[int, ...] = (7, 30, 90)
ANOMALY_WINDOWS: Tuple[int, ...] = (7, 30)

# Co-metrics carried into the feature vector as lag-1 covariates per target.
COVARIATE_SETS: Dict[str, Tuple[str, ...]] = {
    "temperature_c": ("temperature_min_c", "temperature_max_c", "relative_humidity_pct",
                      "cloud_coverage_pct", "solar_radiation_kwh_m2", "surface_pressure_hpa"),
    "temperature_min_c": ("temperature_c", "temperature_max_c", "relative_humidity_pct",
                          "cloud_coverage_pct", "solar_radiation_kwh_m2"),
    "temperature_max_c": ("temperature_c", "temperature_min_c", "relative_humidity_pct",
                          "cloud_coverage_pct", "solar_radiation_kwh_m2"),
    "precipitation_mm": ("relative_humidity_pct", "surface_pressure_hpa",
                         "cloud_coverage_pct", "solar_radiation_kwh_m2"),
    "relative_humidity_pct": ("temperature_c", "surface_pressure_hpa", "cloud_coverage_pct"),
    "wind_speed_ms": ("surface_pressure_hpa", "temperature_c"),
    "wind_direction_deg": ("surface_pressure_hpa", "temperature_c"),
    "surface_pressure_hpa": ("temperature_c", "wind_speed_ms"),
    "cloud_coverage_pct": ("temperature_c", "relative_humidity_pct", "solar_radiation_kwh_m2"),
    "solar_radiation_kwh_m2": ("temperature_c", "cloud_coverage_pct"),
}
DEFAULT_COVARIATES: Tuple[str, ...] = ("relative_humidity_pct", "surface_pressure_hpa")


def covariates_for(target: str) -> List[str]:
    return list(COVARIATE_SETS.get(target, DEFAULT_COVARIATES))


def _parse_day(raw: Any) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _to_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


class LocationHistory:
    """Ascending date axis + aligned value arrays for one location.

    features_at builds the vector for a date from entries strictly before it;
    append extends the axis so recursive predictions feed back as observations.
    """

    def __init__(self, location_code: str, region: str, latitude: float, longitude: float,
                 cov_names: Sequence[str], lags: Sequence[int], windows: Sequence[int],
                 baselines: Dict[str, float]):
        self.location_code = location_code
        self.region = region
        self.latitude = latitude
        self.longitude = longitude
        self.cov_names = list(cov_names)
        self.lags = list(lags)
        self.windows = list(windows)
        self.baselines = baselines
        self.ords: List[int] = []
        self.values: List[Optional[float]] = []
        self.cov: Dict[str, List[Optional[float]]] = {name: [] for name in self.cov_names}
        self._pos_by_ord: Dict[int, int] = {}

    def append(self, day: date, value: Optional[float], cov_values: Optional[Dict[str, Any]] = None,
               overwrite: bool = False) -> None:
        ord_day = day.toordinal()
        if self.ords and ord_day <= self.ords[-1]:
            if overwrite and ord_day == self.ords[-1]:
                self.values[-1] = value
                for name in self.cov_names:
                    self.cov[name][-1] = _to_float((cov_values or {}).get(name))
                return
            if ord_day < self.ords[-1]:
                return  # out-of-order rows are ignored, never re-sorted mid-stream
        self.ords.append(ord_day)
        self.values.append(value)
        for name in self.cov_names:
            self.cov[name].append(_to_float((cov_values or {}).get(name)))
        self._pos_by_ord[ord_day] = len(self.ords) - 1

    def baseline_mean(self, month: int) -> Optional[float]:
        value = self.baselines.get(f"{self.region}|{month}")
        if value is None:
            value = self.baselines.get(f"{self.location_code}|{month}")
        if value is None:
            value = self.baselines.get(f"GLOBAL|{month}")
        return value

    def _lag(self, ord_day: int, lag: int) -> Optional[float]:
        pos = self._pos_by_ord.get(ord_day - lag)
        if pos is None:
            return None
        return self.values[pos]

    def _window(self, ord_day: int, width: int) -> Tuple[int, int]:
        # calendar window of the `width` days strictly before ord_day
        lo = ord_day - width
        hi = ord_day - 1
        a = bisect.bisect_left(self.ords, lo)
        b = bisect.bisect_right(self.ords, hi)
        return a, b

    def features_at(self, day: date) -> Dict[str, Optional[float]]:
        ord_day = day.toordinal()
        feats: Dict[str, Optional[float]] = {}
        doy = day.timetuple().tm_yday
        feats["year"] = float(day.year)
        feats["month"] = float(day.month)
        feats["day"] = float(day.day)
        feats["doy_sin"] = math.sin(2.0 * math.pi * doy / 365.25)
        feats["doy_cos"] = math.cos(2.0 * math.pi * doy / 365.25)
        feats["month_sin"] = math.sin(2.0 * math.pi * day.month / 12.0)
        feats["month_cos"] = math.cos(2.0 * math.pi * day.month / 12.0)
        feats["latitude"] = self.latitude
        feats["longitude"] = self.longitude

        for lag in self.lags:
            feats[f"lag_{lag}"] = self._lag(ord_day, lag)
        feats["missing_lag_1"] = 0.0 if feats["lag_1"] is not None else 1.0
        feats["missing_lag_7"] = 0.0 if feats["lag_7"] is not None else 1.0

        for width in self.windows:
            a, b = self._window(ord_day, width)
            window = [self.values[i] for i in range(a, b) if self.values[i] is not None]
            feats[f"roll_count_{width}"] = float(len(window))
            if not window:
                feats[f"roll_mean_{width}"] = None
                feats[f"roll_std_{width}"] = None
                feats[f"roll_min_{width}"] = None
                feats[f"roll_max_{width}"] = None
            else:
                mean = sum(window) / len(window)
                feats[f"roll_mean_{width}"] = mean
                feats[f"roll_min_{width}"] = min(window)
                feats[f"roll_max_{width}"] = max(window)
                if len(window) >= 2:
                    var = sum((v - mean) ** 2 for v in window) / (len(window) - 1)
                    feats[f"roll_std_{width}"] = math.sqrt(var)
                else:
                    feats[f"roll_std_{width}"] = None

        base_month = self.baseline_mean(day.month)
        feats["clim_baseline_month"] = base_month
        for width in ANOMALY_WINDOWS:
            a, b = self._window(ord_day, width)
            deviations = []
            for i in range(a, b):
                value = self.values[i]
                if value is None:
                    continue
                ref = self.baseline_mean(date.fromordinal(self.ords[i]).month)
                if ref is not None:
                    deviations.append(value - ref)
            feats[f"clim_anomaly_mean_{width}"] = (sum(deviations) / len(deviations)) if deviations else None

        # data-availability descriptors (never imputed with zero)
        last_pos = None
        for i in range(bisect.bisect_left(self.ords, ord_day) - 1, -1, -1):
            if self.values[i] is not None:
                last_pos = i
                break
        feats["days_since_last_obs"] = float(ord_day - self.ords[last_pos]) if last_pos is not None else None
        a365, b365 = self._window(ord_day, 365)
        feats["obs_density_365"] = float(b365 - a365) / 365.0 if self.ords else None

        for name in self.cov_names:
            pos = self._pos_by_ord.get(ord_day - 1)
            value = self.cov[name][pos] if pos is not None else None
            feats[f"cov_{name}_lag1"] = value
            feats[f"cov_{name}_available"] = 0.0 if value is None else 1.0
        return feats


def group_observations(observations: Sequence[Dict[str, Any]], target: str,
                       cov_names: Sequence[str]) -> List[Dict[str, Any]]:
    """Split raw observation documents into per-location ascending streams."""
    by_location: Dict[str, Dict[str, Any]] = {}
    for row in observations:
        code = str(row.get("location_code") or row.get("region_code") or "UNKNOWN").upper()
        day = _parse_day(row.get("observation_date"))
        if day is None:
            continue
        entry = by_location.setdefault(code, {
            "location_code": code,
            "region": str(row.get("region_code") or "GLOBAL"),
            "rows": [],
        })
        entry["rows"].append((day, row))
    groups = []
    for entry in sorted(by_location.values(), key=lambda item: item["location_code"]):
        rows = sorted(entry["rows"], key=lambda item: (item[0],))
        seen: Dict[Any, Any] = {}
        for day, row in rows:  # one row per day, last write wins
            seen[day] = row
        days = sorted(seen)
        groups.append({
            "location_code": entry["location_code"],
            "region": entry["region"],
            "first_row": seen[days[0]],
            "days": days,
            "rows": [seen[day] for day in days],
        })
    return groups


def fit_preprocess(observations: Sequence[Dict[str, Any]], target: str,
                   cov_names: Sequence[str]) -> Dict[str, Any]:
    """Fit every trainable preprocessing step on the training window only."""
    if not observations:
        raise InsufficientData("Cannot fit preprocessing: the training window holds no observations.",
                               available=0, required=1)
    raw_baselines = seasonal_baselines(list(observations), target)
    baselines = {key: float(value["mean"]) for key, value in raw_baselines.items()
                 if value.get("mean") is not None}
    # GLOBAL|month fallback computed on the same training rows.
    frame = analytics.to_frame(list(observations))
    if not frame.empty and target in frame:
        frame = frame.dropna(subset=[target])
        if "month" in frame:
            global_month = frame.groupby("month")[target].mean()
            for month, mean in global_month.items():
                baselines[f"GLOBAL|{int(month)}"] = float(mean)
    return {
        "target_variable": target,
        "covariates": list(cov_names),
        "lags": list(LAGS),
        "windows": list(WINDOWS),
        "anomaly_windows": list(ANOMALY_WINDOWS),
        "baselines": baselines,
        "baseline_groups": len(raw_baselines),
        "imputation": "frozen-median",
    }


def build_histories(groups: Sequence[Dict[str, Any]], target: str, cov_names: Sequence[str],
                   preprocess: Dict[str, Any], upto: Optional[date] = None) -> List[LocationHistory]:
    """Replay observation rows into LocationHistory streams (optionally up to a date)."""
    baselines = preprocess.get("baselines", {})
    histories: List[LocationHistory] = []
    for group in groups:
        history = LocationHistory(group["location_code"], group["region"],
                                  _to_float((group["rows"][0] or {}).get("latitude")) or 0.0,
                                  _to_float((group["rows"][0] or {}).get("longitude")) or 0.0,
                                  cov_names, preprocess.get("lags", list(LAGS)),
                                  preprocess.get("windows", list(WINDOWS)), baselines)
        for day, row in zip(group["days"], group["rows"]):
            if upto is not None and day > upto:
                break
            value = _to_float(row.get(target))
            history.append(day, value, {name: row.get(name) for name in cov_names}, overwrite=True)
        histories.append(history)
    return histories


def build_features(observations: Sequence[Dict[str, Any]], target: str, *,
                   preprocess: Optional[Dict[str, Any]] = None
                   ) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """Turn observation documents into a chronological feature frame.

    Returns ``(frame, feature_definition, preprocess)``. frame carries date,
    location_code, region_code, y plus the feature vector. A supplied preprocess
    reuses its frozen baselines - nothing is re-fitted on unseen data.
    """
    if target not in analytics.METRICS:
        raise InsufficientData(f"Target '{target}' is not a canonical metric.")
    cov_names = covariates_for(target)
    if preprocess is None:
        preprocess = fit_preprocess(observations, target, cov_names)
    else:
        preprocess = dict(preprocess)
        preprocess.setdefault("covariates", cov_names)
        cov_names = preprocess["covariates"]

    groups = group_observations(observations, target, cov_names)
    if not groups:
        raise InsufficientData("No usable observations for feature engineering.", available=0)

    rows: List[Dict[str, Any]] = []
    for group in groups:
        history = LocationHistory(group["location_code"], group["region"],
                                  _to_float((group["rows"][0] or {}).get("latitude")) or 0.0,
                                  _to_float((group["rows"][0] or {}).get("longitude")) or 0.0,
                                  cov_names, preprocess["lags"], preprocess["windows"],
                                  preprocess.get("baselines", {}))
        for day, row in zip(group["days"], group["rows"]):
            feats = history.features_at(day)  # only past values - computed before append
            value = _to_float(row.get(target))
            if value is not None:
                record = dict(feats)
                record.update({"date": day, "location_code": group["location_code"],
                               "region_code": group["region"], "y": value})
                rows.append(record)
            history.append(day, value, {name: row.get(name) for name in cov_names}, overwrite=True)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise InsufficientData(
            f"No observations carry a non-null '{target}' value in the selected scope.", available=0)
    frame = frame.sort_values(["date", "location_code"], kind="stable").reset_index(drop=True)
    feature_names = [column for column in frame.columns
                     if column not in ("date", "location_code", "region_code", "y")]
    preprocess["feature_names"] = feature_names

    definition = {
        "features": feature_names,
        "feature_groups": {
            "calendar": ["year", "month", "day", "doy_sin", "doy_cos", "month_sin", "month_cos"],
            "geography": ["latitude", "longitude"],
            "target_lags": [f"lag_{lag}" for lag in preprocess["lags"]],
            "rolling": [f"roll_{stat}_{width}" for width in preprocess["windows"]
                        for stat in ("mean", "std", "min", "max", "count")],
            "climatology": ["clim_baseline_month",
                            *[f"clim_anomaly_mean_{width}" for width in preprocess.get("anomaly_windows", ANOMALY_WINDOWS)]],
            "availability": ["missing_lag_1", "missing_lag_7", "days_since_last_obs", "obs_density_365"],
            "covariate_lag1": [f"cov_{name}_lag1" for name in cov_names]
                              + [f"cov_{name}_available" for name in cov_names],
        },
        "encodings": {
            "calendar": "year + raw month + day, day-of-year and month via sin/cos pairs",
            "categorical": "no one-hot station identifiers; locality is carried by the "
                           "region-month climatology baseline and latitude/longitude",
            "climatology_baseline": "per (region, month) mean of the target, fitted on the "
                                    "training window with a GLOBAL-month fallback",
        },
        "imputation": "missing values are kept as NaN with indicator columns "
                      "(missing_lag_*, *_available, roll_count_*, days_since_last_obs); "
                      "the linear model additionally imputes training-window medians frozen "
                      "in the artifact. Zero-fill is never used.",
        "window": {
            "lags": list(preprocess["lags"]),
            "rolling_days": list(preprocess["windows"]),
            "mode": "recursive single-step model; multi-step forecasts produced by recursion",
            "row_unit": "one row per (location, observation day); all inputs strictly pre-date the row",
        },
        "target": target,
        "covariates": list(cov_names),
        "leakage_controls": [
            "every feature at date d is computed from values dated < d",
            "climatology baselines and medians fitted on the training window only",
            "chronological train/test split, no shuffling anywhere",
        ],
    }
    return frame, definition, preprocess


__all__ = ["LAGS", "WINDOWS", "LocationHistory", "build_features", "build_histories",
           "covariates_for", "fit_preprocess", "group_observations"]
