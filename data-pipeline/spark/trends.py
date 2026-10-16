"""Job trends — monthly and annual means plus anomaly z-scores.

For each region and variable: monthly mean, annual mean, and the monthly
anomaly ``z = (value - climatology_mean) / climatology_std`` where climatology
is the per-(region, calendar-month) mean/std over the whole window.
"""

from __future__ import annotations

import math

import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, write_partitioned_rows)

VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]
COLUMNS = ["dataset_id", "scope", "region", "variable", "year", "month", "value",
           "climatology_mean", "climatology_std", "z_score"]


def _trend_rows(df: pd.DataFrame, variables: list) -> list:
    out = []
    for var in variables:
        series = df[df[var].notna()]
        if series.empty:
            continue
        for (ds, region), grp in series.groupby(["dataset_id", "region"], dropna=False):
            monthly = grp.groupby("month")[var].agg(["mean", "count"])
            clim_mean = grp.groupby("month")[var].mean()
            clim_std = grp.groupby("month")[var].std()
            for month, row in grp.groupby("month"):
                m = float(row[var].mean())
                mu = float(clim_mean.get(month, math.nan))
                sd = float(clim_std.get(month, math.nan))
                z = (m - mu) / sd if sd and sd > 0 and not math.isnan(sd) else None
                out.append({"dataset_id": ds, "scope": "monthly", "region": region, "variable": var,
                            "year": int(row["year"].iloc[0]), "month": int(month),
                            "value": round_or_none(m), "climatology_mean": round_or_none(mu),
                            "climatology_std": round_or_none(sd), "z_score": round_or_none(z)})
            for year, row in grp.groupby("year"):
                out.append({"dataset_id": ds, "scope": "annual", "region": region, "variable": var,
                            "year": int(year), "month": None,
                            "value": round_or_none(float(row[var].mean())),
                            "climatology_mean": round_or_none(float(grp[var].mean())),
                            "climatology_std": round_or_none(float(grp[var].std())),
                            "z_score": None})
    return out


def run_pandas(params: dict) -> dict:
    summary = new_summary("trends", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), paths=params.get("paths") or None,
                     region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    variables = [params["variable"]] if params.get("variable") in VARIABLES else VARIABLES
    trends = _trend_rows(df, variables)
    manifests = write_partitioned_rows(trends, params.get("dataset") or "mixed",
                                       "trends", COLUMNS, layer="analytics")
    summary.update(rows_out=len(trends), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F
    from spark.common import safe_col

    summary = new_summary("trends", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    var = params.get("variable") if params.get("variable") in VARIABLES else "temperature"
    raw = session.read.json([str(f) for f in files]).dropDuplicates(["dedupe_key"])
    df = raw.select(
        F.col("dataset_id"),
        F.coalesce(safe_col(raw, "location.region"), safe_col(raw, "location.name"),
                   F.lit("unassigned")).alias("region"),
        F.substring("timestamp", 1, 4).cast("int").alias("year"),
        F.substring("timestamp", 6, 2).cast("int").alias("month"),
        safe_col(raw, f"metrics.{var}.value").alias("value")).filter("value IS NOT NULL AND year IS NOT NULL")
    if params.get("region"):
        df = df.filter(F.col("region") == params["region"])
    if params.get("year_from"):
        df = df.filter(F.col("year") >= int(params["year_from"]))
    if params.get("year_to"):
        df = df.filter(F.col("year") <= int(params["year_to"]))
    monthly = df.groupBy("dataset_id", "region", "year", "month").agg(
        F.mean("value").alias("value"), F.count("value").alias("n"))
    clim = monthly.groupBy("dataset_id", "region", "month").agg(
        F.mean("value").alias("climatology_mean"), F.stddev("value").alias("climatology_std"))
    joined = monthly.join(clim, ["dataset_id", "region", "month"], "left") \
        .withColumn("z_score", F.when(F.col("climatology_std") > 0,
                                      (F.col("value") - F.col("climatology_mean")) / F.col("climatology_std"))) \
        .withColumn("scope", F.lit("monthly")).withColumn("variable", F.lit(var))
    annual = df.groupBy("dataset_id", "region", "year").agg(F.mean("value").alias("value")) \
        .withColumn("scope", F.lit("annual")).withColumn("month", F.lit(None).cast("int")) \
        .withColumn("variable", F.lit(var))
    out = joined.unionByName(annual, allowMissingColumns=True)
    bounded = out.limit(500_000).toPandas()
    rows = []
    for r in bounded.itertuples(index=False):
        rows.append({"dataset_id": r.dataset_id, "scope": r.scope, "region": r.region,
                     "variable": var, "year": int(r.year), "month": int(r.month) if r.month == r.month and r.month is not None else None,
                     "value": round_or_none(r.value),
                     "climatology_mean": round_or_none(getattr(r, "climatology_mean", None)),
                     "climatology_std": round_or_none(getattr(r, "climatology_std", None)),
                     "z_score": round_or_none(getattr(r, "z_score", None))})
    manifests = write_partitioned_rows(rows, params.get("dataset") or "mixed", "trends", COLUMNS, layer="analytics")
    summary.update(rows_out=len(rows), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("trends", params, run_spark, run_pandas)
