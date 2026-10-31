"""Alert rule definitions and the evaluation engine.

Rules are stored in the alerts collection with document_type == "rule"; fired alerts
are the documents without that marker. Both use the rule_key index from
common/collections.py, so a fired alert traces back to its rule on one indexed path.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import notifications  # noqa: E402
from common import analytics  # noqa: E402
from common.api import ADMINISTRATOR, ALL_ROLES, ApiError  # noqa: E402
from common.database import get_database  # noqa: E402
from common.repositories import alerts, predictions as predictions_repo, serialize, users, utcnow  # noqa: E402
from config import env  # noqa: E402

logger = logging.getLogger(__name__)

RULE_MARKER = "rule"
SEVERITIES = ("info", "warning", "critical")
ALERT_STATUSES = ("open", "acknowledged", "resolved", "dismissed")
OPERATORS = ("gt", "lt", "between", "outside_band", "deviation_gt_sigma", "anomaly")
# applicable_sources may name observation sources (matched against source_name)
# plus these control tokens, which pick the evaluation target.
SOURCE_CONTROL_TOKENS = ("observations", "predictions")
ALERT_SOURCES = ("rule_engine", "anomaly_scan", "prediction")

SCAN_LOOKBACK_DAYS = max(1, min(env.get_int("ALERT_SCAN_LOOKBACK_DAYS", 7), 366))
MAX_ROWS_PER_RULE = min(20000, max(1000, env.get_int("ALERT_SCAN_MAX_ROWS", 20000)))
MAX_ALERTS_PER_RULE_RUN = max(1, env.get_int("ALERT_MAX_FIRES_PER_RULE", 25))

# Starting points offered to the UI; they differ only in threshold and severity.
RULE_TEMPLATES: List[Dict[str, Any]] = [
    {
        "key": "heat-warning",
        "label": "Extreme heat warning",
        "description": "Fires when a daily mean 2 m temperature exceeds 40 C.",
        "defaults": {
            "name": "Extreme heat warning", "target_variable": "temperature_c",
            "condition": {"operator": "gt", "value": 40.0},
            "severity": "critical", "cooldown_minutes": 720,
            "applicable_sources": ["observations"], "recipients": ["role:climate_analyst"],
        },
    },
    {
        "key": "cold-alert",
        "label": "Cold stress alert",
        "description": "Fires when a daily mean 2 m temperature falls below 0 C.",
        "defaults": {
            "name": "Cold stress alert", "target_variable": "temperature_c",
            "condition": {"operator": "lt", "value": 0.0},
            "severity": "warning", "cooldown_minutes": 720,
            "applicable_sources": ["observations"], "recipients": ["role:climate_analyst"],
        },
    },
    {
        "key": "heavy-rainfall",
        "label": "Heavy rainfall",
        "description": "Fires when daily precipitation exceeds 80 mm.",
        "defaults": {
            "name": "Heavy rainfall", "target_variable": "precipitation_mm",
            "condition": {"operator": "gt", "value": 80.0},
            "severity": "warning", "cooldown_minutes": 360,
            "applicable_sources": ["observations"], "recipients": ["role:climate_analyst"],
        },
    },
    {
        "key": "humidity-band",
        "label": "Humidity outside comfort band",
        "description": "Fires when relative humidity leaves the 20-85 percent range.",
        "defaults": {
            "name": "Humidity outside comfort band",
            "target_variable": "relative_humidity_pct",
            "condition": {"operator": "outside_band", "value": 20.0, "value_upper": 85.0},
            "severity": "info", "cooldown_minutes": 1440,
            "applicable_sources": ["observations"], "recipients": ["role:viewer"],
        },
    },
    {
        "key": "statistical-anomaly",
        "label": "Statistical anomaly",
        "description": "Fires on observations more than 2.5 standard deviations away from "
                       "the climatology computed from the stored record itself.",
        "defaults": {
            "name": "Statistical anomaly", "target_variable": "temperature_c",
            "condition": {"operator": "deviation_gt_sigma", "sigma": 2.5},
            "severity": "warning", "cooldown_minutes": 720,
            "applicable_sources": ["observations"], "recipients": ["role:climate_analyst"],
        },
    },
]


def templates() -> List[Dict[str, Any]]:
    """Adds supported_variable so the UI can flag templates for unstored variables."""
    known = set(analytics.METRICS)
    output = []
    for template in RULE_TEMPLATES:
        variable = template["defaults"].get("target_variable")
        output.append({
            **template,
            "supported_variable": bool(variable in known),
        })
    return output


def rule_key_for(name: str, existing: Iterable[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (name or "rule").strip().lower()).strip("-")[:48] or "rule"
    taken = set(existing)
    candidate = base
    counter = 2
    while candidate in taken:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def _normalise_scope(scope: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    scope = scope or {}
    return {
        "regions": [str(item).strip() for item in analytics.as_list(scope.get("regions"))][:50],
        "location_codes": [str(item).strip().upper()
                           for item in analytics.as_list(scope.get("location_codes"))][:50],
        "countries": [str(item).strip() for item in analytics.as_list(scope.get("countries"))][:50],
        "datasets": [str(item).strip() for item in analytics.as_list(scope.get("datasets"))][:50],
    }


def _normalise_condition(condition: Optional[Dict[str, Any]], *, partial: bool) -> Dict[str, Any]:
    condition = condition or {}
    out: Dict[str, Any] = {}
    if "operator" in condition or not partial:
        operator = str(condition.get("operator") or "").strip()
        if operator not in OPERATORS:
            raise ApiError("validation_failed",
                           f"condition.operator must be one of: {', '.join(OPERATORS)}.")
        out["operator"] = operator
    if "value" in condition or not partial:
        value = condition.get("value")
        if out.get("operator") in ("gt", "lt", "between", "outside_band") and value is None:
            raise ApiError("validation_failed",
                           "condition.value is required for gt, lt, between and "
                           "outside_band operators.")
        if value is not None:
            out["value"] = _number(value, "condition.value")
        else:
            out["value"] = None
    if "value_upper" in condition or not partial:
        upper = condition.get("value_upper")
        out["value_upper"] = None if upper is None else _number(upper, "condition.value_upper")
    if "sigma" in condition or not partial:
        sigma = condition.get("sigma")
        if out.get("operator") in ("deviation_gt_sigma", "anomaly") and sigma is None:
            sigma = 2.5
        out["sigma"] = None if sigma is None else _number(sigma, "condition.sigma")
    if out.get("operator") in ("between", "outside_band") and out.get("value_upper") is None:
        raise ApiError("validation_failed",
                       f"The {out.get('operator')} operator needs condition.value_upper.")
    if out.get("sigma") is not None and out["sigma"] <= 0:
        raise ApiError("validation_failed", "condition.sigma must be greater than zero.")
    return out


def _number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", f"{label} must be numeric.")
    if math.isnan(number) or math.isinf(number):
        raise ApiError("validation_failed", f"{label} must be a finite number.")
    return round(number, 6)


def _normalise_recipients(recipients: Any) -> List[str]:
    """Accept user ids, ``role:<role>`` selectors or ``all``."""
    items = analytics.as_list(recipients)
    if isinstance(recipients, str):
        items = [part.strip() for part in recipients.split(",") if part.strip()]
    cleaned: List[str] = []
    for item in items:
        token = str(item).strip()
        if not token:
            continue
        if token.lower() == "all":
            cleaned.append("all")
            continue
        if token.lower().startswith("role:"):
            role = token.split(":", 1)[1].strip().lower()
            if role not in ALL_ROLES:
                raise ApiError("validation_failed", f"Unknown recipient role selector '{token}'.")
            cleaned.append(f"role:{role}")
            continue
        if not re.fullmatch(r"[0-9a-fA-F]{24}", token):
            raise ApiError("validation_failed",
                           "recipients must be account ids, 'role:<role>' selectors or 'all'.")
        cleaned.append(token)
    return list(dict.fromkeys(cleaned))[:500]


def validate_target_variable(value: Any) -> str:
    metric = str(value or "").strip()
    if metric not in analytics.METRICS:
        raise ApiError("validation_failed",
                       f"target_variable must be one of: {', '.join(analytics.METRICS)}.")
    return metric


def create_rule(payload: Dict[str, Any], *, actor_id: str) -> Dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if len(name) < 3 or len(name) > 120:
        raise ApiError("validation_failed", "name must be between 3 and 120 characters.")
    existing_keys = {str(item) for item in alerts.distinct("rule_key",
                                                          {"document_type": RULE_MARKER})}
    condition = _normalise_condition(payload.get("condition"), partial=False)
    document = {
        "document_type": RULE_MARKER,
        "rule_key": payload.get("rule_key") or rule_key_for(name, existing_keys),
        "name": name,
        "description": str(payload.get("description") or "")[:1000],
        "is_enabled": bool(payload.get("is_enabled", True)),
        "target_variable": validate_target_variable(payload.get("target_variable")),
        "scope": _normalise_scope(payload.get("scope")),
        "condition": condition,
        "applicable_sources": _normalise_sources(payload.get("applicable_sources")),
        "severity": _severity(payload.get("severity")),
        "cooldown_minutes": _cooldown(payload.get("cooldown_minutes")),
        "recipients": _normalise_recipients(payload.get("recipients") or []),
        "created_by": str(actor_id),
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "last_evaluated_at": None,
        "last_triggered_at": None,
        "evaluation_state": {},
    }
    if not re.fullmatch(r"[0-9a-z][0-9a-z\-]{1,63}", document["rule_key"]):
        raise ApiError("validation_failed", "rule_key may contain only lowercase letters, "
                                           "digits and hyphens.")
    document["_id"] = _object_id(alerts.insert_one(document))
    return serialize(document) or {}


def update_rule(rule_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    document = _rule_document(rule_id)
    updates: Dict[str, Any] = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if len(name) < 3 or len(name) > 120:
            raise ApiError("validation_failed", "name must be between 3 and 120 characters.")
        updates["name"] = name
    if "description" in payload:
        updates["description"] = str(payload.get("description") or "")[:1000]
    if "is_enabled" in payload:
        updates["is_enabled"] = bool(payload["is_enabled"])
    if "target_variable" in payload:
        updates["target_variable"] = validate_target_variable(payload["target_variable"])
    if "scope" in payload:
        updates["scope"] = _normalise_scope(payload["scope"])
    if "condition" in payload:
        merged = dict(document.get("condition") or {})
        merged.update(payload.get("condition") or {})
        updates["condition"] = _normalise_condition(merged, partial=False)
    if "applicable_sources" in payload:
        updates["applicable_sources"] = _normalise_sources(payload["applicable_sources"])
    if "severity" in payload:
        updates["severity"] = _severity(payload["severity"])
    if "cooldown_minutes" in payload:
        updates["cooldown_minutes"] = _cooldown(payload["cooldown_minutes"])
    if "recipients" in payload:
        updates["recipients"] = _normalise_recipients(payload["recipients"])
    if not updates:
        raise ApiError("validation_failed", "No updatable rule fields were supplied.")
    updates["updated_at"] = utcnow()
    updated = alerts.update_by_id(rule_id, updates, return_document=True)
    return serialize(updated) or {}


def set_rule_enabled(rule_id: str, enabled: bool) -> Dict[str, Any]:
    _rule_document(rule_id)
    updated = alerts.update_by_id(rule_id, {"is_enabled": bool(enabled), "updated_at": utcnow()},
                                 return_document=True)
    return serialize(updated) or {}


def delete_rule(rule_id: str) -> int:
    """Delete the rule only; alerts it already fired stay for the audit trail."""
    _rule_document(rule_id)
    return alerts.delete_by_id(rule_id)


def get_rule(rule_id: str) -> Dict[str, Any]:
    return serialize(_rule_document(rule_id)) or {}


def list_rules(*, enabled_only: bool = False, page: int = 1, page_size: int = 50,
               include_recipients: bool = True) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {"document_type": RULE_MARKER}
    if enabled_only:
        criteria["is_enabled"] = True
    result = alerts.paginate(criteria, page=page, page_size=page_size,
                            sort=[("created_at", -1)])
    if not include_recipients:
        for item in result["items"]:
            item.pop("recipients", None)
            item.pop("created_by", None)
    return result


def _normalise_sources(value: Any) -> List[str]:
    sources = [str(item).strip() for item in analytics.as_list(value) if str(item).strip()]
    cleaned: List[str] = []
    for source in sources[:60]:
        token = source.lower()
        if token in SOURCE_CONTROL_TOKENS:
            cleaned.append(token)
        else:
            cleaned.append(source[:80])
    return list(dict.fromkeys(cleaned))


def _severity(value: Any) -> str:
    severity = str(value or "warning").strip().lower()
    if severity not in SEVERITIES:
        raise ApiError("validation_failed", f"severity must be one of: {', '.join(SEVERITIES)}.")
    return severity


def _cooldown(value: Any) -> int:
    if value is None:
        return max(0, env.get_int("ALERT_COOLDOWN_MINUTES", 60))
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", "cooldown_minutes must be an integer.")
    if minutes < 0 or minutes > 10080:
        raise ApiError("validation_failed", "cooldown_minutes must be between 0 and 10080.")
    return minutes


def _rule_document(rule_id: str) -> Dict[str, Any]:
    document = alerts.find_one({"_id": _object_id(rule_id), "document_type": RULE_MARKER})
    if document is None:
        raise ApiError("not_found", "No such alert rule.", status=404)
    return document


def _object_id(value: str):
    from common.repositories import to_object_id

    return to_object_id(value)


def resolve_recipients(rule: Dict[str, Any]) -> List[str]:
    selectors = rule.get("recipients") or []
    if not selectors:
        selectors = [f"role:{ADMINISTRATOR}"]
    ids: List[str] = []
    for selector in selectors:
        token = str(selector)
        if token.lower() == "all":
            criteria: Dict[str, Any] = {"is_active": True}
        elif token.lower().startswith("role:"):
            criteria = {"is_active": True, "role": token.split(":", 1)[1].strip().lower()}
        else:
            criteria = {"_id": _maybe_id(token), "is_active": True}
        for document in users.find(criteria, projection={"_id": 1}, limit=1000):
            ids.append(str(document["_id"]))
    return list(dict.fromkeys(ids))


def _maybe_id(value: str):
    from common.repositories import maybe_object_id

    return maybe_object_id(value)


def scan_window(rule: Dict[str, Any]) -> Tuple[str, str]:
    """Bounded observation window for one evaluation (ISO date strings)."""
    end = utcnow().date()
    lookback = SCAN_LOOKBACK_DAYS
    last_triggered = rule.get("last_triggered_at")
    if isinstance(last_triggered, datetime):
        # Never scan further back than twice the cooldown, but always at least a day.
        since = (end - last_triggered.astimezone(timezone.utc).date()).days + 1
        lookback = max(1, min(lookback, max(1, since * 2)))
    start = end - timedelta(days=lookback - 1)
    return start.isoformat(), end.isoformat()


def _matching_observations(rule: Dict[str, Any], *, start_date: str, end_date: str,
                          metric: str) -> List[Dict[str, Any]]:
    scope = rule.get("scope") or {}
    criteria = analytics.observation_query(
        dataset_ids=scope.get("datasets") or None,
        regions=scope.get("regions") or None,
        location_codes=scope.get("location_codes") or None,
        countries=scope.get("countries") or None,
        start_date=start_date,
        end_date=end_date,
        metric=metric,
        limit=MAX_ROWS_PER_RULE,
    )
    rows = analytics.fetch_observations(criteria)
    sources = [item for item in (rule.get("applicable_sources") or [])
               if item.lower() not in SOURCE_CONTROL_TOKENS]
    if sources:
        allowed = {str(item).strip().lower() for item in sources}
        rows = [row for row in rows if str(row.get("source_name") or "").lower() in allowed]
    return rows


def _within_scope(row: Dict[str, Any], rule: Dict[str, Any]) -> bool:
    scope = rule.get("scope") or {}
    if scope.get("regions") and str(row.get("region_code")) not in set(scope["regions"]):
        return False
    if scope.get("location_codes") and str(row.get("location_code", "")).upper() \
            not in set(scope["location_codes"]):
        return False
    if scope.get("countries") and str(row.get("country")) not in set(scope["countries"]):
        return False
    if scope.get("datasets") and str(row.get("dataset_id")) not in set(scope["datasets"]):
        return False
    return True


def _condition_matches(rule: Dict[str, Any], value: float) -> bool:
    condition = rule.get("condition") or {}
    operator = condition.get("operator")
    threshold = condition.get("value")
    if operator == "gt":
        return threshold is not None and value > float(threshold)
    if operator == "lt":
        return threshold is not None and value < float(threshold)
    if operator == "between":
        upper = condition.get("value_upper")
        return threshold is not None and upper is not None \
            and float(threshold) <= value <= float(upper)
    if operator == "outside_band":
        upper = condition.get("value_upper")
        return threshold is not None and upper is not None \
            and not float(threshold) <= value <= float(upper)
    return False


def candidates_for_rule(rule: Dict[str, Any], *, persist: bool) -> Dict[str, Any]:
    """Evaluate one rule and return real candidates (persisted or dry-run)."""
    metric = str(rule.get("target_variable") or "")
    if metric not in analytics.METRICS:
        return {"rule_id": str(rule.get("id") or rule.get("_id")), "candidates": [],
                "observations_scanned": 0, "predictions_scanned": 0,
                "state": {"error": "target_variable is not a supported metric"}}

    start_date, end_date = scan_window(rule)
    try:
        rows = _matching_observations(rule, start_date=start_date, end_date=end_date, metric=metric)
    except analytics.InsufficientData as exc:
        return {"rule_id": str(rule.get("id") or rule.get("_id")), "candidates": [],
                "observations_scanned": 0, "predictions_scanned": 0,
                "state": {"insufficient_data": exc.message,
                          "available": exc.available, "required": exc.required},
                "scanned_range": {"start": start_date, "end": end_date}}

    operator = (rule.get("condition") or {}).get("operator")
    unit = analytics.METRIC_UNITS.get(metric, "")
    candidates: List[Dict[str, Any]] = []
    baseline_note: Optional[str] = None

    if operator in ("gt", "lt", "between", "outside_band"):
        for row in rows:
            value = row.get(metric)
            if value is None:
                continue
            value = float(value)
            if not _condition_matches(rule, value):
                continue
            candidates.append(_candidate_from_row(rule, row, metric, unit, value))
    else:
        sigma = (rule.get("condition") or {}).get("sigma") or 2.5
        scope = rule.get("scope") or {}
        single_region = scope["regions"][0] if len(scope.get("regions") or []) == 1 else None
        single_location = scope["location_codes"][0] if len(scope.get("location_codes") or []) == 1 \
            else None
        result = analytics.detect_anomalies(
            rows, metric, method="statistical_baseline", threshold_sigma=float(sigma),
            region_code=single_region, location_code=single_location,
        )
        baseline_note = result.get("note")
        for anomaly in result.get("anomalies") or []:
            if not _within_scope(anomaly, rule):
                continue
            candidates.append(_candidate_from_anomaly(rule, anomaly, metric, unit, result))
        if not candidates and baseline_note:
            logger.info("Rule %s produced no candidates: %s", rule.get("rule_key"), baseline_note)

    prediction_candidates: List[Dict[str, Any]] = []
    predictions_scanned = 0
    if "predictions" in [str(item).lower() for item in (rule.get("applicable_sources") or [])]:
        prediction_result = evaluate_predictions_for_rule(rule, persist=False)
        prediction_candidates = prediction_result["candidates"]
        predictions_scanned = prediction_result["predictions_scanned"]

    candidates.extend(prediction_candidates)
    candidates.sort(key=lambda item: item["sort_key"], reverse=True)
    limited = candidates[:MAX_ALERTS_PER_RULE_RUN]

    created: List[Dict[str, Any]] = []
    deduped = 0
    for candidate in limited:
        if persist:
            outcome = _persist_candidate(rule, candidate)
            if outcome is None:
                deduped += 1
                continue
            created.append(outcome)
        else:
            created.append(candidate)

    state: Dict[str, Any] = {
        "last_run_at": utcnow(),
        "persisted": bool(persist),
        "observations_scanned": len(rows),
        "predictions_scanned": predictions_scanned,
        "matched": len(candidates),
        "returned": len(limited),
        "alerts_created": len(created) if persist else 0,
        "suppressed_by_dedupe": deduped,
        "scanned_range": {"start": start_date, "end": end_date},
        "unit": unit,
    }
    if baseline_note:
        state["note"] = baseline_note
    if not rows and not predictions_scanned:
        state["insufficient_data"] = (
            "No stored observations (or predictions) match this rule's scope and window, "
            "so nothing could be evaluated. The rule fired nothing rather than assuming a value.")
    return {"rule_id": str(rule.get("id") or rule.get("_id")),
            "rule_key": rule.get("rule_key"), "observations_scanned": len(rows),
            "predictions_scanned": predictions_scanned, "candidates": created,
            "candidate_count": len(created), "deduped": deduped, "state": state,
            "dry_run": not persist}


def _candidate_from_row(rule: Dict[str, Any], row: Dict[str, Any], metric: str, unit: str,
                       value: float) -> Dict[str, Any]:
    condition = rule.get("condition") or {}
    threshold = condition.get("value")
    if condition.get("operator") in ("between", "outside_band"):
        reference = f"{threshold}..{condition.get('value_upper')}"
    else:
        reference = threshold
    return {
        "sort_key": abs(value - float(threshold or 0)),
        "target_variable": metric,
        "unit": unit,
        "value": round(value, 4),
        "threshold_or_baseline": reference,
        "deviation_score": None,
        "method": "absolute_threshold",
        "region_code": row.get("region_code"),
        "location_code": row.get("location_code"),
        "location_name": row.get("location_name"),
        "observation_date": row.get("observation_date"),
        "dataset_id": str(row.get("dataset_id")) if row.get("dataset_id") else None,
        "prediction_id": None,
        "source": "rule_engine",
        "explanation": (
            f"{analytics.METRIC_LABELS.get(metric, metric)} measured {round(value, 3)}{unit} at "
            f"{row.get('location_name') or row.get('location_code') or 'the monitored point'} on "
            f"{row.get('observation_date')}, which satisfies the '{condition.get('operator')}' "
            f"condition against {reference}{unit if unit else ''} "
            f"(source {row.get('source_name') or 'unrecorded'})."
        ),
        "metadata": {"source_name": row.get("source_name"),
                     "matched_by": "observation_threshold"},
    }


def _candidate_from_anomaly(rule: Dict[str, Any], anomaly: Dict[str, Any], metric: str,
                            unit: str, result: Dict[str, Any]) -> Dict[str, Any]:
    baseline = anomaly.get("baseline") or {}
    reference = baseline.get("mean")
    if reference is None:
        reference = baseline.get("reference")
    return {
        "sort_key": abs(float(anomaly.get("deviation_score") or 0)),
        "target_variable": metric,
        "unit": unit,
        "value": anomaly.get("value"),
        "threshold_or_baseline": reference,
        "deviation_score": anomaly.get("deviation_score"),
        "method": anomaly.get("method") or "statistical_baseline",
        "region_code": anomaly.get("region_code"),
        "location_code": anomaly.get("location_code"),
        "location_name": anomaly.get("location_name"),
        "observation_date": anomaly.get("observation_date"),
        "dataset_id": anomaly.get("dataset_id"),
        "prediction_id": None,
        "source": "anomaly_scan",
        "explanation": anomaly.get("explanation"),
        "metadata": {"sigma": (rule.get("condition") or {}).get("sigma"),
                     "direction": anomaly.get("direction"),
                     "baseline_sample_size": baseline.get("sample_size",
                                                          baseline.get("rows_evaluated")),
                     "anomaly_severity": anomaly.get("severity"),
                     "evaluated_observations": result.get("evaluated_observations")},
    }


PREDICTION_VALUE_FIELDS = ("predicted_value", "value", "forecast_value", "prediction",
                           "mean_prediction")


def evaluate_predictions_for_rule(rule: Dict[str, Any], *, persist: bool = True) -> Dict[str, Any]:
    """Scan stored forecast documents for a rule; nothing is modelled here."""
    metric = str(rule.get("target_variable") or "")
    scope = rule.get("scope") or {}
    criteria: Dict[str, Any] = {}
    if metric:
        criteria["target_variable"] = metric
    if scope.get("regions"):
        criteria["region_code"] = {"$in": scope["regions"]}
    if scope.get("location_codes"):
        criteria["location_code"] = {"$in": scope["location_codes"]}
    if scope.get("datasets"):
        criteria["dataset_id"] = {"$in": [_maybe_id(item) for item in scope["datasets"]]}

    documents = predictions_repo.find(criteria, sort=[("forecast_date", -1)],
                                     limit=min(2000, MAX_ROWS_PER_RULE))
    candidates: List[Dict[str, Any]] = []
    for document in documents:
        field, value = _prediction_value(document)
        if value is None:
            continue
        if not _condition_matches(rule, value):
            continue
        forecast_date = _iso_day(document.get("forecast_date") or document.get("generated_at"))
        candidates.append({
            "sort_key": abs(value - float((rule.get("condition") or {}).get("value") or 0)),
            "target_variable": metric,
            "unit": analytics.METRIC_UNITS.get(metric, str(document.get("unit") or "")),
            "value": round(value, 4),
            "threshold_or_baseline": (rule.get("condition") or {}).get("value"),
            "deviation_score": None,
            "method": "model_forecast",
            "region_code": document.get("region_code"),
            "location_code": document.get("location_code"),
            "location_name": document.get("location_name"),
            "observation_date": forecast_date,
            "dataset_id": str(document.get("dataset_id")) if document.get("dataset_id") else None,
            "prediction_id": str(document["_id"]),
            "source": "prediction",
            "explanation": (
                f"The stored forecast expects {analytics.METRIC_LABELS.get(metric, metric)} of "
                f"{round(value, 3)}{analytics.METRIC_UNITS.get(metric, '')} on {forecast_date} "
                f"for {document.get('location_name') or document.get('region_code') or 'the scope'}, "
                f"which satisfies the '{(rule.get('condition') or {}).get('operator')}' condition. "
                "This is a model output, not a measured observation."
            ),
            "metadata": {"model_id": str(document.get("model_id")) if document.get("model_id")
                         else None,
                         "value_field": field,
                         "confidence": document.get("confidence"),
                         "matched_by": "prediction_threshold"},
        })

    limited = candidates[:MAX_ALERTS_PER_RULE_RUN]
    created: List[Dict[str, Any]] = []
    deduped = 0
    for candidate in limited:
        if persist:
            outcome = _persist_candidate(rule, candidate)
            if outcome is None:
                deduped += 1
                continue
            created.append(outcome)
        else:
            created.append(candidate)
    return {"rule_key": rule.get("rule_key"), "predictions_scanned": len(documents),
            "candidates": created, "deduped": deduped, "dry_run": not persist,
            "note": None if documents else "No stored prediction documents matched this scope."}


def _prediction_value(document: Dict[str, Any]) -> Tuple[Optional[str], Optional[float]]:
    for field in PREDICTION_VALUE_FIELDS:
        value = document.get(field)
        if value is None:
            continue
        try:
            return field, float(value)
        except (TypeError, ValueError):
            continue
    return None, None


def _iso_day(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, str):
        return value[:10]
    return None


def alert_exists(rule_key: str, observation_date: Optional[str]) -> bool:
    """Dedupe contract: one alert per rule per observation date."""
    if not rule_key:
        return False
    criteria = {"document_type": {"$ne": RULE_MARKER}, "rule_key": rule_key}
    if observation_date:
        criteria["observation_date"] = observation_date
    return alerts.exists(criteria)


def _persist_candidate(rule: Dict[str, Any], candidate: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rule_key = str(rule.get("rule_key") or "")
    observation_date = candidate.get("observation_date")
    if alert_exists(rule_key, observation_date):
        return None
    now = utcnow()
    document = {
        "rule_id": str(rule.get("id") or rule.get("_id")),
        "rule_key": rule_key,
        "name": rule.get("name"),
        "severity": rule.get("severity") or "warning",
        "status": "open",
        "triggered_at": now,
        "target_variable": candidate.get("target_variable"),
        "unit": candidate.get("unit"),
        "value": candidate.get("value"),
        "threshold_or_baseline": candidate.get("threshold_or_baseline"),
        "deviation_score": candidate.get("deviation_score"),
        "method": candidate.get("method"),
        "region_code": candidate.get("region_code"),
        "location_code": candidate.get("location_code"),
        "location_name": candidate.get("location_name"),
        "observation_date": observation_date,
        "dataset_id": candidate.get("dataset_id"),
        "prediction_id": candidate.get("prediction_id"),
        "explanation": candidate.get("explanation"),
        "acknowledged_by": None,
        "acknowledged_at": None,
        "resolved_by": None,
        "resolved_at": None,
        "resolution_note": None,
        "notification_ids": [],
        "source": candidate.get("source") if candidate.get("source") in ALERT_SOURCES
        else "rule_engine",
        "created_at": now,
        "metadata": candidate.get("metadata") or {},
    }
    alert_id = alerts.insert_one(document)

    notification_ids = notifications.queue_for_recipients(
        resolve_recipients(rule),
        type="alert",
        title=f"{(rule.get('severity') or 'warning').upper()} alert: {rule.get('name')}",
        body=str(candidate.get("explanation") or "")[:4000],
        dedupe_key=f"alert:{rule_key}:{observation_date}",
        related_resource={"type": "alert", "id": alert_id},
        severity=rule.get("severity"),
        cooldown_minutes=_cooldown(rule.get("cooldown_minutes")),
    )
    if notification_ids:
        alerts.update_by_id(alert_id, {"notification_ids": notification_ids})
    return serialize(alerts.find_one({"_id": _object_id(alert_id)})) or {"id": alert_id}


def in_cooldown(rule: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    last_triggered = rule.get("last_triggered_at")
    if not isinstance(last_triggered, datetime):
        return False
    minutes = _cooldown(rule.get("cooldown_minutes"))
    if minutes <= 0:
        return False
    reference = now or utcnow()
    if last_triggered.tzinfo is None:
        last_triggered = last_triggered.replace(tzinfo=timezone.utc)
    return reference - last_triggered < timedelta(minutes=minutes)


def evaluate_rule(rule: Dict[str, Any], *, persist: bool = True) -> Dict[str, Any]:
    """Full single-rule path, including cooldown and evaluation-state recording."""
    if persist and in_cooldown(rule):
        state = {
            "last_run_at": utcnow(),
            "skipped": "cooldown",
            "cooldown_minutes": _cooldown(rule.get("cooldown_minutes")),
            "last_triggered_at": rule.get("last_triggered_at"),
        }
        _record_evaluation_state(str(rule.get("id") or rule.get("_id")), state, triggered=False)
        return {"rule_key": rule.get("rule_key"), "skipped": "cooldown", "candidates": [],
                "candidate_count": 0, "alerts_created": 0, "notifications_queued": 0,
                "state": state, "dry_run": False}

    result = candidates_for_rule(rule, persist=persist)
    created = [item for item in result["candidates"] if item.get("id")]
    if persist:
        _record_evaluation_state(str(rule.get("id") or rule.get("_id")), result["state"],
                                 triggered=bool(created))
    result["alerts_created"] = len(created)
    result["notifications_queued"] = sum(len(item.get("notification_ids") or []) for item in created)
    return result


def _record_evaluation_state(rule_id: str, state: Dict[str, Any], *, triggered: bool) -> None:
    updates: Dict[str, Any] = {
        "last_evaluated_at": utcnow(),
        "evaluation_state": {key: _json_safe(value) for key, value in state.items()},
    }
    if triggered:
        updates["last_triggered_at"] = utcnow()
    alerts.update_by_id(rule_id, updates)


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in list(value)[:20]]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in list(value.items())[:20]}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) or len(value) <= 200 else value[:200]
    return str(value)[:200]


def evaluate_enabled_rules() -> Dict[str, Any]:
    """Scheduled entry point: evaluate every enabled rule and return the counts."""
    started = utcnow()
    rules = alerts.find({"document_type": RULE_MARKER, "is_enabled": True},
                       sort=[("created_at", 1)], limit=200)
    summary = {
        "rules_evaluated": 0,
        "rules_skipped_cooldown": 0,
        "rules_with_errors": 0,
        "observations_scanned": 0,
        "predictions_scanned": 0,
        "alerts_created": 0,
        "notifications_queued": 0,
        "suppressed_by_dedupe": 0,
        "errors": [],
    }
    for rule in (serialize(item) or {} for item in rules):
        rule["id"] = rule.get("id") or str(rule.get("_id"))
        summary["rules_evaluated"] += 1
        try:
            outcome = evaluate_rule(rule, persist=True)
        except Exception as exc:  # noqa: BLE001 - one bad rule must not stop the sweep
            summary["rules_with_errors"] += 1
            summary["errors"].append({"rule_key": rule.get("rule_key"),
                                      "error": type(exc).__name__})
            logger.error("Rule %s failed: %s", rule.get("rule_key"), exc)
            continue
        if outcome.get("skipped") == "cooldown":
            summary["rules_skipped_cooldown"] += 1
        summary["observations_scanned"] += int(outcome.get("state", {}).get(
            "observations_scanned", 0) or 0)
        summary["predictions_scanned"] += int(outcome.get("state", {}).get(
            "predictions_scanned", 0) or 0)
        summary["alerts_created"] += int(outcome.get("alerts_created", 0) or 0)
        summary["notifications_queued"] += int(outcome.get("notifications_queued", 0) or 0)
        summary["suppressed_by_dedupe"] += int(outcome.get("state", {}).get(
            "suppressed_by_dedupe", 0) or 0)

    summary["attempted"] = len(rules)
    summary["duration_ms"] = round((utcnow() - started).total_seconds() * 1000, 2)
    summary["finished_at"] = utcnow()
    try:
        get_database().flush_if_dirty()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Post-evaluation flush failed: %s", exc)
    return summary


def evaluate_rule_by_id(rule_id: str) -> Dict[str, Any]:
    document = _rule_document(rule_id)
    return evaluate_rule(serialize(document) or {})


def dry_run_rule(rule_id: str) -> Dict[str, Any]:
    """``POST /rules/<id>/test`` - would-fire candidates, nothing persisted."""
    document = _rule_document(rule_id)
    rule = serialize(document) or {}
    result = candidates_for_rule(rule, persist=False)
    result["recipients_resolved"] = len(resolve_recipients(rule))
    result["in_cooldown"] = in_cooldown(rule)
    result["would_persist"] = len([item for item in result["candidates"]
                                  if not alert_exists(str(rule.get("rule_key")),
                                                      item.get("observation_date"))])
    return result


def list_alerts(*, status_filter: Optional[str], severity: Optional[str],
                region: Optional[str], rule_key: Optional[str],
                start_date: Optional[str], end_date: Optional[str],
                page: int, page_size: int) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {"document_type": {"$ne": RULE_MARKER}}
    if status_filter:
        criteria["status"] = status_filter
    if severity:
        criteria["severity"] = severity
    if region:
        criteria["region_code"] = region
    if rule_key:
        criteria["rule_key"] = rule_key
    window: Dict[str, Any] = {}
    if start_date:
        window["$gte"] = start_date
    if end_date:
        window["$lte"] = end_date
    if window:
        criteria["observation_date"] = window
    return alerts.paginate(criteria, page=page, page_size=page_size,
                           sort=[("triggered_at", -1)])


def get_alert(alert_id: str) -> Dict[str, Any]:
    document = alerts.find_one({"_id": _object_id(alert_id), "document_type": {"$ne": RULE_MARKER}})
    if document is None:
        raise ApiError("not_found", "No such alert.", status=404)
    result = serialize(document) or {}
    result["rule"] = _rule_summary(result.get("rule_id"))
    return result


def _rule_summary(rule_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not rule_id:
        return None
    try:
        document = alerts.find_one({"_id": _object_id(str(rule_id)), "document_type": RULE_MARKER})
    except Exception:  # noqa: BLE001 - a malformed rule id must not break the alert view
        return None
    if document is None:
        return None
    return {"id": str(document["_id"]), "rule_key": document.get("rule_key"),
            "name": document.get("name"), "condition": document.get("condition"),
            "scope": document.get("scope"), "is_enabled": document.get("is_enabled")}


def acknowledge_alert(alert_id: str, *, user_id: str, role: str) -> Dict[str, Any]:
    document = alerts.find_one({"_id": _object_id(alert_id),
                               "document_type": {"$ne": RULE_MARKER}})
    if document is None:
        raise ApiError("not_found", "No such alert.", status=404)
    if document.get("status") in ("resolved", "dismissed"):
        raise ApiError("conflict", f"This alert is already {document['status']}.", status=409)
    updates: Dict[str, Any] = {"acknowledged_by": str(user_id),
                               "acknowledged_by_role": role,
                               "acknowledged_at": utcnow()}
    if document.get("status") == "open":
        updates["status"] = "acknowledged"
    updated = alerts.update_by_id(alert_id, updates, return_document=True)
    return serialize(updated) or {}


def resolve_alert(alert_id: str, *, user_id: str, role: str, note: Optional[str],
                  status: str = "resolved") -> Dict[str, Any]:
    if status not in ("resolved", "dismissed"):
        raise ApiError("validation_failed", "An alert is closed as resolved or dismissed.")
    document = alerts.find_one({"_id": _object_id(alert_id),
                               "document_type": {"$ne": RULE_MARKER}})
    if document is None:
        raise ApiError("not_found", "No such alert.", status=404)
    if not (note or "").strip():
        raise ApiError("validation_failed",
                       "A resolution note is required so the closure is auditable.")
    updated = alerts.update_by_id(alert_id, {
        "status": status,
        "resolved_by": str(user_id),
        "resolved_by_role": role,
        "resolved_at": utcnow(),
        "resolution_note": str(note)[:2000],
        "acknowledged_at": document.get("acknowledged_at") or utcnow(),
        "acknowledged_by": document.get("acknowledged_by") or str(user_id),
    }, return_document=True)
    return serialize(updated) or {}


def summary() -> Dict[str, Any]:
    """Counts by severity/status/region plus the newest alert - all real queries."""
    repository = alerts
    criteria = {"document_type": {"$ne": RULE_MARKER}}
    by_status = {status: repository.count({**criteria, "status": status})
                 for status in ALERT_STATUSES}
    by_severity = {severity: repository.count({**criteria, "severity": severity})
                   for severity in SEVERITIES}
    open_regions = repository.find({"document_type": {"$ne": RULE_MARKER}, "status": "open"},
                                  projection={"region_code": 1}, limit=2000)
    counts: Dict[str, int] = {}
    for document in open_regions:
        key = str(document.get("region_code") or "unattributed")
        counts[key] = counts.get(key, 0) + 1
    newest = repository.find(criteria, sort=[("triggered_at", -1)], limit=1)
    evaluated = repository.find({"document_type": RULE_MARKER},
                                projection={"last_evaluated_at": 1}, limit=500)
    stamps = [item.get("last_evaluated_at") for item in evaluated if item.get("last_evaluated_at")]
    return {
        "total": repository.count(criteria),
        # Flat keys are the shape the web client binds to; they are the same counts.
        "open": by_status.get("open", 0),
        "acknowledged": by_status.get("acknowledged", 0),
        "resolved": by_status.get("resolved", 0),
        "dismissed": by_status.get("dismissed", 0),
        "by_status": by_status,
        "by_severity": by_severity,
        "by_region": [{"region_code": code, "count": value}
                      for code, value in sorted(counts.items(), key=lambda item: item[1],
                                                reverse=True)[:20]],
        "open_by_region": dict(sorted(counts.items(), key=lambda item: item[1], reverse=True)[:20]),
        "open_by_region_sampled": len(open_regions) >= 2000,
        "newest": serialize(newest[0]) if newest else None,
        "newest_alert": serialize(newest[0]) if newest else None,
        "rule_count": repository.count({"document_type": RULE_MARKER}),
        "rules_enabled": repository.count({"document_type": RULE_MARKER, "is_enabled": True}),
        "enabled_rule_count": repository.count({"document_type": RULE_MARKER, "is_enabled": True}),
        "last_evaluation": max(stamps) if stamps else None,
        "note": "Counts are computed from stored alert documents; nothing is estimated.",
    }
