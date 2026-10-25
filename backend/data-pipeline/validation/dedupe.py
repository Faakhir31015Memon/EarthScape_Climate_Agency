"""Duplicate detection for canonical observations.

Rule (mirrors ``schemas/observation.schema.json``): ``dedupe_key`` is the
SHA-1 hex digest of ``dataset_id|timestamp|granularity|station_or_cell`` where
``station_or_cell`` is the station id when known, otherwise a coarse geohash
cell — latitude/longitude rounded to 2 decimals (~1.1 km), which keeps
near-coincident grid points from double-counting.
"""

from __future__ import annotations

import hashlib
from typing import Iterable, Optional


def station_or_cell(record: dict) -> str:
    loc = record.get("location") or {}
    src = record.get("source") or {}
    station = src.get("station_id") or loc.get("station_id")
    if station:
        return str(station)
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is None or lon is None:
        return "unknown"
    return f"{float(lat):.2f},{float(lon):.2f}"


def make_dedupe_key(record: dict) -> str:
    payload = "|".join([
        str(record.get("dataset_id", "")),
        str(record.get("timestamp", "")),
        str(record.get("granularity", "")),
        station_or_cell(record),
    ])
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def ensure_key(record: dict) -> dict:
    """Attach (or correct) the dedupe key on a record; pure, deterministic."""
    record["dedupe_key"] = make_dedupe_key(record)
    return record


class DeduplicationIndex:
    """Idempotent seen-key tracker usable for both batch and stream paths."""

    def __init__(self, seen: Optional[Iterable[str]] = None) -> None:
        self.seen: set = set(seen or ())
        self.rejected_duplicates = 0

    def accept(self, record: dict) -> bool:
        key = record.get("dedupe_key") or make_dedupe_key(record)
        if key in self.seen:
            self.rejected_duplicates += 1
            return False
        self.seen.add(key)
        return True

    def filter_batch(self, records: Iterable[dict]):
        kept = []
        for rec in records:
            if self.accept(rec):
                kept.append(rec)
        return kept
