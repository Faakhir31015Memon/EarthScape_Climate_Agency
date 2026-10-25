"""The reproducible 14-step training pipeline.

Steps (numbered as in the platform spec, logged via ``progress_cb``):

 1. validate dataset (real ``climate_observations`` only - refuses to invent data)
 2. define target + horizon
 3. select & engineer features (ml.features.build)
 4. sort chronologically per location
 5. chronological train/test split (never shuffled)
 6. fit preprocessing on the training window only
 7. fit baselines (naive persistence, seasonal-naive 365 d)
 8. fit candidates (Ridge, RandomForest, XGBoost when importable)
 9. evaluate MAE / RMSE / MAPE (strictly-positive targets only) / R2 + skill vs baselines
10. empirical uncertainty bands per lead time
11. persist artifact with joblib into ``settings.MODEL_STORE``
12. register the model version in ``model_registry`` (status candidate)
13. write the metrics report (markdown + JSON)
14. return metrics + artifact path + feature importances

Every metric is computed from a true recursive multi-step simulation on the
holdout: the model never sees actual intermediate values at evaluation time.
"""
from __future__ import annotations

import importlib.util
import logging
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler

from common import analytics, storage_paths
from common.analytics import InsufficientData, METRIC_UNITS
from common.repositories import model_registry
from config import settings
from ml.evaluation import metrics as ml_metrics
from ml.evaluation.report import write_run_report
from ml.features.build import (build_features, covariates_for, fit_preprocess,
                               group_observations)
from ml.inference.predictor import (ARTIFACT_SCHEMA, UNCERTAINTY_METHOD, LocationHistory,
                                    seasonal_naive, simulate_from_origin)
from ml.inference import predictor

logger = logging.getLogger(__name__)

DEFAULT_MIN_TRAIN_ROWS = 250
MIN_EVAL_POINTS = 30
DEFAULT_TEST_FRACTION = 0.2
ARTIFACT_DIR_NAME = "models"
PIPELINE_VERSION = "1"

XGBOOST_AVAILABLE = importlib.util.find_spec("xgboost") is not None

ALGORITHMS: Dict[str, Dict[str, Any]] = {
    "ridge": {
        "label": "Ridge regression (linear)",
        "min_rows": 150,
        "nan_native": False,
        "requirements": "training-window median imputation + scaling (fitted on train only)",
    },
    "random_forest": {
        "label": "Random forest regressor",
        "min_rows": 250,
        "nan_native": True,
        "requirements": "handles NaN directly; ensemble of 200 trees",
    },
    "xgboost": {
        "label": "XGBoost gradient boosting",
        "min_rows": 250,
        "nan_native": True,
        "requirements": "requires the xgboost package; handles NaN natively",
    },
}

DEFAULT_ALGORITHMS: Tuple[str, ...] = ("ridge", "random_forest", "xgboost")


def default_model_name(target: str, algorithm: str) -> str:
    return f"{target}-{algorithm}-daily"


def next_version(name: str) -> int:
    rows = model_registry.find({"name": name}, projection={"version": 1},
                               sort=[("version", -1)], limit=1)
    return int(rows[0]["version"]) + 1 if rows else 1


def build_candidate(name: str) -> Any:
    if name == "ridge":
        return SklearnPipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", Ridge(alpha=1.0)),
        ])
    if name == "random_forest":
        return RandomForestRegressor(n_estimators=200, max_depth=16, min_samples_leaf=3,
                                     n_jobs=2, random_state=42)
    if name == "xgboost":
        if not XGBOOST_AVAILABLE:
            raise InsufficientData("XGBoost is not installed in this environment.")
        from xgboost import XGBRegressor

        return XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=6,
                             subsample=0.9, colsample_bytree=0.9, tree_method="hist",
                             random_state=42, n_jobs=2, verbosity=0)
    raise InsufficientData(f"Unknown algorithm '{name}'. Available: {', '.join(ALGORITHMS)}.")


