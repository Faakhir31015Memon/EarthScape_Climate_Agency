"""Job aggregate_regions — region x month x year aggregations per variable.

Engines: PySpark (``group``/``agg`` on the cluster or local[*]) when usable,
otherwise the pandas fallback. Output rows and ``analytics/`` layout are
identical; missing measurements never contribute (and never count as 0).
"""

from __future__ import annotations

import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, safe_div, write_partitioned_rows)

COLUMNS = ["dataset_id", "region", "year", "month", "variable", "count", "mean", "min", "max", "sum"]
VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]


def _agg_rows(df: pd.DataFrame, dataset: str) -> list:
    rows = []
    variables = [v for v in VARIABLES if v in df.columns and df[v].notna().any()]
    for (ds, region, year, month), grp in df.groupby(["dataset_id", "region", "year", "month"], dropna=False):
        for var in variables:
            series = grp[var].dropna()
            if series.empty:
                continue
            rows.append({
                "dataset_id": ds or dataset, "region": region, "year": year, "month": month,
                "variable": var, "count": int(series.shape[0]),
                "mean": round_or_none(series.mean()), "min": round_or_none(series.min()),
                "max": round_or_none(series.max()), "sum": round_or_none(series.sum()) if var == "precipitation" else None,
            })
    return rows


def run_pandas(params: dict) -> dict:
    summary = new_summary("aggregate_regions", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), paths=params.get("paths") or None,
                     region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows for the requested dataset/filters")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    agg = _agg_rows(df, params.get("dataset") or "mixed")
    manifests = write_partitioned_rows(agg, params.get("dataset") or "mixed",
                                       "aggregate_regions", COLUMNS, layer="analytics")
    summary.update(rows_out=len(agg), outputs=[m["path"] for m in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import SparkUnavailable, get_session

    summary = new_summary("aggregate_regions", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    from pyspark.sql import functions as F
    from spark.common import safe_col

    df = session.read.json([str(f) for f in files]).dropDuplicates(["dedupe_key"])
    df = (df.select(
        F.col("dataset_id"),
        F.coalesce(safe_col(df, "location.region"), safe_col(df, "location.name"),
                   F.lit("unassigned")).alias("region"),
        F.year(F.substring("timestamp", 1, 10).cast("date")).alias("year"),
        F.month(F.substring("timestamp", 1, 10).cast("date")).alias("month"),
        *[safe_col(df, f"metrics.{v}.value").alias(v) for v in VARIABLES])
        .filter("year IS NOT NULL"))
    if params.get("region"):
        df = df.filter(F.col("region") == params["region"])
    if params.get("year_from"):
        df = df.filter(F.col("year") >= int(params["year_from"]))
    if params.get("year_to"):
        df = df.filter(F.col("year") <= int(params["year_to"]))
    long_df = df
    agg_rows = []
    for var in VARIABLES:
        grouped = (long_df.filter(F.col(var).isNotNull())
                   .groupBy("dataset_id", "region", "year", "month")
                   .agg(F.count(var).alias("count"), F.mean(var).alias("mean"),
                        F.min(var).alias("min"), F.max(var).alias("max"),
                        F.sum(var).alias("total")))
        bounded = grouped.limit(100_000).toPandas()  # already-aggregated frame: bounded
        for r in bounded.itertuples(index=False):
            agg_rows.append({
                "dataset_id": r.dataset_id, "region": r.region, "year": int(r.year), "month": int(r.month),
                "variable": var, "count": int(r.count), "mean": round_or_none(r.mean),
                "min": round_or_none(r.min), "max": round_or_none(r.max),
                "sum": round_or_none(r.total) if var == "precipitation" else None,
            })
    manifests = write_partitioned_rows(agg_rows, params.get("dataset") or "mixed",
                                       "aggregate_regions", COLUMNS, layer="analytics")
    summary.update(rows_out=len(agg_rows), outputs=[m["path"] for m in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def _resolve_files(params: dict) -> list:
    from pathlib import Path

    if params.get("paths"):
        return [Path(p) for p in params["paths"]]
    from spark.common import find_input

    return find_input(params.get("dataset"))


def run(params: dict) -> dict:
    return execute_job("aggregate_regions", params, run_spark, run_pandas)
