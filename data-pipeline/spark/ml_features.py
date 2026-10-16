"""Job ml_features — lag / rolling / seasonal features for machine learning.

Builds per-series (dataset + region + station) daily features for the chosen
variable: lags 1/3/7, rolling 7 and 30 windows (mean, std), first difference,
seasonal encodings (month sin/cos, day-of-year sin/cos). Missing measurements
stay missing: windows use ``min_periods`` so a lag of an absent day is absent,
never 0. Output goes to the ``ml-features/`` data-lake layer.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, write_partitioned_rows)

VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]


def feature_columns(var: str) -> list:
    return [
        "dedupe_key", "dataset_id", "region", "station_id", "timestamp", "year", "month", "day",
        "variable", "value", f"{var}_lag1", f"{var}_lag3", f"{var}_lag7",
        f"{var}_diff1", f"{var}_roll7_mean", f"{var}_roll7_std",
        f"{var}_roll30_mean", f"{var}_roll30_std",
        "month_sin", "month_cos", "doy_sin", "doy_cos", "quality_status",
    ]


def _build(df: pd.DataFrame, var: str) -> list:
    out = []
    key = ["dataset_id", "region", "station_id"]
    for grpkey, grp in df[df[var].notna()].groupby(key, dropna=False):
        g = grp.sort_values("timestamp").copy()
        s = g[var]
        g[f"{var}_lag1"] = s.shift(1)
        g[f"{var}_lag3"] = s.shift(3)
        g[f"{var}_lag7"] = s.shift(7)
        g[f"{var}_diff1"] = s.diff(1)
        g[f"{var}_roll7_mean"] = s.rolling(7, min_periods=4).mean()
        g[f"{var}_roll7_std"] = s.rolling(7, min_periods=4).std()
        g[f"{var}_roll30_mean"] = s.rolling(30, min_periods=14).mean()
        g[f"{var}_roll30_std"] = s.rolling(30, min_periods=14).std()
        doy = pd.to_datetime(g["timestamp"].str[:10]).dt.dayofyear
        g["month_sin"] = np.sin(2 * np.pi * g["month"] / 12.0)
        g["month_cos"] = np.cos(2 * np.pi * g["month"] / 12.0)
        g["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
        g["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
        g = g.assign(variable=var)
        for r in g[feature_columns(var)].dropna(axis=0, how=None).itertuples(index=False):
            row = r._asdict()
            out.append({k: (round_or_none(v) if isinstance(v, (int, float, np.floating)) and k not in
                            ("year", "month", "day") else v) for k, v in row.items()})
    return out


def run_pandas(params: dict) -> dict:
    summary = new_summary("ml_features", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), paths=params.get("paths") or None,
                     region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    var = params.get("variable") if params.get("variable") in VARIABLES else "temperature"
    feats = _build(df, var)
    manifests = write_partitioned_rows(feats, params.get("dataset") or "mixed",
                                       f"ml_features_{var}", feature_columns(var), layer="ml-features")
    summary.update(rows_out=len(feats), variable=var,
                   outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F
    from pyspark.sql import Window

    summary = new_summary("ml_features", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    var = params.get("variable") if params.get("variable") in VARIABLES else "temperature"
    df = session.read.json([str(f) for f in files]).select(
        F.col("dedupe_key"), F.col("dataset_id"),
        F.coalesce(F.col("location.region"), F.col("location.name"), F.lit("unassigned")).alias("region"),
        F.coalesce(F.col("location.station_id"), F.lit("")).alias("station_id"),
        F.col("timestamp"), F.col("quality.status").alias("quality_status"),
        F.substring("timestamp", 1, 4).cast("int").alias("year"),
        F.substring("timestamp", 6, 2).cast("int").alias("month"),
        F.substring("timestamp", 9, 2).cast("int").alias("day"),
        F.col(f"metrics.{var}.value").alias("value")).filter("value IS NOT NULL")
    w = Window.partitionBy("dataset_id", "region", "station_id").orderBy("timestamp")
    df = (df
          .withColumn(f"{var}_lag1", F.lag("value", 1).over(w))
          .withColumn(f"{var}_lag3", F.lag("value", 3).over(w))
          .withColumn(f"{var}_lag7", F.lag("value", 7).over(w))
          .withColumn(f"{var}_diff1", F.col("value") - F.lag("value", 1).over(w))
          .withColumn(f"{var}_roll7_mean", F.avg("value").over(w.rowsBetween(-6, 0)))
          .withColumn(f"{var}_roll7_std", F.stddev("value").over(w.rowsBetween(-6, 0)))
          .withColumn(f"{var}_roll30_mean", F.avg("value").over(w.rowsBetween(-29, 0)))
          .withColumn(f"{var}_roll30_std", F.stddev("value").over(w.rowsBetween(-29, 0)))
          .withColumn("month_sin", F.sin(2 * F.pi() * F.col("month") / 12))
          .withColumn("month_cos", F.cos(2 * F.pi() * F.col("month") / 12))
          .withColumn("variable", F.lit(var)))
    pdf = df.limit(2_000_000).toPandas()
    feats = []
    for r in pdf.itertuples(index=False):
        row = r._asdict()
        feats.append({k: round_or_none(v) if isinstance(v, float) and k not in ("year", "month", "day") else v
                      for k, v in row.items()})
    manifests = write_partitioned_rows(feats, params.get("dataset") or "mixed",
                                       f"ml_features_{var}", feature_columns(var), layer="ml-features")
    summary.update(rows_out=len(feats), variable=var, outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("ml_features", params, run_spark, run_pandas)
