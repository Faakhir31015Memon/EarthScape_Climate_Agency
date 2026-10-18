"""Notification creation, delivery policy and read state.

Delivery honesty:

* ``in_app`` notifications are *stored* and then read by the recipient; a document
  becomes ``sent`` once it is durably persisted (never before).
* ``email`` notifications are only attempted when ``common.emailer.is_configured()``
  is true. Otherwise the document is written with ``delivery_status="suppressed"``
  and an explicit reason, so the UI can say "email is not configured here" instead
  of pretending a message was delivered.
* A ``dedupe_key`` plus the cooldown window stops the same unchanged condition from
  notifying twice, and retry attempts are capped by ``NOTIFICATION_MAX_ATTEMPTS``.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, Iterable, List, Optional

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common import emailer
from common.api import ApiError
from common.repositories import maybe_object_id, notifications, serialize, utcnow, users  # noqa: E402
from config import env  # noqa: E402

logger = logging.getLogger(__name__)

CHANNELS = ("in_app", "email")
TYPES = ("alert", "report", "system", "ticket")

MAX_ATTEMPTS = max(1, env.get_int("NOTIFICATION_MAX_ATTEMPTS", 5))
COOLDOWN_MINUTES = max(0, env.get_int("ALERT_COOLDOWN_MINUTES", 60))
#: A notification that failed to deliver is retried with a bounded linear backoff.
RETRY_BASE_SECONDS = max(15, env.get_int("NOTIFICATION_RETRY_BASE_SECONDS", 60))


def cooldown_window() -> timedelta:
    return timedelta(minutes=COOLDOWN_MINUTES)


def _oid(value: str):
    return maybe_object_id(value)


def recent_duplicate(dedupe_key: Optional[str], *,
                     window: Optional[timedelta] = None) -> Optional[Dict[str, Any]]:
    """Return an already-sent notification for this key inside the cooldown window."""
    if not dedupe_key:
        return None
    floor = utcnow() - (window or cooldown_window())
    matches = notifications.find({"dedupe_key": dedupe_key, "created_at": {"$gte": floor}},
                                 sort=[("created_at", -1)], limit=1)
    return matches[0] if matches else None


def channels_for_user(user_id: str) -> Dict[str, bool]:
    """Per-user channel preferences (defaults: in-app on, email only if configured)."""
    document = users.find_one({"_id": _oid(user_id)})
    preferences = (document or {}).get("preferences", {}) or {}
    channels = preferences.get("notification_channels") or {}
    if not isinstance(channels, dict):
        channels = {}
    return {
        "in_app": bool(channels.get("in_app", True)),
        "email": bool(channels.get("email", env.get_bool("ENABLE_EMAIL_NOTIFICATIONS"))),
        "email_configured": emailer.is_configured(),
    }


def create_notification(
    *,
    recipient_user_id: str,
    channel: str,
    type: str,
    title: str,
    body: str,
    dedupe_key: Optional[str] = None,
    related_resource: Optional[Dict[str, Any]] = None,
    severity: Optional[str] = None,
    deliver: bool = True,
    cooldown_minutes: Optional[int] = None,
) -> Dict[str, Any]:
    """Insert (and optionally deliver) one notification, honouring the dedupe policy."""
    if channel not in CHANNELS:
        raise ValueError(f"Unknown notification channel '{channel}'.")
    if type not in TYPES:
        raise ValueError(f"Unknown notification type '{type}'.")

    window = cooldown_window() if cooldown_minutes is None else timedelta(
        minutes=max(0, int(cooldown_minutes)))
    duplicate = recent_duplicate(dedupe_key, window=window)
    if duplicate is not None:
        suppressed = serialize(duplicate) or {}
        suppressed["suppressed_as_duplicate"] = True
        return suppressed

    document = {
        "recipient_user_id": str(recipient_user_id),
        "channel": channel,
        "type": type,
        "title": str(title or "")[:200],
        "body": str(body or "")[:4000],
        "dedupe_key": dedupe_key,
        "related_resource": related_resource or {},
        # Carried through so unread counters can flag critical alerts without joining
        # back to the alert collection; None for non-alert notices.
        "severity": severity if severity in ("info", "warning", "critical") else None,
        "delivery_status": "pending",
        "attempt_count": 0,
        "next_retry_at": None,
        "read_at": None,
        "acknowledged_at": None,
        "created_at": utcnow(),
        "sent_at": None,
        "failure_reason": None,
    }
    document["_id"] = _oid(notifications.insert_one(document))
    created = serialize(document) or {}
    created["suppressed_as_duplicate"] = False
    if deliver:
        return deliver_notification(str(created["id"]))
    return created


def deliver_notification(notification_id: str) -> Dict[str, Any]:
    """Attempt delivery once and record the true outcome."""
    repository = notifications
    document = repository.find_one({"_id": _oid(notification_id)})
    if document is None:
        raise ValueError("Notification no longer exists.")

    attempts = int(document.get("attempt_count", 0)) + 1
    recipient_id = str(document.get("recipient_user_id"))

    if document.get("channel") == "in_app":
        # In-app delivery is complete once the row is stored for the recipient.
        updates = {"delivery_status": "sent", "sent_at": utcnow(), "attempt_count": attempts,
                   "next_retry_at": None, "failure_reason": None}
        return serialize(repository.update_by_id(notification_id, updates, return_document=True)) or {}

    recipient = users.find_one({"_id": _oid(recipient_id)})
    address = (recipient or {}).get("email")
    if not recipient or not address:
        return _mark_failure(repository, notification_id, attempts,
                             "The recipient account has no email address on file.")
    if not emailer.is_configured():
        return serialize(repository.update_by_id(notification_id, {
            "delivery_status": "suppressed",
            "attempt_count": attempts,
            "next_retry_at": None,
            "failure_reason": ("Email delivery is not configured on this deployment "
                               "(SMTP_HOST and ENABLE_EMAIL_NOTIFICATIONS are unset). "
                               "The notification remains available in-app."),
        }, return_document=True)) or {}

    delivered = emailer.send_email(to=address, subject=document.get("title", "Notification"),
                                   body=document.get("body", ""), user_id=recipient_id)
    if delivered:
        return serialize(repository.update_by_id(notification_id, {
            "delivery_status": "sent", "sent_at": utcnow(), "attempt_count": attempts,
            "next_retry_at": None, "failure_reason": None,
        }, return_document=True)) or {}
    return _mark_failure(repository, notification_id, attempts,
                         "The SMTP server refused or failed to accept the message.")


def _mark_failure(repository, notification_id: str, attempts: int, reason: str) -> Dict[str, Any]:
    exhausted = attempts >= MAX_ATTEMPTS
    updates = {
        "delivery_status": "failed" if exhausted else "pending",
        "attempt_count": attempts,
        "failure_reason": reason,
        "next_retry_at": None if exhausted else utcnow() + timedelta(
            seconds=RETRY_BASE_SECONDS * attempts),
    }
    return serialize(repository.update_by_id(notification_id, updates, return_document=True)) or {}


def retry_due_notifications(limit: int = 50) -> Dict[str, int]:
    """Deliver notifications whose retry window has opened. Returns real counts."""
    repository = notifications
    now = utcnow()
    due = repository.find({"delivery_status": {"$in": ["pending", "failed"]},
                           "next_retry_at": {"$ne": None, "$lte": now}},
                          sort=[("next_retry_at", 1)], limit=max(1, min(int(limit), 200)))
    sent = failed = suppressed = 0
    for document in due:
        outcome = deliver_notification(str(document["_id"]))
        status = outcome.get("delivery_status")
        if status == "sent":
            sent += 1
        elif status == "suppressed":
            suppressed += 1
        else:
            failed += 1
    return {"considered": len(due), "sent": sent, "suppressed": suppressed, "still_pending": failed}


def queue_for_recipients(
    recipient_ids: Iterable[str],
    *,
    type: str,
    title: str,
    body: str,
    dedupe_key: Optional[str] = None,
    related_resource: Optional[Dict[str, Any]] = None,
    severity: Optional[str] = None,
    cooldown_minutes: Optional[int] = None,
) -> List[str]:
    """Fan one message out to the resolved recipients, respecting preferences."""
    notification_ids: List[str] = []
    for recipient_id in dict.fromkeys([str(item) for item in recipient_ids if item]):
        channels = channels_for_user(recipient_id)
        for channel in CHANNELS:
            if not channels.get(channel):
                continue
            per_recipient_key = (f"{dedupe_key}:{recipient_id}:{channel}" if dedupe_key else None)
            created = create_notification(
                recipient_user_id=recipient_id, channel=channel, type=type,
                title=title, body=body, dedupe_key=per_recipient_key,
                related_resource=related_resource, severity=severity,
                cooldown_minutes=cooldown_minutes,
            )
            if not created.get("suppressed_as_duplicate") and created.get("id"):
                notification_ids.append(str(created["id"]))
    return notification_ids


def list_for_user(user_id: str, *, page: int, page_size: int, unread_only: bool = False,
                  type_filter: Optional[str] = None,
                  channel: Optional[str] = None,
                  delivery_status: Optional[str] = None) -> Dict[str, Any]:
    """Recipients are always scoped to the authenticated account - server side."""
    criteria: Dict[str, Any] = {"recipient_user_id": str(user_id)}
    if unread_only:
        criteria["read_at"] = None
    if type_filter:
        criteria["type"] = type_filter
    if channel:
        criteria["channel"] = channel
    if delivery_status:
        criteria["delivery_status"] = delivery_status
    return notifications.paginate(criteria, page=page, page_size=page_size,
                                  sort=[("created_at", -1)])


def unread_counts(user_id: str) -> Dict[str, Any]:
    criteria = {"recipient_user_id": str(user_id), "read_at": None}
    by_type: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    for document in notifications.find(criteria,
                                       projection={"type": 1, "channel": 1, "severity": 1},
                                       limit=2000):
        key = str(document.get("type"))
        by_type[key] = by_type.get(key, 0) + 1
        severity = str(document.get("severity") or "unspecified")
        by_severity[severity] = by_severity.get(severity, 0) + 1
    total = sum(by_type.values())
    return {
        "total": total,
        # Flat keys are what the web client binds to.
        "unread": total,
        "critical": by_severity.get("critical", 0),
        "by_type": by_type,
        "by_severity": by_severity,
        "sampled_limit": 2000,
        "exact": total < 2000,
    }


def _owned(notification_id: str, user_id: str) -> Dict[str, Any]:
    document = notifications.find_one({"_id": _oid(notification_id)})
    if document is None or str(document.get("recipient_user_id")) != str(user_id):
        raise ApiError("not_found", "No such notification for this account.", status=404)
    return document


def mark_read(notification_id: str, user_id: str) -> Dict[str, Any]:
    _owned(notification_id, user_id)
    updated = notifications.update_by_id(notification_id,
                                        {"read_at": utcnow()}, return_document=True)
    return serialize(updated) or {}


def acknowledge(notification_id: str, user_id: str) -> Dict[str, Any]:
    """Recipient acknowledgement: recorded separately from ``read_at``."""
    document = _owned(notification_id, user_id)
    if document.get("read_at") is None:
        document = notifications.update_by_id(notification_id, {"read_at": utcnow()},
                                             return_document=True) or document
    updated = notifications.update_by_id(notification_id, {"acknowledged_at": utcnow()},
                                        return_document=True)
    return serialize(updated) or {}


def mark_all_read(user_id: str, *, type_filter: Optional[str] = None) -> int:
    criteria: Dict[str, Any] = {"recipient_user_id": str(user_id), "read_at": None}
    if type_filter:
        criteria["type"] = type_filter
    return notifications.update_many(criteria, {"$set": {"read_at": utcnow(),
                                                        "acknowledged_at": utcnow()}})


def detail(notification_id: str, user_id: str) -> Dict[str, Any]:
    return serialize(_owned(notification_id, user_id)) or {}


def get_preferences(user_id: str) -> Dict[str, Any]:
    channels = channels_for_user(user_id)
    return {
        "channels": {"in_app": channels["in_app"], "email": channels["email"]},
        "email_delivery_configured": channels["email_configured"],
        "note": ("Email is only attempted when SMTP is configured on this deployment; "
                 "otherwise those notifications are recorded as suppressed."),
        "cooldown_minutes": COOLDOWN_MINUTES,
        "max_attempts": MAX_ATTEMPTS,
    }


def set_preferences(user_id: str, channels: Dict[str, bool]) -> Dict[str, Any]:
    in_app = bool(channels.get("in_app", True))
    email = bool(channels.get("email", False))
    document = users.find_one({"_id": _oid(user_id)})
    if document is None:
        raise ApiError("not_found", "Account not found.", status=404)
    preferences = dict(document.get("preferences") or {})
    merged_channels = dict(preferences.get("notification_channels") or {})
    merged_channels.update({"in_app": in_app, "email": email})
    preferences["notification_channels"] = merged_channels
    users.update_by_id(user_id, {"preferences": preferences, "updated_at": utcnow()})
    return get_preferences(user_id)


def counts() -> Dict[str, int]:
    """Delivery-state counts, used by the monitoring surfaces."""
    repository = notifications
    out: Dict[str, int] = {}
    for status in ("pending", "sent", "failed", "suppressed"):
        out[status] = repository.count({"delivery_status": status})
    out["total"] = repository.count({})
    return out
