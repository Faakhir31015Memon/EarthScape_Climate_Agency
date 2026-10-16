"""Validation engine: schema, coords, units, outliers, dedupe, report."""

import hashlib

from validation.dedupe import DeduplicationIndex, make_dedupe_key, station_or_cell
from validation.quality import BatchValidator, validate_batch, validate_record
from validation.units import convert_metric


def base_record(**over):
    rec = {
        "timestamp": "2024-06-01T00:00:00Z", "granularity": "daily", "dataset_id": "t",
        "source": {"provider": "open_meteo", "kind": "api"},
        "location": {"latitude": 24.86, "longitude": 67.0, "region": "Sindh"},
        "metrics": {"temperature": {"value": 30.0, "unit": "C"}},
    }
    rec["dedupe_key"] = make_dedupe_key(rec)
    rec.update(over)
    return rec


def test_bad_coordinates_rejected():
    rec = base_record(location={"latitude": 200.0, "longitude": 67.0})
    out = validate_record(rec)
    assert out.record is None
    assert any(i["rule"] in ("schema", "coordinates") for i in out.issues)


def test_unparsable_timestamp_rejected():
    rec = base_record(timestamp="not-a-date")
    assert validate_record(rec).record is None


def test_outlier_flagged_but_kept():
    rec = base_record(metrics={"temperature": {"value": 999.0, "unit": "C"}})
    out = validate_record(rec)
    assert out.record is not None
    assert out.record["quality"]["status"] == "suspect"
    assert "out_of_range:temperature" in out.record["quality"]["flags"]
    # value preserved, not silently dropped or zeroed
    assert out.record["metrics"]["temperature"]["value"] == 999.0


def test_unit_conversions_recorded():
    v, u, note = convert_metric("temperature", 313.15, "K")
    assert (round(v, 2), u) == (40.0, "C") and "K -> C" in note
    v, u, note = convert_metric("wind_speed", 36.0, "km/h")
    assert (round(v, 3), u) == (10.0, "m/s") and note
    v, u, note = convert_metric("precipitation", 1.0, "inches")
    assert (round(v, 2), u) == (25.4, "mm")
    v, u, note = convert_metric("pressure", 101325.0, "Pa")
    assert (round(v, 1), u) == (1013.2, "hPa")
    v, u, note = convert_metric("humidity", 0.55, "fraction")
    assert abs(v - 55.0) < 1e-9 and u == "%"
    v, u, note = convert_metric("wind_speed", 10.0, "mile/h")
    assert abs(v - 4.4704) < 1e-4 and u == "m/s"


def test_missing_metric_is_omitted_not_zero():
    rec = base_record(metrics={"temperature": {"value": 30.0, "unit": "C"},
                               "precipitation": {"value": None, "unit": "mm"}})
    out = validate_record(rec)
    assert "precipitation" not in out.record["metrics"]
    assert out.record["quality"]["missing_fields"]


def test_timestamp_normalised_to_utc():
    rec = base_record(timestamp="2024-06-01T05:00:00+05:00")
    out = validate_record(rec)
    assert out.record["timestamp"] == "2024-06-01T00:00:00Z"


def test_batch_dedupe_counts_duplicates():
    a = base_record()
    b = base_record()  # same key
    c = base_record(timestamp="2024-06-02T00:00:00Z")
    c["dedupe_key"] = make_dedupe_key(c)
    accepted, report = validate_batch([a, b, c])
    assert len(accepted) == 2
    assert report.duplicates == 1
    assert 0.0 <= report.dq_score <= 1.0
    json_doc = report.to_dict()
    assert json_doc["totals"]["input"] == 3


def test_report_examples_capped_at_20():
    recs = []
    for i in range(30):
        r = base_record(timestamp=f"2024-06-{i + 1:02d}T00:00:00Z",
                        metrics={"temperature": {"value": 100.0 + i, "unit": "C"}})
        r["dedupe_key"] = make_dedupe_key(r)
        recs.append(r)
    accepted, report = validate_batch(recs)
    assert len(accepted) == 30  # suspect records are kept, never dropped
    assert len(report.examples) <= 20


def test_dedupe_key_is_sha1_of_documented_rule():
    rec = base_record()
    expected = hashlib.sha1("t|2024-06-01T00:00:00Z|daily|24.86,67.00".encode()).hexdigest()
    assert rec["dedupe_key"] == expected
    assert station_or_cell(rec) == "24.86,67.00"


def test_dedupe_prefers_station_id():
    rec = base_record(location={"latitude": 24.86, "longitude": 67.0, "station_id": "OPGC"})
    assert station_or_cell(rec) == "OPGC"
    k1, k2 = make_dedupe_key(rec), make_dedupe_key(rec)
    assert k1 == k2  # stability across calls


def test_dedup_index_state_survives_recreation():
    idx = DeduplicationIndex()
    rec = base_record()
    assert idx.accept(rec) and idx.rejected_duplicates == 0
    again = DeduplicationIndex(idx.seen)
    assert not again.accept(rec)
    assert again.rejected_duplicates == 1


def test_empty_metrics_rejected():
    out = validate_record(base_record(metrics={}))
    assert out.record is None