def feature_importances(estimator: Any, feature_names: Sequence[str]) -> Optional[List[Dict[str, Any]]]:
    try:
        if isinstance(estimator, SklearnPipeline):
            ridge = estimator.named_steps["model"]
            coefs = np.abs(np.asarray(ridge.coef_, dtype=float))
        elif hasattr(estimator, "feature_importances_"):
            coefs = np.abs(np.asarray(estimator.feature_importances_, dtype=float))
        else:
            return None
    except (AttributeError, TypeError, ValueError):
        return None
    total = float(coefs.sum())
    if total <= 0:
        return None
    ranked = sorted(zip(feature_names, coefs), key=lambda item: item[1], reverse=True)
    return [{"feature": str(name), "importance": round(float(value) / total, 5)}
            for name, value in ranked[:20]]


def _as_day(raw: Any) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def fetch_scope_observations(*, regions: Optional[Sequence[str]],
                             location_codes: Optional[Sequence[str]],
                             start: Optional[str], end: Optional[str]) -> List[Dict[str, Any]]:
    criteria = analytics.observation_query(regions=regions, location_codes=location_codes,
                                           start_date=start, end_date=end)
    return analytics.fetch_observations(criteria)


# --------------------------------------------------------------------------- main
def run_training(*, target_variable: str,
                 regions: Optional[Sequence[str]] = None,
                 location_codes: Optional[Sequence[str]] = None,
                 start: Optional[str] = None,
                 end: Optional[str] = None,
                 horizon_days: Optional[int] = None,
                 algorithms: Sequence[str] = DEFAULT_ALGORITHMS,
                 min_train_rows: Optional[int] = None,
                 name: Optional[str] = None,
                 trained_by: str = "system",
                 requested_by: Optional[str] = None,
                 observations: Optional[Sequence[Dict[str, Any]]] = None,
                 precreated: Optional[Sequence[Dict[str, Any]]] = None,
                 progress_cb: Optional[Callable[[str, int], None]] = None,
                 run_notes: Optional[str] = None) -> Dict[str, Any]:
    """Train every requested candidate and register each as ``candidate``.

    Returns the per-run summary plus one result dict per algorithm. Raises
    :class:`common.analytics.InsufficientData` (with real counts) when the
    stored record cannot support an honest training run - it never proceeds on
    fabricated data.
    """
    started = time.monotonic()
    run_id = uuid.uuid4().hex[:12]

    def progress(stage: str, pct: int) -> None:
        logger.info("training[%s] %s%% %s", run_id, pct, stage)
        if progress_cb:
            try:
                progress_cb(stage, pct)
            except Exception as exc:  # noqa: BLE001 - progress must never break training
                logger.warning("progress callback failed: %s", exc)

    # ---------------------------------------------------------- 1. validate data
    if target_variable not in analytics.METRICS:
        raise InsufficientData(
            f"Target '{target_variable}' is not a canonical metric. Options: "
            f"{', '.join(analytics.METRICS)}.")
    if not regions and not location_codes:
        raise InsufficientData("Training needs a scope: regions[] and/or location_codes[].")
    requested = [a for a in (algorithms or DEFAULT_ALGORITHMS) if a in ALGORITHMS]
    if not requested:
        raise InsufficientData(f"No valid algorithms requested. Options: {', '.join(ALGORITHMS)}.")
    if observations is None:
        observations = fetch_scope_observations(regions=regions, location_codes=location_codes,
                                                start=start, end=end)
    observations = [row for row in observations if _as_day(row.get("observation_date")) is not None]
    if not observations:
        raise InsufficientData(
            "No training data available: climate_observations holds no documents for the "
            "requested scope/window. Ingest real observations before training - the pipeline "
            "refuses to fabricate data.", available=0)
    populated = [row for row in observations if row.get(target_variable) is not None]
    if not populated:
        raise InsufficientData(
            f"'{target_variable}' is null in every observation in scope "
            f"({len(observations)} rows inspected). Choose another target or scope.",
            available=len(observations), required=1)
    min_rows = int(min_train_rows or DEFAULT_MIN_TRAIN_ROWS)

    # --------------------------------------------------- 2. target + horizon
    ceiling = predictor.max_horizon_days()
    requested_horizon = int(horizon_days or ceiling)
    if requested_horizon < 1:
        raise InsufficientData("horizon_days must be >= 1.")

    # Repeatable options snapshot: lets registry models be retrained via the API
    # even when the pipeline was invoked directly (not through services.train_model).
    run_options: Dict[str, Any] = {
        "target_variable": target_variable,
        "regions": list(regions or []),
        "location_codes": list(location_codes or []),
        "start": start,
        "end": end,
        "horizon_days": requested_horizon,
        "algorithms": requested,
        "min_train_rows": min_rows,
        "name": name,
    }

    # --------------------------------------------- 3/4. features + chronological sort
    progress("engineering features", 10)
    cov_names = covariates_for(target_variable)
    all_days = sorted({_as_day(row["observation_date"]) for row in observations})
    if len(all_days) < 60:
        raise InsufficientData(
            f"Only {len(all_days)} distinct observation days exist in scope; at least 60 are "
            "required before a chronological split is meaningful.",
            available=len(all_days), required=60)

    # --------------------------------------------- 5. chronological train/test split
    split_cut = max(1, int(len(all_days) * (1.0 - DEFAULT_TEST_FRACTION)))
    split_day = all_days[split_cut]
    train_obs = [row for row in observations if _as_day(row["observation_date"]) < split_day]
    test_obs = [row for row in observations if _as_day(row["observation_date"]) >= split_day]

    # ------------------------------------------------- 6. fit preprocessing (train only)
    progress("fitting preprocessing on the training window", 20)
    preprocess = fit_preprocess(train_obs, target_variable, cov_names)
    train_frame, feature_definition, preprocess = build_features(
        train_obs, target_variable, preprocess=preprocess)
    feature_names = preprocess["feature_names"]
    train_frame = train_frame.sort_values(["date", "location_code"], kind="stable").reset_index(drop=True)

    if len(train_frame) < min_rows:
        raise InsufficientData(
            f"Training window has only {len(train_frame)} usable rows "
            f"for '{target_variable}' (minimum {min_rows}). Widen the scope/period or "
            "ingest more observations.", available=len(train_frame), required=min_rows)

    groups = group_observations(observations, target_variable, cov_names)
    eval_sets: Dict[str, Tuple[List[date], Dict[date, Optional[float]], LocationHistory]] = {}
    for group in groups:
        rows = [(day, _num(row.get(target_variable))) for day, row in zip(group["days"], group["rows"])]
        actuals = {day: value for day, value in rows}
        eval_days = [day for day, value in rows if value is not None and day >= split_day]
        if not eval_days:
            continue
        template = LocationHistory(group["location_code"], group["region"],
                                   _num(group["rows"][0].get("latitude")) or 0.0,
                                   _num(group["rows"][0].get("longitude")) or 0.0,
                                   cov_names, preprocess["lags"], preprocess["windows"],
                                   preprocess["baselines"])
        eval_sets[group["location_code"]] = (eval_days, actuals, template)
    total_eval_points = sum(len(days) for days, _, _ in eval_sets.values())
    if total_eval_points < MIN_EVAL_POINTS:
        raise InsufficientData(
            f"Holdout has only {total_eval_points} points with real values; at least "
            f"{MIN_EVAL_POINTS} are needed for honest metrics.",
            available=total_eval_points, required=MIN_EVAL_POINTS)
    all_eval_days = sorted({day for days, _, _ in eval_sets.values() for day in days})
    eval_horizon = min(max(1, (all_eval_days[-1] - split_day).days + 1), ceiling,
                       max(1, requested_horizon))

    x_train = train_frame[feature_names].astype(float).to_numpy()
    y_train = train_frame["y"].astype(float).to_numpy()

    # ------------------------------------------------------------- 7. baselines
    progress("fitting naive, seasonal-naive and climatology baselines", 30)
    naive_pairs: List[Tuple[float, float]] = []
    seasonal_pairs: List[Tuple[float, float]] = []
    climatology_pairs: List[Tuple[float, float]] = []
    climatology_step_pairs: List[Tuple[int, float, float]] = []
    for loc, (eval_days, actuals, template) in eval_sets.items():
        last_real = None
        for day in sorted(actuals):
            if day < split_day and actuals[day] is not None:
                last_real = actuals[day]
        if last_real is not None:
            naive_pairs.extend((actuals[day], last_real) for day in eval_days
                               if actuals[day] is not None)
        seasonal = seasonal_naive(actuals, (split_day - timedelta(days=1)).toordinal(), eval_days)
        seasonal_pairs.extend((actuals[day], seasonal[day]) for day in eval_days
                              if day in seasonal and actuals[day] is not None)
        # The anchor a long-lead forecast can fall back to: the (region, month) climatology
        # frozen from the training window - the same value the features were built from.
        for day in eval_days:
            anchor = template.baseline_mean(day.month)
            if anchor is None or actuals.get(day) is None:
                continue
            climatology_pairs.append((actuals[day], anchor))
            climatology_step_pairs.append(((day - split_day).days + 1, actuals[day], anchor))
    naive_mae = ml_metrics.mae(naive_pairs)
    seasonal_mae = ml_metrics.mae(seasonal_pairs)
    climatology_mae = ml_metrics.mae(climatology_pairs)
    climatology_by_lead = ml_metrics.mae_by_lead(climatology_step_pairs)
    baseline_payload = {
        "naive_baseline": ml_metrics.evaluate(naive_pairs),
        "seasonal_baseline": ml_metrics.evaluate(seasonal_pairs),
        "climatology_baseline": ml_metrics.evaluate(climatology_pairs, naive_mae),
    }
    climatology_bands = ml_metrics.per_step_residual_quantiles(climatology_step_pairs, eval_horizon)

    # ---------------------------------------- 8/9/10. fit, evaluate, uncertainty
    results: List[Dict[str, Any]] = []
    dataset_ids = sorted({str(row.get("dataset_id")) for row in observations if row.get("dataset_id")})
    period = {
        "start": all_days[0].isoformat(),
        "end": all_days[-1].isoformat(),
        "locations": sorted(g["location_code"] for g in groups),
        "regions": sorted({g["region"] for g in groups}),
        "split_day": split_day.isoformat(),
    }

    for index, algorithm in enumerate(requested):
        if algorithm == "xgboost" and not XGBOOST_AVAILABLE:
            results.append({"algorithm": algorithm, "status": "skipped",
                            "error": "xgboost is not installed in this environment"})
            continue
        progress(f"fitting {algorithm}", 40 + index * 15)
        algo_started = time.monotonic()
        try:
            estimator = build_candidate(algorithm)
            estimator.fit(x_train, y_train)
        except InsufficientData as exc:
            results.append({"algorithm": algorithm, "status": "skipped", "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - one failed candidate must not sink the run
            logger.exception("fit failed for %s", algorithm)
            results.append({"algorithm": algorithm, "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}"[:400]})
            continue

        pairs: List[Tuple[float, float]] = []
        step_pairs: List[Tuple[int, float, float]] = []
        for loc, (eval_days, actuals, template) in eval_sets.items():
            simulated = simulate_from_origin(template, [(day, value) for day, value in
                                                        sorted(actuals.items())],
                                             split_day - timedelta(days=1), feature_names,
                                             estimator, eval_days)
            for day, actual, predicted in simulated:
                if actual is None:
                    continue
                pairs.append((actual, predicted))
                step_pairs.append(((day - split_day).days + 1, actual, predicted))
        measured = ml_metrics.evaluate(pairs, naive_mae)
        bands = ml_metrics.per_step_residual_quantiles(step_pairs, eval_horizon)
        model_by_lead = ml_metrics.mae_by_lead(step_pairs)
        skillful_lead = ml_metrics.skillful_lead_days(model_by_lead, climatology_by_lead,
                                                      eval_horizon)
        uncertainty_method = UNCERTAINTY_METHOD if any(b is not None for b in bands) else None
        duration = round(time.monotonic() - algo_started, 2)

        results.append({
            "algorithm": algorithm,
            "status": "trained",
            "estimator": estimator,
            "metrics": {**measured,
                        "seasonal_baseline_mae": seasonal_mae,
                        "climatology_baseline_mae": climatology_mae,
                        "skill_vs_seasonal_baseline": ml_metrics.skill_vs_baseline(
                            measured["mae"], seasonal_mae),
                        "skill_vs_climatology_baseline": ml_metrics.skill_vs_baseline(
                            measured["mae"], climatology_mae),
                        "mae_by_lead": model_by_lead,
                        "climatology_mae_by_lead": climatology_by_lead,
                        "skillful_lead_days": skillful_lead,
                        "horizon_days": eval_horizon,
                        "split_strategy": "chronological"},
            "train_size": int(len(train_frame)),
            "test_size": int(len(pairs)),
            "uncertainty_bands": bands,
            "uncertainty_method": uncertainty_method,
            "feature_importance": feature_importances(estimator, feature_names),
            "duration_seconds": duration,
            "naive_baseline": baseline_payload["naive_baseline"],
            "seasonal_baseline": baseline_payload["seasonal_baseline"],
            "climatology_baseline": baseline_payload["climatology_baseline"],
            "climatology_uncertainty_bands": climatology_bands,
        })

    trained = [item for item in results if item.get("status") == "trained"]
    if not trained:
        errors = "; ".join(f"{item['algorithm']}: {item.get('error')}"
                           for item in results if item.get("status") != "trained")
        raise InsufficientData(f"Every candidate failed to train: {errors}")

    best_mae = min(item["metrics"]["mae"] for item in trained if item["metrics"]["mae"] is not None)

    # ------------------------------------------------------ 11/12/13. persist + register
    progress("persisting artifacts and registering versions", 80)
    precreated_by_algo = {str(item.get("algorithm")): item for item in (precreated or [])}
    store = Path(settings.MODEL_STORE) / ARTIFACT_DIR_NAME
    store.mkdir(parents=True, exist_ok=True)
    comparison_table = ml_metrics.holdout_comparison_table([
        {"algorithm": item["algorithm"], "metrics": item["metrics"],
         "train_size": item["train_size"], "test_size": item["test_size"],
         "naive_baseline": item["naive_baseline"], "seasonal_baseline": item["seasonal_baseline"],
         "climatology_baseline": item["climatology_baseline"]}
        for item in trained])

    for item in trained:
        doc = _registry_document(item, run_id, target_variable, period, feature_definition,
                                 dataset_ids, trained_by, requested_by, run_notes,
                                 precreated_by_algo.get(item["algorithm"]), best_mae=best_mae,
                                 training_options=run_options)
        doc["status"] = "candidate"
        artifact_path = store / f"{doc['name']}-v{doc['version']}.joblib"
        artifact = {
            "schema": ARTIFACT_SCHEMA,
            "model": item["estimator"],
            "target_variable": target_variable,
            "preprocess": {key: preprocess[key] for key in
                           ("baselines", "covariates", "lags", "windows",
                            "anomaly_windows", "feature_names", "imputation") if key in preprocess},
            "uncertainty_bands": item["uncertainty_bands"],
            "climatology_uncertainty_bands": item["climatology_uncertainty_bands"],
            "skillful_lead_days": item["metrics"]["skillful_lead_days"],
            "evaluated_horizon_days": item["metrics"]["horizon_days"],
            "locations_in_scope": period["locations"],
            "model_name": doc["name"],
            "model_version": doc["version"],
            "run_id": run_id,
            "pipeline_version": PIPELINE_VERSION,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        joblib.dump(artifact, artifact_path)
        doc["artifact_uri"] = storage_paths.stored_key(artifact_path)
        doc["artifact_format"] = "joblib"
        doc["duration_seconds"] = item["duration_seconds"]
        doc["skillful_lead_days"] = item["metrics"]["skillful_lead_days"]
        doc.pop("estimator", None)

        if precreated_by_algo.get(item["algorithm"]) is not None:
            model_registry.update_by_id(str(precreated_by_algo[item["algorithm"]]["model_id"]),
                                        {"$set": doc})
            doc["_id"] = precreated_by_algo[item["algorithm"]]["model_id"]
        else:
            try:
                doc["_id"] = model_registry.insert_one(doc)
            except Exception as exc:  # noqa: BLE001 - name/version race: bump and retry once
                logger.warning("registry insert race for %s: %s", doc["name"], exc)
                doc["version"] = next_version(doc["name"])
                new_path = store / f"{doc['name']}-v{doc['version']}.joblib"
                artifact_path.rename(new_path)
                artifact_path = new_path
                doc["artifact_uri"] = storage_paths.stored_key(artifact_path)
                doc["_id"] = model_registry.insert_one(doc)

        item["model_id"] = str(doc["_id"])
        item["model_name"] = doc["name"]
        item["model_version"] = doc["version"]
        item["artifact_uri"] = storage_paths.stored_key(artifact_path)
        item["report_paths"] = write_run_report(doc, {
            "run_id": run_id,
            "target_unit": METRIC_UNITS.get(target_variable),
            "split_day": period["split_day"],
            "notes": run_notes,
            "requested_by": requested_by,
            "observations_in_scope": len(observations),
            "baseline_comparison": baseline_payload,
        }, comparison_table)
        item.pop("naive_baseline", None)
        item.pop("seasonal_baseline", None)
        item.pop("estimator", None)

    progress("training complete", 100)
    return {
        "run_id": run_id,
        "target_variable": target_variable,
        "training_period": period,
        "train_size": int(len(train_frame)),
        "test_size": int(max(item["test_size"] for item in trained)),
        "observations_in_scope": len(observations),
        "dataset_ids": dataset_ids,
        "naive_baseline_mae": naive_mae,
        "seasonal_baseline_mae": seasonal_mae,
        "comparison_table": comparison_table,
        "results": results,
        "duration_seconds": round(time.monotonic() - started, 2),
    }


def _num(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(value) or np.isinf(value) else value


def _registry_document(item: Dict[str, Any], run_id: str, target: str, period: Dict[str, Any],
                      feature_definition: Dict[str, Any], dataset_ids: Sequence[str],
                      trained_by: str, requested_by: Optional[str], run_notes: Optional[str],
                      precreated: Optional[Dict[str, Any]],
                      best_mae: Optional[float] = None,
                      training_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    algorithm = item["algorithm"]
    if precreated is not None:
        name = precreated["name"]
        version = int(precreated["version"])
    else:
        name = default_model_name(target, algorithm)
        version = next_version(name)
    is_best = (item["metrics"]["mae"] is not None and best_mae is not None
               and abs(item["metrics"]["mae"] - best_mae) < 1e-9)
    notes = run_notes or ""
    if is_best:
        notes = (notes + " " if notes else "") + "Best holdout MAE of this training run."
    limitations = [
        "Statistical extrapolation of stored station observations; no physical atmospheric model "
        "is involved.",
        "Weather vs climate: this is a day-to-day weather forecast. It is not evidence of a "
        "climate trend and must not be presented as one.",
        "Multi-day forecasts are recursive; errors compound with lead time, so late-horizon values "
        "carry wider empirical bands.",
        "One pooled model generalises across locations through lat/lon and region-month climatology; "
        "stations with sparse history are forecast least accurately.",
        f"Knowledge cutoff: the record ends {period['end']}; regime shifts after that date are "
        "not learned.",
    ]
    if algorithm == "ridge":
        limitations.append("Linear model: it cannot represent threshold or convective behaviour.")
    return {
        "name": name,
        "version": version,
        "algorithm": algorithm,
        "target_variable": target,
        "feature_definition": feature_definition,
        "training_period": {
            "start": period["start"], "end": period["end"],
            "locations": period["locations"], "regions": period["regions"],
            "train_before": period["split_day"],
        },
        "train_size": item["train_size"],
        "test_size": item["test_size"],
        "evaluation_metrics": item["metrics"],
        "artifact_uri": None,
        "artifact_format": "joblib",
        "status": "training",
        "created_at": (precreated or {}).get("created_at") or datetime.now(timezone.utc),
        "trained_by": trained_by,
        "requested_by": requested_by,
        "dataset_ids": list(dataset_ids),
        "notes": notes or None,
        "limitations": limitations,
        "feature_importance": item["feature_importance"],
        "uncertainty_method": item["uncertainty_method"],
        "error_message": None,
        "run_id": run_id,
        "horizon_days": item["metrics"]["horizon_days"],
        "training_options": (precreated or {}).get("training_options") or training_options,
    }


__all__ = ["run_training", "ALGORITHMS", "DEFAULT_ALGORITHMS", "build_candidate",
          "default_model_name", "next_version", "feature_importances",
          "XGBOOST_AVAILABLE", "fetch_scope_observations"]
