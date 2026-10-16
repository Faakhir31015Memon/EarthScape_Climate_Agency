"""Job correlations — pairwise Pearson correlation matrix between variables.

Computed on the observation level (pairwise-complete: a missing measurement in
one variable simply does not contribute to that pair — never imputed).
"""

from __future__ import annotations

import itertools

import pandas as pd

from spark.common import (execute_job, finish_summary, load_rows, new_summary,
                          round_or_none, write_partitioned_rows)

VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
             "humidity", "wind_speed", "pressure", "solar_radiation"]
COLUMNS = ["dataset_id", "variable_a", "variable_b", "pearson_r", "n_pairs"]


def run_pandas(params: dict) -> dict:
    summary = new_summary("correlations", params, "pandas")
    rows = load_rows(dataset=params.get("dataset"), paths=params.get("paths") or None,
                     region=params.get("region"),
                     year_from=params.get("year_from"), year_to=params.get("year_to"))
    if not rows:
        summary.update(status="unavailable", reason="no input rows")
        return finish_summary(summary)
    df = pd.DataFrame(rows)
    variables = [params["variable"]] if params.get("variable") in VARIABLES else VARIABLES
    pairs = [p for p in itertools.combinations(variables, 2)
             if df[p[0]].notna().any() and df[p[1]].notna().any()]
    out = []
    for ds, grp in df.groupby("dataset_id", dropna=False):
        for a, b in pairs:
            sub = grp[[a, b]].dropna()
            if len(sub) < 3:
                continue
            r = sub[a].corr(sub[b])
            out.append({"dataset_id": ds, "variable_a": a, "variable_b": b,
                        "pearson_r": round_or_none(r), "n_pairs": int(len(sub))})
    manifests = write_partitioned_rows(out, params.get("dataset") or "mixed",
                                       "correlations", COLUMNS, layer="analytics")
    top = sorted(out, key=lambda x: abs(x["pearson_r"] or 0), reverse=True)[:5]
    summary.update(rows_out=len(out), strongest=top,
                   outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run_spark(params: dict) -> dict:
    from spark.common import get_session
    from spark.aggregate_regions import _resolve_files
    from pyspark.sql import functions as F

    summary = new_summary("correlations", params, "pyspark")
    session = get_session()
    files = _resolve_files(params)
    if not files:
        summary.update(status="unavailable", reason="no input JSONL files found")
        return finish_summary(summary)
    df = session.read.json([str(f) for f in files]).select(
        F.col("dataset_id"),
        *[F.col(f"metrics.{v}.value").alias(v) for v in VARIABLES])
    out = []
    for ds, grp in df.groupby("dataset_id"):
        for a, b in itertools.combinations(VARIABLES, 2):
            sub = grp.select(a, b).dropna()
            n = sub.count()
            if n < 3:
                continue
            out.append({"dataset_id": ds, "variable_a": a, "variable_b": b,
                        "pearson_r": round_or_none(sub.stat.corr(a, b)), "n_pairs": int(n)})
    manifests = write_partitioned_rows(out, params.get("dataset") or "mixed",
                                       "correlations", COLUMNS, layer="analytics")
    summary.update(rows_out=len(out), outputs=[x["path"] for x in manifests],
                   storage_engine=manifests[0]["engine"] if manifests else None)
    return finish_summary(summary)


def run(params: dict) -> dict:
    return execute_job("correlations", params, run_spark, run_pandas)
