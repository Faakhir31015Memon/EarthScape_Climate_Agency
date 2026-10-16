"""Cross-engine reference check: MapReduce (Hadoop Streaming) vs pandas/Spark.

Two independent implementations must agree, otherwise one of them is lying.
This recomputes the ``temp_region_year`` aggregation (region x year count and
mean temperature) with pandas from the *same* JSONL input the streaming job
consumed, compares against the streaming job's TSV output, and reports the
maximum absolute difference. Tolerance 0.01 °C (both sides round to 3-4 dp).

Usage (repo root):
    python spark/unit_reference.py --input <observations.jsonl> --dataset <slug>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from pipeline_config import layer_path

MEAN_TEMPERATURE_DOC = (
    "Mapper takes metrics.temperature when present, else the midpoint of "
    "temperature_max/min — identical logic on the pandas side below."
)


def pandas_reference(input_path: Path) -> list:
    rows = []
    with open(input_path, "r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            metrics = rec.get("metrics") or {}
            t = None
            if "temperature" in metrics:
                t = float(metrics["temperature"]["value"])
            elif "temperature_max" in metrics and "temperature_min" in metrics:
                t = (float(metrics["temperature_max"]["value"]) + float(metrics["temperature_min"]["value"])) / 2.0
            elif "temperature_max" in metrics:
                t = float(metrics["temperature_max"]["value"])
            elif "temperature_min" in metrics:
                t = float(metrics["temperature_min"]["value"])
            if t is None:
                continue
            loc = rec.get("location") or {}
            region = loc.get("region") or loc.get("name") or "unassigned"
            rows.append({"region": region, "year": rec["timestamp"][:4], "t": t})
    if not rows:
        return []
    df = pd.DataFrame(rows)
    agg = df.groupby(["region", "year"])["t"].agg(["count", "mean"]).reset_index()
    return [{"region": r.region, "year": r.year, "count": int(r.count), "mean_c": float(r["mean"])}
            for r in agg.itertuples(index=False)]


def load_mr_output(dataset: str, job: str = "temp_region_year") -> list:
    tsv = layer_path("processed", f"dataset={dataset}", job) / "part-00000.tsv"
    if not tsv.is_file():
        raise FileNotFoundError(f"mapreduce output missing: {tsv}; run mapreduce/local_runner.py first")
    out = []
    for line in tsv.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        region, year, count, mean_c, _min_c, _max_c = line.split("\t")
        out.append({"region": region, "year": year, "count": int(count), "mean_c": float(mean_c)})
    return out


def crosscheck(input_path: Path, dataset: str) -> dict:
    ref = {(r["region"], str(r["year"])): r for r in pandas_reference(input_path)}
    mr = {(r["region"], str(r["year"])): r for r in load_mr_output(dataset)}
    mismatches = []
    if set(ref) != set(mr):
        mismatches.append({"kind": "keys", "only_pandas": sorted(set(ref) - set(mr))[:5],
                           "only_mapreduce": sorted(set(mr) - set(ref))[:5]})
    max_diff = 0.0
    for key, expected in ref.items():
        got = mr.get(key)
        if not got:
            continue
        if got["count"] != expected["count"]:
            mismatches.append({"kind": "count", "key": list(key), "pandas": expected["count"], "mapreduce": got["count"]})
        diff = abs(got["mean_c"] - round(expected["mean_c"], 3))
        max_diff = max(max_diff, diff)
        if diff > 0.01:
            mismatches.append({"kind": "mean", "key": list(key), "pandas": expected["mean_c"], "mapreduce": got["mean_c"]})
    return {
        "check": "temp_region_year: pandas-engine job vs hadoop-streaming-local",
        "groups_pandas": len(ref), "groups_mapreduce": len(mr),
        "max_abs_mean_diff_c": round(max_diff, 6),
        "mismatches": mismatches,
        "verdict": "PASS" if not mismatches else "FAIL",
        "note": MEAN_TEMPERATURE_DOC,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--dataset", required=True)
    args = parser.parse_args()
    result = crosscheck(args.input, args.dataset)
    print(json.dumps(result, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
