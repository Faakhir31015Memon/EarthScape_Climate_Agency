"""Job historical_compare — each year vs a baseline period per region+variable.

``baseline_from``/``baseline_to`` define the reference climatology window
(default: the earliest 10 complete years in the data). Output: annual mean,
baseline mean, absolute and percentage delta.
"""

from __future__ import annotations

import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, write_partitioned_rows)

VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]
COLUMNS = ["dataset_id", "region", "variable", "year", "annual_mean", "baseline_from",
           "baseline_to", "baseline_mean", "delta", "delta_pct"]


def _compare(df: pd.DataFrame, variables: list, baseline_from, baseline_to) -> list:
    out = []
    for var in variables:
        series = df[df[var].notna()]
        if series.empty:
            continue
        for (ds, region), grp in series.groupby(["dataset_id", "region"], dropna=False):
            years = sorted(grp["year"].unique())
            b_from = baseline_from if baseline_from is not None else years[0]
            b_to = baseline_to if baseline_to is not None else min(years[0] + 9, years[-1])
            base = grp[(grp["year"] >= b_from) & (grp["year"] <= b_to)][var].mean()
            for year, sub in grp.groupby("year"):
                am = float(sub[var].mean())
                delta = am - float(base)
                out.append({
                    "dataset_id": ds, "region": region, "variable": var, "year": int(year),
                    "annual_mean": round_or_none(am), "baseline_from": int(b_from),
                    "baseline_to": int(b_to), "baseline_mean": round_or_none(float(base)),
                    "delta": round_or_none(delta),
                    "delta_pct": round_or_none(100.0 * delta / base) if base else None,
                })
    return out


def run_pandas(params: dict) -> dict:
    summary = new_summary("historical_compare", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    variables = [params["variable"]] if params.get("variable") in VARIABLES else VARIABLES
    compared = _compare(df, variables, params.get("baseline_from"), params.get("baseline_to"))
    manifests = write_partitioned_rows(compared, params.get("dataset") or "mixed",
                                       "historical_compare", COLUMNS, layer="analytics")
    summary.update(rows_out=len(compared), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F

    summary = new_summary("historical_compare", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    var = params.get("variable") if params.get("variable") in VARIABLES else "temperature"
    df = session.read.json([str(f) for f in files]).select(
        F.col("dataset_id"),
        F.coalesce(F.col("location.region"), F.col("location.name"), F.lit("unassigned")).alias("region"),
        F.substring("timestamp", 1, 4).cast("int").alias("year"),
        F.col(f"metrics.{var}.value").alias("value")).filter("value IS NOT NULL AND year IS NOT NULL")
    b_from = int(params.get("baseline_from") or df.agg(F.min("year")).first()[0])
    b_to = int(params.get("baseline_to") or (b_from + 9))
    annual = df.groupBy("dataset_id", "region", "year").agg(F.mean("value").alias("annual_mean"))
    baseline = df.filter((F.col("year") >= b_from) & (F.col("year") <= b_to)) \
        .groupBy("dataset_id", "region").agg(F.mean("value").alias("baseline_mean"))
    joined = annual.join(baseline, ["dataset_id", "region"], "left") \
        .withColumn("delta", F.col("annual_mean") - F.col("baseline_mean")) \
        .withColumn("delta_pct", 100.0 * F.col("delta") / F.col("baseline_mean"))
    rows = [{"dataset_id": r.dataset_id, "region": r.region, "variable": var, "year": int(r.year),
             "annual_mean": round_or_none(r.annual_mean), "baseline_from": b_from, "baseline_to": b_to,
             "baseline_mean": round_or_none(r.baseline_mean), "delta": round_or_none(r.delta),
             "delta_pct": round_or_none(r.delta_pct)}
            for r in joined.limit(500_000).toPandas().itertuples(index=False)]
    manifests = write_partitioned_rows(rows, params.get("dataset") or "mixed",
                                       "historical_compare", COLUMNS, layer="analytics")
    summary.update(rows_out=len(rows), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("historical_compare", params, run_spark, run_pandas)
