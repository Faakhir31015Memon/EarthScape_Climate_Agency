"""Chronological split correctness + honest availability."""

from __future__ import annotations

import pandas as pd

from ml.features.time_features import add_time_features
from ml.training.forecast import train_forecaster


def _train(tidy):
    return train_forecaster(tidy, "temperature", "Testville", "daily",
                            horizon_steps=7, folds=3, save=False,
                            models=["naive", "linear_ridge"])


def test_split_is_chronological(tidy_frame):
    result = _train(tidy_frame)
    assert result["status"] == "trained", result
    split = result["split"]
    train_end = pd.Timestamp(split["train_end"])
    test_start = pd.Timestamp(split["test_start"])
    assert test_start > train_end, "test origins must all be AFTER the last training origin"
    assert split["method"] == "chronological (no shuffle)"
    assert split["train_rows"] + split["test_rows"] == 400 - 7  # rows with a valid target


def test_derivation_is_order_invariant(daily_series):
    """Features depend on TIME, not on row order: a shuffled frame re-sorted by
    time must reproduce the exact same matrix (no accidental position leakage)."""
    X_sorted = add_time_features(pd.DataFrame({"value": daily_series}))
    df = daily_series.to_frame("value").sample(frac=1.0, random_state=3)
    X_resorted = add_time_features(df.sort_index())
    pd.testing.assert_frame_equal(X_sorted, X_resorted, check_freq=False)


def test_training_window_ends_before_test_window(tidy_frame):
    result = _train(tidy_frame)
    train_end = pd.Timestamp(result["split"]["train_end"])
    test_end = pd.Timestamp(result["availability"]["date_end"])
    assert train_end < test_end


def test_availability_refuses_thin_slice(tidy_frame):
    thin = tidy_frame.iloc[:40]  # 40 rows < MIN_TRAINING_ROWS (240)
    result = train_forecaster(thin, "temperature", "Testville", "daily",
                              horizon_steps=30, save=False)
    assert result["status"] == "insufficient_data"
    assert result["trained"] is False
    assert "MIN_TRAINING_ROWS" in result["availability"]["reason"]
