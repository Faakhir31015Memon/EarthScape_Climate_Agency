"""Alert rule and event services: CRUD, evaluation entry points, acknowledgement flow.

Every read/write goes through PyMongo helpers; role rules live here (not in the views)
so the same policy applies whichever transport calls it.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from pymongo.errors import DuplicateKeyError

from apps.alerts import evaluation, models, notifier
from apps.authentication.permissions import role_allows
from common.db import collection, find, find_one, insert_one, now_utc, oid, update_one
from common.exceptions import Conflict, Forbidden, NotFound, ValidationError
from common.security import sanitize_text
from common.serialization import document_to_api, parse_datetime


def _require_rule(rule_id: str) -> dict:
    parsed = oid(rule_id)
    rule = find_one("alert_rules", {"_id": parsed}) if parsed else None
    if not rule:
        raise NotFound("Alert rule not found.")
    return rule


def list_rules(*, enabled: str = "", condition: str = "", variable: str = "", search: str = "",
               page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if enabled in ("true", "false"):
        query["enabled"] = enabled == "true"
    if condition:
        query["condition"] = condition
    if variable:
        query["target_variable"] = variable
    if search:
        query["name"] = {"$regex": sanitize_text(search, 60), "$options": "i"}
    total = collection("alert_rules").count_documents(query)
    rows = find("alert_rules", query, sort=[("created_at", -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(row) for row in rows], total


def get_rule(rule_id: str) -> dict:
    return document_to_api(_require_rule(rule_id))


def create_rule(payload: Any, actor_id: str) -> dict:
    document = models.rule_document(payload, actor_id)
    try:
        document["_id"] = oid(insert_one("alert_rules", document))
    except DuplicateKeyError:
        raise Conflict(f"An alert rule named '{document['name']}' already exists.") from None
    return document_to_api(document)


def _assert_can_modify(rule: dict, identity) -> None:
    if role_allows(identity.role, "alert.rule.admin"):
        return
    if str(rule.get("created_by")) != str(identity.user_id):
        raise Forbidden("Only the rule owner or an administrator may change this rule.")


def update_rule(rule_id: str, payload: Any, identity) -> dict:
    rule = _require_rule(rule_id)
    _assert_can_modify(rule, identity)
    changes = models.rule_changes(payload)
    update_one("alert_rules", {"_id": rule["_id"]}, {"$set": changes})
    return document_to_api(find_one("alert_rules", {"_id": rule["_id"]}))


def set_enabled(rule_id: str, enabled: bool, identity) -> dict:
    rule = _require_rule(rule_id)
    _assert_can_modify(rule, identity)
    update_one("alert_rules", {"_id": rule["_id"]},
               {"$set": {"enabled": bool(enabled), "updated_at": now_utc()}})
    return document_to_api(find_one("alert_rules", {"_id": rule["_id"]}))


def delete_rule(rule_id: str, identity) -> dict:
    rule = _require_rule(rule_id)
    if not role_allows(identity.role, "alert.rule.admin"):
        raise Forbidden("Deleting alert rules requires the alert.rule.admin capability.")
    events = collection("alert_events").count_documents({"rule_id": str(rule["_id"])})
    collection("alert_rules").delete_one({"_id": rule["_id"]})
    return {"deleted": True, "id": str(rule["_id"]), "name": rule.get("name"),
            "retained_events": events,
            "note": "Historic alert events are retained for audit."}


def evaluate_rule_by_id(rule_id: str) -> dict:
    rule = _require_rule(rule_id)
    if not rule.get("enabled"):
        return {"rule_id": rule_id, "status": "disabled",
                "notes": ["rule is disabled; enable it or use the include_disabled flag"],
                "events_created": []}
    return evaluation.evaluate_rule(rule)


def evaluate_sweep(*, include_disabled: bool = False) -> dict:
    return evaluation.evaluate_all(include_disabled=include_disabled)


def _event_query(params: dict[str, str]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    status = params.get("status")
    if status:
        if status not in models.EVENT_STATUSES:
            raise ValidationError(f"Unknown event status '{status}'.")
        query["status"] = status
    if params.get("severity"):
        query["severity"] = params["severity"]
    if params.get("rule_id"):
        query["rule_id"] = str(params["rule_id"])
    if params.get("variable"):
        query["target_variable"] = params["variable"]
    _from = parse_datetime(params.get("from"))
    _to = parse_datetime(params.get("to"))
    window: dict[str, Any] = {}
    if _from:
        window["$gte"] = _from
    if _to:
        window["$lte"] = _to
    if window:
        query["detected_at"] = window
    return query


def list_events(params: dict[str, str], *, page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    query = _event_query(params)
    total = collection("alert_events").count_documents(query)
    rows = find("alert_events", query, sort=[("detected_at", -1)],
               skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(row) for row in rows], total


def _require_event(event_id: str) -> dict:
    parsed = oid(event_id)
    event = find_one("alert_events", {"_id": parsed}) if parsed else None
    if not event:
        raise NotFound("Alert event not found.")
    return event


def get_event(event_id: str) -> dict:
    event = _require_event(event_id)
    payload = document_to_api(event)
    payload["rule"] = document_to_api(find_one("alert_rules", {"_id": oid(event.get("rule_id"))}))
    payload["notifications"] = [
        document_to_api(row) for row in
        find("notifications", {"_id": {"$in": [oid(nid) for nid in (event.get("notification_ids") or [])
                                            if oid(nid)]}}, limit=50)
    ]
    return payload


def _transition(event_id: str, identity, *, to_status: str, note: str | None,
                actor_field: str, at_field: str) -> dict:
    event = _require_event(event_id)
    current = event.get("status")
    allowed_transitions = {
        "open": ("acknowledged", "resolved"),
        "acknowledged": ("resolved", "open"),
        "resolved": ("open",),
    }
    if to_status == current:
        raise Conflict(f"Event is already '{current}'.")
    if to_status not in allowed_transitions.get(str(current), ()):
        raise Conflict(f"Cannot move an event from '{current}' to '{to_status}'.")
    moment = now_utc()
    update: dict[str, Any] = {
        "status": to_status, at_field: moment, actor_field: str(identity.user_id),
        "updated_at": moment,
    }
    collection("alert_events").update_one(
        {"_id": event["_id"]},
        {"$set": update, "$push": {"audit": {
            "at": moment, "actor": str(identity.user_id), "actor_role": identity.role,
            "action": to_status, "reason": sanitize_text(note or "", 400) or None}}},
    )
    collection("notifications").update_many(
        {"alert_event_id": str(event["_id"])},
        {"$set": {"acknowledged_at": moment, "updated_at": moment}},
    )
    return document_to_api(find_one("alert_events", {"_id": event["_id"]}))


def acknowledge(event_id: str, identity, *, note: str | None = None) -> dict:
    return _transition(event_id, identity, to_status="acknowledged", note=note,
                       actor_field="acknowledged_by", at_field="acknowledged_at")


def resolve(event_id: str, identity, *, note: str | None = None) -> dict:
    return _transition(event_id, identity, to_status="resolved", note=note,
                       actor_field="resolved_by", at_field="resolved_at")


def reopen(event_id: str, identity, *, note: str | None = None) -> dict:
    return _transition(event_id, identity, to_status="open", note=note,
                       actor_field="reopened_by", at_field="reopened_at")


def event_counts() -> dict[str, Any]:
    pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
    rows = collection("alert_events").aggregate(pipeline, maxTimeMS=10000)
    counts = {status: 0 for status in models.EVENT_STATUSES}
    for row in rows:
        counts[str(row["_id"] or "unknown")] = row["count"]
    severities = collection("alert_events").aggregate(
        [{"$match": {"status": "open"}}, {"$group": {"_id": "$severity", "count": {"$sum": 1}}}],
        maxTimeMS=10000,
    )
    return {
        "by_status": counts,
        "open_by_severity": {str(row["_id"]): row["count"] for row in severities},
        "rules": {"total": collection("alert_rules").estimated_document_count(),
                  "enabled": collection("alert_rules").count_documents({"enabled": True})},
        "email_enabled": bool(settings.EMAIL_ENABLED),
    }


def test_notification(identity, *, channel: str = "in_app") -> dict:
    """Explicitly targeted self-test; records a real notification document."""
    if channel not in ("in_app", "email"):
        raise ValidationError("channel must be 'in_app' or 'email'.")
    created = notifier.notify_user(
        str(identity.user_id), "EarthScape notification test",
        "This notification proves the delivery pipeline for channel "
        f"'{channel}'. Email stays pending until SMTP is configured.",
        kind="test", severity="info", channels=[channel], link="/notifications",
    )
    return {
        "created": [document_to_api(row) for row in created],
        "delivery": [row.get("delivery") for row in created],
        "email_enabled": bool(settings.EMAIL_ENABLED),
    }
