"""Forecast inference for registered models.

Forecasting mode (documented, as required by the spec): a **single-step model
used recursively**. One regressor predicts day ``d`` from features dated
``< d``; for multi-day forecasts the prediction for day ``d`` is fed back into
the history before predicting ``d + 1``. This matches deployment reality (the
true intermediate values are not known at forecast time) and the holdout
evaluation below uses exactly the same recursion, so reported metrics are
multi-step metrics, not optimistic one-step metrics.

Uncertainty bands are empirical: per-lead-time residual quantiles measured on
the chronological holdout during training and frozen into the artifact. When a
holdout was too small to produce them, ``uncertainty.available`` is false and
the note says why. A band is a historical error envelope, never a guarantee.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import joblib
import numpy as np

from common import storage_paths
from common.analytics import InsufficientData, METRIC_UNITS
from config import env
from ml.features.build import LocationHistory, group_observations

logger = logging.getLogger(__name__)

ARTIFACT_SCHEMA = 1
UNCERTAINTY_METHOD = "empirical_holdout_residual_quantiles_per_lead_time"


def max_horizon_days() -> int:
    return env.get_int("FORECAST_MAX_HORIZON_DAYS", 45)


class HorizonTooLong(Exception):
    def __init__(self, requested: int, allowed: int):
        super().__init__(f"Requested horizon {requested} days exceeds the configured maximum {allowed}.")
        self.requested = requested
        self.allowed = allowed


def validate_horizon(horizon_days: int, allowed: Optional[int] = None) -> int:
    horizon_days = int(horizon_days)
    ceiling = int(allowed if allowed is not None else max_horizon_days())
    if horizon_days < 1:
        raise InsufficientData("horizon_days must be at least 1.")
    if horizon_days > ceiling:
        raise HorizonTooLong(horizon_days, ceiling)
    return horizon_days


# ----------------------------------------------------------------- artifacts
def load_artifact(model_document: Dict[str, Any]) -> Dict[str, Any]:
    path = storage_paths.stored_path(model_document.get("artifact_uri"))
    if path is None:
        raise InsufficientData(f"Model '{model_document.get('name')}' has no persisted artifact.")
    if not path.is_file():
        raise InsufficientData(
            f"Artifact for model '{model_document.get('name')}' v{model_document.get('version')} "
            f"is registered but the file is missing at {path.name}.")
    artifact = joblib.load(path)
    if artifact.get("schema") != ARTIFACT_SCHEMA:
        raise InsufficientData("Model artifact was written by an incompatible pipeline version.")
    return artifact


# ------------------------------------------------------------------- history
def _parse_day(raw: Any) -> Optional[date]:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def scope_locations(scope: Dict[str, Any], observations: Sequence[Dict[str, Any]]
                    ) -> Tuple[str, Optional[str], List[str]]:
    """Resolve {region_code|location_code} -> (kind, code, location codes present)."""
    location_code = (scope or {}).get("location_code")
    region_code = (scope or {}).get("region_code")
    if location_code:
        code = str(location_code).upper()
        present = sorted({str(row.get("location_code") or "").upper() for row in observations})
        if code not in present:
            raise InsufficientData(f"No stored observations for location '{code}'.")
        return "location", code, [code]
    if region_code:
        wanted = str(region_code)
        present = sorted({str(row.get("location_code") or "").upper()
                          for row in observations if str(row.get("region_code") or "") == wanted})
        if not present:
            raise InsufficientData(f"No stored observations for region '{wanted}'.")
        return "region", wanted, present
    raise InsufficientData("A forecast needs a scope with region_code or location_code.")


def build_location_histories(observations: Sequence[Dict[str, Any]], artifact: Dict[str, Any],
                             origin: date) -> Dict[str, LocationHistory]:
    groups = group_observations([row for row in observations if _parse_day(row.get("observation_date"))],
                                artifact["target_variable"], artifact["preprocess"]["covariates"])
    histories: Dict[str, LocationHistory] = {}
    for group in groups:
        history = LocationHistory(group["location_code"], group["region"],
                                  float((group["rows"][0] or {}).get("latitude") or 0.0),
                                  float((group["rows"][0] or {}).get("longitude") or 0.0),
                                  artifact["preprocess"]["covariates"],
                                  artifact["preprocess"]["lags"],
                                  artifact["preprocess"]["windows"],
                                  artifact["preprocess"]["baselines"])
        for day, row in zip(group["days"], group["rows"]):
            if day > origin:
                break
            history.append(day, _value(row.get(artifact["target_variable"])),
                           {name: row.get(name) for name in artifact["preprocess"]["covariates"]},
                           overwrite=True)
        histories[group["location_code"]] = history
    return histories


def _value(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


# ---------------------------------------------------------------- recursion
def _feature_row(history: LocationHistory, day: date, feature_names: Sequence[str]) -> List[Optional[float]]:
    feats = history.features_at(day)
    return [feats.get(name) for name in feature_names]


def recursive_forecast(model: Any, history: LocationHistory, feature_names: Sequence[str],
                       dates: Sequence[date]) -> List[float]:
    """Feed each prediction back into the history before predicting the next day."""
    predictions: List[float] = []
    for day in dates:
        row = _feature_row(history, day, feature_names)
        matrix = np.array([[None if value is None else float(value) for value in row]], dtype=float)
        value = float(model.predict(matrix)[0])
        predictions.append(value)
        history.append(day, value, overwrite=False)
    return predictions


def simulate_from_origin(history_template: LocationHistory, rows: Sequence[Tuple[date, Optional[float]]],
                         origin: date, feature_names: Sequence[str], model: Any,
                         eval_days: Sequence[date]) -> List[Tuple[date, Optional[float], float]]:
    """Recursive multi-step simulation used for holdout evaluation.

    Observations are replayed up to ``origin``; from ``origin + 1`` onward the
    model only ever sees its own previous predictions (true intermediate values
    are never fed back), then the simulated value is compared with the actual
    stored on each evaluation day. Reported metrics are therefore multi-step
    metrics, matching deployment behaviour.
    """
    history = LocationHistory(history_template.location_code, history_template.region,
                              history_template.latitude, history_template.longitude,
                              history_template.cov_names, history_template.lags,
                              history_template.windows, history_template.baselines)
    actual_by_day: Dict[date, Optional[float]] = {day: value for day, value in rows}
    for day, value in rows:
        if day <= origin:
            history.append(day, value, {}, overwrite=True)
    wanted = set(eval_days)
    results: List[Tuple[date, Optional[float], float]] = []
    cursor = origin
    last_day = max(wanted) if wanted else origin
    while cursor < last_day:
        cursor = cursor + timedelta(days=1)
        row = _feature_row(history, cursor, feature_names)
        matrix = np.array([[None if v is None else float(v) for v in row]], dtype=float)
        predicted = float(model.predict(matrix)[0])
        history.append(cursor, predicted)
        if cursor in wanted:
            results.append((cursor, actual_by_day.get(cursor), predicted))
    return results


def naive_persistence(origin_value: Optional[float], eval_days: Sequence[date]) -> Dict[date, float]:
    if origin_value is None:
        return {}
    return {day: float(origin_value) for day in eval_days}


def seasonal_naive(rows: Dict[date, Optional[float]], origin_ordinal: int,
                   eval_days: Sequence[date], seasonal_period: int = 365) -> Dict[date, float]:
    """yhat(d) = actual(d-365) when known, else the model's own earlier guess yhat(d-365)."""
    out: Dict[date, float] = {}
    by_day: Dict[date, float] = {}
    for day in sorted(eval_days):
        reference = day - timedelta(days=seasonal_period)
        value = None
        if reference in rows and rows[reference] is not None and reference.toordinal() <= origin_ordinal:
            value = float(rows[reference])
        elif reference in by_day:
            value = by_day[reference]
        if value is None:
            last = next((by_day[d] for d in sorted(by_day, reverse=True) if d < day), None)
            if last is None:
                continue
            value = last
        by_day[day] = value
        out[day] = value
    return out


