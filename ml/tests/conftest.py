"""Shared fixtures: small deterministic synthetic series (no network, no disk data)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture()
def daily_series() -> pd.Series:
    """400 days of seasonal signal + deterministic noise, UTC daily index."""
    rng = np.random.default_rng(7)
    index = pd.date_range("2022-01-01", periods=400, freq="D", tz="UTC")
    doy = index.dayofyear.to_numpy()
    values = 20.0 + 8.0 * np.sin(2 * np.pi * doy / 366.0) + rng.normal(0, 1.0, len(index))
    return pd.Series(values, index=index, name="temperature")


@pytest.fixture()
def tidy_frame(daily_series: pd.Series) -> pd.DataFrame:
    rows = []
    for ts, v in daily_series.items():
        rows.append({
            "timestamp": ts, "region": "Testville", "station": "TEST-1",
            "latitude": 24.86, "longitude": 67.01, "variable": "temperature",
            "value": float(v), "unit": "degC", "granularity": "daily",
            "dataset_id": "synthetic", "provenance_file": "memory://synthetic",
        })
    cols = ["timestamp", "region", "station", "latitude", "longitude", "variable",
            "value", "unit", "granularity", "dataset_id", "provenance_file"]
    frame = pd.DataFrame(rows, columns=cols)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    return frame


@pytest.fixture()
def model_root(tmp_path, monkeypatch) -> Path:
    """Redirect MODEL_ARTIFACT_ROOT at a tmp dir for artifact round-trip tests."""
    monkeypatch.setenv("MODEL_ARTIFACT_ROOT", str(tmp_path / "models"))
    return tmp_path / "models"
