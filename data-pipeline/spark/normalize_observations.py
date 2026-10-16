"""Job normalize_observations — large-file ingestion and normalisation.

Reads raw JSONL observation files from the data lake (or an explicit path),
runs them through the validation engine (schema, timestamps, units, plausibility,
dedupe) and writes validated canonical JSONL into the partitioned
``validated/`` layer plus the ValidationReport JSON.

Engine: validation is pure Python; the Spark DataFrame API adds nothing here, so
this job always reports ``engine_used="python-validation"`` (honest, identical
output contract as every other job).
"""

from __future__ import annotations

import json
from pathlib import Path

from pipeline_config import partition_dir
from spark.common import (finish_summary, new_summary, write_partitioned_rows)
from validation.quality import BatchValidator


def run(params: dict) -> dict:
    params = dict(params or {})
    dataset = params.get("dataset") or "mixed"
    paths = params.get("paths")
    summary = new_summary("normalize_observations", params, "python-validation")

    if paths:
        files = [Path(p) for p in paths]
    else:
        from spark.common import find_input

        files = find_input(params.get("dataset"), layers=("raw",))
    if not files:
        summary.update(status="unavailable",
                       reason="no raw JSONL inputs found in the data lake for this dataset")
        return finish_summary(summary)

    validator = BatchValidator()
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        validator.add(json.loads(line))
                    except json.JSONDecodeError:
                        validator.report.total_input += 1
                        validator.report.rejected += 1
                        validator.report.bump("unparsable_line")
    report = validator.report.finish()

    out_files = []
    grouped: dict = {}
    for rec in validator.accepted:
        ts = rec["timestamp"]
        grouped.setdefault((ts[:4], ts[5:7]), []).append(rec)
    for (year, month), bucket in grouped.items():
        directory = partition_dir("validated", dataset, int(year), int(month))
        path = directory / "observations.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for rec in bucket:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out_files.append(str(path))
    report_path = partition_dir("analytics", dataset, 1970, 1) / f"validation_report.json"
    report_path.write_text(report.to_json(), encoding="utf-8")

    summary.update(
        rows_out=report.accepted,
        totals=report.to_dict()["totals"],
        data_quality_score=round(report.dq_score, 4),
        outputs=out_files + [str(report_path)],
        status="ok" if report.accepted else "error",
    )
    return finish_summary(summary)