# ------------------------------------------------------------------ forecast
def forecast(*, model_document: Dict[str, Any], observations: Sequence[Dict[str, Any]],
             scope: Dict[str, Any], horizon_days: int,
             forecast_origin_date: Optional[str] = None,
             requested_by: Optional[str] = None) -> Dict[str, Any]:
    """Produce a prediction document (not yet persisted) for one model + scope."""
    target = model_document["target_variable"]
    artifact = load_artifact(model_document)
    horizon_days = validate_horizon(horizon_days)

    days = [_parse_day(row.get("observation_date")) for row in observations]
    days = [day for day in days if day is not None]
    if not days:
        raise InsufficientData("No dated observations are available for the selected scope.")
    latest_observation = max(days)

    origin = latest_observation
    if forecast_origin_date:
        requested = _parse_day(forecast_origin_date)
        if requested is None:
            raise InsufficientData("forecast_origin_date must be YYYY-MM-DD.")
        if requested > latest_observation:
            raise InsufficientData(
                f"forecast_origin_date {requested.isoformat()} is after the most recent stored "
                f"observation ({latest_observation.isoformat()}); forecasts never invent input data.")
        origin = requested

    kind, code, present = scope_locations(scope, observations)
    scope_observations = [row for row in observations
                          if (str(row.get("location_code") or "").upper() in present
                              or (kind == "region" and str(row.get("region_code") or "") == code))]
    histories = build_location_histories(scope_observations, artifact, origin)
    usable = {loc: hist for loc, hist in histories.items()
              if hist.ords and any(v is not None for v in hist.values)}
    if not usable:
        raise InsufficientData(
            f"No observed {target} history exists for this scope up to {origin.isoformat()}.")

    feature_names = artifact["preprocess"]["feature_names"]
    forecast_dates = [origin + timedelta(days=step) for step in range(1, horizon_days + 1)]
    per_location: Dict[str, List[float]] = {}
    for loc, hist in usable.items():
        per_location[loc] = recursive_forecast(artifact["model"], hist, feature_names, forecast_dates)

    # precipitation is physically bounded below by zero; clamp and disclose it.
    clamped = target == "precipitation_mm"
    means: List[float] = []
    for i, day in enumerate(forecast_dates):
        column = [values[i] for values in per_location.values()]
        mean = sum(column) / len(column)
        if clamped:
            mean = max(mean, 0.0)
        means.append(round(mean, 3))

    bands = artifact.get("uncertainty_bands") or []
    uncertainty_available = bool(bands) and any(band is not None for band in bands)
    values: List[Dict[str, Any]] = []
    for i, day in enumerate(forecast_dates):
        band = bands[i] if i < len(bands) else None
        lower = upper = None
        if band and uncertainty_available:
            lower, upper = round(means[i] + band[0], 3), round(means[i] + band[1], 3)
            if clamped:
                lower = max(lower, 0.0)
        values.append({
            "forecast_date": day.isoformat(),
            "value": means[i],
            "unit": METRIC_UNITS[target],
            "lower_bound": lower,
            "upper_bound": upper,
        })

    now = datetime.now(timezone.utc)
    freshness = (now.date() - origin).days
    evaluation_snapshot = {key: value for key, value in (model_document.get("evaluation_metrics") or {}).items()}

    caveats = [
        "This is a statistical projection, not a guaranteed outcome; actual conditions will differ.",
        "Weather forecasts describe day-to-day atmospheric state; they are not evidence of climate "
        "trends, and climate trends do not determine any single day's weather.",
        f"Model '{model_document.get('name')}' v{model_document.get('version')} was trained on "
        f"{model_document.get('train_size')} chronological rows with holdout MAE "
        f"{evaluation_snapshot.get('mae')} {METRIC_UNITS[target]}; error grows with lead time.",
    ]
    if kind == "region":
        caveats.append(
            f"Region scope is the mean of {len(per_location)} per-location station forecasts; "
            "station values are point observations, not a regional reanalysis.")
    if clamped:
        caveats.append("Negative precipitation predictions were clamped to zero (physically bounded metric).")
    if not uncertainty_available:
        caveats.append("No uncertainty band: the training holdout did not provide enough residuals "
                       "for an honest empirical envelope.")

    document = {
        "model_id": str(model_document.get("_id") or model_document.get("id")),
        "model_name": model_document.get("name"),
        "model_version": model_document.get("version"),
        "target_variable": target,
        "scope": {"kind": kind, "code": code, "locations": sorted(usable.keys())},
        "region_code": code if kind == "region" else None,
        "location_code": code if kind == "location" else None,
        "horizon_days": horizon_days,
        "generated_at": now,
        "forecast_origin_date": origin.isoformat(),
        "values": values,
        "units": METRIC_UNITS[target],
        "method": f"{model_document.get('algorithm')} - recursive single-step multi-day daily forecast",
        "training_period": model_document.get("training_period"),
        "evaluation_metrics_snapshot": evaluation_snapshot,
        "data_freshness_days": freshness,
        "provenance": {
            "requested_by": requested_by,
            "feature_source": "climate_observations (MongoDB) + frozen training preprocessing",
            "actual_observations_used": int(sum(
                1 for hist in usable.values() for v in hist.values if v is not None)),
        },
        "status": "completed",
        "uncertainty": {
            "available": bool(uncertainty_available),
            "method": UNCERTAINTY_METHOD if uncertainty_available else None,
            "level": 0.95 if uncertainty_available else None,
            "note": ("Bands are empirical residual quantiles from the chronological holdout at each "
                     "lead time - a historical error envelope, not a probability guarantee."
                     if uncertainty_available else
                     "The training holdout produced too few residuals for an honest band; none is shown."),
        },
        "caveats": caveats,
    }
    return document


