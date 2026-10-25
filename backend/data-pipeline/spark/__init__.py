"""EarthScape processing jobs: PySpark engines with pandas fallbacks.

``jobs.json`` is the machine-readable catalogue consumed by the backend
"Big Data processing workspace"."""

from spark.common import SparkUnavailable, get_session, spark_importable  # noqa: F401

JOB_MODULES = {
    "normalize_observations": "spark.normalize_observations",
    "profile_missing": "spark.profile_missing",
    "aggregate_regions": "spark.aggregate_regions",
    "trends": "spark.trends",
    "historical_compare": "spark.historical_compare",
    "anomaly_features": "spark.anomaly_features",
    "correlations": "spark.correlations",
    "ml_features": "spark.ml_features",
}


def get_job(job_id: str):
    import importlib

    module_path = JOB_MODULES.get(job_id)
    if module_path is None:
        raise KeyError(f"unknown job {job_id!r}; catalogue ids: {sorted(JOB_MODULES)}")
    return importlib.import_module(module_path)


def run_job(job_id: str, params: dict = None) -> dict:
    return get_job(job_id).run(params or {})
