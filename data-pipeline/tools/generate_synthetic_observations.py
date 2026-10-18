#!/usr/bin/env python3
"""Generate a clearly-labelled SYNTHETIC stress dataset for pipeline verification.

The output lives under ``sample-data/generated/`` and is **not** climate truth:
values are drawn from smooth seasonal toy curves plus seeded noise. Its only
purpose is to exercise the PySpark/MapReduce code paths at volume. A companion
``*.meta.json`` records that fact so nothing downstream can mistake it for
observations.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "sample-data" / "generated"
sys.path.insert(0, str(REPO_ROOT / "backend"))

from common import regions as region_catalog  # noqa: E402
from common.collections import observation_dedupe_key  # noqa: E402

# (base annual mean, seasonal amplitude, wet-season probability) per region - toy
# values only. Unknown regions fall back to the global average.
TOY_CURVE = {
    "Sindh": (27.0, 6.0, 0.08), "Punjab": (24.0, 9.0, 0.12),
    "Khyber Pakhtunkhwa": (21.0, 8.0, 0.15), "Balochistan": (22.0, 10.0, 0.05),
    "Gilgit-Baltistan": (12.0, 11.0, 0.18), "Azad Jammu & Kashmir": (19.0, 7.0, 0.2),
    "South Asia": (25.0, 7.0, 0.25), "Middle East": (29.0, 8.0, 0.02),
    "North Africa": (23.0, 8.0, 0.03), "East Africa": (19.0, 2.5, 0.3),
    "Western Europe": (11.0, 6.0, 0.3), "Northern Europe": (5.0, 8.0, 0.25),
    "North America": (12.0, 11.0, 0.2), "East Asia": (16.0, 10.0, 0.28),
    "Oceania": (18.0, 6.0, 0.2), "South America": (20.0, 5.0, 0.3),
}
DEFAULT_CURVE = (18.0, 8.0, 0.15)


def generate(days: int, seed: int, miss_rate: float, start: date) -> list[dict]:
    rng = random.Random(seed)
    rows: list[dict] = []
    locations = region_catalog.LOCATIONS
    for day_offset in range(days):
        day = start + timedelta(days=day_offset)
        for location in locations:
            base, amp, wet = TOY_CURVE.get(location["region_code"], DEFAULT_CURVE)
            seasonal = -amp * math.cos(2 * math.pi * (day.timetuple().tm_yday / 365.25))
            temp = round(base + seasonal + rng.gauss(0, 1.8), 2)
            precip = round(max(0.0, rng.expovariate(1 / 6.0)), 2) if rng.random() < wet else 0.0
            row = {
                "dataset_id": "synthetic-stress",
                "source_name": "synthetic-generator",
                "observation_date": day.isoformat(),
                "year": day.year,
                "month": day.month,
                "location_code": location["code"],
                "location_name": location["name"],
                "region_code": location["region_code"],
                "country": location["country"],
                "latitude": float(location["latitude"]),
                "longitude": float(location["longitude"]),
                "quality_flags": [],
                "unit_audit": "synthetic-canonical",
                "schema_version": 3,
                "ingested_at": datetime.now(timezone.utc).isoformat(),
                "dedupe_key": observation_dedupe_key("synthetic-stress", location["code"],
                                                     day.isoformat()),
                "temperature_c": temp,
                "temperature_min_c": round(temp - abs(rng.gauss(4, 1)), 2),
                "temperature_max_c": round(temp + abs(rng.gauss(4, 1)), 2),
                "precipitation_mm": precip,
                "relative_humidity_pct": round(min(100, max(0, rng.gauss(60, 15))), 1),
                "wind_speed_ms": round(abs(rng.gauss(3, 1.5)), 2),
                "wind_direction_deg": round(rng.uniform(0, 360), 1),
                "surface_pressure_hpa": round(rng.gauss(1013 - location["elevation_m"] * 0.11, 4), 1),
                "cloud_coverage_pct": round(rng.uniform(0, 100), 1),
                "solar_radiation_kwh_m2": round(max(0.0, rng.gauss(5.5, 1.5)), 2),
            }
            # Inject realistic defects: missing values stay null, never zero.
            roll = rng.random()
            if roll < miss_rate:
                row["temperature_c"] = None
                row["quality_flags"].append("temperature_c_absent")
            if roll > 1 - miss_rate / 2:
                row["precipitation_mm"] = None
                row["quality_flags"].append("precipitation_mm_absent")
            if roll > 0.995:
                row["relative_humidity_pct"] = 140.0
                row["quality_flags"].append("relative_humidity_pct_out_of_range")
            if rng.random() < 0.002:  # duplicate delivery
                row["dedupe_key"] = row["dedupe_key"]  # dedupe_key preserved on purpose
            rows.append(row)
    # Add explicit duplicate records (same dedupe_key) to prove dedupe works.
    for source in rows[: max(1, len(rows) // 100)]:
        rows.append(dict(source))
    return rows


def write_parquet(rows: list[dict], path: Path) -> None:
    import pandas as pd

    frame = pd.DataFrame(rows)
    for metric in ("temperature_c", "temperature_min_c", "temperature_max_c",
                   "precipitation_mm", "relative_humidity_pct", "wind_speed_ms",
                   "wind_direction_deg", "surface_pressure_hpa", "cloud_coverage_pct",
                   "solar_radiation_kwh_m2", "latitude", "longitude"):
        frame[metric] = pd.to_numeric(frame[metric], errors="coerce").astype("float64")
    frame["year"] = frame["year"].astype("int32")
    frame["month"] = frame["month"].astype("int32")
    frame.to_parquet(path, index=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=400)
    parser.add_argument("--seed", type=int, default=20260101)
    parser.add_argument("--miss-rate", type=float, default=0.06)
    parser.add_argument("--start", default="2022-01-01")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    start = date.fromisoformat(args.start)
    rows = generate(args.days, args.seed, args.miss_rate, start)
    stamp = start.isoformat()
    parquet_path = OUT_DIR / f"synthetic_observations_{stamp}_{args.days}d.parquet"
    jsonl_path = OUT_DIR / f"synthetic_observations_{stamp}_{args.days}d.jsonl"
    meta_path = OUT_DIR / f"synthetic_observations_{stamp}_{args.days}d.meta.json"
    write_parquet(rows, parquet_path)
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    meta_path.write_text(json.dumps({
        "warning": "SYNTHETIC DATA - generated for pipeline verification only. "
                   "These numbers are NOT climate observations and must never be "
                   "presented as climate truth.",
        "generator": "data-pipeline/tools/generate_synthetic_observations.py",
        "rows": len(rows),
        "days": args.days,
        "start": args.start,
        "seed": args.seed,
        "miss_rate": args.miss_rate,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [parquet_path.name, jsonl_path.name],
    }, indent=2), encoding="utf-8")
    print(json.dumps({"rows": len(rows), "parquet": str(parquet_path),
                      "jsonl": str(jsonl_path), "meta": str(meta_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
