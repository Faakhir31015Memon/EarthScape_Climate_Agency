"""Schema compatibility between the batch and stream ingestion paths.

Both engines must emit records with the *identical* canonical top-level field
set (``schemas/observation.schema.json``). ``assert_batch_stream_compatible``
is called by the polling/streaming writers and by the test suite so any drift
fails loudly at runtime instead of corrupting the lake.
"""

from __future__ import annotations

from schemas import canonical_metric_names, load_observation_schema

CANONICAL_TOP_LEVEL = frozenset(load_observation_schema()["required"]) | {"quality", "provenance"}
CANONICAL_METRICS = tuple(canonical_metric_names())


def field_set(record: dict) -> frozenset:
    return frozenset(record.keys())


def assert_same_field_set(a: dict, b: dict, context: str = "") -> None:
    sa, sb = field_set(a), field_set(b)
    if sa != sb:
        raise AssertionError(
            f"batch/stream schema drift {context}: only_a={sorted(sa - sb)} only_b={sorted(sb - sa)}")


def assert_canonical(record: dict, context: str = "") -> None:
    missing = CANONICAL_TOP_LEVEL - field_set(record)
    extra = field_set(record) - CANONICAL_TOP_LEVEL
    if missing or extra:
        raise AssertionError(
            f"record not canonical {context}: missing={sorted(missing)} extra={sorted(extra)}")
    for metric, meas in (record.get("metrics") or {}).items():
        if metric not in CANONICAL_METRICS:
            raise AssertionError(f"unknown metric {metric!r} {context}")
        if meas.get("value") is None:
            raise AssertionError(f"metric {metric!r} present with null value {context} — must be omitted, never zero-filled")


def assert_batch_stream_compatible(batch_records: list, stream_records: list) -> dict:
    if not batch_records or not stream_records:
        return {"compatible": True, "checked": 0,
                "note": "one side empty; nothing to compare (honest, not a failure)"}
    template = field_set(batch_records[0])
    checked = 0
    for rec in list(batch_records) + list(stream_records):
        assert_canonical(rec, f"dedupe_key={rec.get('dedupe_key')}")
        if field_set(rec) != template:
            assert_same_field_set(rec, batch_records[0], f"dedupe_key={rec.get('dedupe_key')}")
        checked += 1
    return {"compatible": True, "checked": checked, "field_set": sorted(template)}
