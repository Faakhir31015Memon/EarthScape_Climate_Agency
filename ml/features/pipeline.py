"""Feature pipeline: sklearn FunctionTransformer + ColumnTransformer, fit on train only.

``ClimateFeaturePipeline.fit`` learns per-column medians (for missing-value
imputation) and observed min/max ranges **from the training slice only**, and
freezes them into a ``feature_spec`` dict. Inference reuses the identical
derivation through ``build_feature_matrix`` (in time_features) and refuses to
transform a frame whose columns do not match the stored spec.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler
from sklearn.impute import SimpleImputer

from .time_features import (DEFAULT_FEATURES, add_time_features, build_feature_matrix,
                            feature_derivations, leakage_check)

CALENDAR_COLS = {"doy_sin", "doy_cos", "month", "year"}


def _identity(df):
    """Module-level identity transform (lambdas are not joblib/pickle safe)."""
    return df


def build_prep_pipeline(feature_names: list[str], scale: bool = False) -> Pipeline:
    numeric = [c for c in feature_names if c not in CALENDAR_COLS]
    calendar = [c for c in feature_names if c in CALENDAR_COLS]
    numeric_steps: list[tuple[str, Any]] = [("impute", SimpleImputer(strategy="median"))]
    if scale:
        numeric_steps.append(("scale", StandardScaler()))
    transforms: list[tuple[str, Any, list[str]]] = [("numeric", Pipeline(numeric_steps), numeric)]
    if calendar:
        transforms.append(("calendar", SimpleImputer(strategy="median",
                                                     fill_value=0.0), calendar))
    prep = ColumnTransformer(transforms, remainder="drop")
    return Pipeline([
        ("select", FunctionTransformer(_identity, validate=True)),
        ("prep", prep),
    ])


class ClimateFeaturePipeline:
    """Owns the feature spec: names, derivations, train-fitted imputation stats."""

    def __init__(self, feature_names: Optional[list[str]] = None,
                 lags: Optional[list[int]] = None, windows: Optional[list[int]] = None,
                 statics: Optional[dict[str, float]] = None, scale: bool = False) -> None:
        self.feature_names = list(feature_names or DEFAULT_FEATURES)
        self.lags = lags
        self.windows = windows
        self.statics = statics or {}
        self.scale = scale
        self.pipeline_: Optional[Pipeline] = None
        self.spec_: Optional[dict[str, Any]] = None
        self.median_by_column_: dict[str, float] = {}
        self.range_by_column_: dict[str, tuple[float, float]] = {}

    # -------------------------------------------------------------- derivation
    def derive(self, series: pd.Series, feature_names: Optional[list[str]] = None) -> pd.DataFrame:
        return add_time_features(pd.DataFrame({"value": series}),
                                 lags=self.lags, windows=self.windows, statics=self.statics,
                                 feature_names=feature_names or self.feature_names)

    def matrix(self, series: pd.Series, horizon_steps: int):
        """Identical at train and inference: (X_raw, y, index)."""
        return build_feature_matrix(series, horizon_steps, feature_names=self.feature_names,
                                    statics=self.statics, lags=self.lags, windows=self.windows)

    # -------------------------------------------------------------- fit / transform
    def fit(self, X_train: pd.DataFrame, gap_stats: Optional[dict[str, int]] = None) -> "ClimateFeaturePipeline":
        X_train = X_train[self.feature_names]
        self.median_by_column_ = {c: float(np.nanmedian(X_train[c].to_numpy(dtype="float64")))
                                  for c in X_train.columns}
        self.range_by_column_ = {c: (float(np.nanmin(X_train[c].to_numpy(dtype="float64"))),
                                     float(np.nanmax(X_train[c].to_numpy(dtype="float64"))))
                                 for c in X_train.columns}
        self.pipeline_ = build_prep_pipeline(self.feature_names, scale=self.scale)
        self.pipeline_.fit(X_train, None)
        self.spec_ = {
            "feature_names": list(self.feature_names),
            "derivations": feature_derivations(self.feature_names),
            "imputation": {"strategy": "train_median", "medians": self.median_by_column_,
                           "fitted_on": "training slice only"},
            "training_ranges": {c: list(v) for c, v in self.range_by_column_.items()},
            "scaling": self.scale,
            "gap_handling": gap_stats or {"policy": "missing stays missing; train-median imputation at model level"},
        }
        return self

    def transform(self, X: pd.DataFrame) -> np.ndarray:
        if self.pipeline_ is None:
            raise RuntimeError("feature pipeline not fitted")
        validate_feature_spec(self.spec_, list(X.columns))
        return np.asarray(self.pipeline_.transform(X[self.feature_names]), dtype="float64")

    # -------------------------------------------------------------- leakage guard
    def check_leakage(self, series: pd.Series, horizon_steps: int) -> list[str]:
        def builder(df, lags=None, windows=None):
            return add_time_features(df, lags=lags or self.lags, windows=windows or self.windows,
                                     statics=self.statics)
        df = pd.DataFrame({"value": series})
        return leakage_check(df, horizon_steps, builder=builder, lags=self.lags, windows=self.windows)


def validate_feature_spec(spec: Optional[dict[str, Any]], incoming_columns: list[str]) -> None:
    """Refuse to predict when the stored feature spec does not match the frame."""
    if not spec or "feature_names" not in spec:
        raise ValueError("model is missing a feature spec; refusing to predict")
    stored = list(spec["feature_names"])
    incoming = list(incoming_columns)
    missing = [c for c in stored if c not in incoming]
    extra = [c for c in incoming if c not in stored]
    if missing or extra:
        raise ValueError(
            f"feature spec mismatch — stored {len(stored)} features, incoming {len(incoming)}; "
            f"missing={missing}, unexpected={extra}. Prediction refused.")
