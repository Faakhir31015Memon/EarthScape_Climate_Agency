"""Baseline metric equality on a hand-computable series + MAPE skip honesty."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.evaluation.metrics import evaluate_forecast, mae, mape, rmse
from ml.training.forecast import train_forecaster


def _linear_tidy():
    index = pd.date_range("2021-01-01", periods=400, freq="D", tz="UTC")
    values = np.arange(1.0, 401.0)  # s(t) = day number, strictly linear
    rows = [{"timestamp": ts, "region": "Lineartown", "station": "L1",
             "latitude": 30.0, "longitude": 70.0, "variable": "temperature",
             "value": v, "unit": "degC", "granularity": "daily",
             "dataset_id": "synthetic", "provenance_file": "memory://linear"}
            for ts, v in zip(index, values)]
    return pd.DataFrame(rows)


def test_hand_computable_naive_mae():
    """On s(t)=t with H=3, naive predicts s(t)=t for target t+3 -> error exactly 3."""
    result = train_forecaster(_linear_tidy(), "temperature", "Lineartown", "daily",
                              horizon_steps=3, folds=2, save=False,
                              models=["naive", "linear_ridge"])
    assert result["status"] == "trained", result
    naive = next(c for c in result["comparison"] if c["algorithm"] == "naive")
    assert abs(naive["mae"] - 3.0) < 1e-9
    assert abs(naive["rmse"] - 3.0) < 1e-9
    assert abs(naive["skill_vs_naive"]) < 1e-12
    # ridge can represent y = lag_1 + 3 exactly -> must beat the baseline on this series
    ridge = next(c for c in result["comparison"] if c["algorithm"] == "linear_ridge")
    assert ridge["mae"] < naive["mae"]


def test_mape_skipped_on_zero_actuals():
    y_true = np.array([0.0, 1.0, 2.0])
    y_pred = np.array([0.0, 1.0, 3.0])
    value, reason = mape(y_true, y_pred)
    assert value is None and reason and "actual values are ~0" in reason
    bundle = evaluate_forecast(y_true, y_pred)
    assert bundle["mape"] is None and bundle["mape_skipped_reason"]
    assert bundle["mae"] == 2.0 / 3.0 and abs(bundle["rmse"] - np.sqrt(2.0 / 3.0)) < 1e-12


def test_mape_works_when_actuals_nonzero():
    y_true = np.array([2.0, 4.0])
    y_pred = np.array([1.0, 4.0])
    value, reason = mape(y_true, y_pred)
    assert reason is None and abs(value - 25.0) < 1e-9  # 50% and 0% -> mean 25
