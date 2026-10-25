"""mlkit: the single facade the Django backend imports.

All public entry points of the ML package are re-exported here. The backend
persists metadata; this package never touches MongoDB and runs fully offline
from files on disk (data lake + sample-data + artifacts/models).
"""

from __future__ import annotations

from typing import Any, Optional

import pandas as pd

from . import mlconfig
from .datasets.loader import discover_files, load_observations, loading_report, TIDY_COLUMNS, VARIABLES
from .datasets.frame import DataAvailability, SeriesAssembler, summarize_availability
from .features.time_features import build_feature_matrix, leakage_check, DEFAULT_FEATURES
from .features.pipeline import ClimateFeaturePipeline, validate_feature_spec
from .features.correlation import region_correlations, variable_correlations
from .evaluation.metrics import evaluate_forecast, mae, mape, rmse, r2, skill_vs_baseline
from .evaluation.backtest import walk_forward
from .training.forecast import train_forecaster, xgboost_status
from .training.registry_entry import RegistryEntry
from .training.store import load_model, save_model, list_models, registry_documents, attach_registry_entry
from .inference.predict import forecast, PROJECTION_NOTE
from .anomaly.threshold import check_thresholds, DEFAULT_LIMITS
from .anomaly.statistical import statistical_anomalies
from .anomaly.isolation_forest import isolation_forest_anomalies, isolation_forest_status
from .anomaly.severity import severity_for, MAPPING_DOC
from .anomaly.explain import explain_detection

__all__ = [
    "data_sources", "load_observations", "availability_report",
    "assemble_series", "train_model", "evaluate_model", "forecast",
    "detect_anomalies", "registry_entries", "model_capabilities",
    "variable_correlations", "region_correlations", "xgboost_status",
    "isolation_forest_status", "load_model", "TIDY_COLUMNS", "VARIABLES",
    "PROJECTION_NOTE", "MAPPING_DOC", "DEFAULT_LIMITS",
]


def data_sources(root: Optional[Any] = None) -> dict[str, Any]:
    """What climate data actually exists on disk right now (files + row counts)."""
    found = discover_files()
    tidy = load_observations()
    return {"discovered": {k: [str(p) for p in v] for k, v in found.items()},
            "loading": loading_report(tidy),
            "storage_root": str(mlconfig.storage_root()),
            "model_root": str(mlconfig.model_artifact_root())}


def availability_report(min_rows: Optional[int] = None) -> list[dict[str, Any]]:
    """Honest per-(region, variable, granularity) modelling verdicts."""
    tidy = load_observations()
    return summarize_availability(tidy, min_rows=min_rows)


def assemble_series(region: str, variable: str, granularity: str = "daily",
                    tidy: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    assembled = SeriesAssembler(tidy if tidy is not None else load_observations()).assemble(
        region, variable, granularity)
    return {"availability": assembled.availability.as_dict(),
            "rows_present": int(assembled.series.notna().sum())}


def train_model(target_variable: str, region: str, granularity: str = "daily",
                horizon_steps: int = 30, folds: int = 5, save: bool = True,
                tidy: Optional[pd.DataFrame] = None) -> dict[str, Any]:
    """Train the full model ladder on one (region, variable) slice."""
    return train_forecaster(tidy if tidy is not None else load_observations(),
                            target_variable=target_variable, region=region,
                            granularity=granularity, horizon_steps=horizon_steps,
                            folds=folds, save=save)


def evaluate_model(model_ref: str) -> dict[str, Any]:
    """Stored evaluation + backtest numbers for one registered model (no retraining)."""
    loaded = load_model(model_ref)
    entry = loaded.metadata.get("registry_entry", loaded.metadata)
    return {"model_ref": f"{loaded.name}@{loaded.version}",
            "algorithm": loaded.algorithm,
            "horizon_validated_for": loaded.validated_horizon,
            "evaluation_metrics": entry.get("evaluation_metrics", {}),
            "backtest": entry.get("backtest", loaded.metadata.get("backtest", {})),
            "comparison": loaded.metadata.get("comparison", []),
            "training_period": entry.get("training_period", {}),
            "test_period": entry.get("test_period", {}),
            "data_source_files": entry.get("data_source_files", []),
            "limitations": entry.get("limitations", [])}


def detect_anomalies(region: str, variable: str, method: str = "statistical",
                     origin: Optional[str] = None, granularity: str = "daily",
                     tidy: Optional[pd.DataFrame] = None, **kwargs: Any) -> dict[str, Any]:
    frame = tidy if tidy is not None else load_observations()
    assembled = SeriesAssembler(frame).assemble(region, variable, granularity)
    unit = assembled.availability.unit or "1"
    if method == "statistical":
        return statistical_anomalies(assembled.series, variable, region, unit=unit,
                                     origin=origin, **kwargs)
    if method == "threshold":
        result = check_thresholds(assembled.series, variable)
        result["region"] = region
        return result
    if method == "isolation_forest":
        return isolation_forest_anomalies(assembled.series, variable, region, unit=unit,
                                          origin=origin, statics={"latitude": assembled.latitude,
                                                                  "elevation_m": assembled.elevation_m},
                                          **kwargs)
    return {"status": "error", "reason": f"unknown anomaly method '{method}'"}


def registry_entries() -> list[dict[str, Any]]:
    """model_registry-ready documents for every saved model (backend imports these)."""
    return registry_documents()


def model_capabilities() -> dict[str, Any]:
    """Environment capability report: which model tiers can actually run today."""
    return {"xgboost": xgboost_status(), "isolation_forest": isolation_forest_status(),
            "parquet_engine": _parquet_status(),
            "min_training_rows": mlconfig.min_training_rows(),
            "model_artifact_root": str(mlconfig.model_artifact_root())}


def _parquet_status() -> dict[str, Any]:
    try:
        import pyarrow  # noqa: F401
        return {"available": True, "version": getattr(pyarrow, "__version__", "unknown")}
    except Exception as exc:
        return {"available": False, "reason": f"pyarrow missing ({exc}); processed/*.parquet skipped"}