# --------------------------------------------------------------- persistence
def persist_prediction(document: Dict[str, Any]) -> str:
    from common.repositories import predictions, utcnow

    record = dict(document)
    record.setdefault("created_at", utcnow())
    return predictions.insert_one(record)


def forecast_summary_for_scope(*, region_code: Optional[str] = None, location_code: Optional[str] = None,
                               target_variable: Optional[str] = None) -> Dict[str, Any]:
    """Compact latest-forecast payload for the dashboard (never fabricates a series)."""
    from common.repositories import model_registry, predictions, utcnow

    query: Dict[str, Any] = {}
    if region_code:
        query["region_code"] = region_code
    if location_code:
        query["location_code"] = str(location_code).upper()
    if target_variable:
        query["target_variable"] = target_variable
    rows = predictions.find(query, sort=[("generated_at", -1)], limit=5)
    if not rows:
        active = model_registry.find({"status": "active", **(
            {"target_variable": target_variable} if target_variable else {})}, limit=5)
        return {
            "available": False,
            "note": ("No stored forecast covers this scope yet." if active else
                     "No trained model is active for this target."),
            "scope": {"region_code": region_code, "location_code": location_code},
        }
    latest = rows[0]
    age_hours = None
    generated = latest.get("generated_at")
    if isinstance(generated, datetime):
        moment = generated if generated.tzinfo else generated.replace(tzinfo=timezone.utc)
        age_hours = round((datetime.now(timezone.utc) - moment).total_seconds() / 3600.0, 1)
    return {
        "available": True,
        "prediction_id": str(latest.get("_id")),
        "model_name": latest.get("model_name"),
        "model_version": latest.get("model_version"),
        "target_variable": latest.get("target_variable"),
        "unit": latest.get("units"),
        "horizon_days": latest.get("horizon_days"),
        "forecast_origin_date": latest.get("forecast_origin_date"),
        "generated_at": generated,
        "age_hours": age_hours,
        "first_value": (latest.get("values") or [{}])[0].get("value") if latest.get("values") else None,
        "point_count": len(latest.get("values") or []),
        "uncertainty_available": bool((latest.get("uncertainty") or {}).get("available")),
        "data_freshness_days": latest.get("data_freshness_days"),
        "note": "Latest stored forecast for this scope; forecasts are projections, not guarantees.",
    }


__all__ = ["forecast", "load_artifact", "persist_prediction", "recursive_forecast",
           "forecast_summary_for_scope", "validate_horizon", "HorizonTooLong",
           "simulate_from_origin", "naive_persistence", "seasonal_naive", "ARTIFACT_SCHEMA",
           "UNCERTAINTY_METHOD", "max_horizon_days"]
