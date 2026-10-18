#!/usr/bin/env python3
"""Run one or more EarthScape Spark jobs against a lake/file input.

Example (verified on the development machine)::

    python data-pipeline/tools/run_spark_jobs.py \
        --input sample-data/generated/synthetic_observations_*.jsonl \
        --jobs normalise_and_partition,missing_value_profiling,regional_climate_aggregation

Prints each job's summary JSON. The regional job is paired with the MapReduce
pipeline over the same input so the cross-engine agreement check has real data
to compare. Non-zero exit if any selected job fails.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "data-pipeline" / "spark"))
sys.path.insert(0, str(ROOT / "data-pipeline"))


CROSSCHECK_MAX_ROWS = 500_000


def run_mapreduce(input_path: Path, spark=None, validated_prefix: str | None = None) -> str:
    """Execute mapper|sort|reducer locally and store the TSV in the lake analytics layer.

    When ``validated_prefix`` is given, the deduped validated partition is streamed
    to JSON first so MapReduce and Spark aggregate *identical rows* - otherwise the
    comparison would just re-discover the duplicates Spark removed.
    """
    import tempfile

    source = input_path
    if validated_prefix is not None:
        from jobs import load_source  # noqa: WPS433 - same module already on path

        frame, _resolved = load_source(spark, {"input_path": validated_prefix})
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
        count = 0
        for row in frame.toLocalIterator():
            handle.write(json.dumps(row.asDict(recursive=True), default=str) + "\n")
            count += 1
            if count >= CROSSCHECK_MAX_ROWS:
                break
        handle.close()
        source = Path(handle.name)
    mr_dir = ROOT / "data-pipeline" / "mapreduce"
    py = sys.executable
    mapper = subprocess.run([py, str(mr_dir / "temperature_aggregation_mapper.py")],
                            stdin=open(source, encoding="utf-8"),
                            capture_output=True, text=True)
    sorter = subprocess.run([py, str(mr_dir / "sort_by_key.py")], input=mapper.stdout,
                            capture_output=True, text=True)
    reducer = subprocess.run([py, str(mr_dir / "temperature_aggregation_reducer.py")],
                             input=sorter.stdout, capture_output=True, text=True)
    if mapper.returncode or sorter.returncode or reducer.returncode:
        raise RuntimeError("MapReduce cross-check run failed")
    from common.data_lake import get_data_lake

    lake = get_data_lake()
    lake.ensure_layout()
    result = lake.write_text(reducer.stdout,
                             f"mapreduce-crosscheck-{source.stem}.tsv", layer="analytics")
    if not result.ok:
        raise RuntimeError(f"could not store MapReduce output: {result.error}")
    return result.path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="file or lake prefix of observations")
    parser.add_argument("--jobs", default="all",
                        help="comma list from: normalise_and_partition,missing_value_profiling,"
                             "regional_climate_aggregation,trend_analysis,anomaly_feature_generation,"
                             "correlation_analysis,ml_feature_prep,export_processed_parquet")
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--metric", default="temperature_c")
    parser.add_argument("--with-mapreduce-check", action="store_true")
    args = parser.parse_args()

    import jobs as J
    from spark_session import build_spark_session, session_summary

    selected = list(J.JOBS) if args.jobs == "all" else [j.strip() for j in args.jobs.split(",")]
    input_path = Path(args.input)

    spark = build_spark_session("earthscape-job-runner")
    print("SPARK SESSION " + json.dumps(session_summary(spark)))
    if args.with_mapreduce_check and "regional_climate_aggregation" not in selected:
        print("CROSSCHECK NOT REQUESTED: --with-mapreduce-check only applies to "
              "regional_climate_aggregation, which is not in --jobs.")
    if args.with_mapreduce_check and "normalise_and_partition" not in selected:
        print("CROSSCHECK UNAVAILABLE: the MapReduce comparison aggregates the validated "
              "partition produced by normalise_and_partition. Add it to --jobs before "
              "regional_climate_aggregation, otherwise the regional job reports why it "
              "compared nothing.")
    failures = 0
    produced_prefix = None
    mr_key = None
    for name in selected:
        func = J.JOBS.get(name)
        if func is None:
            print(f"JOB {name} SKIPPED (unknown)")
            failures += 1
            continue
        options = {"dataset_id": args.dataset_id, "metric": args.metric}
        if name == "normalise_and_partition":
            options["input_path"] = str(input_path)
        else:
            options["input_path"] = produced_prefix or str(input_path)
        if name == "regional_climate_aggregation":
            if args.with_mapreduce_check and produced_prefix:
                mr_key = run_mapreduce(input_path, spark=spark, validated_prefix=produced_prefix)
            if mr_key:
                options["mapreduce_output_path"] = mr_key
        try:
            summary, out = func(spark, options)
            if name == "normalise_and_partition":
                produced_prefix = out
            print(f"JOB {name} RESULT " + json.dumps(summary, default=str))
        except Exception as exc:  # noqa: BLE001 - one job failing must not hide the others
            failures += 1
            print(f"JOB {name} FAILED: {str(exc)[:400]}")
    spark.stop()
    from common.database import get_database

    get_database().flush_if_dirty()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
