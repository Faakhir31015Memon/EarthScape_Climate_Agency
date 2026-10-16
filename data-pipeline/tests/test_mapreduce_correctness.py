"""MapReduce streaming jobs vs hand-computed references (no cluster needed).

The tiny fixture below is hand-computable:
Karachi/Sindh: 2024 temps 30, 34 (mean 32, n=2, min 30, max 34); 2025 temp 28.
Quetta/Balochistan 2024: 20 and 24 (mean 22). A missing temperature (only
precipitation) contributes nothing — never a 0.
"""

import json
import subprocess
import sys
from pathlib import Path

STREAMING = Path(__file__).resolve().parents[1] / "mapreduce" / "streaming"

FIXTURE = [
    {"timestamp": "2024-06-01T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 24.86, "longitude": 67.0, "region": "Sindh"},
     "metrics": {"temperature": {"value": 30.0, "unit": "C"}}, "dedupe_key": "a"},
    {"timestamp": "2024-07-01T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 24.87, "longitude": 67.01, "region": "Sindh"},
     "metrics": {"temperature": {"value": 34.0, "unit": "C"},
                 "precipitation": {"value": 12.5, "unit": "mm"}}, "dedupe_key": "b"},
    {"timestamp": "2025-01-01T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 24.86, "longitude": 67.0, "region": "Sindh"},
     "metrics": {"temperature": {"value": 28.0, "unit": "C"}}, "dedupe_key": "c"},
    {"timestamp": "2024-06-01T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 30.18, "longitude": 66.98, "region": "Balochistan"},
     "metrics": {"temperature": {"value": 20.0, "unit": "C"},
                 "precipitation": {"value": 0.0, "unit": "mm"}}, "dedupe_key": "d"},
    {"timestamp": "2024-06-02T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 30.18, "longitude": 66.98, "region": "Balochistan"},
     "metrics": {"temperature": {"value": 24.0, "unit": "C"}}, "dedupe_key": "e"},
    {"timestamp": "2024-06-03T00:00:00Z", "granularity": "daily", "dataset_id": "fx",
     "source": {"provider": "sample", "kind": "sample"},
     "location": {"latitude": 30.18, "longitude": 66.98, "region": "Balochistan"},
     "metrics": {"precipitation": {"value": 5.0, "unit": "mm"}}, "dedupe_key": "f"},
]


def _run(script: str, stdin_text: str, *extra) -> list:
    proc = subprocess.run([sys.executable, str(STREAMING / script), *extra],
                          input=stdin_text, capture_output=True, text=True, check=True)
    return [ln.split("\t") for ln in proc.stdout.splitlines() if ln.strip()]


def _pipeline(mapper: str, reducer: str, combiner: bool = False) -> list:
    mapped = _run(mapper, "\n".join(json.dumps(r) for r in FIXTURE) + "\n")
    mapped.sort(key=lambda parts: parts[:-1])
    lines = "\n".join("\t".join(p) for p in mapped) + "\n"
    if combiner:
        combined = _run(reducer, lines, "--combiner")
        combined.sort(key=lambda parts: parts[:-1])
        lines = "\n".join("\t".join(p) for p in combined) + "\n"
    return _run(reducer, lines)


def test_temp_region_year_matches_hand_reference():
    rows = _pipeline("mapper_temp_region_year.py", "reducer_temp_region_year.py")
    got = {tuple(r[:2]): (int(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows}
    assert got == {
        ("Sindh", "2024"): (2, 32.0, 30.0, 34.0),
        ("Sindh", "2025"): (1, 28.0, 28.0, 28.0),
        ("Balochistan", "2024"): (2, 22.0, 20.0, 24.0),
    }


def test_temp_reducer_is_combiner_friendly():
    plain = _pipeline("mapper_temp_region_year.py", "reducer_temp_region_year.py")
    with_combiner = _pipeline("mapper_temp_region_year.py", "reducer_temp_region_year.py", combiner=True)
    assert sorted(map(tuple, plain)) == sorted(map(tuple, with_combiner))


def test_precip_region_sum_and_mean():
    rows = _pipeline("mapper_precip_region.py", "reducer_precip_region.py")
    got = {tuple(r[:2]): (int(r[2]), float(r[3]), float(r[4])) for r in rows}
    # only records WITH precipitation contribute (record f: 5.0 mm, record b: 12.5, d: 0.0)
    assert got[("Sindh", "2024-07")] == (1, 12.5, 12.5)
    assert got[("Balochistan", "2024-06")] == (2, 5.0, 2.5)
    assert all(count > 0 for (_region, _m), (count, _s, _mean) in got.items())


def test_obs_count_by_dataset_month():
    rows = _pipeline("mapper_obs_count.py", "reducer_obs_count.py")
    got = {tuple(r[:2]): int(r[2]) for r in rows}
    assert got == {
        ("fx", "2024-06"): 4,
        ("fx", "2024-07"): 1,
        ("fx", "2025-01"): 1,
    }
