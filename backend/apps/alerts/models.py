"""Pure dict builders and normalisers for alert rules, events and notifications.

Nothing here touches MongoDB: every function accepts plain values and returns a
BSON-ready document, query or diff so evaluation and persistence stay independent.

Observation shape is fixed by ``data-pipeline/schemas/observation.schema.json``:
measurements live at ``metrics.<variable>.value`` / ``.unit`` and an absent
measurement is *omitted*, never stored as zero. ``measurement()`` therefore returns
``None`` when the metric is missing, which is what keeps evaluation honest.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from common.db import now_utc
from common.security import sanitize_text

CONDITIONS = frozenset(
    {"above", "below", "anomaly", "trend_change", "forecast_above", "forecast_below"}
)
COMPARISON_CONDITIONS = frozenset({"above", "below", "forecast_above", "forecast_below"})
FORECAST_CONDITIONS = frozenset({"forecast_above", "forecast_below"})
BASELINE_CONDITIONS = frozenset({"anomaly", "trend_change"})
ANALYTICS_SUMMARY_TYPE = "anomaly_features"
SEVERITIES = ("info", "warning", "severe")
EVENT_STATUSES = ("open", "acknowledged", "resolved")

_SEASONS = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring", 4: "spring", 5: "spring",
    6: "summer", 7: "summer", 8: "summer",
    9: "autumn", 10: "autumn", 11: "autumn",
}


def season_for(moment: datetime) -> str:
    """Meteorological season (northern-hemisphere convention, stated in explanations)."""
    return _SEASONS[moment.month]


def _clean_str(value: Any, max_length: int) -> Optional[str]:
    if value in (None, ""):
        return None
    return sanitize_text(value, max_length=max_length)


def normalize_scope(raw: Any) -> dict[str, Any]:
    """Keep only the supported scope keys, with validated types."""
    scope = raw if isinstance(raw, dict) else {}
    clean: dict[str, Any] = {}
    region = _clean_str(scope.get("region"), 80)
    if region:
        clean["region"] = region
    station = _clean_str(scope.get("station_id"), 64)
    if station:
        clean["station_id"] = station
    bbox = scope.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            west, south, east, north = (float(v) for v in bbox)
        except (TypeError, ValueError):
            south = north = west = east = 0.0
        if -180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90:
            clean["bbox"] = [west, south, east, north]
    return clean


def _clean_recipients(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    for item in raw:
        text = _clean_str(item, 40)
        if text and text not in seen:
            seen.append(text)
    return seen[:25]


def _clean_channels(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return ["in_app"]
    channels = [str(c).strip().lower() for c in raw if str(c).strip().lower() in ("in_app", "email")]
    return list(dict.fromkeys(channels)) or ["in_app"]


def rule_document(payload: Any, created_by: str) -> dict[str, Any]:
    """Build an insert-ready ``alert_rules`` document from a validated request model."""
    now = now_utc()
    return {
        "name": sanitize_text(payload.name, max_length=120),
        "description": _clean_str(payload.description, 1000) or "",
        "enabled": bool(payload.enabled),
        "target_variable": payload.target_variable,
        "condition": payload.condition,
        "threshold": float(payload.threshold) if payload.threshold is not None else None,
        "geographic_scope": normalize_scope(payload.geographic_scope),
        "data_source": _clean_str(payload.data_source, 60),
        "severity": payload.severity,
        "cooldown_minutes": int(payload.cooldown_minutes),
        "recipients": _clean_recipients(payload.recipients),
        "notify_channels": _clean_channels(payload.notify_channels),
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "last_triggered_at": None,
        "trigger_count": 0,
        "last_evaluation": None,
    }


def rule_changes(payload: Any) -> dict[str, Any]:
    """Build the ``$set`` fragment for a rule update from the same request model."""
    changes: dict[str, Any] = {
        "name": sanitize_text(payload.name, max_length=120),
        "description": _clean_str(payload.description, 1000) or "",
        "enabled": bool(payload.enabled),
        "target_variable": payload.target_variable,
        "condition": payload.condition,
        "threshold": float(payload.threshold) if payload.threshold is not None else None,
        "geographic_scope": normalize_scope(payload.geographic_scope),
        "data_source": _clean_str(payload.data_source, 60),
        "severity": payload.severity,
        "cooldown_minutes": int(payload.cooldown_minutes),
        "recipients": _clean_recipients(payload.recipients),
        "notify_channels": _clean_channels(payload.notify_channels),
        "updated_at": now_utc(),
    }
    return changes


def measurement(observation: dict, variable: str) -> Optional[tuple[float, str]]:
    """Return ``(value, unit)`` only when the measurement genuinely exists."""
    metrics = observation.get("metrics")
    if not isinstance(metrics, dict):
        return None
    entry = metrics.get(variable)
    if not isinstance(entry, dict):
        return None
    value = entry.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    unit = _clean_str(entry.get("unit"), 24) or "unknown"
    return float(value), unit


def is_rejected(observation: dict) -> bool:
    quality = observation.get("quality")
    return isinstance(quality, dict) and quality.get("status") == "rejected"


def observation_match(rule: dict, since: datetime, until: datetime) -> dict[str, Any]:
    """Indexed ``$match`` for a bounded recent window: time first, then scope.

    Only documents that actually carry the target measurement are considered, so a
    missing metric can never be compared against a threshold.
    """
    match: dict[str, Any] = {
        "timestamp": {"$gte": since, "$lte": until},
        f"metrics.{rule['target_variable']}.value": {"$type": ["double", "int", "decimal"]},
    }
    scope = rule.get("geographic_scope") or {}
    if scope.get("region"):
        match["location.region"] = scope["region"]
    if scope.get("station_id"):
        match["$or"] = [
            {"location.station_id": scope["station_id"]},
            {"source.station_id": scope["station_id"]},
        ]
    bbox = scope.get("bbox")
    if bbox:
        west, south, east, north = bbox
        match["location.longitude"] = {"$gte": west, "$lte": east}
        match["location.latitude"] = {"$gte": south, "$lte": north}
    if rule.get("data_source"):
        match["source.provider"] = rule["data_source"]
    return match


def observation_pipeline(match: dict[str, Any], variable: str, limit: int) -> list[dict]:
    """Project just the fields evaluation needs, newest first, hard-bounded."""
    return [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {"$limit": max(10, int(limit))},
        {"$project": {
            "timestamp": 1,
            "granularity": 1,
            "dataset_id": 1,
            "dedupe_key": 1,
            "metrics": 1,
            "location.region": 1,
            "location.station_id": 1,
            "location.latitude": 1,
            "location.longitude": 1,
            "source.provider": 1,
            "quality.status": 1,
            "value": f"$metrics.{variable}.value",
            "unit": f"$metrics.{variable}.unit",
        }},
    ]


def scope_label(scope: dict[str, Any]) -> str:
    if scope.get("region"):
        return str(scope["region"])
    if scope.get("station_id"):
        return f"station:{scope['station_id']}"
    if scope.get("bbox"):
        return "bbox:" + ",".join(f"{v:g}" for v in scope["bbox"])
    return "global"


def dedupe_key(rule_id: Any, scope_value: str, variable: str, moment: datetime) -> str:
    """One event per (rule, scope, variable, UTC day) - enforced by the unique index."""
    day = moment.astimezone(timezone.utc).strftime("%Y-%m-%d")
    joined = f"rule:{rule_id}|scope:{scope_value}|variable:{variable}|day:{day}"
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()


def event_document(*, rule: dict, rule_id: str, dedupe: str, observation: dict,
                   value: float, unit: str, baseline: Optional[float],
                   explanation: str, severity: str, detected_at: datetime,
                   source_kind: str) -> dict[str, Any]:
    """`source_kind` is ``observation`` or ``prediction``; reference holds real ids/values."""
    scope = rule.get("geographic_scope") or {}
    location = observation.get("location") if isinstance(observation.get("location"), dict) else {}
    reference_id = _clean_str(str(observation.get("_id", "")), 40) or None
    reference = {
        "kind": source_kind,
        "observation_id": reference_id if source_kind == "observation" else None,
        "prediction_id": reference_id if source_kind == "prediction" else None,
        "dataset_id": _clean_str(observation.get("dataset_id"), 64),
        "dedupe_key": _clean_str(observation.get("dedupe_key"), 128),
        "timestamp": observation.get("timestamp"),
        "granularity": _clean_str(observation.get("granularity"), 24),
        "region": _clean_str(location.get("region"), 80),
        "station_id": _clean_str(location.get("station_id") or observation.get("station_id"), 64),
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "variable": rule["target_variable"],
        "value": value,
        "unit": unit,
        "quality_status": _clean_str(
            (observation.get("quality") or {}).get("status") if isinstance(observation.get("quality"), dict) else None,
            24,
        ),
        "model_ref": _clean_str(observation.get("model_ref"), 120),
    }
    # `detected_at` is when the condition was observed; `recorded` is when we wrote it.
    recorded = now_utc()
    return {
        "rule_id": rule_id,
        "rule_name": rule.get("name", ""),
        "dedupe_key": dedupe,
        "severity": severity if severity in SEVERITIES else "warning",
        "status": "open",
        "detected_at": detected_at,
        "description": f"{rule.get('name', '')}: {explanation}"[:1000],
        "condition": rule.get("condition"),
        "target_variable": rule["target_variable"],
        "scope_label": scope.get("region") or scope_label(scope),
        "reference": reference,
        "comparison": {
            "observed_value": value,
            "unit": unit,
            "threshold": rule.get("threshold"),
            "baseline": baseline,
            "deviation": round(value - baseline, 6) if baseline is not None else None,
            "basis": "threshold" if baseline is None else "baseline",
        },
        "explanation": explanation[:1500],
        "acknowledged_by": None,
        "acknowledged_at": None,
        "resolved_by": None,
        "resolved_at": None,
        "notification_ids": [],
        "audit": [{"at": recorded, "actor": "system", "action": "created",
                   "reason": "alert evaluation matched the rule condition"}],
        "created_at": recorded,
        "updated_at": recorded,
    }


def explanation_for(rule: dict, value: float, unit: str, scope_value: str,
                    moment: datetime, baseline: Optional[float]) -> str:
    condition = rule.get("condition")
    threshold = rule.get("threshold")
    season = season_for(moment)
    when = moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = rule["target_variable"]
    if baseline is not None:
        delta = value - baseline
        direction = "above" if delta >= 0 else "below"
        return (
            f"{subject} measured {value:g} {unit} in {scope_value} at {when}; baseline "
            f"{baseline:g} {unit} -> {abs(delta):g} {unit} {direction} baseline "
            f"(season: {season}, northern-hemisphere meteorological convention)."
        )
    if condition in ("forecast_above", "forecast_below"):
        relation = "exceeds" if condition == "forecast_above" else "falls below"
        return (
            f"Forecast {subject} of {value:g} {unit} for {scope_value} at {when} {relation} "
            f"the configured threshold {threshold:g} {unit} (season: {season})."
        )
    relation = "is at or above" if str(condition) == "above" else "is at or below"
    return (
        f"{subject} {relation} threshold: {value:g} {unit} vs {threshold:g} {unit} in "
        f"{scope_value} at {when} (season: {season})."
    )


def cooldown_blocked(rule: dict, moment: datetime, last_event_at: Optional[datetime]) -> Optional[str]:
    """Return the suppression reason when the rule is still inside its quiet period."""
    anchors = [stamp for stamp in (rule.get("last_triggered_at"), last_event_at) if stamp]
    if not anchors:
        return None
    newest = max(anchors)
    quiet = int(rule.get("cooldown_minutes") or 0)
    if quiet <= 0:
        return None
    newest = newest if newest.tzinfo else newest.replace(tzinfo=timezone.utc)
    elapsed_minutes = max(0.0, (moment - newest.astimezone(timezone.utc)).total_seconds() / 60.0)
    if elapsed_minutes < quiet:
        return (
            f"cooldown active: {elapsed_minutes:.0f} min since last trigger, "
            f"{quiet} min configured"
        )
    return None
