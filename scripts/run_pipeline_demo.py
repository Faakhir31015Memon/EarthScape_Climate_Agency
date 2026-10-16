#!/usr/bin/env python3
"""End-to-end pipeline demo with an honest per-stage PASS/FAIL/SKIPPED table.

Stages: fetch/reuse real sample -> normalise+validate -> MapReduce (Hadoop
Streaming scripts via the local runner) -> cross-engine reference check ->
Spark-or-pandas jobs -> Parquet outputs -> streaming-path status.

Nothing is faked: a stage that cannot run reports FAIL/SKIPPED with the real
reason (e.g. "kafka-python not installed", "provider unreachable").
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "data-pipeline"))

from pipeline_config import layer_path  # noqa: E402

ROWS: list = []


def stage(name: str, status: str, detail: str) -> None:
    ROWS.append({"stage": name, "status": status, "detail": detail})
    print(f"[{status:7}] {name}: {detail}")


def main() -> int:
    # 1. ingest: reuse fetched real sample if present, else small live fetch
    raw_dir = layer_path("raw", "sample_api")
    raw_files = sorted(raw_dir.glob("*.jsonl"))
    combined = raw_dir / "demo_combined.jsonl"
    if not raw_files or not any(f.name != combined.name for f in raw_files):
        print("no sample data yet -> running live fetch (2024 only, Karachi+Lahore)")
        rc = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "fetch_real_sample_data.py"),
                             "--start", "2024-01-01", "--end", "2024-12-31",
                             "--cities", "Karachi,Lahore"], cwd=str(REPO_ROOT)).returncode
        raw_files = sorted(raw_dir.glob("*.jsonl"))
        if not raw_files:
            stage("1.ingest", "FAIL", "provider unreachable and no local sample to reuse")
            return print_table_and_exit()
        stage("1.ingest", "PASS", f"fetched {len(raw_files)} real city files (exit {rc})")
    else:
        stage("1.ingest", "PASS", f"reusing {len(raw_files)} real fetched files in {raw_dir}")
    with open(combined, "w", encoding="utf-8") as fh:
        n = 0
        for f in raw_files:
            if f == combined:
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    fh.write(line + "\n")
                    n += 1
    print(f"combined raw records: {n}")

    # 2. normalise + validate (spark.normalize_observations job)
    from spark import normalize_observations

    summary = normalize_observations.run({"dataset": "demo_cities", "paths": [str(combined)]})
    status = "PASS" if summary["status"] == "ok" else "FAIL"
    stage("2.validate", status, f"engine={summary['engine_used']} accepted={summary.get('rows_out')} "
                                f"rejected={summary['totals']['rejected']} dup={summary['totals']['duplicates']} "
                                f"dq={summary.get('data_quality_score')}")

    # combine validated partitions into one JSONL for the streaming jobs
    validated_dir = layer_path("validated", "dataset=demo_cities")
    vcombined = validated_dir / "combined.jsonl"
    with open(vcombined, "w", encoding="utf-8") as fh:
        for p in sorted(validated_dir.rglob("observations.jsonl")):
            fh.write(p.read_text(encoding="utf-8"))
    vfiles = summary.get("outputs", [])

    # 3. MapReduce via local streaming runner
    from mapreduce.local_runner import run_all_jobs

    manifests = run_all_jobs(vcombined, dataset="demo_cities")
    stage("3.mapreduce", "PASS", "; ".join(f"{m['job']}: {m['record_in']}in/{m['record_out']}out"
                                           f"({m['engine']})" for m in manifests))

    # 4. cross-engine reference check (MapReduce vs pandas aggregation)
    from spark.unit_reference import crosscheck

    xcheck = crosscheck(vcombined, "demo_cities")
    stage("4.cross-engine", "PASS" if xcheck["verdict"] == "PASS" else "FAIL",
          f"groups={xcheck['groups_pandas']} max-mean-diff={xcheck['max_abs_mean_diff_c']}C "
          f"mismatches={len(xcheck['mismatches'])}")

    # 5. Spark-or-pandas jobs
    from spark import run_job

    for job_id, params in [
        ("aggregate_regions", {"dataset": "demo_cities"}),
        ("trends", {"dataset": "demo_cities", "variable": "temperature_max"}),
        ("historical_compare", {"dataset": "demo_cities", "variable": "temperature"}),
        ("anomaly_features", {"dataset": "demo_cities", "variable": "temperature"}),
        ("profile_missing", {"dataset": "demo_cities"}),
        ("correlations", {"dataset": "demo_cities"}),
        ("ml_features", {"dataset": "demo_cities", "variable": "temperature"}),
    ]:
        s = run_job(job_id, params)
        st = "PASS" if s.get("status") in ("ok", None) and s.get("rows_out", 0) > 0 else \
            ("SKIPPED" if s.get("status") == "unavailable" else "FAIL")
        note = f"engine={s['engine_used']} rows={s.get('rows_out')}"
        if s.get("spark_error"):
            note += f" spark_error={s['spark_error'][:80]}"
        if s.get("storage_engine"):
            note += f" storage={s['storage_engine']}"
        stage(f"5.{job_id}", st, note)

    # 6. Parquet outputs verification
    analytics = layer_path("analytics", "dataset=demo_cities")
    parquets = sorted(analytics.rglob("*.parquet"))
    gz = sorted(analytics.rglob("*.csv.gz"))
    if parquets:
        stage("6.outputs", "PASS", f"{len(parquets)} parquet files (+{len(gz)} csv.gz fallbacks) under {analytics}")
    elif gz:
        stage("6.outputs", "PASS", f"{len(gz)} csv.gz files (pyarrow unavailable at runtime)")
    else:
        stage("6.outputs", "FAIL", "no analytical outputs found")

    # 7. streaming paths (honest availability)
    from streaming import kafka_ingest

    ks = kafka_ingest.status()
    stage("7.kafka", "SKIPPED" if ks["status"] == "unavailable" else "PASS",
          ks.get("reason", f"brokers {ks.get('brokers')}"))
    cp_file = REPO_ROOT / "artifacts" / "checkpoints" / "poller.json"
    if cp_file.is_file():
        cp = json.loads(cp_file.read_text(encoding="utf-8"))
        stage("7.polling", "PASS", f"last_polled_at={cp.get('last_polled_at')} records_written={cp.get('records_written')}")
    else:
        stage("7.polling", "SKIPPED", "poller checkpoint absent; run streaming/polling_ingest.py --once")

    return print_table_and_exit()


def print_table_and_exit() -> int:
    print("\n==== EarthScape pipeline demo — per-stage results "
          f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}) ====")
    width = max(len(r["stage"]) for r in ROWS)
    failures = 0
    for r in ROWS:
        mark = {"PASS": "OK", "FAIL": "XX", "SKIPPED": "--"}[r["status"]]
        print(f" {mark}  {r['stage']:<{width}}  {r['status']:7}  {r['detail']}")
        failures += r["status"] == "FAIL"
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
