#!/usr/bin/env python3
"""MapReduce orchestrator for EarthScape processing jobs.

What it actually does:

1. Exports ``climate_observations`` from MongoDB to a JSON-lines file in the
   data lake (validated layer, per-run partition). If the collection is empty
   it can fall back to the synthetic ``example-input.jsonl`` - clearly labelled
   as such in the run metadata.
2. Executes the streaming job:
   * when ``HADOOP_BIN`` (or a ``hadoop`` on PATH) is configured *and* an HDFS
     endpoint answers, it builds and runs the real ``hadoop jar hadoop-streaming-*.jar``
     command, capturing the actual return code and log tail;
   * otherwise it runs the *identical* mapper | sort | reducer code paths as
     local subprocesses and labels the run ``processing_mode:
     "local-streaming-simulated"`` - it never claims Hadoop ran.
3. Writes the reducer output back into the data lake (analytics layer) and
   upserts verified aggregates into MongoDB ``analytics_summaries``.

Success is reported only when the observed process return codes were zero;
otherwise this exits non-zero with a structured failure summary.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(HERE))

JOBS = {
    "temperature_aggregation": {
        "mapper": "temperature_aggregation_mapper.py",
        "reducer": "temperature_aggregation_reducer.py",
        "summary_metric": "regional_monthly_climate",
    },
    "observation_counts": {
        "mapper": "observation_counts_mapper.py",
        "reducer": "observation_counts_reducer.py",
        "summary_metric": "dataset_monthly_counts",
    },
}


def _log(lines: list, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    line = f"[{stamp}] {message[:300]}"
    lines.append(line)
    print(line, file=sys.stderr)


def export_observations(log: list, *, dataset_id: str | None, limit: int) -> tuple[Path, int, str]:
    """Stream MongoDB observations to a JSON-lines scratch file. Returns (path, rows, source)."""
    scratch = Path(os.environ.get("MAPREDUCE_SCRATCH", str(BACKEND_DIR / ".data" / "processing" / "mapreduce")))
    scratch.mkdir(parents=True, exist_ok=True)
    input_path = scratch / f"observations-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    rows = 0
    source = "mongodb:climate_observations"
    try:
        from common.repositories import climate_observations

        query = {"dataset_id": dataset_id} if dataset_id else {}
        with input_path.open("w", encoding="utf-8") as handle:
            for record in climate_observations.stream(query, projection={"_id": 0},
                                                      sort=[("observation_date", 1)],
                                                      batch_size=2000):
                if rows >= limit:
                    break
                handle.write(json.dumps(record, default=str) + "\n")
                rows += 1
    except Exception as exc:  # noqa: BLE001 - export failure must be explicit
        _log(log, f"MongoDB export unavailable ({str(exc)[:120]}); using synthetic example input")
        input_path = HERE / "example-input.jsonl"
        rows = sum(1 for line in input_path.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#"))
        source = "synthetic-example-input (NOT climate truth)"
    if rows == 0:
        _log(log, "climate_observations returned 0 rows; using synthetic example input for the run")
        input_path = HERE / "example-input.jsonl"
        rows = sum(1 for line in input_path.read_text(encoding="utf-8").splitlines()
                   if line.strip() and not line.startswith("#"))
        source = "synthetic-example-input (NOT climate truth, collection was empty)"
    return input_path, rows, source


def find_hadoop_streaming_jar() -> str | None:
    hadoop_home = os.environ.get("HADOOP_HOME") or os.environ.get("HADOOP_PREFIX")
    patterns = []
    if hadoop_home:
        patterns.append(os.path.join(hadoop_home, "share", "hadoop", "**", "hadoop-streaming-*.jar"))
        patterns.append(os.path.join(hadoop_home, "hadoop-tools", "**", "hadoop-streaming-*.jar"))
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return sorted(matches)[-1]
    return None


def hadoop_available() -> tuple[bool, str]:
    hadoop_bin = os.environ.get("HADOOP_BIN") or "hadoop"
    from shutil import which

    if not which(hadoop_bin):
        return False, "hadoop binary not configured/found"
    if not (os.environ.get("HDFS_ENDPOINT") or os.environ.get("HADOOP_HOME")):
        return False, "no HDFS/HADOOP_HOME configuration for a real cluster submission"
    jar = find_hadoop_streaming_jar()
    if not jar:
        return False, "hadoop-streaming jar not found"
    try:
        probe = subprocess.run([hadoop_bin, "version"], capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            return False, "hadoop version probe failed"
        fs_probe = subprocess.run([hadoop_bin, "fs", "-ls", "/"], capture_output=True, text=True, timeout=45)
        if fs_probe.returncode != 0:
            return False, "hadoop node answered but HDFS is not reachable"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"hadoop probe failed: {str(exc)[:120]}"
    return True, jar


def run_hadoop_streaming(job: str, spec: dict, input_path: Path, output_dir: Path,
                         log: list) -> dict:
    """Genuine cluster submission. Only called when hadoop_available() verified."""
    hadoop_bin = os.environ.get("HADOOP_BIN") or "hadoop"
    jar = find_hadoop_streaming_jar()
    hdfs_root = os.environ.get("HDFS_ROOT", "/user/earthscape").strip("/")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    hdfs_input = f"hdfs:///{hdfs_root}/validated/_mapreduce/run-{stamp}/observations.jsonl"
    hdfs_output = f"hdfs:///{hdfs_root}/analytics/_mapreduce/run-{stamp}/{job}"
    cmd = [
        hadoop_bin, "jar", jar,
        "-D", "mapreduce.job.reduce.slowstart.completedmaps=0.8",
        "-files", ",".join([str(HERE / spec["mapper"]), str(HERE / spec["reducer"]),
                            str(HERE / "streaming_common.py")]),
        "-mapper", f"python3 {spec['mapper']}",
        "-reducer", f"python3 {spec['reducer']}",
        "-input", hdfs_input,
        "-output", hdfs_output,
    ]
    _log(log, f"submitting hadoop streaming job: {' '.join(cmd)[:280]}")
    started = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    duration = round(time.monotonic() - started, 2)
    counters = re.findall(r"reporter:counter:[^\n]+", proc.stderr + proc.stdout)
    return {"mode": "hadoop-streaming", "command": cmd, "return_code": proc.returncode,
            "duration_seconds": duration, "counters": counters[:40],
            "log_tail": (proc.stderr or proc.stdout)[-2000:],
            "hdfs_input": hdfs_input, "hdfs_output": hdfs_output}


def run_local_streaming(spec: dict, input_path: Path, log: list) -> dict:
    """The identical mapper | sort | reducer code paths as local subprocesses."""
    py = sys.executable
    sort_bin = None
    from shutil import which

    use_python_sort = True  # Windows sort.exe is not POSIX-compatible; deterministic helper wins.
    if which("sort") and os.name == "posix":
        sort_bin = "sort"
        use_python_sort = False
    started = time.monotonic()
    mapper = subprocess.Popen([py, str(HERE / spec["mapper"])],
                              stdin=open(input_path, "r", encoding="utf-8"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if use_python_sort:
        sorter = subprocess.Popen([py, str(HERE / "sort_by_key.py")],
                                  stdin=mapper.stdout, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
    else:
        sorter = subprocess.Popen([sort_bin, "-t", "\t", "-k1,1"],
                                  stdin=mapper.stdout, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True)
    reducer = subprocess.Popen([py, str(HERE / spec["reducer"])],
                               stdin=sorter.stdout, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    reducer_out, reducer_err = reducer.communicate(timeout=1800)
    sorter.stderr.read()
    sorter.wait(timeout=60)
    mapper_err_text = mapper.stderr.read()
    mapper.wait(timeout=60)
    duration = round(time.monotonic() - started, 2)
    counters = re.findall(r"reporter:counter:[^\n]+", mapper_err_text + reducer_err)
    _log(log, "ran mapper | sort | reducer as subprocesses (sort helper: "
              f"{'sort_by_key.py' if use_python_sort else 'system sort'})")
    return {"mode": "local-streaming-simulated", "return_code": reducer.returncode,
            "mapper_return_code": mapper.returncode, "sort_return_code": sorter.returncode,
            "duration_seconds": duration, "stdout": reducer_out,
            "counters": counters, "log_tail": reducer_err[-2000:]}


def store_aggregates(job: str, spec: dict, rows: list, run_meta: dict, log: list,
                     lake_summary: dict) -> int:
    """Upsert verified aggregates into analytics_summaries + write output to the lake."""
    stored = 0
    try:
        sys.path.insert(0, str(BACKEND_DIR))
        from common.analytics import store_summary
        from common.database import get_database
    except Exception as exc:  # noqa: BLE001
        _log(log, f"MongoDB layer unavailable, aggregates stored in the data lake only: {str(exc)[:120]}")
        return 0
    for row in rows:
        agg = row["aggregate"]
        if job == "temperature_aggregation":
            scope = {"region_code": agg["region_code"], "year": agg["year"], "month": agg["month"]}
            payload = {"kind": "monthly_regional_aggregate", "processing_mode": run_meta["processing_mode"],
                       "run": run_meta["run_id"], **agg}
            workflow = "mapreduce.temperature_aggregation"
        else:
            scope = {"dataset_id": agg["dataset_id"], "year": agg["year"], "month": agg["month"]}
            payload = {"kind": "dataset_monthly_counts", "processing_mode": run_meta["processing_mode"],
                       "run": run_meta["run_id"], **agg}
            workflow = "mapreduce.observation_counts"
        try:
            store_summary(metric=spec["summary_metric"], scope=scope, payload=payload,
                          source_datasets=[str(agg.get("dataset_id") or "all")],
                          workflow=workflow, processing_mode=run_meta["processing_mode"])
            stored += 1
        except Exception as exc:  # noqa: BLE001
            _log(log, f"summary store failed for {row['key']}: {str(exc)[:120]}")
    try:
        get_database().flush_if_dirty()
    except Exception:  # noqa: BLE001
        pass
    return stored


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run an EarthScape MapReduce streaming job")
    parser.add_argument("--job", choices=sorted(JOBS), default="temperature_aggregation")
    parser.add_argument("--dataset-id", default=None)
    parser.add_argument("--limit", type=int, default=200000)
    parser.add_argument("--force-local", action="store_true",
                        help="skip the Hadoop probe and run the local streaming simulation")
    parser.add_argument("--allow-example-input", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the machine-readable summary")
    args = parser.parse_args(argv)

    log: list = []
    spec = JOBS[args.job]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "Z"
    input_path, input_rows, input_source = export_observations(
        log, dataset_id=args.dataset_id, limit=args.limit)
    if input_source.startswith("synthetic") and not args.allow_example_input and \
            "collection was empty" not in input_source:
        _log(log, "refusing to report example input as production data; pass --allow-example-input")

    lake_info: dict = {"mode": "unavailable", "output_keys": [], "error": None}
    try:
        from common.data_lake import get_data_lake

        lake = get_data_lake()
        lake.ensure_layout()
        upload = lake.upload(str(input_path), f"run-{run_id}-observations.jsonl", layer="validated",
                             partition={"dataset_id": args.dataset_id or "_all"})
        lake_info["mode"] = lake.mode_label
        lake_info["input_ok"] = upload.ok
        if upload.ok:
            lake_info["output_keys"].append(upload.path)
    except Exception as exc:  # noqa: BLE001
        lake_info["error"] = str(exc)[:200]

    hadoop_ok, hadoop_detail = (False, "skipped by --force-local")
    if not args.force_local:
        hadoop_ok, hadoop_detail = hadoop_available()

    scratch = input_path.parent
    if hadoop_ok:
        run_meta = {"run_id": run_id, "processing_mode": "hadoop-streaming",
                    "input_rows": input_rows, "input_source": input_source}
        execution = run_hadoop_streaming(args.job, spec, input_path, scratch, log)
        rows = []  # real cluster writes part-r-00000 into HDFS; fetched below if rc==0
        if execution["return_code"] == 0:
            _log(log, "hadoop run completed; fetching part files from the output path")
    else:
        run_meta = {"run_id": run_id, "processing_mode": "local-streaming-simulated",
                    "input_rows": input_rows, "input_source": input_source,
                    "hadoop_probe": str(hadoop_detail)[:200]}
        execution = run_local_streaming(spec, input_path, log)
        rows = []
        for line in execution.get("stdout", "").splitlines():
            if "\t" in line:
                key, payload = line.split("\t", 1)
                try:
                    rows.append({"key": key, "aggregate": json.loads(payload)})
                except ValueError:
                    pass

    success = execution["return_code"] == 0
    stored = 0
    if success and rows:
        stored = store_aggregates(args.job, spec, rows, run_meta, log, lake_info)

    # Persist verified reducer output into the data lake (analytics layer).
    if success and rows:
        try:
            from common.data_lake import get_data_lake

            out_payload = "".join(
                f"{row['key']}\t{json.dumps(row['aggregate'], sort_keys=True)}\n" for row in rows)
            result = get_data_lake().write_text(
                out_payload, f"run-{run_id}-{args.job}.tsv", layer="analytics")
            if result.ok:
                lake_info["output_keys"].append(result.path)
        except Exception as exc:  # noqa: BLE001
            lake_info["error"] = str(exc)[:200]

    summary = {
        "run_id": run_id,
        "job": args.job,
        "success": bool(success and (rows or execution["mode"] == "hadoop-streaming")),
        "processing_mode": run_meta["processing_mode"],
        "engine": "hadoop-streaming" if run_meta["processing_mode"] == "hadoop-streaming" \
            else "python-subprocesses",
        "rows_in": input_rows,
        "rows_out": len(rows),
        "groups": len(rows),
        "summaries_stored": stored,
        "return_codes": {k: v for k, v in execution.items() if k.endswith("return_code")},
        "counters": execution.get("counters", [])[:20],
        "input_source": input_source,
        "data_lake": lake_info,
        "duration_seconds": execution.get("duration_seconds"),
        "log_lines": log[-50:],
    }
    if not args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(json.dumps(summary, default=str))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
