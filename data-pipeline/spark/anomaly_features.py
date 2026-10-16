"""Job anomaly_features — per-observation anomaly features for ML.

For each variable, each record gets: climatology mean/std for its region and
calendar month, a z-score, and an ``is_anomaly`` flag when |z| >= the
``z_threshold`` parameter (default 2.5). Suspect quality never turns a value
into an anomaly silently — both are reported.
"""

from __future__ import annotations

import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, write_partitioned_rows)

VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]
COLUMNS = ["dedupe_key", "dataset_id", "region", "year", "month", "timestamp",
           "variable", "value", "climatology_mean", "climatology_std", "z_score",
           "is_anomaly", "quality_status"]


def _anomalies(df: pd.DataFrame, variables: list, threshold: float) -> list:
    out = []
    for var in variables:
        series = df[df[var].notna()]
        if series.empty:
            continue
        clim = series.groupby(["dataset_id", "region", "month"])[var].agg(["mean", "std"]).reset_index()
        merged = series.merge(clim, on=["dataset_id", "region", "month"], how="left")
        for r in merged.itertuples(index=False):
            std = getattr(r, "std", None)
            z = None
            if std is not None and std == std and std > 0:
                z = (getattr(r, var) - r.mean) / std
            out.append({
                "dedupe_key": r.dedupe_key, "dataset_id": r.dataset_id, "region": r.region,
                "year": r.year, "month": r.month, "timestamp": r.timestamp, "variable": var,
                "value": round_or_none(getattr(r, var)), "climatology_mean": round_or_none(r.mean),
                "climatology_std": round_or_none(std), "z_score": round_or_none(z),
                "is_anomaly": bool(z is not None and abs(z) >= threshold),
                "quality_status": getattr(r, "quality_status", ""),
            })
    return out


def run_pandas(params: dict) -> dict:
    summary = new_summary("anomaly_features", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), paths=params.get("paths") or None,
                     region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    variables = [params["variable"]] if params.get("variable") in VARIABLES else VARIABLES
    threshold = float(params.get("z_threshold", 2.5))
    feats = _anomalies(df, variables, threshold)
    manifests = write_partitioned_rows(feats, params.get("dataset") or "mixed",
                                       "anomaly_features", COLUMNS, layer="analytics")
    anomalies = sum(1 for f in feats if f["is_anomaly"])
    summary.update(rows_out=len(feats), anomalies=anomalies, z_threshold=threshold,
                   outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F
    from spark.common import safe_col

    summary = new_summary("anomaly_features", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    var = params.get("variable") if params.get("variable") in VARIABLES else "temperature"
    raw = session.read.json([str(f) for f in files]).dropDuplicates(["dedupe_key"])
    df = raw.select(
        F.col("dedupe_key"), F.col("dataset_id"),
        F.coalesce(safe_col(raw, "location.region"), safe_col(raw, "location.name"),
                   F.lit("unassigned")).alias("region"),
        F.col("timestamp"),
        F.substring("timestamp", 1, 4).cast("int").alias("year"),
        F.substring("timestamp", 6, 2).cast("int").alias("month"),
        safe_col(raw, "quality.status").alias("quality_status"),
        safe_col(raw, f"metrics.{var}.value").alias("value")).filter("value IS NOT NULL")
    clim = df.groupBy("dataset_id", "region", "month").agg(
        F.mean("value").alias("climatology_mean"), F.stddev("value").alias("climatology_std"))
    joined = df.join(clim, ["dataset_id", "region", "month"], "left") \
        .withColumn("z_score", F.when(F.col("climatology_std") > 0,
                                      (F.col("value") - F.col("climatology_mean")) / F.col("climatology_std")))
    threshold = float(params.get("z_threshold", 2.5))
    joined = joined.withColumn("is_anomaly", F.abs(F.col("z_score")) >= threshold) \
                   .withColumn("variable", F.lit(var))
    rows = [{"dedupe_key": r.dedupe_key, "dataset_id": r.dataset_id, "region": r.region,
             "year": int(r.year), "month": int(r.month), "timestamp": r.timestamp, "variable": var,
             "value": round_or_none(r.value), "climatology_mean": round_or_none(r.climatology_mean),
             "climatology_std": round_or_none(r.climatology_std), "z_score": round_or_none(r.z_score),
             "is_anomaly": bool(r.is_anomaly), "quality_status": r.quality_status}
            for r in joined.limit(500_000).toPandas().itertuples(index=False)]
    manifests = write_partitioned_rows(rows, params.get("dataset") or "mixed",
                                       "anomaly_features", COLUMNS, layer="analytics")
    summary.update(rows_out=len(rows), anomalies=sum(1 for x in rows if x["is_anomaly"]),
                   outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("anomaly_features", params, run_spark, run_pandas)
