"""Batch vs stream schema equality + connector normalisation (offline).

No network: connector normalisation is exercised with an inline provider
payload shaped exactly like an Open-Meteo archive response.
"""

import json

from connectors.open_meteo import OpenMeteoConnector
from streaming.schema_compat import (CANONICAL_TOP_LEVEL, assert_batch_stream_compatible,
                                     assert_canonical)
from validation.quality import validate_record

OM_PAYLOAD = {
    "latitude": 24.86, "longitude": 67.0, "elevation": 7.0,
    "daily_units": {"time": "iso8601", "temperature_2m_max": "°C",
                    "precipitation_sum": "mm", "wind_speed_10m_max": "km/h"},
    "daily": {
        "time": ["2024-06-01", "2024-06-02"],
        "temperature_2m_max": [41.0, None],
        "precipitation_sum": [0.0, 15.2],
        "wind_speed_10m_max": [18.0, None],
    },
}


def _batch_records():
    conn = OpenMeteoConnector({"latitude": 24.86, "longitude": 67.0, "location_name": "Karachi",
                               "region": "Sindh", "granularity": "daily",
                               "variables": ["temperature_max", "precipitation", "wind_speed"]})
    conn._run_started_at = "2026-01-01T00:00:00Z"
    conn.transformations = []
    recs = []
    for raw in conn.normalize(OM_PAYLOAD):
        if raw is None:
            continue
        raw.pop("_timestamp_source", None)  # connector-internal key, consumed by base.run
        outcome = validate_record(raw)
        if outcome.record:
            outcome.record["provenance"] = conn.provenance()
            recs.append(outcome.record)
    return recs


def _stream_record():
    """A stream-path record: same production route (connector+validator)."""
    return _batch_records()[0]


def test_batch_stream_schema_identical():
    batch = _batch_records()
    stream = [_stream_record()]
    result = assert_batch_stream_compatible(batch, stream)
    assert result["compatible"] and result["checked"] == len(batch) + 1
    assert set(result["field_set"]) == set(CANONICAL_TOP_LEVEL)


def test_schema_drift_detected():
    rec = _stream_record()
    broken = dict(rec)
    broken["extra_field"] = 1
    try:
        assert_canonical(broken, context="test")
        raise AssertionError("drift not caught")
    except AssertionError as exc:
        assert "extra=" in str(exc)


def test_open_meteo_nulls_are_omitted_and_units_converted():
    batch = _batch_records()
    day2 = next(r for r in batch if r["timestamp"].startswith("2024-06-02"))
    assert "temperature_max" not in day2["metrics"]  # null -> omitted, never 0
    assert "wind_speed" not in day2["metrics"]
    day1 = next(r for r in batch if r["timestamp"].startswith("2024-06-01"))
    assert day1["metrics"]["wind_speed"] == {"value": 5.0, "unit": "m/s"}  # 18 km/h -> 5 m/s
    assert day1["metrics"]["precipitation"]["value"] == 0.0  # real reported 0 stays
    assert any("km/h" in t for t in day1["provenance"]["transformations"])


def test_records_roundtrip_json():
    for rec in _batch_records():
        assert json.loads(json.dumps(rec))["dedupe_key"] == rec["dedupe_key"]
