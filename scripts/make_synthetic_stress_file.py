#!/usr/bin/env python3
"""Generate a clearly-labelled SYNTHETIC high-volume CSV for pipeline stress
testing ONLY. ~200k rows, deterministic seed. This data is NOT climate truth:
the filename says SYNTHETIC, a `.meta.json` sidecar carries `synthetic: true`,
and `sample-data/README.md` repeats the warning. Nothing downstream should
present these values as observations.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT = REPO_ROOT / "sample-data" / "SYNTHETIC_high_volume.csv"
SIDECAR = REPO_ROOT / "sample-data" / "SYNTHETIC_high_volume.meta.json"

CITIES = [  # (name, base_temp_C_amplitude, monsoon_mm_factor)
    ("Karachi", 28.0, 12.0, 0.9), ("Lahore", 24.0, 18.0, 1.4),
    ("Islamabad", 23.0, 20.0, 1.6), ("Peshawar", 25.0, 14.0, 1.1),
    ("Quetta", 18.0, 8.0, 0.6), ("Multan", 29.0, 10.0, 1.0),
    ("Hyderabad", 27.0, 11.0, 0.9), ("Sialkot", 24.5, 16.0, 1.3),
]
HEADER = ["synthetic", "city", "date", "temperature_max_C", "temperature_min_C",
          "precipitation_mm", "humidity_pct", "wind_speed_ms", "pressure_hPa"]


def generate(rows: int, seed: int, out_path: Path = OUT) -> dict:
    rng = np.random.default_rng(seed)
    per_city = rows // len(CITIES)
    written = 0
    start = date(2015, 1, 1)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        for name, base, amplitude, rain_factor in CITIES:
            days = np.arange(per_city)
            doy = (days % 365)
            seasonal = base + amplitude * np.sin(2 * np.pi * (doy - 100) / 365.0)
            tmax = seasonal + rng.normal(0, 2.2, per_city)
            tmin = seasonal - (8 + rng.gamma(2, 1.5, per_city))
            monsoon = ((doy >= 150) & (doy <= 270)).astype(float)
            rain = rng.binomial(1, 0.15 + 0.35 * monsoon) * rng.gamma(1.3, 6 * rain_factor, per_city)
            hum = np.clip(35 + 45 * monsoon + rng.normal(0, 9, per_city), 3, 100)
            wind = np.abs(rng.normal(2.6, 1.4, per_city))
            press = 1006 + 12 * np.cos(2 * np.pi * doy / 365.0) + rng.normal(0, 3.5, per_city)
            for i in range(per_city):
                writer.writerow(["SYNTHETIC", name, (start + timedelta(days=int(i))).isoformat(),
                                 f"{tmax[i]:.2f}", f"{tmin[i]:.2f}", f"{max(rain[i], 0):.2f}",
                                 f"{hum[i]:.2f}", f"{wind[i]:.3f}", f"{press[i]:.2f}"])
                written += 1
    meta = {
        "synthetic": True,
        "label": "SYNTHETIC — pipeline volume/stress test data ONLY; NOT climate observations",
        "rows": written,
        "generator": "scripts/make_synthetic_stress_file.py",
        "seed": seed,
        "deterministic": True,
        "cities": [c[0] for c in CITIES],
        "created_at": date.today().isoformat(),
        "do_not": ["publish as data", "use in dashboards as truth", "feed ML training as real"],
    }
    SIDECAR.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    meta = generate(args.rows, args.seed)
    print(f"WROTE {OUT} rows={meta['rows']} synthetic={meta['synthetic']} sidecar={SIDECAR.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
