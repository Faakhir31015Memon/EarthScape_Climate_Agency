"""Leakage-safe temporal feature derivations.

Every feature at row time ``t`` uses only observations at times ``<= t`` while
the supervised target is ``s(t + horizon_steps)`` (strictly later), so no future
information enters the features. Lags/rollings never look ahead.

Naming convention (deliberate, documented): ``lag_1`` is the most recent
observation ``s(t)``; ``lag_k`` = ``s(t - (k - 1))``.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

DEFAULT_LAGS = [1, 2, 3, 7, 14, 30]
DEFAULT_ROLL_WINDOWS = [7, 14, 30]
DEFAULT_FEATURES = [
    "lag_1", "lag_2", "lag_3", "lag_7", "lag_14", "lag_30",
    "roll_mean_7", "roll_std_7", "roll_min_7", "roll_max_7",
    "roll_mean_14", "roll_mean_30", "roll_std_30", "roll_min_30", "roll_max_30",
    "diff_1", "diff_7",
    "doy_sin", "doy_cos", "month", "year",
    "latitude", "elevation_m",
]

DERIVATIONS: dict[str, str] = {}


def _register(name: str, text: str) -> None:
    DERIVATIONS[name] = text


# ---------------------------------------------------------------- primitives
def lag(series: pd.Series, k: int) -> pd.Series:
    """s(t-(k-1)); k=1 is the latest observation. Strictly past-relative-to-target."""
    _register(f"lag_{k}", f"observed value shifted {k - 1} step(s) back from the feature origin t")
    return series.shift(k - 1)


def rolling_stat(series: pd.Series, window: int, stat: str) -> pd.Series:
    """Rolling {stat} over [t-window+1, t]; full windows only (min_periods=window)."""
    name = f"roll_{stat}_{window}"
    _register(name, f"trailing rolling {stat} of the last {window} observations ending at t "
                    f"(min_periods={window}; NaN until a full window exists)")
    roll = series.rolling(window, min_periods=window)
    return getattr(roll, stat)()


def diff(series: pd.Series, steps: int) -> pd.Series:
    _register(f"diff_{steps}", f"s(t) - s(t-{steps})")
    return series.diff(steps)


def dayofyear_sincos(index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series]:
    doy = pd.DatetimeIndex(index).dayofyear
    angle = 2 * np.pi * doy / 366.0
    _register("doy_sin", "sin(2*pi*day_of_year/366): continuous seasonal encoding")
    _register("doy_cos", "cos(2*pi*day_of_year/366): continuous seasonal encoding")
    return pd.Series(np.sin(angle), index=index), pd.Series(np.cos(angle), index=index)


def calendar_features(index: pd.DatetimeIndex) -> dict[str, pd.Series]:
    idx = pd.DatetimeIndex(index)
    sin, cos = dayofyear_sincos(idx)
    _register("month", "calendar month of the feature origin t")
    _register("year", "calendar year (trend anchor; long-horizon use only)")
    return {"doy_sin": sin, "doy_cos": cos, "month": pd.Series(idx.month, index=idx),
            "year": pd.Series(idx.year, index=idx, dtype="float64")}


# ---------------------------------------------------------------- matrix build
def build_series_frame(series: pd.Series) -> pd.DataFrame:
    df = pd.DataFrame({"value": series})
    df.index.name = "timestamp"
    return df


def add_time_features(df: pd.DataFrame,
                      lags: Optional[list[int]] = None,
                      windows: Optional[list[int]] = None,
                      statics: Optional[dict[str, float]] = None,
                      feature_names: Optional[list[str]] = None) -> pd.DataFrame:
    """df must have a DatetimeIndex and a 'value' column. Returns the raw feature frame."""
    lags = lags or DEFAULT_LAGS
    windows = windows or DEFAULT_ROLL_WINDOWS
    series = df["value"]
    out = pd.DataFrame(index=df.index)

    for k in lags:
        out[f"lag_{k}"] = lag(series, k)
    for w in windows:
        out[f"roll_mean_{w}"] = rolling_stat(series, w, "mean")
        out[f"roll_std_{w}"] = rolling_stat(series, w, "std")
        out[f"roll_min_{w}"] = rolling_stat(series, w, "min")
        out[f"roll_max_{w}"] = rolling_stat(series, w, "max")
    out["diff_1"] = diff(series, 1)
    out["diff_7"] = diff(series, 7)
    for name, values in calendar_features(df.index).items():
        out[name] = values.values
    statics = statics or {}
    for name in ("latitude", "longitude", "elevation_m"):
        if name in statics and statics[name] is not None:
            out[name] = float(statics[name])
            _register(name, "static site attribute (no temporal component)")

    if feature_names is not None:
        missing = [c for c in feature_names if c not in out.columns]
        if missing:
            raise ValueError(f"incoming frame lacks features required by the stored spec: {missing}")
        out = out[feature_names]
    return out


def build_feature_matrix(series: pd.Series, horizon_steps: int,
                         feature_names: Optional[list[str]] = None,
                         statics: Optional[dict[str, float]] = None,
                         lags: Optional[list[int]] = None,
                         windows: Optional[list[int]] = None):
    """Single entry point used IDENTICALLY at training and inference.

    Returns (X, y, index): X[t] uses only observations <= t; y[t] = s(t + horizon).
    """
    df = build_series_frame(series)
    X = add_time_features(df, lags=lags, windows=windows, statics=statics, feature_names=feature_names)
    y = series.shift(-horizon_steps)
    y.name = "target"
    return X, y, df.index


def future_value_leak_feature(df: pd.DataFrame) -> pd.Series:
    """DELIBERATELY LEAKY, test-only: peeks at s(t+1). Caught by leakage_check."""
    _register("leaky_next_value", "s(t+1): forbidden future peek (exists only for the leakage test)")
    return df["value"].shift(-1)


def leakage_check(df: pd.DataFrame, horizon_steps: int,
                  builder=None, lags: Optional[list[int]] = None,
                  windows: Optional[list[int]] = None) -> list[str]:
    """Rebuild features after scrambling the tail; any change at earlier rows = leak.

    Returns the offending feature names ([] when clean). Also flags the presence of
    any column whose name pattern is known-forbidden (direct future peek).
    """
    builder = builder or add_time_features
    n = len(df)
    cut = max(1, n // 3)
    tampered = df.copy()
    rng = np.random.default_rng(0)
    tampered.iloc[n - cut:, tampered.columns.get_loc("value")] = rng.normal(0, 1e6, size=cut)

    X_orig = builder(df.copy(), lags=lags, windows=windows)
    X_tamp = builder(tampered.copy(), lags=lags, windows=windows)
    safe_rows = n - cut - horizon_steps  # rows whose target is not in the tampered zone
    violations: list[str] = []
    if safe_rows > 0:
        head_orig = X_orig.iloc[:safe_rows].to_numpy(dtype="float64")
        head_tamp = X_tamp.iloc[:safe_rows].to_numpy(dtype="float64")
        for j, col in enumerate(X_orig.columns):
            a, b = head_orig[:, j], head_tamp[:, j]
            same = np.array_equal(a, b) or (np.allclose(np.nan_to_num(a, nan=-9e18),
                                                        np.nan_to_num(b, nan=-9e18), rtol=1e-9))
            if not same:
                violations.append(str(col))
    forbidden = [c for c in X_orig.columns if "leaky" in str(c) or str(c).startswith("future")]
    if forbidden:
        violations.extend(forbidden)
    return sorted(set(violations))


def feature_derivations(names: list[str]) -> list[dict[str, str]]:
    """Provenance records for the registry: name + how it was derived."""
    records = []
    for name in names:
        text = DERIVATIONS.get(name)
        if text is None:
            prefix = name.split("_")[0]
            fallback = {
                "roll": f"trailing rolling {name.split('_')[1]} over {name.split('_')[-1]} steps ending at t",
                "lag": "observed value strictly at or before the feature origin t",
            }.get(prefix, "temporal feature over observations at or before t")
            text = fallback
        records.append({"name": name, "derivation": text})
    return records
