"""Notification creation and delivery for alert events (and sibling apps).

Delivery is recorded honestly:

* ``in_app`` - writing the ``notifications`` document *is* the delivery, so the status
  is ``delivered`` and the user's inbox reads it from the same collection.
* ``email``  - only attempted when ``settings.EMAIL_ENABLED`` (which requires both
  ``EMAIL_HOST`` and ``DEFAULT_FROM_EMAIL``). Otherwise the notification is stored with
  ``delivery_status = "pending_email_disabled"`` and an explicit reason, never a fake
  ``delivered``.

Cross-app interface (used by ``apps.support``): keep it to :func:`notify_user` and
:func:`notify_users`; the signature is deliberately primitive so no app has to import
alert internals.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from django.conf import settings
from pymongo.errors import PyMongoError

from common.db import collection, find, find_one, insert_one, now_utc, oid, update_one
from common.logging_config import get_logger
from common.security import sanitize_text
from common.serialization import document_to_api

logger = get_logger("earthscape.alerts.notifier")

DELIVERED = "delivered"
PENDING_EMAIL_DISABLED = "pending_email_disabled"
FAILED = "failed"

EMAIL_SUPPRESSED_REASON = (
    "Email delivery is disabled: set EMAIL_HOST and DEFAULT_FROM_EMAIL in .env and "
    "re-run the retry endpoint to send this message."
)


def _recipient_email(user_id: str) -> Optional[str]:
    user = find_one("users", {"_id": oid(user_id)}, {"email": 1, "full_name": 1, "is_active": 1})
    if not user:
        return None
    email = user.get("email")
    return str(email) if email else None


def _recipient_name(user_id: str) -> Optional[str]:
    user = find_one("users", {"_id": oid(user_id)}, {"full_name": 1})
    return sanitize_text(user.get("full_name"), max_length=120) if user else None


def build_document(*, recipient: str, channel: str, title: str, body: str, kind: str,
                   severity: str = "info", link: str = "", alert_event_id: str | None = None,
                   metadata: dict | None = None, delivery_status: str = DELIVERED,
                   delivery_reason: str = "") -> dict[str, Any]:
    now = now_utc()
    return {
        "recipient": sanitize_text(recipient, max_length=40),
        "channel": channel,
        "kind": sanitize_text(kind, max_length=32) or "system",
        "severity": severity if severity in ("info", "warning", "severe") else "info",
        "title": sanitize_text(title, max_length=200),
        "body": sanitize_text(body, max_length=2000),
        "link": sanitize_text(link, max_length=300),
        "alert_event_id": alert_event_id,
        "delivery_status": delivery_status,
        "delivery_reason": sanitize_text(delivery_reason, max_length=400),
        "delivered_at": now if delivery_status == DELIVERED else None,
        "read_at": None,
        "acknowledged_at": None,
        "retry_count": 0,
        "last_retry_at": None,
        "metadata": metadata or {},
        "created_at": now,
        "updated_at": now,
    }


def _send_email(to_address: str, subject: str, body: str) -> None:
    from django.core.mail import EmailMessage

    message = EmailMessage(
        subject=subject[:200],
        body=body[:4000],
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_address],
    )
    message.send()


def deliver(notification_id: str) -> dict[str, Any]:
    """Attempt delivery for a stored notification and record the real outcome."""
    doc = find_one("notifications", {"_id": oid(notification_id)})
    if not doc:
        return {"id": notification_id, "delivery_status": FAILED, "reason": "notification not found"}
    channel = doc.get("channel")
    now = now_utc()
    if channel == "in_app":
        update_one("notifications", {"_id": oid(notification_id)}, {"$set": {
            "delivery_status": DELIVERED, "delivered_at": now,
            "delivery_reason": "stored in the recipient's in-app notification inbox",
            "updated_at": now}})
        return {"id": notification_id, "delivery_status": DELIVERED, "channel": "in_app"}

    address = _recipient_email(doc.get("recipient", ""))
    if not settings.EMAIL_ENABLED:
        update_one("notifications", {"_id": oid(notification_id)}, {"$set": {
            "delivery_status": PENDING_EMAIL_DISABLED, "delivery_reason": EMAIL_SUPPRESSED_REASON,
            "updated_at": now}})
        return {"id": notification_id, "delivery_status": PENDING_EMAIL_DISABLED, "channel": "email"}
    if not address:
        update_one("notifications", {"_id": oid(notification_id)}, {"$set": {
            "delivery_status": FAILED, "delivery_reason": "recipient account has no email address",
            "updated_at": now}})
        return {"id": notification_id, "delivery_status": FAILED, "channel": "email"}
    try:
        _send_email(address, doc.get("title", "EarthScape notification"), doc.get("body", ""))
    except PyMongoError:
        raise
    except Exception as exc:  # SMTP/DNS/provider failures are recorded, never hidden
        logger.warning("email delivery failed: %s", type(exc).__name__)
        update_one("notifications", {"_id": oid(notification_id)}, {"$set": {
            "delivery_status": FAILED,
            "delivery_reason": f"smtp error: {type(exc).__name__}: {str(exc)[:200]}",
            "updated_at": now}})
        return {"id": notification_id, "delivery_status": FAILED, "channel": "email",
                "reason": type(exc).__name__}
    update_one("notifications", {"_id": oid(notification_id)}, {"$set": {
        "delivery_status": DELIVERED, "delivered_at": now,
        "delivery_reason": f"accepted by SMTP host {settings.EMAIL_HOST}", "updated_at": now}})
    return {"id": notification_id, "delivery_status": DELIVERED, "channel": "email"}


def notify_user(recipient: str, title: str, body: str, *, kind: str = "system",
                severity: str = "info", channels: Iterable[str] = ("in_app",),
                link: str = "", alert_event_id: str | None = None,
                metadata: dict | None = None) -> list[dict[str, Any]]:
    """Create + deliver notifications for one user. Returns the stored documents."""
    created: list[dict[str, Any]] = []
    user_name = _recipient_name(recipient)
    for channel in dict.fromkeys(c for c in channels if c in ("in_app", "email")):
        greeting = f"Hello {user_name},\n\n" if user_name else ""
        document = build_document(
            recipient=recipient, channel=channel, title=title,
            body=f"{greeting}{body}", kind=kind, severity=severity, link=link,
            alert_event_id=alert_event_id, metadata=metadata,
            delivery_status=FAILED if channel == "email" and not settings.EMAIL_ENABLED else DELIVERED,
            delivery_reason=EMAIL_SUPPRESSED_REASON
            if channel == "email" and not settings.EMAIL_ENABLED else "queued for delivery",
        )
        document["_id"] = oid(insert_one("notifications", document))
        outcome = deliver(str(document["_id"]))
        stored = find_one("notifications", {"_id": document["_id"]}) or document
        stored["delivery"] = outcome
        created.append(stored)
    return created


def notify_users(recipients: Iterable[str], title: str, body: str, **kwargs: Any) -> list[dict[str, Any]]:
    """Fan out :func:`notify_user` to many recipients; deduplicated, order preserved."""
    created: list[dict[str, Any]] = []
    for recipient in dict.fromkeys(r for r in recipients if r):
        created.extend(notify_user(str(recipient), title, body, **kwargs))
    return created


def notify_for_event(event: dict, rule: dict) -> dict[str, Any]:
    """Alert dispatch: in-app always, email only when the rule asks for it.

    Recipients default to the rule owner when the rule lists none, so a rule can never
    be silently un-notified.
    """
    recipients = [str(r) for r in (rule.get("recipients") or []) if r]
    fallback_reason = ""
    if not recipients and rule.get("created_by"):
        recipients = [str(rule.get("created_by"))]
        fallback_reason = "no explicit recipients; notified the rule owner"
    if not recipients:
        return {"notification_ids": [], "recipients": [], "reason": "rule has no recipients and no owner"}

    summary = event.get("description") or event.get("explanation") or ""
    severity = event.get("severity", rule.get("severity", "warning"))
    title = f"[{str(severity).upper()}] Alert: {rule.get('name', 'unnamed rule')}"
    body_lines = [
        f"Rule: {rule.get('name', '')}",
        f"Variable: {event.get('target_variable') or rule.get('target_variable')}",
        f"Condition: {rule.get('condition')} (threshold {rule.get('threshold')})",
        f"Scope: {event.get('scope_label')}",
        f"Detected: {event.get('detected_at')}",
        "",
        summary,
    ]
    created = notify_users(
        recipients, title, "\n".join(body_lines),
        kind="alert", severity=severity,
        channels=rule.get("notify_channels") or ["in_app"],
        link=f"/alerts/events/{event.get('_id')}",
        alert_event_id=str(event.get("_id")),
        metadata={"rule_id": str(event.get("rule_id")), "dedupe_key": event.get("dedupe_key")},
    )
    ids = [str(doc["_id"]) for doc in created]
    update_one("alert_events", {"_id": event["_id"]}, {"$set": {
        "notification_ids": ids, "updated_at": now_utc(),
        "notification_note": fallback_reason or None}})
    statuses = sorted({doc.get("delivery", {}).get("delivery_status") for doc in created})
    return {"notification_ids": ids, "recipients": recipients, "delivery_statuses": statuses,
            "reason": fallback_reason or None,
            "email_enabled": bool(settings.EMAIL_ENABLED)}


def list_for_user(user_id: str, *, unread_only: bool = False, page: int = 1,
                  page_size: int = 25, kind: str = "") -> tuple[list[dict], int]:
    query: dict[str, Any] = {"recipient": user_id}
    if unread_only:
        query["read_at"] = None
    if kind:
        query["kind"] = kind
    table = collection("notifications")
    total = table.count_documents(query)
    docs = find("notifications", query, sort=[("created_at", -1)],
                skip=(page - 1) * page_size, limit=page_size)
    return [document_to_api(doc) for doc in docs], total


def unread_count(user_id: str) -> int:
    return collection("notifications").count_documents({"recipient": user_id, "read_at": None})


def mark_read(notification_id: str, user_id: str) -> dict:
    doc = find_one("notifications", {"_id": oid(notification_id)})
    if not doc:
        from common.exceptions import NotFound

        raise NotFound("Notification not found.")
    if str(doc.get("recipient")) != str(user_id):
        from common.exceptions import Forbidden

        raise Forbidden("You can only manage your own notifications.")
    now = now_utc()
    update_one("notifications", {"_id": doc["_id"]},
               {"$set": {"read_at": doc.get("read_at") or now, "acknowledged_at": now,
                         "updated_at": now}})
    return document_to_api(find_one("notifications", {"_id": doc["_id"]}))


def mark_all_read(user_id: str) -> int:
    result = collection("notifications").update_many(
        {"recipient": user_id, "read_at": None},
        {"$set": {"read_at": now_utc(), "updated_at": now_utc()}},
    )
    return result.modified_count


def retry_delivery(notification_id: str, user_id: str, *, is_admin: bool = False) -> dict[str, Any]:
    doc = find_one("notifications", {"_id": oid(notification_id)})
    if not doc:
        from common.exceptions import NotFound

        raise NotFound("Notification not found.")
    if not is_admin and str(doc.get("recipient")) != str(user_id):
        from common.exceptions import Forbidden

        raise Forbidden("You can only retry your own notifications.")
    if doc.get("channel") != "email":
        return {"id": notification_id, "retryable": False,
                 "reason": "in-app notifications are delivered when the document is written"}
    update_one("notifications", {"_id": doc["_id"]},
               {"$inc": {"retry_count": 1}, "$set": {"last_retry_at": now_utc(), "updated_at": now_utc()}})
    return deliver(notification_id)
