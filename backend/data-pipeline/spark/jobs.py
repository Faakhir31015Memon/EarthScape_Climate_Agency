"""Real PySpark jobs for the EarthScape data lake.

Every job is ``function(spark, options) -> (summary_dict, output_path)`` and is
invoked either by the processing workflow catalogue
(``backend/apps/processing/workflows.py``) or from a shell for verification.

Conventions:
* inputs are data-lake paths (Parquet/JSON); whole datasets are never
  ``collect()``-ed - aggregations stay in Spark and only bounded previews
  (``take(n)``) come back;
* outputs are written partitioned (``dataset/year/month``) into the lake via
  ``common.data_lake.DataLake`` - which reports honestly whether the backend is
  HDFS or the local data-lake directory;
* ``regional_climate_aggregation`` reproduces the MapReduce reducer's numbers
  and ``compare_with_mapreduce`` proves the two engines agree.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
BACKEND_DIR = HERE.parents[1]
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "data-pipeline"))

from schemas.observation_schema import METRIC_FIELDS, OBSERVATION_FIELDS, spark_schema  # noqa: E402

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.window import Window  # noqa: E402

MAX_PREVIEW_ROWS = 200


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_observation_frame(df):
    """Coerce any readable frame into the canonical observation schema."""
    schema = spark_schema()
    required = {field.name: field for field in schema.fields}
    available = set(df.columns)
    out = df
    # Normalise the date string before casting.
    if "observation_date" in available:
        out = out.withColumn("observation_date",
                             F.substring(F.col("observation_date").cast("string"), 1, 10))
    for name, field in required.items():
        if name not in available:
            out = out.withColumn(name, F.lit(None).cast(field.dataType))
        elif name != "observation_date":
            out = out.withColumn(name, F.col(name).cast(field.dataType))
    ordered = [out[name] for name in required]
    return out.select(*ordered)


def _list_local_data_files(root: Path, fmt: str) -> List[str]:
    suffixes = {".parquet"} if fmt == "parquet" else {".json", ".jsonl"}
    files = sorted(str(p).replace("\\", "/") for p in root.rglob("*")
                   if p.is_file() and p.suffix in suffixes and not p.name.startswith("_")
                   and not p.name.startswith("."))
    return files


def _spark_read_files(spark, files: List[str], fmt: str):
    """Read an explicit file list (Spark cannot listStatus local dirs without
    Hadoop native libs on Windows; explicit files still distribute correctly)."""
    if fmt == "parquet":
        return spark.read.parquet(*files)
    if fmt == "json":
        return spark.read.json(*files)
    if fmt == "csv":
        return spark.read.csv(*files, header=True, inferSchema=True)
    # extension-agnostic: probe each file
    first = files[0].lower()
    if first.endswith(".parquet"):
        return spark.read.parquet(*files)
    if first.endswith((".jsonl", ".json")):
        return spark.read.json(*files)
    return spark.read.csv(*files, header=True, inferSchema=True)


def load_source(spark, options: Dict[str, Any]):
    """Read the job input from a lake path/local dir/file given in options.

    Returns the frame plus a portable provenance reference (`lake://…` or a
    repository-relative key), never an absolute path - the job summaries store
    it and have to stay readable on another machine.
    """
    path = options.get("input_path") or options.get("input")
    if not path:
        raise ValueError("Spark job requires an 'input_path' (data-lake key or file path).")
    from common.data_lake import get_data_lake

    lake = get_data_lake()
    candidates: List[str] = []
    local = Path(path)
    if local.exists():
        candidates = [str(local.resolve())]
    else:
        lake_local = lake.local_path(path)
        if lake_local.exists():
            candidates = [str(lake_local)]
    if not candidates:
        layer = path.split("/")[0]
        listing = lake.list_partition(layer, None, recursive=True)
        hits = [e for e in listing.get("entries", []) if path in e or Path(path).name in e]
        candidates = [str(lake.local_path(h)) for h in hits[:20]]
    if not candidates:
        raise FileNotFoundError(f"Input '{path}' was not found locally or in the data lake")
    target = Path(candidates[0])
    provenance = lake.portable_reference(target)
    if target.is_dir():
        for fmt in ("parquet", "json", "csv"):
            files = _list_local_data_files(target, fmt)
            if files:
                frame = _spark_read_files(spark, files, fmt)
                return ensure_observation_frame(frame).cache(), provenance
        raise FileNotFoundError(f"No parquet/json data files under {target}")
    lowered = str(target).lower()
    if lowered.endswith(".parquet"):
        frame = spark.read.parquet(str(target))
    elif lowered.endswith((".jsonl", ".json")):
        frame = spark.read.json(str(target))
    else:
        frame = spark.read.csv(str(target), header=True, inferSchema=True)
    return ensure_observation_frame(frame).cache(), provenance


MAX_ARROW_FALLBACK_ROWS = 1_000_000
_LAST_WRITE_NOTE = ""


def _arrow_partitioned_write(df, target_root: Path, partition_by: List[str],
                             fmt: str) -> List[str]:
    """Fallback writer for hosts where Spark's Hadoop committer cannot run
    (Windows local mode without native libs): Spark still does the *computation*,
    we stream each partition with toLocalIterator (never collect the whole frame)
    and write one file per partition with pyarrow/pandas."""
    import pandas as pd

    total = df.count()
    if total > MAX_ARROW_FALLBACK_ROWS:
        raise RuntimeError(
            f"Spark native write failed and the Arrow fallback is capped at "
            f"{MAX_ARROW_FALLBACK_ROWS} rows (frame has {total}). Run this job on a "
            f"host with working Hadoop native filesystem support.")
    target_root.mkdir(parents=True, exist_ok=True)
    written: List[str] = []
    if partition_by:
        keys = df.select(*partition_by).distinct().take(5000)
    else:
        keys = [None]
    for combo in keys:
        part = df
        rel = target_root
        if combo is not None:
            row = combo.asDict()
            for column in partition_by:
                value = row[column]
                rel = rel / f"{column}={'null' if value is None else value}"
            part = df
            for column in partition_by:
                value = row[column]
                part = part.filter(F.col(column) == value) if value is not None \
                    else part.filter(F.col(column).isNull())
        rel.mkdir(parents=True, exist_ok=True)
        records = [r.asDict(recursive=True) for r in part.toLocalIterator()]
        if fmt == "parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            table = pa.Table.from_pylist(records)
            # A column that is entirely null infers pa.null(), which Spark's
            # Parquet reader refuses to map; give those columns a concrete type.
            cast_fields = []
            for field in table.schema:
                if pa.types.is_null(field.type):
                    cast_fields.append((field.name, pa.string()))
                else:
                    cast_fields.append((field.name, field.type))
            target_schema = pa.schema([pa.field(name, typ) for name, typ in cast_fields])
            table = table.cast(target_schema)
            out = rel / "part-00000.parquet"
            pq.write_table(table, out)
        else:
            frame_pd = pd.DataFrame.from_records(records)
            for col in frame_pd.columns:
                if col == "ingested_at" and not frame_pd[col].empty:
                    frame_pd[col] = pd.to_datetime(frame_pd[col], utc=True, errors="coerce")
            out = rel / "part-00000.json"
            frame_pd.to_json(out, orient="records", lines=True, date_unit="us")
        written.append(str(out.relative_to(target_root)).replace("\\", "/"))
    return written


def _write_frame_to_lake(df, layer: str, key_prefix: str, *, partition_by: List[str],
                        fmt: str = "parquet") -> Tuple[str, List[str]]:
    """Write a partitioned frame into the lake; returns (logical prefix, files)."""
    global _LAST_WRITE_NOTE
    from common.data_lake import get_data_lake

    lake = get_data_lake()
    scratch = Path(tempfile.mkdtemp(prefix="earthscape-spark-out-")) / key_prefix.strip("/")
    writer_note = "spark-native-write"
    try:
        writer = df.write.mode("overwrite")
        if fmt == "parquet":
            if partition_by:
                writer = writer.partitionBy(*partition_by)
            writer.parquet(str(scratch))
        else:
            writer.json(str(scratch))
        files = sorted(str(p.relative_to(scratch)).replace("\\", "/")
                       for p in scratch.rglob("*") if p.is_file())
    except Exception as exc:  # noqa: BLE001 - Windows local mode without Hadoop native libs
        import shutil

        shutil.rmtree(scratch, ignore_errors=True)
        writer_note = f"spark-native-write failed ({str(exc).splitlines()[0][:120]}); " \
                      f"arrow fallback writer used (per-partition streaming, not collect)"
        scratch = Path(tempfile.mkdtemp(prefix="earthscape-spark-out-")) / key_prefix.strip("/")
        files = _arrow_partitioned_write(df, scratch, partition_by, fmt)
    written: List[str] = []
    if lake.mode == "local":
        import shutil

        target_root = lake.local_path(f"{layer}/{key_prefix.strip('/')}/")
        target_root.parent.mkdir(parents=True, exist_ok=True)
        if target_root.exists():
            shutil.rmtree(target_root)
        shutil.copytree(scratch, target_root)
        written = [f"{layer}/{key_prefix.strip('/')}/{f}" for f in files]
    else:
        for rel in files:
            result = lake.upload(str(scratch / rel), f"{key_prefix.strip('/')}/{rel}",
                                 layer=layer, overwrite=True)
            if result.ok:
                written.append(result.path)
    import shutil as _shutil

    _shutil.rmtree(scratch.parent, ignore_errors=True)
    _LAST_WRITE_NOTE = writer_note
    return f"{layer}/{key_prefix.strip('/')}", written


def _lake_uri(key_or_local: str) -> str:
    from common.data_lake import get_data_lake

    try:
        lake = get_data_lake()
        if "://" in key_or_local:
            return key_or_local
        return lake.storage_reference(key_or_local)
    except Exception:  # noqa: BLE001
        return key_or_local


def _preview(df, limit: int = 25) -> List[Dict[str, Any]]:
    return [row.asDict(recursive=True) for row in df.take(min(limit, MAX_PREVIEW_ROWS))]


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, digits)


def normalise_and_partition(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    rows_in = frame.count()
    deduped = frame.dropDuplicates(["dedupe_key"])
    rows_out = deduped.count()
    duplicates = rows_in - rows_out
    with_date = deduped.filter(F.col("observation_date").isNotNull()
                               & F.col("region_code").isNotNull())
    rejected = rows_out - with_date.count()
    stamped = with_date.withColumn("unit_audit", F.coalesce(F.col("unit_audit"),
                                                            F.lit("converted to canonical units")))
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    dataset_token = str(options.get("dataset_id") or "_all")
    prefix, files = _write_frame_to_lake(
        stamped, "validated", f"dataset={dataset_token}/run-{run}",
        partition_by=["dataset_id", "year", "month"])
    summary = {
        "job": "normalise_and_partition",
        "engine": "pyspark",
        "spark_version": spark.sparkContext.version,
        "input": resolved_input,
        "rows_in": rows_in,
        "rows_out": rows_out,
        "duplicates_dropped": duplicates,
        "rejected_missing_date_or_region": rejected,
        "files_written": len(files),
        "output_prefix": prefix,
        "writer": _LAST_WRITE_NOTE,
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


def missing_value_profiling(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    total = frame.count()
    columns = frame.columns
    exprs = []
    for column in columns:
        if column == "quality_flags":
            exprs.append(F.sum(F.when(F.size(F.col(column)) > 0, 1).otherwise(0)).alias("__flagged"))
        else:
            exprs.append(F.sum(F.when(F.col(column).isNull(), 1).otherwise(0)).alias(column))
    row = frame.agg(*exprs).collect()[0]
    profiles = {}
    for column in columns:
        if column == "quality_flags":
            continue
        nulls = int(row[column] or 0)
        profiles[column] = {"null_count": nulls,
                            "null_pct": _round(nulls / total * 100, 2) if total else None}
    flagged = int(row.__getitem__("__flagged") or 0)
    profile = {
        "profiled_at": utcnow().isoformat(),
        "row_count": total,
        "columns": profiles,
        "flagged_rows": flagged,
        "flagged_pct": _round(flagged / total * 100, 2) if total else None,
        "input": resolved_input,
    }
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    from common.data_lake import get_data_lake

    key = f"missing-value-profile-{run}.json"
    result = get_data_lake().write_text(json.dumps(profile, indent=2, default=str), key,
                                        layer="analytics")
    profile["output_ok"] = result.ok
    profile["processing_mode"] = f"pyspark:{spark.sparkContext.master}"
    profile["job"] = "missing_value_profiling"
    profile["engine"] = "pyspark"
    return profile, (result.path if result.ok else "analytics/" + key)


def aggregate_monthly_regional(frame):
    """The exact reducer semantics: mean over usable temps, sum over usable precip."""
    usable = frame.filter(F.col("temperature_c").isNotNull() | F.col("precipitation_mm").isNotNull())
    grouped = usable.groupBy("region_code", "year", "month").agg(
        F.count("*").alias("observation_count"),
        F.avg("temperature_c").alias("mean_temperature_c"),
        F.count("temperature_c").alias("temperature_value_count"),
        F.min("temperature_c").alias("temperature_min_c"),
        F.max("temperature_c").alias("temperature_max_c"),
        F.sum("precipitation_mm").alias("precipitation_sum_mm"),
        F.count("precipitation_mm").alias("precipitation_value_count"),
    )
    return grouped.withColumn("mean_temperature_c", F.round("mean_temperature_c", 4)) \
        .withColumn("precipitation_sum_mm", F.round("precipitation_sum_mm", 4)) \
        .withColumn("temperature_min_c", F.round("temperature_min_c", 4)) \
        .withColumn("temperature_max_c", F.round("temperature_max_c", 4)) \
        .orderBy("region_code", "year", "month")


def compare_with_mapreduce(agg_rows: List[Dict[str, Any]], mr_tsv_text: str,
                           tolerance: float = 1e-3) -> Dict[str, Any]:
    mr: Dict[str, Dict[str, Any]] = {}
    for line in mr_tsv_text.splitlines():
        if "\t" not in line:
            continue
        key, payload = line.split("\t", 1)
        try:
            mr[key] = json.loads(payload)
        except ValueError:
            continue
    compared = mismatches = 0
    details: List[str] = []
    for row in agg_rows:
        key = f"{row['region_code']}|{row['year']}|{row['month']}"
        expected = mr.get(key)
        if expected is None:
            continue
        compared += 1
        for field in ("mean_temperature_c", "precipitation_sum_mm",
                      "observation_count", "temperature_min_c", "temperature_max_c"):
            spark_value, mr_value = row.get(field), expected.get(field)
            if spark_value is None and mr_value is None:
                continue
            if spark_value is None or mr_value is None or abs(float(spark_value) - float(mr_value)) > tolerance:
                mismatches += 1
                details.append(f"{key}.{field}: spark={spark_value} mapreduce={mr_value}")
    return {"groups_compared": compared, "mismatches": mismatches,
            "agrees": compared > 0 and mismatches == 0,
            "mapreduce_groups": len(mr), "details": details[:10]}


def regional_climate_aggregation(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    grouped = aggregate_monthly_regional(frame).cache()
    rows = grouped.toLocalIterator() if grouped.rdd.getNumPartitions() <= 8 else grouped.take(5000)
    collected: List[Dict[str, Any]] = []
    for row in rows:
        record = row.asDict() if hasattr(row, "asDict") else dict(row)
        record["region_code"] = str(record["region_code"])
        collected.append(record)
    annual = frame.filter(F.col("temperature_c").isNotNull()).groupBy("region_code", "year").agg(
        F.avg("temperature_c").alias("annual_mean_temperature_c"),
        F.sum("precipitation_mm").alias("annual_precipitation_sum_mm"),
        F.count("*").alias("annual_observation_count"),
    ).orderBy("region_code", "year")
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    prefix, files = _write_frame_to_lake(grouped, "analytics", f"regional-monthly-{run}",
                                         partition_by=["year", "month"])
    _write_frame_to_lake(annual, "analytics", f"regional-annual-{run}", partition_by=["year"])
    agreement = None
    mr_path = options.get("mapreduce_output_path")
    if mr_path:
        from common.data_lake import get_data_lake

        text = get_data_lake().read_text(mr_path)
        if text.get("ok"):
            agreement = compare_with_mapreduce(collected, text["content"])
        else:
            agreement = {"available": False, "mapreduce_output": mr_path,
                         "reason": "The MapReduce result at %s could not be read (%s), so the two "
                                   "engines were not compared."
                                   % (mr_path, text.get("error") or "unknown lake error")}
    else:
        # A null agreement with no explanation reads like a silent failure. Say which
        # invocation would actually produce the comparison.
        agreement = {"available": False,
                     "reason": "No MapReduce output was supplied, so nothing was compared. Run "
                               "run_spark_jobs.py with --with-mapreduce-check and include "
                               "normalise_and_partition before regional_climate_aggregation in "
                               "--jobs; the cross-check needs the validated partition this job "
                               "consumes, not the raw input."}
    summary = {
        "job": "regional_climate_aggregation",
        "engine": "pyspark",
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "input": resolved_input,
        "monthly_groups": len(collected),
        "files_written": len(files),
        "preview": collected[:10],
        "agrees_with_mapreduce": agreement,
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


def trend_analysis(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    annual = frame.filter(F.col("temperature_c").isNotNull()).groupBy("region_code", "year").agg(
        F.avg("temperature_c").alias("annual_mean_temperature_c"),
        F.sum("precipitation_mm").alias("annual_precipitation_sum_mm"),
        F.count("*").alias("observation_count"),
    )
    window = Window.partitionBy("region_code").orderBy("year")
    trended = annual.withColumn("previous_year_mean_c",
                                 F.lag("annual_mean_temperature_c", 1).over(window)) \
        .withColumn("yoy_delta_c", F.round("annual_mean_temperature_c"
                                           - F.col("previous_year_mean_c"), 4)) \
        .withColumn("annual_mean_temperature_c", F.round("annual_mean_temperature_c", 4)) \
        .orderBy("region_code", "year")
    rows = [row.asDict() for row in trended.take(2000)]
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    prefix, files = _write_frame_to_lake(trended, "analytics", f"trend-annual-{run}",
                                         partition_by=["year"])
    regions = sorted({str(row["region_code"]) for row in rows})
    first_last = {}
    for region in regions[:50]:
        series = [row for row in rows if row["region_code"] == region]
        if len(series) >= 2:
            first_last[region] = {
                "first_year": series[0]["year"], "first_mean_c": series[0]["annual_mean_temperature_c"],
                "last_year": series[-1]["year"], "last_mean_c": series[-1]["annual_mean_temperature_c"],
                "total_change_c": _round(float(series[-1]["annual_mean_temperature_c"])
                                         - float(series[0]["annual_mean_temperature_c"])),
            }
    summary = {
        "job": "trend_analysis",
        "engine": "pyspark",
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "input": resolved_input,
        "region_count": len(regions),
        "annual_rows": len(rows),
        "files_written": len(files),
        "preview": rows[:10],
        "regional_change_first_vs_last": first_last,
        "caveat": "Year-over-year deltas on incomplete coverage can reflect station mix, not climate.",
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


def anomaly_feature_generation(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    metric = options.get("metric", "temperature_c")
    if metric not in METRIC_FIELDS:
        raise ValueError(f"Unsupported metric '{metric}'")
    usable = frame.filter(F.col(metric).isNotNull() & F.col("region_code").isNotNull())
    baseline = usable.groupBy("region_code", "month").agg(
        F.avg(metric).alias("baseline_mean"),
        F.stddev(metric).alias("baseline_std"),
        F.count("*").alias("baseline_n"),
    ).withColumn("baseline_mean", F.round("baseline_mean", 4)) \
        .withColumn("baseline_std", F.round("baseline_std", 4))
    joined = usable.join(baseline, ["region_code", "month"], "left")
    scored = joined.withColumn(
        "z_score", F.round((F.col(metric) - F.col("baseline_mean"))
                           / F.when(F.col("baseline_std") > 0, F.col("baseline_std")).otherwise(None), 4))
    time_window = Window.partitionBy("region_code", "month").orderBy("observation_date") \
        .rowsBetween(-2, 2)
    featured = scored \
        .withColumn("rolling_mean_5", F.round(F.avg(metric).over(time_window), 4)) \
        .withColumn("deviation_from_baseline", F.round(F.col(metric) - F.col("baseline_mean"), 4)) \
        .withColumn("anomaly_flag_2_5_sigma", F.abs(F.col("z_score")) >= 2.5) \
        .select("dedupe_key", "dataset_id", "region_code", "location_code", "observation_date",
                "year", "month", metric, "baseline_mean", "baseline_std", "baseline_n",
                "deviation_from_baseline", "z_score", "rolling_mean_5", "anomaly_flag_2_5_sigma",
                "schema_version", "ingested_at")
    stats = featured.agg(
        F.count("*").alias("feature_rows"),
        F.sum(F.when(F.col("anomaly_flag_2_5_sigma"), 1).otherwise(0)).alias("flagged"),
        F.max(F.abs("z_score")).alias("max_abs_z"),
    ).collect()[0]
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    prefix, files = _write_frame_to_lake(featured, "ml-features", f"anomaly-{metric}-{run}",
                                         partition_by=["dataset_id", "year", "month"])
    summary = {
        "job": "anomaly_feature_generation",
        "engine": "pyspark",
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "input": resolved_input,
        "metric": metric,
        "feature_rows": int(stats["feature_rows"] or 0),
        "flagged_2_5_sigma": int(stats["flagged"] or 0),
        "max_abs_z": _round(stats["max_abs_z"]),
        "baseline_groups": baseline.count(),
        "files_written": len(files),
        "preview": _preview(featured, 5),
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


def correlation_analysis(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    metrics = options.get("metrics") or ["temperature_c", "precipitation_mm",
                                         "relative_humidity_pct", "surface_pressure_hpa"]
    metrics = [m for m in metrics if m in METRIC_FIELDS]
    group_by = options.get("group_by", "region_code")
    cells: List[Dict[str, Any]] = []
    method = "spark-statcorr"
    try:
        for i, left in enumerate(metrics):
            for right in metrics[i + 1:]:
                pair = frame.filter(F.col(left).isNotNull() & F.col(right).isNotNull())
                n = pair.count()
                if n < 8:
                    cells.append({"x": left, "y": right, "coefficient": None,
                                  "sample_size": n, "note": "insufficient data"})
                    continue
                coefficient = pair.stat.corr(left, right)
                cells.append({"x": left, "y": right, "coefficient": _round(coefficient, 4),
                              "sample_size": int(n)})
    except Exception:  # noqa: BLE001 - pandas fallback on grouped splits (documented)
        method = "pandas-pearson-on-grouped-splits"
        grouped = frame.groupBy(group_by, "year", "month").agg(
            *[F.avg(m).alias(m) for m in metrics]).toPandas()
        for m1 in metrics:
            for m2 in metrics:
                if m1 >= m2:
                    continue
                sample = grouped[[m1, m2]].dropna()
                if len(sample) < 3:
                    continue
                cells.append({"x": m1, "y": m2,
                              "coefficient": _round(float(sample[m1].corr(sample[m2])), 4),
                              "sample_size": int(len(sample))})
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    from common.data_lake import get_data_lake

    payload = {"job": "correlation_analysis", "engine": "pyspark",
               "method": method, "cells": cells, "group_by": group_by,
               "input": resolved_input, "processing_mode": f"pyspark:{spark.sparkContext.master}",
               "caveat": "Correlation is co-variation only, not climate causation.",
               "computed_at": utcnow().isoformat()}
    key = f"correlation-{run}.json"
    result = get_data_lake().write_text(json.dumps(payload, indent=2, default=str), key,
                                        layer="analytics")
    payload["output_ok"] = result.ok
    return payload, (result.path if result.ok else f"analytics/{key}")


def ml_feature_prep(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    metric = options.get("metric", "temperature_c")
    lags = int(options.get("lags", 3))
    monthly = frame.filter(F.col(metric).isNotNull()).groupBy(
        "region_code", "year", "month").agg(
        F.avg(metric).alias("monthly_mean"),
        F.count("*").alias("monthly_obs"),
    )
    period = monthly.withColumn("period_index", F.col("year") * 12 + F.col("month"))
    window = Window.partitionBy("region_code").orderBy("period_index")
    featured = period
    for lag in range(1, lags + 1):
        featured = featured.withColumn(f"lag_{lag}", F.lag("monthly_mean", lag).over(window))
    featured = featured.withColumn("rolling_mean_3",
                                   F.round(F.avg("monthly_mean").over(window.rowsBetween(-2, 0)), 4)) \
        .withColumn("rolling_std_3",
                    F.round(F.stddev("monthly_mean").over(window.rowsBetween(-2, 0)), 4)) \
        .withColumn("monthly_mean", F.round("monthly_mean", 4)) \
        .withColumn("target_next_month", F.lead("monthly_mean", 1).over(window)) \
        .orderBy("region_code", "year", "month")
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    prefix, files = _write_frame_to_lake(featured, "ml-features", f"supervised-{metric}-{run}",
                                         partition_by=["year", "month"])
    rows = featured.count()
    summary = {
        "job": "ml_feature_prep",
        "engine": "pyspark",
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "input": resolved_input,
        "metric": metric,
        "lag_order": lags,
        "feature_rows": int(rows),
        "files_written": len(files),
        "feature_columns": [c for c in featured.columns],
        "preview": _preview(featured, 5),
        "note": "Rows keep their region/month columns so the ML agent can group or filter.",
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


def export_processed_parquet(spark, options: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    frame, resolved_input = load_source(spark, options)
    run = utcnow().strftime("%Y%m%dT%H%M%SZ")
    prefix, files = _write_frame_to_lake(frame, "exports", f"observations-export-{run}",
                                         partition_by=["dataset_id", "year", "month"])
    lake_uris = [_lake_uri(f) for f in files]
    stored_keys: List[str] = []
    persist = options.get("persist_summaries", True)
    if persist:
        try:
            from common.analytics import store_summary
            from common.database import get_database

            agg_frame = aggregate_monthly_regional(frame)
            for row in agg_frame.take(500):
                record = row.asDict()
                stored_keys.append(store_summary(
                    metric="regional_monthly_climate",
                    scope={"region_code": str(record["region_code"]), "year": record["year"],
                           "month": record["month"]},
                    payload={"kind": "monthly_regional_aggregate", **record},
                    source_datasets=[str(options.get("dataset_id") or "all")],
                    workflow="spark.export_processed_parquet",
                    processing_mode="pyspark"))
            get_database().flush_if_dirty()
        except Exception as exc:  # noqa: BLE001 - export success must not hinge on Mongo
            stored_keys = []
            files_note = f"summary persistence failed: {str(exc)[:150]}"
        else:
            files_note = ""
    else:
        files_note = ""
    summary = {
        "job": "export_processed_parquet",
        "engine": "pyspark",
        "processing_mode": f"pyspark:{spark.sparkContext.master}",
        "input": resolved_input,
        "rows_exported": frame.count(),
        "files_written": len(files),
        "data_lake_uris": lake_uris[:50],
        "summary_keys_stored": len(stored_keys),
        "note": files_note or None,
        "completed_at": utcnow().isoformat(),
    }
    return summary, prefix


JOBS: Dict[str, Any] = {
    "normalise_and_partition": normalise_and_partition,
    "missing_value_profiling": missing_value_profiling,
    "regional_climate_aggregation": regional_climate_aggregation,
    "trend_analysis": trend_analysis,
    "anomaly_feature_generation": anomaly_feature_generation,
    "correlation_analysis": correlation_analysis,
    "ml_feature_prep": ml_feature_prep,
    "export_processed_parquet": export_processed_parquet,
}
