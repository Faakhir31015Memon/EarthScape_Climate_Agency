"""Forecasting from saved artifacts, with hard honesty guards.

``forecast()`` never silently degrades: a horizon longer than the model was
validated for is REJECTED, a feature-spec mismatch refuses prediction, and
uncertainty bands are returned only when the stored model actually supports
them (else null with the reason). Output frames every number as a projection,
not a guaranteed outcome.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from ..datasets.frame import SeriesAssembler
from ..datasets import loader
from ..features.pipeline import ClimateFeaturePipeline
from ..features.time_features import add_time_features
from ..training import store
from ..training.forecast import GRANULARITY_FREQ, SEASONAL_PERIOD

PROJECTION_NOTE = (
    "These are projections from a fitted statistical model, not guaranteed outcomes. "
    "Short-range values are weather forecasts; they say nothing about long-term climate trends."
)


def forecast(model_ref: str, target_variable: str, region: str, horizon_steps: int,
             origin_date: Optional[str] = None, tidy: Optional[pd.DataFrame] = None,
             z: float = 1.96) -> dict[str, Any]:
    """Return a JSON-safe forecast dict or a typed refusal."""
    try:
        loaded = store.load_model(model_ref)
    except (FileNotFoundError, ValueError) as exc:
        return {"status": "error", "reason": f"model load refused: {exc}", "model_ref": model_ref}

    meta = loaded.metadata
    warnings: list[str] = []

    # ---- consistency of the request against the stored model
    stored_target = str(meta.get("target_variable", loaded.payload.get("target_variable", "")))
    stored_region = str(meta.get("region", loaded.payload.get("region", "")))
    if stored_target and target_variable and stored_target != target_variable:
        return {"status": "error",
                "reason": f"model was trained on target '{stored_target}', request asks '{target_variable}'"}
    if stored_region and region and stored_region != region:
        warnings.append(f"requested region '{region}' differs from training region '{stored_region}'")

    # ---- horizon rejection (never silently degrade)
    validated_horizon = loaded.validated_horizon
    if horizon_steps > validated_horizon:
        return {"status": "rejected_horizon",
                "reason": (f"requested horizon {horizon_steps} steps exceeds the horizon this model "
                           f"was validated for ({validated_horizon}); refusing to extrapolate the model "
                           f"beyond its tested range"),
                "validated_horizon": validated_horizon, "requested_horizon": horizon_steps}

    granularity = str(loaded.payload.get("granularity", meta.get("granularity", "daily")))
    freq = GRANULARITY_FREQ.get(granularity, "D")
    step = pd.Timedelta(1, unit=freq)

    # ---- live data
    if tidy is None:
        tidy = loader.load_observations()
    assembled = SeriesAssembler(tidy).assemble(region or stored_region, target_variable, granularity)
    availability = assembled.availability
    if not availability.enough_rows:
        return {"status": "insufficient_data",
                "reason": f"cannot forecast {target_variable} for {region}: {availability.reason}",
                "availability": availability.as_dict()}
    series = assembled.series
    if origin_date:
        cutoff = pd.to_datetime(origin_date, utc=True)
        series = series[series.index <= cutoff]
        if series.empty:
            return {"status": "error", "reason": f"no observations at or before origin_date={origin_date}"}
    if series.dropna().empty:
        return {"status": "error", "reason": "series contains no observed values"}

    last_observed = series.dropna().index.max()
    origin = series.index.max()
    freshness = {
        "origin_timestamp": str(origin),
        "last_observed_value_timestamp": str(last_observed),
        "requested_origin_date": origin_date,
        "staleness_hours": round(float((pd.Timestamp.now(tz="UTC") - last_observed) / pd.Timedelta(hours=1)), 2),
    }

    # recent missing data warning
    tail = series.iloc[-min(len(series), horizon_steps):]
    missing_recent = int(tail.isna().sum())
    if missing_recent:
        warnings.append(f"{missing_recent}/{len(tail)} of the last {len(tail)} calendar slots before the "
                        f"origin are missing; features use train-median imputation for those slots")

    # ---- build features at the origin row via the SAME derivation used in training
    statics = {"latitude": assembled.latitude, "longitude": assembled.longitude,
               "elevation_m": assembled.elevation_m}
    spec_names = list(loaded.feature_spec["feature_names"])
    X_full = add_time_features(pd.DataFrame({"value": series}), statics=statics,
                               feature_names=spec_names)

    horizon = validated_horizon
    # Direct-H model: the forecast for time t0+k (1<=k<=H) is produced with the
    # feature origin rolled back to t0+k-H so that every input is a REAL observation
    # (no recursive feeding of predicted values). Earliest steps are therefore
    # "issued early", latest step uses the true origin. Documented as a warning.
    origins = [origin + (k - horizon) * step for k in range(1, horizon + 1)]
    targets = [origin + k * step for k in range(1, horizon + 1)]
    missing_origins = [str(t) for t in origins if t not in X_full.index]
    if missing_origins:
        warnings.append(f"{len(missing_origins)} feature-origin slots are outside the calendar index; "
                        f"nearest available rows used")
        reindexed = X_full.reindex(sorted(list(X_full.index.union(pd.DatetimeIndex(origins))))).ffill(limit=1)
    else:
        reindexed = X_full
    X_origins = reindexed.loc[[t for t in origins if t in reindexed.index]]
    if len(X_origins) < horizon:
        warnings.append("forecast shortened to the number of valid feature origins available")

    algorithm = loaded.algorithm

    # ---- feature extrapolation check vs training ranges
    ranges = loaded.feature_spec.get("training_ranges", {})
    extrap = []
    for col, (lo, hi) in ranges.items():
        if col in X_origins.columns:
            vals = X_origins[col].dropna()
            if len(vals) and (float(vals.min()) < lo or float(vals.max()) > hi):
                extrap.append(col)
    if extrap:
        warnings.append(f"features outside their training range (model extrapolating): {sorted(extrap)}")

    # ---- predictions
    preds: np.ndarray
    tree_spread: Optional[np.ndarray] = None
    if algorithm == "naive":
        preds = X_origins["lag_1"].ffill().bfill().to_numpy(dtype="float64")
    elif algorithm == "seasonal_naive":
        known = series.ffill()
        period = int(loaded.payload.get("seasonal_period", SEASONAL_PERIOD.get(granularity, 365)))
        naive_tail = X_origins["lag_1"].ffill().bfill().to_numpy(dtype="float64")
        preds = []
        for i, target_ts in enumerate(targets):
            season_ts = target_ts - period * step
            if known.index.min() <= season_ts <= known.index.max():
                val = known.asof(season_ts)
                preds.append(float(val) if pd.notna(val) else float(naive_tail[i]))
            else:
                preds.append(float(naive_tail[i]))
        preds = np.asarray(preds, dtype="float64")
        warnings.append("seasonal naive used (value from the same season last year where available)")
    else:
        try:
            preds = np.asarray(loaded.predict_validated(X_origins), dtype="float64")
        except ValueError as exc:
            return {"status": "error", "reason": f"feature spec validation refused prediction: {exc}"}
        estimator = loaded.payload.get("estimator")
        if algorithm == "random_forest" and estimator is not None:
            try:
                rf = estimator[-1]
                per_tree = np.stack([tree.predict(X_origins[spec_names]) for tree in rf.estimators_])
                tree_spread = per_tree.std(axis=0, ddof=1)
            except Exception as exc:
                warnings.append(f"per-tree spread unavailable: {exc}")

    # ---- uncertainty: only where the fitted model genuinely supports it
    unc_meta = loaded.payload.get("uncertainty", {})
    uncertainty: Optional[dict[str, Any]] = None
    uncertainty_reason: Optional[str] = None
    if unc_meta.get("supported"):
        sigma = float(unc_meta.get("sigma_train_residual", 0.0))
        steps = np.arange(1, len(preds) + 1)
        lead_var = (sigma ** 2) * steps
        if tree_spread is not None and len(tree_spread) == len(preds):
            lead_var = lead_var + tree_spread ** 2
        half = z * np.sqrt(lead_var)
        uncertainty = {
            "method": unc_meta.get("method") + ("+ensemble_tree_spread" if tree_spread is not None else ""),
            "coverage_nominal": unc_meta.get("coverage_nominal", 0.95),
            "lower": [None if not np.isfinite(v) else round(float(v), 4) for v in preds - half],
            "upper": [None if not np.isfinite(v) else round(float(v), 4) for v in preds + half],
            "caveat": unc_meta.get("note"),
        }
    else:
        uncertainty_reason = unc_meta.get("reason", "the fitted model does not expose residual or "
                                                    "ensemble spread; bands would be fabricated")

    # ---- non-finite guard
    if not np.all(np.isfinite(preds)):
        warnings.append("some predicted values are non-finite and were emitted as null")

    unit = availability.unit or "unknown"
    warnings.extend([
        PROJECTION_NOTE,
        "steps 1..H use feature origins rolled back so only observed inputs are used; "
        "the step-H value is the true origin-date forecast",
    ])
    if horizon != loaded.horizon_steps:
        warnings.append(f"request horizon {horizon_steps} <= validated {validated_horizon}; the model is "
                        f"a direct-{validated_horizon}-step model, so shorter steps inherit its inputs "
                        f"(see origin-rollback note)")

    metrics = {}
    registry_entry = meta.get("registry_entry", {})
    if registry_entry:
        metrics = registry_entry.get("evaluation_metrics", {})
    elif "evaluation_metrics" in meta:
        metrics = meta["evaluation_metrics"]

    return {
        "status": "forecast",
        "model": {"model_name": loaded.name, "model_version": loaded.version, "algorithm": algorithm,
                  "artifact_uri": meta.get("artifact_uri", "")},
        "target_variable": target_variable, "region": region or stored_region,
        "granularity": granularity, "horizon_steps": int(len(preds)),
        "validated_horizon": validated_horizon,
        "units": unit,
        "training_period": loaded.payload.get("training_period", meta.get("training_period", {})),
        "timestamps": [str(t) for t in targets[:len(preds)]],
        "values": [None if not np.isfinite(v) else round(float(v), 4) for v in preds],
        "metrics": metrics,
        "backtest_aggregate": meta.get("backtest", loaded.payload.get("backtest", {})),
        "data_freshness": freshness,
        "availability": availability.as_dict(),
        "uncertainty": uncertainty,
        "uncertainty_reason": uncertainty_reason,
        "warnings": warnings,
    }
