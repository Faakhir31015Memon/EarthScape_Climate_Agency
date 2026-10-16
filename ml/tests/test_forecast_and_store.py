"""Forecast horizon rejection + artifact round-trip through joblib + spec validation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.inference.predict import forecast
from ml.training import store
from ml.training.forecast import train_forecaster


def test_forecast_rejects_horizon_beyond_validation(tidy_frame, model_root):
    result = train_forecaster(tidy_frame, "temperature", "Testville", "daily",
                              horizon_steps=7, folds=2, save=True,
                              models=["naive", "linear_ridge"])
    assert result["status"] == "trained", result
    model_ref = f"{result['model_name']}@{result['model_version']}"

    refused = forecast(model_ref, "temperature", "Testville", 30, tidy=tidy_frame)
    assert refused["status"] == "rejected_horizon"
    assert refused["validated_horizon"] == 7 and refused["requested_horizon"] == 30

    accepted = forecast(model_ref, "temperature", "Testville", 7, tidy=tidy_frame)
    assert accepted["status"] == "forecast", accepted
    assert len(accepted["values"]) == 7
    assert accepted["model"]["model_name"] == result["model_name"]
    assert accepted["units"] == "degC"


def test_forecast_refuses_wrong_target(tidy_frame, model_root):
    result = train_forecaster(tidy_frame, "temperature", "Testville", "daily",
                              horizon_steps=7, save=False, folds=2,
                              models=["naive"])
    # nothing saved for precipitation under that name -> load refusal, no invented forecast
    out = forecast(f"{result['model_name']}@{result['model_version']}",
                   "precipitation", "Testville", 7, tidy=tidy_frame)
    assert out["status"] == "error"


def test_joblib_roundtrip_and_spec_validation(model_root):
    from sklearn.linear_model import LinearRegression
    from sklearn.pipeline import Pipeline, make_pipeline
    from ml.features.pipeline import build_prep_pipeline

    names = ["lag_1", "roll_mean_7"]
    X = pd.DataFrame({"lag_1": np.arange(50.0), "roll_mean_7": np.arange(50.0) * 2})
    y = X["lag_1"] * 3.0 + 1.0
    model = make_pipeline(build_prep_pipeline(names, scale=False), LinearRegression())
    model.fit(X, y)
    payload = {"algorithm": "linear_ridge", "estimator": model,
               "feature_spec": {"feature_names": names}, "feature_names": names,
               "horizon_steps": 5, "validated_horizon": 5, "granularity": "daily"}
    saved = store.save_model(payload, {"target_variable": "temperature", "region": "Rtville"},
                             "roundtrip-test")
    loaded = store.load_model(f"roundtrip-test@{saved['model_version']}")
    pred = loaded.predict_validated(X)
    np.testing.assert_allclose(pred, y.to_numpy(), rtol=1e-9)

    bad = X.copy()
    bad["unexpected_col"] = 1.0
    try:
        loaded.predict_validated(bad)
        raise AssertionError("mismatched feature frame must be refused")
    except ValueError as exc:
        assert "feature spec mismatch" in str(exc)

    missing = X.drop(columns=["roll_mean_7"])
    try:
        loaded.predict_validated(missing)
        raise AssertionError("missing feature must be refused")
    except ValueError as exc:
        assert "feature spec mismatch" in str(exc)
