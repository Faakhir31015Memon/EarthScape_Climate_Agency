"""Supervised lagged-window training for temperature / precipitation.

Model ladder (project rule 3): naive baseline -> seasonal naive -> linear ridge
-> RandomForest -> XGBoost (guarded; recorded as unavailable if the package
fails to import). Data is split CHRONOLOGICALLY, every preprocessing step is
fit on the training portion only, and features pass a leakage check before any
model is fitted. A complex model beating the naive baseline by <5% is flagged
as such instead of hyped.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline as SkPipeline
from sklearn.preprocessing import StandardScaler

from ..datasets.frame import SeriesAssembler
from ..evaluation import metrics as M
from ..evaluation.backtest import walk_forward
from ..features.pipeline import ClimateFeaturePipeline, build_prep_pipeline
from .. import mlconfig
from .registry_entry import RegistryEntry
from . import store

GRANULARITY_FREQ = {"hourly": "h", "daily": "D", "monthly": "MS"}
SEASONAL_PERIOD = {"hourly": 24, "daily": 365, "monthly": 12}
BASELINE_ALGORITHMS = ("naive", "seasonal_naive")
MIN_TRAIN_SLICE = 60


def xgboost_status() -> dict[str, Any]:
    try:
        import xgboost  # noqa: F401
        return {"available": True, "version": getattr(xgboost, "__version__", "unknown"), "reason": None}
    except Exception as exc:
        return {"available": False, "version": None, "reason": f"xgboost import failed: {exc}"}


def _make_model(algorithm: str, feature_names: list[str]):
    """Fresh un-fitted sklearn pipeline (prep refit happens inside each factory)."""
    if algorithm == "linear_ridge":
        return SkPipeline([
            ("prep", build_prep_pipeline(feature_names, scale=True)),
            ("model", Ridge(alpha=1.0)),
        ])
    if algorithm == "random_forest":
        return SkPipeline([
            ("prep", build_prep_pipeline(feature_names, scale=False)),
            ("model", RandomForestRegressor(n_estimators=300, min_samples_leaf=2,
                                            random_state=0, n_jobs=-1)),
        ])
    if algorithm == "xgboost":
        from xgboost import XGBRegressor
        return SkPipeline([
            ("prep", build_prep_pipeline(feature_names, scale=False)),
            ("model", XGBRegressor(n_estimators=400, learning_rate=0.05, max_depth=5,
                                   subsample=0.9, colsample_bytree=0.9, random_state=0,
                                   n_jobs=-1)),
        ])
    raise ValueError(f"unknown algorithm {algorithm}")


def _sklearn_factory(algorithm: str, feature_names: list[str]):
    def factory(X_train: pd.DataFrame, y_train: pd.Series, ctx: dict):
        model = _make_model(algorithm, feature_names)
        model.fit(X_train[feature_names], y_train)

        def predict(X_test: pd.DataFrame) -> np.ndarray:
            return np.asarray(model.predict(X_test[feature_names]), dtype="float64")
        predict.model = model  # keep for final fit reuse
        return predict
    return factory


def _naive_factory():
    def factory(X_train, y_train, ctx):
        def predict(X_test):
            return X_test["lag_1"].ffill().bfill().to_numpy(dtype="float64")
        return predict
    return factory


def _seasonal_naive_factory(horizon: int, period: int, freq: str):
    def factory(X_train, y_train, ctx):
        series = ctx.get("series")
        if series is None:
            raise RuntimeError("seasonal naive requires the raw series in ctx")
        known = series.ffill()
        step = pd.Timedelta(1, unit=freq)

        def predict(X_test):
            out = []
            for ts in X_test.index:
                target_ts = ts + horizon * step
                season_ts = target_ts - period * step
                if season_ts >= known.index.min() and season_ts <= known.index.max():
                    val = known.asof(season_ts)
                    out.append(float(val) if pd.notna(val) else float(X_test["lag_1"].ffill().bfill().loc[ts]))
                else:
                    out.append(float(X_test["lag_1"].ffill().bfill().loc[ts]))
            return np.asarray(out, dtype="float64")
        return predict
    return factory


def _uncertainty_for(algorithm: str, y_train, train_pred) -> dict[str, Any]:
    """Rule 5: intervals only where the fitted model genuinely supports them."""
    if algorithm in BASELINE_ALGORITHMS:
        return {"supported": False,
                "reason": f"{algorithm} has no fitted residual structure on the training set; "
                          f"an empirical interval would be manufactured, so it is omitted"}
    resid = np.asarray(y_train, dtype="float64") - np.asarray(train_pred, dtype="float64")
    sigma = float(np.std(resid, ddof=1))
    return {
        "supported": True,
        "method": "residual_gaussian_train",
        "sigma_train_residual": sigma,
        "growth": "sqrt(lead_steps)",
        "coverage_nominal": 0.95,
        "tree_spread_available": algorithm in ("random_forest", "xgboost"),
        "note": "sigma fitted on TRAINING residuals; ensemble per-tree spread added where available. "
                "Train residual spread can UNDERSTATE true forecast error (in-sample optimism) — "
                "compare with walk-forward RMSE before quoting bands.",
    }


def train_forecaster(tidy: pd.DataFrame, target_variable: str, region: str,
                     granularity: str = "daily", horizon_steps: int = 30,
                     test_fraction: float = 0.2, folds: int = 5, save: bool = True,
                     models: Optional[list[str]] = None, min_train_rows: Optional[int] = None,
                     seed_prefix: Optional[str] = None) -> dict[str, Any]:
    """Full honest pipeline: assemble -> availability -> leakage check -> split ->
    ladder -> backtest -> artifacts -> registry entry. Returns a JSON-safe report."""
    granularity = granularity if granularity in GRANULARITY_FREQ else "daily"
    horizon_steps = int(horizon_steps)
    assembler = SeriesAssembler(tidy, min_rows=min_train_rows)
    assembled = assembler.assemble(region, target_variable, granularity)
    availability = assembled.availability
    xgb = xgboost_status()

    if not availability.enough_rows:
        return {"status": "insufficient_data", "trained": False,
                "availability": availability.as_dict(), "xgboost": xgb,
                "message": f"insufficient data for a {horizon_steps}-step forecast of "
                           f"{target_variable} in {region}: {availability.reason}"}

    series = assembled.series
    statics = {"latitude": assembled.latitude, "longitude": assembled.longitude,
               "elevation_m": assembled.elevation_m}
    pipeline = ClimateFeaturePipeline(statics=statics)

    # ---- leakage guard: refuse to train when any feature sees the future
    violations = pipeline.check_leakage(series, horizon_steps)
    if violations:
        raise RuntimeError(f"leakage check failed, refusing to train: {violations}")

    X, y, index = pipeline.matrix(series, horizon_steps)
    valid = y.notna()
    Xv, yv = X[valid], y[valid]
    n = len(Xv)
    n_test = max(horizon_steps, int(round(n * test_fraction)))
    n_train = n - n_test
    if n_train < MIN_TRAIN_SLICE:
        return {"status": "insufficient_data", "trained": False,
                "availability": {**availability.as_dict(),
                                 "reason": f"after building lagged windows only {n} rows remain "
                                           f"({n_train} train / {n_test} test); need >= "
                                           f"{MIN_TRAIN_SLICE} train rows"},
                "xgboost": xgb}

    X_tr, y_tr = Xv.iloc[:n_train], yv.iloc[:n_train]
    X_te, y_te = Xv.iloc[n_train:], yv.iloc[n_train:]
    pipeline.fit(X_tr, gap_stats={
        "policy": ("missing observations stay missing in the series; the model input imputes NaN "
                   "features with train-slice medians (SimpleImputer fit on training only)"),
        "calendar_gaps_in_range": availability.gap_count,
        "expected_slots": availability.expected_slots,
        "duplicate_collapses": availability.duplicate_collapses,
    })

    ladder = list(models or ["naive", "seasonal_naive", "linear_ridge", "random_forest"])
    if xgb["available"] and "xgboost" not in ladder:
        ladder.append("xgboost")
    ladder = [a for a in ladder if a != "xgboost" or xgb["available"]]

    freq = GRANULARITY_FREQ[granularity]
    period = SEASONAL_PERIOD.get(granularity, 365)
    known = series.ffill()
    step = pd.Timedelta(1, unit=freq)

    predictions: dict[str, np.ndarray] = {}
    fitted_models: dict[str, Any] = {}
    train_predictions: dict[str, np.ndarray] = {}

    for algorithm in ladder:
        if algorithm == "naive":
            predictions["naive"] = X_te["lag_1"].ffill().bfill().to_numpy(dtype="float64")
            train_predictions["naive"] = X_tr["lag_1"].ffill().bfill().to_numpy(dtype="float64")
            continue
        if algorithm == "seasonal_naive":
            out = []
            naive_tail = X_te["lag_1"].ffill().bfill()
            for i, ts in enumerate(X_te.index):
                target_ts = ts + horizon_steps * step
                season_ts = target_ts - period * step
                if known.index.min() <= season_ts <= known.index.max():
                    val = known.asof(season_ts)
                    out.append(float(val) if pd.notna(val) else float(naive_tail.iloc[i]))
                else:
                    out.append(float(naive_tail.iloc[i]))
            predictions["seasonal_naive"] = np.asarray(out, dtype="float64")
            train_predictions["seasonal_naive"] = X_tr["lag_1"].ffill().bfill().to_numpy(dtype="float64")
            continue
        model = _make_model(algorithm, pipeline.feature_names)
        model.fit(X_tr[pipeline.feature_names], y_tr)
        fitted_models[algorithm] = model
        predictions[algorithm] = np.asarray(model.predict(X_te[pipeline.feature_names]), dtype="float64")
        train_predictions[algorithm] = np.asarray(model.predict(X_tr[pipeline.feature_names]),
                                                  dtype="float64")

    baseline_mae = M.mae(y_te.to_numpy(), predictions["naive"])
    comparison: list[dict[str, Any]] = []
    for algorithm in ladder:
        m = M.evaluate_forecast(y_te.to_numpy(), predictions[algorithm])
        skill = M.skill_vs_baseline(m["mae"], baseline_mae)
        entry = {"algorithm": algorithm, "role": "baseline" if algorithm in BASELINE_ALGORITHMS else "model",
                 **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()},
                 "skill_vs_naive": None if skill is None else round(skill, 4),
                 "beats_naive_by_5pct": None if algorithm in BASELINE_ALGORITHMS or skill is None
                                        else bool(skill >= 0.05)}
        if algorithm == "xgboost" and not xgb["available"]:
            entry["note"] = f"not trained: {xgb['reason']}"
        comparison.append(entry)
    if not xgb["available"]:
        comparison.append({"algorithm": "xgboost", "role": "model", "trained": False,
                           "available": False, "note": f"unavailable: {xgb['reason']}"})

    # ---- choose best model; honesty flag for <5% gains over the naive baseline
    scored = [c for c in comparison if "mae" in c and c.get("mae") is not None]
    best = min(scored, key=lambda c: c["mae"])
    chosen_algorithm = best["algorithm"]
    best_model_entry = None
    model_scores = [c for c in scored if c["role"] == "model" and c["algorithm"] in fitted_models]
    if model_scores:
        best_model_entry = min(model_scores, key=lambda c: c["mae"])
    status = "candidate"
    limitations: list[str] = []
    if best_model_entry is None:
        limitations.append("no complex model could be fitted on this slice; baseline retained")
        chosen_algorithm = "naive" if best["role"] == "baseline" else chosen_algorithm
    else:
        skill_best = best_model_entry.get("skill_vs_naive")
        if skill_best is None or skill_best < 0.05:
            status = "candidate"
            limitations.append(
                f"best complex model ({best_model_entry['algorithm']}) beats the naive baseline by only "
                f"{(skill_best or 0) * 100:.1f}% (<5%): reported as marginal, NOT as a validated improvement")
        else:
            status = "registered"
    if chosen_algorithm in BASELINE_ALGORITHMS:
        status = "candidate"
        limitations.append("the retained best predictor is a baseline; no fitted model beat it on the "
                           "chronological holdout")

    # ---- walk-forward backtest over the tail (out-of-sample aggregate numbers)
    factories: dict[str, Any] = {"naive": _naive_factory()}
    if period > horizon_steps:
        factories["seasonal_naive"] = _seasonal_naive_factory(horizon_steps, period, freq)
    for algorithm in ladder:
        if algorithm in fitted_models:
            factories[algorithm] = _sklearn_factory(algorithm, pipeline.feature_names)
    backtest = walk_forward(Xv, yv, factories, horizon_steps=horizon_steps, folds=folds,
                            warmup_train_rows=n_train, series=series)
    if not backtest.get("available"):
        limitations.append(f"walk-forward backtest skipped: {backtest.get('reason')}")

    # ---- uncertainty for the chosen algorithm
    if chosen_algorithm in fitted_models:
        unc = _uncertainty_for(chosen_algorithm, y_tr, train_predictions[chosen_algorithm])
    else:
        unc = _uncertainty_for(chosen_algorithm, y_tr, train_predictions.get(chosen_algorithm, y_tr))

    # ---- artifacts + registry entry
    model_name = seed_prefix or f"{target_variable}-{region}-{granularity}-h{horizon_steps}"
    artifact_uri = ""
    payload = None
    if save:
        payload = {
            "algorithm": chosen_algorithm,
            "estimator": fitted_models.get(chosen_algorithm),  # None for baselines
            "feature_spec": pipeline.spec_,
            "feature_names": pipeline.feature_names,
            "horizon_steps": horizon_steps,
            "validated_horizon": horizon_steps,
            "granularity": granularity,
            "seasonal_period": period,
            "series_tail": {
                "timestamps": [str(t) for t in series.index[-(period + horizon_steps + 40):]],
                "values": [None if pd.isna(v) else float(v)
                           for v in series.iloc[-(period + horizon_steps + 40):]],
            },
            "uncertainty": unc,
            "target_variable": target_variable,
            "region": region,
            "training_period": {"start": str(series.index.min()), "end": str(X_tr.index.max())},
            "test_period": {"start": str(X_te.index.min()), "end": str(X_te.index.max())},
        }
        metadata = {
            "model_name": model_name, "algorithm": chosen_algorithm,
            "target_variable": target_variable, "region": region, "granularity": granularity,
            "horizon_steps": horizon_steps, "availability": availability.as_dict(),
            "comparison": comparison, "xgboost": xgb, "status": status,
            "leakage_check": {"ran": True, "violations": []},
            "data_source_files": availability.provenance_files,
            "rows_used": int(availability.rows),
            "training_rows": int(n_train), "test_rows": int(n_test),
        }
        saved = store.save_model(payload, metadata, model_name)
        artifact_uri = saved["artifact_uri"]
        version = saved["model_version"]
    else:
        version = mlconfig.ENV.raw("MODEL_VERSION_PRESET", "") or "unsaved"

    chosen_metrics = next((c for c in comparison if c["algorithm"] == chosen_algorithm), {})
    entry = RegistryEntry(
        model_name=model_name, model_version=version, algorithm=chosen_algorithm,
        target_variable=target_variable, granularity=granularity, horizon_steps=horizon_steps,
        feature_definition=pipeline.spec_["derivations"],
        training_period={"start": str(X_tr.index.min()), "end": str(X_tr.index.max())},
        test_period={"start": str(X_te.index.min()), "end": str(X_te.index.max())},
        rows_used=int(n),
        evaluation_metrics={
            "mae": chosen_metrics.get("mae"), "rmse": chosen_metrics.get("rmse"),
            "mape": chosen_metrics.get("mape"), "mape_skipped_reason": chosen_metrics.get("mape_skipped_reason"),
            "r2": chosen_metrics.get("r2"), "baseline_mae": round(baseline_mae, 4),
            "skill_vs_baseline": chosen_metrics.get("skill_vs_naive"),
            "test_n": int(n_test),
        },
        artifact_uri=artifact_uri, status=status,
        data_source_files=availability.provenance_files,
        limitations=limitations + [
            "short-range weather forecast on observed cadence, not a long-term climate trend statement",
            "forecast values are projections with uncertainty, never guaranteed outcomes",
            "chronological split only; all preprocessing fit on training slice",
        ],
        region=region,
        backtest={k: v for k, v in backtest.items() if k != "per_fold"} if backtest.get("available")
                 else backtest,
    )
    entry_dict = entry.to_dict()
    if save:
        store.attach_registry_entry(model_name, version, entry_dict)
    return {
        "status": "trained", "trained": True,
        "model_name": model_name, "model_version": version,
        "chosen_algorithm": chosen_algorithm, "registry_status": status,
        "availability": availability.as_dict(),
        "split": {"train_rows": int(n_train), "test_rows": int(n_test),
                  "train_end": str(X_tr.index.max()), "test_start": str(X_te.index.min()),
                  "method": "chronological (no shuffle)"},
        "comparison": comparison, "xgboost": xgb,
        "backtest": backtest, "uncertainty": unc,
        "registry": entry_dict, "artifact_uri": artifact_uri,
        "limitations": limitations,
    }
