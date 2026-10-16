"""Job profile_missing — per-field completeness profile over time.

For each dataset/year/month: share of observations carrying each canonical
measurement. Missing values stay *missing* — this job is precisely about
measuring that honestly.
"""

from __future__ import annotations

import pandas as pd

from spark.common import (FLAT_METRIC_FIELDS, execute_job, finish_summary,
                          load_rows, new_summary, round_or_none,
                          write_partitioned_rows)

COLUMNS = ["dataset_id", "year", "month", "observations"] + [f"completeness_{m}" for m in FLAT_METRIC_FIELDS]


def _profile(df: pd.DataFrame) -> list:
    rows = []
    for (ds, year, month), grp in df.groupby(["dataset_id", "year", "month"], dropna=False):
        n = len(grp)
        row = {"dataset_id": ds, "year": year, "month": month, "observations": n}
        for m in FLAT_METRIC_FIELDS:
            row[f"completeness_{m}"] = round_or_none(grp[m].notna().sum() / n if n else None)
        rows.append(row)
    return rows


def run_pandas(params: dict) -> dict:
    summary = new_summary("profile_missing", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    prof = _profile(pd.DataFrame(rows))
    manifests = write_partitioned_rows(prof, params.get("dataset") or "mixed",
                                       "profile_missing", COLUMNS, layer="analytics")
    overall = {}
    for m in FLAT_METRIC_FIELDS:
        vals = [r[f"completeness_{m}"] for r in prof if r[f"completeness_{m}"] is not None]
        overall[m] = round_or_none(sum(vals) / len(vals)) if vals else None
    summary.update(rows_out=len(prof), outputs=[x["path"] for x in manifests],
                   overall_completeness=overall,
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F
    from pyspark.sql.types import StructType, StructField, StringType, DoubleType

    summary = new_summary("profile_missing", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    schema = StructType([
        StructField("dataset_id", StringType()),
        StructField("timestamp", StringType()),
        StructField("metrics", StructType([
            StructField(name, StructType([StructField("value", DoubleType())])) for name in FLAT_METRIC_FIELDS])),
    ])
    df = session.read.schema(schema).json([str(f) for f in files])
    df = df.withColumn("year", F.substring("timestamp", 1, 4).cast("int")) \
           .withColumn("month", F.substring("timestamp", 6, 2).cast("int"))
    if params.get("year_from"):
        df = df.filter(F.col("year") >= int(params["year_from"]))
    if params.get("year_to"):
        df = df.filter(F.col("year") <= int(params["year_to"]))
    aggs = [F.count(F.col(f"metrics.{m}.value")).alias(f"n_{m}") for m in FLAT_METRIC_FIELDS]
    grouped = df.groupBy("dataset_id", "year", "month").agg(F.count(F.lit(1)).alias("observations"), *aggs)
    bounded = grouped.limit(100_000).toPandas()
    rows = []
    for r in bounded.itertuples(index=False):
        row = {"dataset_id": r.dataset_id, "year": r.year, "month": r.month, "observations": int(r.observations)}
        for m in FLAT_METRIC_FIELDS:
            n = int(getattr(r, f"n_{m}"))
            row[f"completeness_{m}"] = round_or_none(n / row["observations"] if row["observations"] else None)
        rows.append(row)
    manifests = write_partitioned_rows(rows, params.get("dataset") or "mixed",
                                       "profile_missing", COLUMNS, layer="analytics")
    summary.update(rows_out=len(rows), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("profile_missing", params, run_spark, run_pandas)
