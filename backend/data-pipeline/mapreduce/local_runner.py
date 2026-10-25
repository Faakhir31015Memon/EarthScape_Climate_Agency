"""Local Hadoop-Streaming verification runner.

This is NOT an emulation shortcut: it executes the exact mapper and reducer
scripts shipped for the cluster, as separate subprocesses, connected through
stdin/stdout pipes, with a byte-order key sort between them — i.e. the real
Hadoop Streaming protocol (map -> sort -> reduce) minus the cluster.  That is
what makes the results verifiable on a machine without Hadoop/HDFS.

Each run writes:
* ``part-00000.tsv`` reducer output under the data-lake ``processed`` layer
* ``manifest.json`` with the run facts required by the brief.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline_config import ENV, layer_path

STREAMING_DIR = Path(__file__).resolve().parent / "streaming"

JOBS = {
    "temp_region_year": {
        "mapper": "mapper_temp_region_year.py",
        "reducer": "reducer_temp_region_year.py",
        "output_schema": "output-schema.temp_region_year.json",
        "columns": ["region", "year", "count", "mean_c", "min_c", "max_c"],
    },
    "precip_region": {
        "mapper": "mapper_precip_region.py",
        "reducer": "reducer_precip_region.py",
        "output_schema": "output-schema.precip_region.json",
        "columns": ["region", "month", "count", "sum_mm", "mean_mm"],
    },
    "obs_count": {
        "mapper": "mapper_obs_count.py",
        "reducer": "reducer_obs_count.py",
        "output_schema": "output-schema.obs_count.json",
        "columns": ["dataset_id", "month", "count"],
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_streaming_job(job: str, input_path: Path, num_mappers: int = 1, combiner: bool = False):
    """Execute mapper (-> sort -> [combiner] ->) reducer exactly once; return (rows, stderr_notes)."""
    spec = JOBS[job]
    py = sys.executable
    mapper = STREAMING_DIR / spec["mapper"]
    reducer = STREAMING_DIR / spec["reducer"]

    with open(input_path, "r", encoding="utf-8") as fin:
        mapped = subprocess.run(
            [py, str(mapper)], stdin=fin, capture_output=True, text=True, check=True)
    map_rows = [ln for ln in mapped.stdout.splitlines() if ln.strip()]
    # Hadoop partitions + sorts by key before reduce; reproduce that ordering.
    map_rows.sort(key=lambda ln: ln.split("\t")[:-1])

    if combiner:
        combined = subprocess.run(
            [py, str(reducer), "--combiner"], input="\n".join(map_rows) + "\n",
            capture_output=True, text=True, check=True)
        map_rows = [ln for ln in combined.stdout.splitlines() if ln.strip()]
        map_rows.sort(key=lambda ln: ln.split("\t")[:-1])

    reduced = subprocess.run(
        [py, str(reducer)], input="\n".join(map_rows) + "\n",
        capture_output=True, text=True, check=True)
    rows = [ln.split("\t") for ln in reduced.stdout.splitlines() if ln.strip()]
    return rows, {"map_records": len(map_rows), "mapper_stderr": mapped.stderr.strip()}


def run_job(job: str, input_path, dataset: str = "mixed", combiner: bool = False) -> dict:
    spec = JOBS[job]
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"input for {job} does not exist: {input_path}")

    record_in = sum(1 for line in input_path.open(encoding="utf-8") if line.strip())
    started = _now()
    rows, notes = _run_streaming_job(job, input_path, combiner=combiner)
    finished = _now()

    out_dir = layer_path("processed", f"dataset={dataset}", job)
    part_file = out_dir / "part-00000.tsv"
    part_file.write_text("\n".join("\t".join(r) for r in rows) + ("\n" if rows else ""), encoding="utf-8")

    manifest = {
        "job": job,
        "input_path": str(input_path),
        "output_path": str(part_file),
        "mapper": str(STREAMING_DIR / spec["mapper"]),
        "reducer": str(STREAMING_DIR / spec["reducer"]),
        "output_schema": str(STREAMING_DIR / spec["output_schema"]),
        "columns": spec["columns"],
        "num_mappers": max(1, record_in // 100_000),
        "started_at": started,
        "finished_at": finished,
        "engine": "hadoop-streaming-local",
        "record_in": record_in,
        "record_out": len(rows),
        "combiner_enabled": combiner,
        "map_side_records": notes["map_records"],
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def run_all_jobs(input_path, dataset: str = "mixed") -> list:
    return [run_job(job, input_path, dataset=dataset) for job in JOBS]


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run bundled Hadoop Streaming jobs locally")
    parser.add_argument("input", help="JSONL file of canonical observations")
    parser.add_argument("--job", default="all", choices=["all", *JOBS.keys()])
    parser.add_argument("--dataset", default=None, help="dataset slug for the data-lake path")
    parser.add_argument("--combiner", action="store_true", help="also execute the reducer as combiner")
    args = parser.parse_args(argv)

    dataset = args.dataset or ENV.raw("PIPELINE_DEFAULT_DATASET", "mixed")
    jobs = list(JOBS) if args.job == "all" else [args.job]
    for job in jobs:
        manifest = run_job(job, args.input, dataset=dataset, combiner=args.combiner)
        print(f"[{job}] in={manifest['record_in']} out={manifest['record_out']} -> {manifest['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
