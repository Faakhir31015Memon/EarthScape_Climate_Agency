"""Alert rule evaluation against real climate data.

Dedupe and cooldown policy (authoritative)
------------------------------------------
1. **One event per rule + scope + variable + UTC day.** ``alert_events.dedupe_key`` is
   ``sha1("rule:<id>|scope:<region-or-station>|variable:<v>|day:<YYYY-MM-DD>")`` and the
   migration installs a *unique* index on it. Every evaluation pass attempts the insert
   and treats ``DuplicateKeyError`` as "already alerted", so a 5-minute cron can never
   spam the same condition within a day - no read-then-write race is involved.
2. **``cooldown_minutes`` is a quiet period on the rule.** Before the insert is
   attempted, ``models.cooldown_blocked()`` compares the candidate timestamp against the
   newest of ``rule.last_triggered_at`` and the most recent event for that rule. While
   inside the window the match is reported as ``suppressed`` (never silently dropped) so
   an operator can see the condition fired but was muted. This also spans midnight,
   which the day-key alone would not.
3. A suppressed or deduped match still refreshes ``rule.last_evaluation`` so the UI can
   prove the rule was actually checked.

Data sources
------------
* ``above`` / ``below``: recent ``climate_observations`` inside a bounded lookback
  window, read through an indexed ``$match`` on ``timestamp`` plus scope. Only documents
  that literally carry ``metrics.<variable>.value`` are considered; a missing measurement
  is never coerced to zero (see ``data-pipeline/schemas/observation.schema.json``).
* ``forecast_above`` / ``forecast_below``: the newest ``predictions`` documents for the
  rule's variable/region.
* ``anomaly`` / ``trend_change``: require a pre-computed baseline. The ML app may publish
  ``analytics_summaries`` documents with ``summary_type = "anomaly_features"``; when one
  exists its real statistics are used. When it does not, the result is an explicit
  ``insufficient_baseline`` note - anomalies are never invented here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pymongo.errors import DuplicateKeyError

from apps.alerts import models, notifier
from common.db import aggregate, collection, find, find_one, insert_one, now_utc, oid, update_one
from common.env import ENV
from common.logging_config import get_logger

logger = get_logger("earthscape.alerts.evaluation")

LOOKBACK_HOURS = ENV.integer("ALERT_LOOKBACK_HOURS", 72)
SCAN_LIMIT = ENV.integer("ALERT_SCAN_LIMIT", 400)
PREDICTION_LIMIT = ENV.integer("ALERT_PREDICTION_LIMIT", 50)
SUMMARY_LIMIT = ENV.integer("ALERT_SUMMARY_LIMIT", 20)
MAX_TIME_MS = ENV.integer("ALERT_MAX_TIME_MS", 10000)

DEFAULT_SIGMA = 3.0


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _aware(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _recent_event_stamp(rule_id: str) -> Optional[datetime]:
    rows = find("alert_events", {"rule_id": rule_id}, sort=[("detected_at", -1)], limit=1)
    return _aware(rows[0].get("detected_at")) if rows else None


def _already_alerted(dedupe: str) -> bool:
    return find_one("alert_events", {"dedupe_key": dedupe}, {"_id": 1}) is not None


def _persist_event(event: dict) -> tuple[Optional[dict], str]:
    """Insert honouring the unique dedupe index; returns (event, 'created'|'deduped')."""
    try:
        event["_id"] = oid(insert_one("alert_events", event))
        return event, "created"
    except DuplicateKeyError:
        return None, "deduped"


def _dispatch(event: dict, rule: dict) -> dict[str, Any]:
    try:
        return notifier.notify_for_event(event, rule)
    except Exception as exc:  # an alert that fired must stay visible even if notify fails
        logger.error("notification dispatch failed: %s", type(exc).__name__, exc_info=True)
        update_one("alert_events", {"_id": event["_id"]}, {"$push": {"audit": {
            "at": now_utc(), "actor": "system", "action": "notification_failed",
            "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}}})
        return {"notification_ids": [], "error": type(exc).__name__}


def _mark_triggered(rule_id: str, moment: datetime) -> None:
    update_one("alert_rules", {"_id": oid(rule_id)},
               {"$inc": {"trigger_count": 1},
                "$set": {"last_triggered_at": moment, "last_evaluation": moment, "updated_at": moment}})


def _touch_evaluation(rule_id: str, moment: datetime) -> None:
    update_one("alert_rules", {"_id": oid(rule_id)},
               {"$set": {"last_evaluation": moment, "updated_at": moment}})


def _bucket_by_scope(rule: dict, documents: list[dict]) -> dict[str, list[dict]]:
    """Newest-first documents grouped by the alert scope they belong to."""
    scope = rule.get("geographic_scope") or {}
    variable = rule["target_variable"]
    buckets: dict[str, list[dict]] = {}
    for doc in documents:
        if models.is_rejected(doc):
            continue
        reading = models.measurement(doc, variable)
        if reading is None:
            continue
        location = doc.get("location") if isinstance(doc.get("location"), dict) else {}
        if scope.get("region"):
            key = str(scope["region"])
        elif scope.get("station_id"):
            key = f"station:{scope['station_id']}"
        else:
            key = str(location.get("region") or location.get("station_id")
                      or f"cell:{location.get('latitude')},{location.get('longitude')}")
        entry = {"doc": doc, "value": reading[0], "unit": reading[1],
                 "timestamp": _aware(doc.get("timestamp"))}
        buckets.setdefault(key, []).append(entry)
    return buckets


def _open_scoped_event(rule: dict, rule_id: str, *, scope_value: str, value: float, unit: str,
                       source: dict, source_kind: str, baseline: Optional[float],
                       now: datetime) -> tuple[dict[str, Any], Optional[dict]]:
    """Apply dedupe + cooldown, then insert and notify. Returns (outcome, event|None)."""
    timestamp = _aware(source.get("timestamp")) or now
    dedupe = models.dedupe_key(rule_id, scope_value, rule["target_variable"], timestamp)
    if _already_alerted(dedupe):
        return {"state": "deduped", "scope": scope_value}, None
    blocked = models.cooldown_blocked(rule, timestamp, _recent_event_stamp(rule_id))
    if blocked:
        return {"state": "suppressed", "scope": scope_value, "reason": blocked}, None
    explanation = models.explanation_for(rule, value, unit, scope_value, timestamp, baseline)
    event = models.event_document(
        rule=rule, rule_id=rule_id, dedupe=dedupe, observation=source, value=value, unit=unit,
        baseline=baseline, explanation=explanation, severity=rule.get("severity", "warning"),
        detected_at=timestamp, source_kind=source_kind,
    )
    stored, outcome = _persist_event(event)
    if outcome != "created" or not stored:
        return {"state": "deduped", "scope": scope_value}, None
    dispatch = _dispatch(stored, rule)
    _mark_triggered(rule_id, timestamp)
    return {"state": "created", "scope": scope_value, "value": value, "unit": unit,
            "event_id": str(stored["_id"]), "notifications": dispatch}, stored


def _evaluate_comparison(rule: dict, rule_id: str, now: datetime, *, mode: str) -> dict[str, Any]:
    threshold = rule.get("threshold")
    if threshold is None:
        return {"status": "misconfigured",
                "notes": ["threshold is required for the above/below conditions"]}
    since = now - timedelta(hours=LOOKBACK_HOURS)
    match = models.observation_match(rule, since, now)
    pipeline = models.observation_pipeline(match, rule["target_variable"], SCAN_LIMIT)
    rows = aggregate("climate_observations", pipeline, max_time_ms=MAX_TIME_MS)
    buckets = _bucket_by_scope(rule, rows)
    if not buckets:
        return {"status": "no_data", "scanned": len(rows), "considered": 0,
                "window_hours": LOOKBACK_HOURS,
                "notes": [f"no {rule['target_variable']} measurements inside the last "
                          f"{LOOKBACK_HOURS}h window for this scope"]}

    created: list[dict] = []
    deduped = 0
    suppressed: list[str] = []
    matches = 0
    for scope_value, readings in buckets.items():
        newest = readings[0]
        value = newest["value"]
        if not (value >= float(threshold) if mode == "above" else value <= float(threshold)):
            continue
        matches += 1
        outcome, _event = _open_scoped_event(
            rule, rule_id, scope_value=scope_value, value=value, unit=newest["unit"],
            source=newest["doc"], source_kind="observation", baseline=None, now=now,
        )
        if outcome["state"] == "created":
            created.append(outcome)
        elif outcome["state"] == "suppressed":
            suppressed.append(f"{scope_value}: {outcome['reason']}")
        else:
            deduped += 1

    status = "triggered" if created else ("suppressed" if suppressed else
                                          ("already_alerted" if deduped else "no_match"))
    return {
        "status": status,
        "window_hours": LOOKBACK_HOURS,
        "scanned": len(rows),
        "scopes_evaluated": len(buckets),
        "conditions_matched": matches,
        "events_created": created,
        "deduped": deduped,
        "suppressed": suppressed,
        "threshold": threshold,
    }


def _prediction_value(doc: dict, variable: str) -> tuple[Optional[float], str, Optional[str]]:
    """Read a forecast value from a ``predictions`` document without inventing one.

    ``predictions`` is owned by the ML app, so the plausible layouts are probed in a
    documented order and the first genuine number wins. ``None`` means "no readable
    value", which is reported as such rather than defaulted.
    """
    label: Optional[str] = None
    candidates: list[Any] = [doc.get("forecast_value"), doc.get("value"),
                             doc.get(f"{variable}_forecast"), doc.get("prediction")]
    unit = doc.get("unit") or doc.get("units")
    steps = doc.get("steps") or doc.get("series") or doc.get("forecast")
    if isinstance(steps, list):
        for step in reversed(steps):
            if not isinstance(step, dict):
                continue
            candidates.append(step.get("value") or step.get("forecast_value"))
            unit = step.get("unit") or unit
            label = str(step.get("step") or step.get("horizon") or step.get("index") or "")
            break
    for candidate in candidates:
        number = _num(candidate)
        if number is not None:
            return number, (str(unit) if unit else "unknown"), (label or None)
    return None, "unknown", None


def _evaluate_forecast(rule: dict, rule_id: str, now: datetime, *, mode: str) -> dict[str, Any]:
    threshold = rule.get("threshold")
    if threshold is None:
        return {"status": "misconfigured",
                "notes": ["threshold is required for the forecast conditions"]}
    match: dict[str, Any] = {"target_variable": rule["target_variable"]}
    scope = rule.get("geographic_scope") or {}
    if scope.get("region"):
        match["geographic_scope.region"] = scope["region"]
    rows = find("predictions", match, sort=[("generated_at", -1)], limit=PREDICTION_LIMIT)
    if not rows:
        return {"status": "no_predictions", "scanned": 0,
                "notes": [f"no predictions stored for target_variable="
                          f"'{rule['target_variable']}'"
                          + (f" in region '{scope['region']}'" if scope.get("region") else "")
                          + "; run the ML forecast endpoint first"]}

    created: list[dict] = []
    deduped = 0
    suppressed: list[str] = []
    unreadable = 0
    for doc in rows:
        value, unit, label = _prediction_value(doc, rule["target_variable"])
        if value is None:
            unreadable += 1
            continue
        if not (value >= float(threshold) if mode == "above" else value <= float(threshold)):
            continue
        region = str((doc.get("geographic_scope") or {}).get("region") or scope.get("region")
                     or "global")
        outcome, event = _open_scoped_event(
            rule, rule_id, scope_value=region, value=value, unit=unit,
            source=doc, source_kind="prediction", baseline=None, now=now,
        )
        if outcome["state"] == "created" and event:
            event["reference"]["model_ref"] = doc.get("model_ref") or doc.get("model_version_id")
            event["reference"]["horizon_step"] = label
            event["reference"]["observation_id"] = None
            update_one("alert_events", {"_id": event["_id"]}, {"$set": {
                "reference.model_ref": event["reference"]["model_ref"],
                "reference.horizon_step": label,
                "reference.observation_id": None,
                "updated_at": now_utc()}})
            created.append(outcome)
        elif outcome["state"] == "suppressed":
            suppressed.append(f"{region}: {outcome['reason']}")
        else:
            deduped += 1

    if created:
        status = "triggered"
    elif unreadable == len(rows):
        status = "unreadable_predictions"
    elif suppressed:
        status = "suppressed"
    elif deduped:
        status = "already_alerted"
    else:
        status = "no_match"
    return {"status": status, "scanned": len(rows), "events_created": created,
            "deduped": deduped, "suppressed": suppressed,
            "documents_without_a_readable_value": unreadable, "threshold": threshold}


def _baseline_record(rule: dict) -> tuple[Optional[dict], str]:
    """Latest ``anomaly_features`` summary for this variable, if the ML app published one."""
    scope = rule.get("geographic_scope") or {}
    match: dict[str, Any] = {"summary_type": models.ANALYTICS_SUMMARY_TYPE}
    if scope.get("region"):
        match["$or"] = [{"scope.region": scope["region"]}, {"region": scope["region"]},
                        {"scope_label": scope["region"]}]
    rows = find("analytics_summaries", match, sort=[("computed_at", -1)], limit=SUMMARY_LIMIT)
    if not rows:
        return None, (f"no analytics_summaries document with summary_type="
                      f"'{models.ANALYTICS_SUMMARY_TYPE}' exists yet; the ML app must publish a "
                      "baseline before anomaly or trend_change rules can be evaluated")
    variable = rule["target_variable"]
    for row in rows:
        payload = row.get("features") or row.get("variables") or row.get("data") or row
        if not isinstance(payload, dict):
            continue
        record = payload.get(variable)
        if record is None and payload.get("variable") == variable:
            record = payload
        if not isinstance(record, dict):
            continue
        has_baseline = _num(record.get("mean")) is not None
        has_deviation = _num(record.get("z_score")) is not None
        if has_baseline or has_deviation:
            merged = dict(record)
            merged.setdefault("computed_at", row.get("computed_at"))
            merged.setdefault("window_days", row.get("window_days") or row.get("window"))
            merged.setdefault("dataset_id", row.get("dataset_id"))
            return merged, ""
    return None, (f"an anomaly summary exists but carries no baseline statistics for variable "
                  f"'{variable}'; no anomaly is claimed without a real mean/stddev or z-score")


def _evaluate_baseline_condition(rule: dict, rule_id: str, now: datetime) -> dict[str, Any]:
    condition = rule.get("condition")
    baseline, note = _baseline_record(rule)
    if baseline is None:
        return {"status": "insufficient_baseline", "events_created": [], "notes": [note]}

    if condition == "trend_change" and rule.get("threshold") is None:
        return {"status": "misconfigured", "events_created": [],
                "notes": ["trend_change needs a threshold: the minimum absolute change "
                          "percentage (or slope) that counts as a trend break"]}
    limit = float(rule["threshold"]) if rule.get("threshold") is not None else DEFAULT_SIGMA

    mean = _num(baseline.get("mean"))
    stddev = _num(baseline.get("std")) or _num(baseline.get("stddev"))
    current = _num(baseline.get("current_value")) or _num(baseline.get("value"))
    z_score = _num(baseline.get("z_score"))
    if z_score is None and mean is not None and stddev:
        z_score = (current - mean) / stddev if current is not None else None
    change_pct = _num(baseline.get("trend_change_pct"))
    slope = _num(baseline.get("slope"))

    unit = str(baseline.get("unit") or "unknown")
    if condition == "anomaly":
        matched = z_score is not None and abs(z_score) >= limit
        observed = current if current is not None else z_score
        deviation = (f"z-score {z_score:.2f} against sigma {limit:.2f}"
                     if z_score is not None else "no z-score in the summary")
        basis = "z_score"
    else:
        reference = change_pct if change_pct is not None else slope
        matched = reference is not None and abs(reference) >= limit
        observed = reference
        deviation = (f"change {reference:+.3f} against threshold {limit:+.3f}"
                     if reference is not None else "no trend_change_pct or slope in the summary")
        basis = "trend_change_pct" if change_pct is not None else "slope"

    summary = {"mean": mean, "std": stddev, "z_score": z_score, "current_value": current,
               "trend_change_pct": change_pct, "slope": slope, "basis": basis,
               "window_days": baseline.get("window_days"), "computed_at": baseline.get("computed_at")}
    if not matched:
        return {"status": "no_match", "events_created": [], "baseline": summary,
                "notes": [deviation]}

    scope_value = models.scope_label(rule.get("geographic_scope") or {})
    source = {
        "_id": baseline.get("source_id") or "",
        "timestamp": _aware(baseline.get("computed_at")) or now,
        "dataset_id": baseline.get("dataset_id"),
        "granularity": baseline.get("granularity"),
        "location": {"region": scope_value},
    }
    outcome, event = _open_scoped_event(
        rule, rule_id, scope_value=scope_value,
        value=float(observed if observed is not None else limit), unit=unit,
        source=source, source_kind="observation", baseline=mean, now=now,
    )
    if outcome["state"] == "created" and event:
        update_one("alert_events", {"_id": event["_id"]}, {"$set": {
            "comparison.basis": basis, "comparison.z_score": z_score,
            "comparison.change_pct": change_pct, "comparison.slope": slope,
            "reference.baseline_summary": {k: v for k, v in summary.items() if v is not None},
            "updated_at": now_utc()}})
        return {"status": "triggered", "events_created": [outcome], "baseline": summary}
    if outcome["state"] == "suppressed":
        return {"status": "suppressed", "events_created": [],
                "suppressed": [f"{scope_value}: {outcome['reason']}"], "baseline": summary}
    return {"status": "already_alerted", "deduped": 1, "baseline": summary}


def evaluate_rule(rule: dict, *, now: Optional[datetime] = None) -> dict[str, Any]:
    """Evaluate one rule document. Data-shape problems are reported, never raised."""
    rule_id = str(rule.get("_id"))
    moment = _aware(now) or now_utc()
    condition = rule.get("condition")
    base: dict[str, Any] = {
        "rule_id": rule_id, "name": rule.get("name"), "condition": condition,
        "target_variable": rule.get("target_variable"),
        "scope": models.scope_label(rule.get("geographic_scope") or {}),
        "events_created": [], "notes": [],
    }
    try:
        if condition == "above":
            result = _evaluate_comparison(rule, rule_id, moment, mode="above")
        elif condition == "below":
            result = _evaluate_comparison(rule, rule_id, moment, mode="below")
        elif condition == "forecast_above":
            result = _evaluate_forecast(rule, rule_id, moment, mode="above")
        elif condition == "forecast_below":
            result = _evaluate_forecast(rule, rule_id, moment, mode="below")
        elif condition in ("anomaly", "trend_change"):
            result = _evaluate_baseline_condition(rule, rule_id, moment)
        else:
            result = {"status": "unsupported_condition",
                      "notes": [f"unknown condition '{condition}'"]}
    except Exception as exc:
        logger.error("rule evaluation failed: %s", type(exc).__name__, exc_info=True)
        result = {"status": "error", "notes": [f"{type(exc).__name__}: {str(exc)[:200]}"]}
    base.update(result)
    if base["status"] != "triggered":
        _touch_evaluation(rule_id, moment)
    return base


def evaluate_all(*, rule_ids: Optional[list[str]] = None, include_disabled: bool = False) -> dict[str, Any]:
    """Sweep enabled rules (or an explicit subset) and summarise the pass."""
    moment = now_utc()
    match: dict[str, Any] = {} if include_disabled else {"enabled": True}
    if rule_ids:
        match["_id"] = {"$in": [oid(value) for value in rule_ids if oid(value)]}
    rules = find("alert_rules", match, sort=[("created_at", 1)],
                 limit=ENV.integer("ALERT_RULES_PER_PASS", 200))
    results = [evaluate_rule(rule, now=moment) for rule in rules]
    counts: dict[str, int] = {}
    for item in results:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return {
        "evaluated_at": moment.isoformat(),
        "rules_evaluated": len(rules),
        "events_created": sum(len(item.get("events_created", [])) for item in results),
        "status_counts": counts,
        "results": results,
        "lookback_hours": LOOKBACK_HOURS,
        "scan_limit_per_rule": SCAN_LIMIT,
        "policy": "unique dedupe_key per (rule, scope, variable, UTC day) + per-rule cooldown",
    }
