"""Anomaly detection: planted spike flagged + explained; thin baselines refuse."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.anomaly.statistical import statistical_anomalies
from ml.anomaly.threshold import check_thresholds
from ml.anomaly.severity import severity_for


def _flat_series(days: int, spike_day: int | None = None, spike_value: float = 45.0) -> pd.Series:
    rng = np.random.default_rng(11)
    index = pd.date_range("2022-01-01", periods=days, freq="D", tz="UTC")
    values = 20.0 + rng.normal(0.0, 0.5, days)
    if spike_day is not None:
        values[spike_day] = spike_value
    return pd.Series(values, index=index, name="temperature")


def test_planted_spike_flagged_and_explained():
    series = _flat_series(1095, spike_day=700, spike_value=45.0)
    result = statistical_anomalies(series, "temperature", "Spikeville", unit="degC",
                                   min_baseline=30)
    assert result["status"] == "ok"
    assert result["flagged"] >= 1
    spike = next((d for d in result["detections"]
                  if d["timestamp"].startswith(str(series.index[700].date()))), None)
    assert spike is not None, "planted spike must appear among detections"
    assert spike["severity"] in ("warning", "severe")
    assert spike["observed_value"] == 45.0
    assert spike["baseline_value"] is not None and abs(spike["baseline_value"] - 20.0) < 2.0
    assert spike["robust_sigma"] is not None and spike["robust_sigma"] > 3.0
    assert spike["season"]["day_of_year"] == int(series.index[700].dayofyear)
    assert spike["region"] == "Spikeville"
    assert spike["method"] == "statistical_doy_climatology_mad"
    assert spike["observable_deviation"], "every detection must carry its observable deviation"
    assert "not a causal attribution" in spike["note"]


def test_thin_baseline_refuses_to_declare_exceptional():
    """~90 days gives each day-of-year window far fewer than MIN_BASELINE_SAMPLE
    comparable values; the detector must skip, not flag, the spike."""
    series = _flat_series(90, spike_day=60, spike_value=45.0)
    result = statistical_anomalies(series, "temperature", "Thinville", unit="degC",
                                   min_baseline=30)
    assert result["insufficient_baseline_slots"] > 0
    spike_flagged = any(d["timestamp"].startswith(str(series.index[60].date()))
                        for d in result["detections"])
    assert not spike_flagged, "thin baseline must never justify an anomaly claim"
    assert any("MIN_BASELINE_SAMPLE" in n for n in result["notes"])


def test_threshold_violation_and_severity_mapping():
    series = _flat_series(10)
    series.iloc[3] = 999.0  # impossible temperature
    result = check_thresholds(series, "temperature")
    assert result["available"] and result["violations"] == 1
    detection = result["detections"][0]
    assert detection["breach"] == "above_max" and detection["breach_level"] == "extreme"
    assert severity_for(None, "extreme") == "warning"      # threshold-only caps at warning
    assert severity_for(4.2, None) == "severe"             # 4.2 sigma is severe
    assert severity_for(2.5, "marginal") == "info"
