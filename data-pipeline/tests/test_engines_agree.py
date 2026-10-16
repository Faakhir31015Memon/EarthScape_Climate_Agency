"""Independent implementations must agree: MapReduce streaming vs pandas job.

Runs the Hadoop-Streaming scripts (via the subprocess local runner core) and
the pandas engine of ``spark.aggregate_regions`` over the SAME fixture, then
compares weighted annual means per region. Also verifies the Spark/pandas
engine-selection honesty (engine_used is recorded).
"""

import json
import shutil
from pathlib import Path

import pytest

from mapreduce.local_runner import _run_streaming_job
from pipeline_config import datalake_root
from spark.aggregate_regions import run_pandas
from spark.unit_reference import pandas_reference

FIXTURE = []


def _rec(ts, region, temp=None, precip=None):
    metrics = {}
    if temp is not None:
        metrics["temperature"] = {"value": temp, "unit": "C"}
    if precip is not None:
        metrics["precipitation"] = {"value": precip, "unit": "mm"}
    return {"timestamp": ts, "granularity": "daily", "dataset_id": "xr",
            "source": {"provider": "sample", "kind": "sample"},
            "location": {"latitude": 24.86, "longitude": 67.0, "region": region},
            "metrics": metrics, "dedupe_key": "k" + ts + region}


for i in range(40):
    FIXTURE.append(_rec(f"2023-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T00:00:00Z", "Sindh",
                        temp=25.0 + (i % 7), precip=(i % 5) * 2.0))
    FIXTURE.append(_rec(f"2024-{(i % 12) + 1:02d}-{(i % 27) + 1:02d}T00:00:00Z", "Punjab",
                        temp=30.0 - (i % 6), precip=None if i % 7 == 0 else float(i)))


@pytest.fixture()
def fixture_file(tmp_path: Path) -> Path:
    p = tmp_path / "xr.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in FIXTURE) + "\n", encoding="utf-8")
    return p


def test_mapreduce_and_pandas_engine_agree(fixture_file: Path):
    dataset = "engine_agreement_test"
    out_dir = datalake_root() / "processed" / f"dataset={dataset}"
    an_dir = datalake_root() / "analytics" / f"dataset={dataset}"
    try:
        mr_rows, _ = _run_streaming_job("temp_region_year", fixture_file)
        summary = run_pandas({"dataset": dataset, "paths": [str(fixture_file)],
                              "variable": "temperature"})
        agg = summary["rows_out"]
        assert agg > 0, summary
        # weighted annual mean from the monthly job rows
        yearly: dict = {}
        import pandas as pd

        frames = []
        for path in summary["outputs"]:
            p = Path(path)
            if p.suffix == ".parquet":
                frames.append(pd.read_parquet(p))
            else:
                frames.append(pd.read_csv(p))
        pdf = pd.concat(frames)
        pdf = pdf[pdf["variable"] == "temperature"]
        for r in pdf.itertuples(index=False):
            key = (r.region, int(r.year))
            tot, cnt = yearly.get(key, (0.0, 0))
            yearly[key] = (tot + float(r.mean) * int(r.count), cnt + int(r.count))
        annual = {k: v[0] / v[1] for k, v in yearly.items()}

        mr = {(r[0], int(r[1])): (int(r[2]), float(r[3])) for r in mr_rows}
        assert set(mr) == set(annual)
        for key in mr:
            count, mean = mr[key]
            assert count == yearly[key][1], key
            assert abs(mean - annual[key]) < 0.02, (key, mean, annual[key])
        # and the streaming output matches the documented independent reference
        ref = {(r["region"], int(r["year"])): (r["count"], r["mean_c"]) for r in
               pandas_reference(fixture_file)}
        assert ref == mr or all(abs(ref[k][1] - mr[k][1]) < 0.01 and ref[k][0] == mr[k][0]
                                for k in ref)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(an_dir, ignore_errors=True)


def test_engine_choice_is_recorded_honestly(fixture_file: Path):
    from spark.common import spark_importable
    from spark.aggregate_regions import run

    dataset = "engine_record_test"
    out_dir = datalake_root() / "processed" / f"dataset={dataset}"
    an_dir = datalake_root() / "analytics" / f"dataset={dataset}"
    try:
        summary = run({"dataset": dataset, "paths": [str(fixture_file)]})
        assert summary["engine_used"] in ("pyspark", "pandas")
        if not spark_importable():
            assert summary["engine_used"] == "pandas"
            assert "not installed" in summary.get("engine_note", "")
        assert summary["rows_out"] > 0
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        shutil.rmtree(an_dir, ignore_errors=True)
