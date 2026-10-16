"""Leakage guards: real features must be future-invariant; a planted leak must be caught."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.features.time_features import (add_time_features, future_value_leak_feature,
                                       leakage_check)


def test_features_invariant_to_future_values(daily_series):
    """Scramble the last quarter of the series; all feature values whose target
    is outside the scrambled zone must be byte-identical."""
    df = pd.DataFrame({"value": daily_series})
    horizon = 7
    violations = leakage_check(df, horizon)
    assert violations == [], f"leakage detected in production features: {violations}"

    n = len(df)
    cut = n // 4
    tampered = df.copy()
    tampered.iloc[n - cut:, 0] = np.random.default_rng(1).normal(500, 10, cut)
    X_orig = add_time_features(df)
    X_tamp = add_time_features(tampered)
    safe_rows = n - cut - horizon
    pd.testing.assert_frame_equal(X_orig.iloc[:safe_rows], X_tamp.iloc[:safe_rows])


def test_deliberate_leak_is_caught(daily_series):
    """The forbidden s(t+1) peek must be flagged by the checker."""
    df = pd.DataFrame({"value": daily_series})

    def leaky_builder(frame, lags=None, windows=None):
        out = add_time_features(frame, lags=[1, 2], windows=[7])
        out["leaky_next_value"] = future_value_leak_feature(frame)
        return out

    violations = leakage_check(df, horizon_steps=7, builder=leaky_builder,
                               lags=[1, 2], windows=[7])
    assert "leaky_next_value" in violations
