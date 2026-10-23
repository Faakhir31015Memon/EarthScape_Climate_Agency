"""Registry of processing workflows the platform can run.

Every entry maps to real code already present in ``data-pipeline/`` (MapReduce
streaming orchestrator, PySpark job catalog, Structured Streaming processor)
and ``ml/features/build.py``. Nothing here invents a function name or executes
an arbitrary path: the API only accepts a workflow ``id`` from this registry and
a bounded set of validated parameters.

Availability of each workflow is *probed* rather than asserted (see
:mod:`apps.processing.engines`). When a prerequisite is missing the workflow
entry carries an ``unavailable_reason`` that names the specific env key or
package, so a client sees the same truth the operator does.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common import analytics  # noqa: E402


# ------------------------------------------------------------------- parameter spec
# Every spec entry follows the ``ProcessingWorkflow.parameter_spec`` contract in
# frontend/src/types/api.ts: {type, required, default?, min?, max?, choices?,
# label?, help?}. Views validate free-form client parameters against this spec
# before the task is dispatched.
_METRIC_CHOICES = tuple(analytics.METRICS)


def _dataset_id_param() -> Dict[str, Any]:
    return {
        "type": "str",
        "required": False,
        "label": "Dataset identifier",
        "help": "Optional MongoDB dataset _id (24-hex). Empty runs across every dataset.",
    }


def _limit_param(default: int = 200000, maximum: int = 500000) -> Dict[str, Any]:
    return {
        "type": "int",
        "required": False,
        "default": default,
        "min": 1,
        "max": maximum,
        "label": "Row limit",
        "help": "Cap on observation rows read from MongoDB or the lake.",
    }


# ---------------------------------------------------------------------- registry
# engine values follow ProcessingWorkflow['engine'] in api.ts:
#   'mapreduce' | 'pyspark' | 'streaming' | 'mongodb'
WORKFLOWS: Dict[str, Dict[str, Any]] = {
    "mapreduce.observation_counts": {
        "label": "MapReduce: observation counts per dataset/month",
        "description": (
            "Streams climate_observations into a JSON-lines scratch file, runs "
            "the mapper | sort | reducer pipeline from data-pipeline/mapreduce "
            "and upserts verified counts into analytics_summaries. Uses the "
            "real Hadoop Streaming jar when hadoop + HDFS are reachable; "
            "otherwise it runs the *identical* mapper/reducer code as local "
            "subprocesses and labels the run local-streaming-simulated."
        ),
        "engine": "mapreduce",
        "requires": ["python (subprocess)", "climate_observations collection",
                     "data-pipeline/mapreduce scripts"],
        "script": "data-pipeline/mapreduce/run_streaming_job.py",
        "script_args": ["--job", "observation_counts"],
        "task_name": "processing.mapreduce.observation_counts",
        "parameter_spec": {
            "dataset_id": _dataset_id_param(),
            "limit": _limit_param(default=200000, maximum=1_000_000),
            "force_local": {
                "type": "bool",
                "required": False,
                "default": False,
                "label": "Skip Hadoop probe",
                "help": "Run the local streaming simulation without checking hadoop first.",
            },
        },
    },
    "mapreduce.temperature_aggregation": {
        "label": "MapReduce: regional temperature aggregation",
        "description": (
            "Runs the temperature-aggregation mapper/reducer over the exported "
            "observations to produce per (region, year, month) mean, min, max "
            "and precipitation totals, then persists those aggregates as "
            "analytics summaries. Engine is reported honestly: hadoop-streaming "
            "only when the cluster probe returned zero; otherwise the run is "
            "labelled local-streaming-simulated."
        ),
        "engine": "mapreduce",
        "requires": ["python (subprocess)", "climate_observations collection",
                     "data-pipeline/mapreduce scripts"],
        "script": "data-pipeline/mapreduce/run_streaming_job.py",
        "script_args": ["--job", "temperature_aggregation"],
        "task_name": "processing.mapreduce.temperature_aggregation",
        "parameter_spec": {
            "dataset_id": _dataset_id_param(),
            "limit": _limit_param(default=200000, maximum=1_000_000),
            "force_local": {
                "type": "bool", "required": False, "default": False,
                "label": "Skip Hadoop probe",
            },
        },
    },
    "spark.export_observations_to_lake": {
        "label": "Spark: export observations to the data lake",
        "description": (
            "PySpark job ``export_processed_parquet``: reads a partitioned "
            "observation frame from the lake, writes partitioned Parquet into "
            "the exports layer and upserts regional monthly aggregates into "
            "analytics_summaries. Reports the real backend (hdfs or "
            "local-data-lake) and the exact files written."
        ),
        "engine": "pyspark",
        "requires": ["pyspark installed", "SPARK_ENABLED", "input file or lake prefix",
                     "data-pipeline/spark/jobs.py::export_processed_parquet"],
        "spark_job": "export_processed_parquet",
        "task_name": "processing.spark.export_observations_to_lake",
        "parameter_spec": {
            "input_path": {
                "type": "str", "required": False,
                "label": "Input file or lake prefix",
                "help": "Absolute path to a real observation export, or a data-lake key. "
                        "There is no sample fallback - the job fails when this is empty.",
            },
            "dataset_id": _dataset_id_param(),
            "persist_summaries": {
                "type": "bool", "required": False, "default": True,
                "label": "Persist analytics summaries",
            },
        },
    },
    "spark.daily_aggregation": {
        "label": "Spark: daily normalisation + partitioning",
        "description": (
            "PySpark job ``normalise_and_partition``: deduplicates on the "
            "observation ``dedupe_key``, rejects rows missing date/region, and "
            "writes partitioned validated data into the lake. This is the "
            "daily-grain normalisation step every other Spark job consumes."
        ),
        "engine": "pyspark",
        "requires": ["pyspark installed", "SPARK_ENABLED",
                     "data-pipeline/spark/jobs.py::normalise_and_partition"],
        "spark_job": "normalise_and_partition",
        "task_name": "processing.spark.daily_aggregation",
        "parameter_spec": {
            "input_path": {
                "type": "str", "required": False,
                "label": "Input file or lake prefix",
                "help": "Absolute path to a real observation export, or a data-lake key.",
            },
            "dataset_id": _dataset_id_param(),
        },
    },
    "spark.seasonality_rollup": {
        "label": "Spark: seasonality / analytics rollup",
        "description": (
            "PySpark job ``regional_climate_aggregation``: reproduces the "
            "MapReduce reducer semantics across (region, year, month) and "
            "annual buckets, writes the analytics partitions, and (when a "
            "MapReduce output path is provided) verifies that both engines "
            "agree to within tolerance. Honest: agrees_with_mapreduce is null "
            "when no cross-check has been run."
        ),
        "engine": "pyspark",
        "requires": ["pyspark installed", "SPARK_ENABLED",
                     "data-pipeline/spark/jobs.py::regional_climate_aggregation"],
        "spark_job": "regional_climate_aggregation",
        "task_name": "processing.spark.seasonality_rollup",
        "parameter_spec": {
            "input_path": {
                "type": "str", "required": False,
                "label": "Input file or lake prefix",
                "help": "Absolute path to a real observation export, or a data-lake key.",
            },
            "dataset_id": _dataset_id_param(),
            "mapreduce_output_path": {
                "type": "str", "required": False,
                "label": "MapReduce TSV key",
                "help": "Optional analytics-layer key of the MapReduce output to cross-check against.",
            },
        },
    },
    "spark.ml_feature_build": {
        "label": "Spark: ML feature build",
        "description": (
            "PySpark job ``ml_feature_prep``: builds supervised lag / rolling "
            "features on monthly means and writes the ml-features partition "
            "back into the lake. The pure-python feature builder in "
            "ml/features/build.py is used by the model training pipeline and "
            "is exercised separately."
        ),
        "engine": "pyspark",
        "requires": ["pyspark installed", "SPARK_ENABLED",
                     "data-pipeline/spark/jobs.py::ml_feature_prep"],
        "spark_job": "ml_feature_prep",
        "task_name": "processing.spark.ml_feature_build",
        "parameter_spec": {
            "input_path": {
                "type": "str", "required": False,
                "label": "Input file or lake prefix",
                "help": "Absolute path to a real observation export, or a data-lake key.",
            },
            "dataset_id": _dataset_id_param(),
            "metric": {
                "type": "choice", "required": False, "default": "temperature_c",
                "choices": _METRIC_CHOICES,
                "label": "Target metric",
            },
            "lags": {
                "type": "int", "required": False, "default": 3, "min": 1, "max": 12,
                "label": "Lag order",
            },
        },
    },
    "mongodb.analytics_summary_rebuild": {
        "label": "Rebuild analytics summaries from MongoDB",
        "description": (
            "Engine-free rollup: aggregates climate_observations with the "
            "platform's own pandas helpers (``common.analytics``) and upserts "
            "regional-monthly, trend and coverage summaries back into "
            "``analytics_summaries``. Always available because it uses only "
            "PyMongo + the analytics module, no external engine."
        ),
        "engine": "mongodb",
        "requires": ["climate_observations collection", "common.analytics"],
        "task_name": "processing.mongodb.analytics_summary_rebuild",
        "parameter_spec": {
            "dataset_id": _dataset_id_param(),
            "metric": {
                "type": "choice", "required": False, "default": "temperature_c",
                "choices": _METRIC_CHOICES,
                "label": "Metric to rebuild",
            },
            "limit": _limit_param(default=50000, maximum=200000),
        },
    },
    "streaming.observation_processor": {
        "label": "Spark Structured Streaming processor",
        "description": (
            "Runs ``data-pipeline/streaming/stream_processor.py --once`` in a "
            "child process: consumes any events queued in the spool directory, "
            "validates them against the canonical schema, dedupes by event_id, "
            "routes failures to dead-letter files and updates the stream "
            "status row in processing_jobs. Honest about state - if Spark is "
            "unavailable the child reports the real failure."
        ),
        "engine": "streaming",
        "requires": ["pyspark installed", "SPARK_ENABLED", "spool directory under PROCESSING_SCRATCH_DIR"],
        "script": "data-pipeline/streaming/stream_processor.py",
        "script_args": ["--once"],
        "task_name": "processing.streaming.observation_processor",
        "parameter_spec": {
            "duration_seconds": {
                "type": "int", "required": False, "default": 0, "min": 0, "max": 300,
                "label": "Continuous-mode duration",
                "help": "0 triggers availableNow (process the spool once and exit).",
            },
        },
    },
}


# ----------------------------------------------------------------------- accessors
def workflow_ids() -> List[str]:
    return sorted(WORKFLOWS)


def get_workflow(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Registry lookup by id only. Never imports a client-supplied module name."""
    return WORKFLOWS.get(workflow_id)


def require_workflow(workflow_id: str) -> Dict[str, Any]:
    entry = get_workflow(workflow_id)
    if entry is None:
        from common.api import ApiError

        raise ApiError("validation_failed",
                       f"Unknown processing workflow '{workflow_id}'.",
                       details={"workflows": workflow_ids()})
    return entry


def available_metric_choices() -> List[str]:
    return list(analytics.METRICS)
