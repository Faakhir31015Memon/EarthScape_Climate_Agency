#!/usr/bin/env python3
"""Fetch REAL daily observations from the Open-Meteo archive API for six
Pakistani cities (2015-2025), push them through the pipeline connectors +
validators, and write:

* ``sample-data/<City>_daily.csv``       flat, human-openable, with header
* ``artifacts/datalake/raw/sample_api/…`` canonical JSONL (raw landing layer)
* ``artifacts/datalake/validated/dataset=…/year=YYYY/month=MM/observations.jsonl``
* ``sample-data/README.md``              provenance: exact API, endpoint,
  retrieval date, variables, units — and the statement that this is real
  retrieved data, not synthetic.

If the provider is unreachable the script exits non-zero and says so; it never
substitutes generated values for missing ones.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data-pipeline"))

from connectors.registry import build_connector  # noqa: E402
from pipeline_config import layer_path, partition_dir, sample_data_root  # noqa: E402

CITIES = [
    {"name": "Karachi", "region": "Sindh", "lat": 24.8607, "lon": 67.0011},
    {"name": "Lahore", "region": "Punjab", "lat": 31.5204, "lon": 74.3587},
    {"name": "Islamabad", "region": "Islamabad Capital Territory", "lat": 33.6844, "lon": 73.0479},
    {"name": "Peshawar", "region": "Khyber Pakhtunkhwa", "lat": 34.0084, "lon": 71.5785},
    {"name": "Quetta", "region": "Balochistan", "lat": 30.1798, "lon": 66.9750},
    {"name": "Multan", "region": "Punjab", "lat": 30.1520, "lon": 71.4109},
]
VARIABLES = ["temperature_max", "temperature_min", "temperature", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]
CSV_COLUMNS = ["city", "region", "country", "latitude", "longitude", "date",
               "temperature_max_C", "temperature_min_C", "temperature_mean_C",
               "precipitation_mm", "humidity_pct", "wind_speed_ms",
               "pressure_hPa", "solar_radiation_MJ_m2", "quality_status",
               "quality_flags", "dedupe_key"]
METRIC_TO_CSV = {
    "temperature_max": "temperature_max_C", "temperature_min": "temperature_min_C",
    "temperature": "temperature_mean_C", "precipitation": "precipitation_mm",
    "humidity": "humidity_pct", "wind_speed": "wind_speed_ms",
    "pressure": "pressure_hPa", "solar_radiation": "solar_radiation_MJ_m2",
}


def fetch_city(city: dict, start: str, end: str):
    connector = build_connector("open_meteo", {
        "latitude": city["lat"], "longitude": city["lon"],
        "location_name": city["name"], "region": city["region"], "country": "PK",
        "start_date": start, "end_date": end, "granularity": "daily",
        "variables": VARIABLES,
        "dataset_id": f"open_meteo_daily_{city['name'].lower()}",
    })
    return connector.run()


def write_csv(records, path: Path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for rec in records:
            row = {
                "city": rec["location"].get("name", ""),
                "region": rec["location"].get("region", ""),
                "country": rec["location"].get("country", ""),
                "latitude": rec["location"]["latitude"],
                "longitude": rec["location"]["longitude"],
                "date": rec["timestamp"][:10],
                "quality_status": rec["quality"]["status"],
                "quality_flags": ";".join(rec["quality"]["flags"]),
                "dedupe_key": rec["dedupe_key"],
            }
            for metric, col in METRIC_TO_CSV.items():
                meas = rec["metrics"].get(metric)
                row[col] = meas["value"] if meas else ""  # empty cell = omitted, never 0
            writer.writerow([row.get(c, "") for c in CSV_COLUMNS])


def write_jsonl_partitioned(records, dataset: str):
    grouped: dict = {}
    for rec in records:
        grouped.setdefault((rec["timestamp"][:4], rec["timestamp"][5:7]), []).append(rec)
    paths = []
    for (year, month), bucket in grouped.items():
        d = partition_dir("validated", dataset, int(year), int(month))
        p = d / "observations.jsonl"
        with open(p, "w", encoding="utf-8") as fh:
            for rec in bucket:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        paths.append(p)
    return paths


def write_readme(retrieved_at: str, start: str, end: str, per_city: dict):
    root = sample_data_root()
    lines = [
        "# sample-data — REAL retrieved climate data (NOT synthetic)",
        "",
        f"- Retrieval date (UTC): **{retrieved_at}**",
        f"- Coverage requested: {start} .. {end} (daily granularity, timezone UTC)",
        "- Provider/API: **Open-Meteo Historical Weather API (ERA5/ERA5-Land based reanalysis)**",
        "- Endpoint: `https://archive-api.open-meteo.com/v1/archive` (no key required)",
        "- Client: `data-pipeline/connectors/open_meteo.py` (one request per city per year segment)",
        "- Variables and units (provider metadata preserved, canonicalised by `validation/units.py`):",
        "  - `temperature_2m_max/min/mean` -> °C (`temperature_max_C` …)",
        "  - `precipitation_sum` -> mm",
        "  - `relative_humidity_2m_mean` -> %",
        "  - `wind_speed_10m_max` -> **converted km/h to m/s** (recorded in `provenance.transformations`)",
        "  - `pressure_msl_mean` -> hPa",
        "  - `shortwave_radiation_sum` -> MJ/m²",
        "- Missing measurements are EMPTY cells in the CSV and are OMITTED from `metrics` in JSONL — never 0.",
        "- Each row also carries the canonical `dedupe_key` and validator quality status/flags.",
        "",
        "| file | city | rows fetched | rows accepted | rejected | status |",
        "|------|------|-------------|---------------|----------|--------|",
    ]
    for city, info in per_city.items():
        lines.append(f"| {info['csv']} | {city} | {info['fetched']} | {info['accepted']} "
                     f"| {info['rejected']} | {info['status']} |")
    lines += [
        "",
        "Also in this folder: `SYNTHETIC_high_volume.csv` — see its `.meta.json` sidecar; that file",
        "is **synthetic** (deterministic test data for volume testing) and must never be presented",
        "as climate truth.",
        "",
    ]
    (root / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--cities", default=None, help="comma-separated subset of city names")
    args = parser.parse_args(argv)

    chosen = CITIES
    if args.cities:
        wanted = {c.strip().lower() for c in args.cities.split(",")}
        chosen = [c for c in CITIES if c["name"].lower() in wanted]

    retrieved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    root = sample_data_root()
    per_city: dict = {}
    failures = 0
    for city in chosen:
        result = fetch_city(city, args.start, args.end)
        slug = city["name"].lower()
        dataset = f"open_meteo_daily_{slug}"
        csv_path = root / f"{city['name']}_daily.csv"
        if not result.records:
            failures += 1
            print(f"FAILED {city['name']}: status={result.status} errors={result.errors}")
            per_city[city["name"]] = {"csv": "-", "fetched": 0, "accepted": 0,
                                      "rejected": result.rejected, "status": result.status}
            continue
        write_csv(result.records, csv_path)
        raw_dir = layer_path("raw", "sample_api")
        flat_raw = raw_dir / f"{dataset}.jsonl"
        with open(flat_raw, "w", encoding="utf-8") as fh:
            for rec in result.records:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        vp = write_jsonl_partitioned(result.records, dataset)
        per_city[city["name"]] = {"csv": csv_path.name,
                                  "fetched": result.accepted + result.rejected + result.skipped,
                                  "accepted": result.accepted,
                                  "rejected": result.rejected, "status": result.status}
        print(f"OK {city['name']}: accepted={result.accepted} rejected={result.rejected} "
              f"csv={csv_path.name} validated-partitions={len(vp)} raw={flat_raw.name}")
    write_readme(retrieved_at, args.start, args.end, per_city)
    total_accepted = sum(v["accepted"] for v in per_city.values())
    print(f"TOTAL accepted={total_accepted} cities={len(chosen)} failures={failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
