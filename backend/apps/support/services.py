"""Support tickets, ticket messages and user feedback.

Ticket numbering is race-free: ``next_ticket_number()`` performs a single atomic
``findOneAndUpdate`` with ``$inc`` on a sequence document inside ``platform_settings``
(key ``counter.support_tickets``), so two concurrent requests can never receive the same
number even though the unique index on ``support_tickets.ticket_number`` would catch it.
Those internal ``counter.*`` keys are hidden from the platform-settings API.

Visibility rules (enforced here, not in the views):

* a non-staff user sees and touches only tickets they opened;
* a holder of ``support.manage`` sees every ticket, may assign, re-prioritise and change
  status, and their replies generate an in-app notification for the requester through
  ``apps.alerts.notifier.notify_user`` (the single documented cross-app entry point).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from apps.alerts import notifier
from apps.authentication.permissions import role_allows
from apps.users import repository as users
from common.db import (aggregate, collection, count, find, find_one, insert_one, now_utc, oid,
                       update_one)
from common.exceptions import Conflict, Forbidden, NotFound, ValidationError
from common.security import sanitize_text
from common.serialization import document_to_api, parse_datetime

COUNTER_KEY = "counter.support_tickets"
TICKET_PREFIX = "ESC"
CATEGORIES = ("technical", "data_quality", "access", "feature_request", "billing", "other")
PRIORITIES = ("low", "medium", "high")
STATUSES = ("open", "in_progress", "waiting_user", "resolved", "closed")

# Legal forward moves. Re-opening a resolved ticket is staff-only (see ``patch_ticket``).
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("in_progress", "waiting_user", "resolved", "closed"),
    "in_progress": ("waiting_user", "resolved", "closed"),
    "waiting_user": ("in_progress", "resolved", "closed"),
    "resolved": ("closed", "in_progress"),
    "closed": ("in_progress",),
}

_COUNTER_COLLECTION = "platform_settings"


def _sequence_value() -> int:
    document = collection(_COUNTER_COLLECTION).find_one_and_update(
        {"key": COUNTER_KEY},
        {"$inc": {"value": 1},
         "$set": {"updated_at": now_utc()},
         "$setOnInsert": {"created_at": now_utc(), "kind": "sequence",
                          "note": "internal ticket counter; not a user-facing setting"}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
        projection={"value": 1},
    )
    value = (document or {}).get("value")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise Conflict("The ticket counter document is not numeric; run scripts/migrate_mongo.py "
                   "and inspect platform_settings['counter.support_tickets'].")


def next_ticket_number() -> str:
    year = datetime.now(timezone.utc).year
    return f"{TICKET_PREFIX}-{year}-{_sequence_value():06d}"


def is_staff(identity) -> bool:
    return role_allows(identity.role, "support.manage")


# ---------------------------------------------------------------------------- tickets


def _ticket_columns() -> dict[str, Any]:
    return {
        "ticket_number": 1, "subject": 1, "category": 1, "priority": 1, "status": 1,
        "created_by": 1, "assigned_to": 1, "created_at": 1, "updated_at": 1,
        "last_activity_at": 1, "message_count": 1,
    }


def create_ticket(payload: Any, identity) -> dict[str, Any]:
    moment = now_utc()
    document = {
        "ticket_number": next_ticket_number(),
        "subject": sanitize_text(payload.subject, max_length=160),
        "body": sanitize_text(payload.body, max_length=6000),
        "category": payload.category,
        "priority": payload.priority,
        "status": "open",
        "created_by": str(identity.user_id),
        "created_by_email": identity.email,
        "created_by_role": identity.role,
        "assigned_to": None,
        "assigned_to_email": None,
        "first_response_at": None,
        "closed_at": None,
        "message_count": 0,
        "unread_for_user": 0,
        "created_at": moment,
        "updated_at": moment,
        "last_activity_at": moment,
    }
    try:
        document["_id"] = oid(insert_one("support_tickets", document))
    except DuplicateKeyError:
        raise Conflict("A ticket with that number already exists; the counter is out of sync.") from None
    add_message(document, identity, document["body"], system=False)
    return document_to_api(find_one("support_tickets", {"_id": document["_id"]}))


def _require_ticket(ticket_id: str) -> dict:
    parsed = oid(ticket_id)
    ticket = find_one("support_tickets", {"_id": parsed}) if parsed else None
    if not ticket:
        ticket = find_one("support_tickets", {"ticket_number": sanitize_text(ticket_id, max_length=40)})
    if not ticket:
        raise NotFound("Support ticket not found.")
    return ticket


def _assert_access(ticket: dict, identity) -> None:
    """Requester or ``support.manage`` — there is no third category of reader."""
    if str(ticket.get("created_by")) == str(identity.user_id):
        return
    if not role_allows(identity.role, "support.manage"):
        raise Forbidden("Support staff access is required for this ticket.")


def list_tickets(identity, params: dict[str, str], *, page: int = 1,
                 page_size: int = 25) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if not is_staff(identity):
        query["created_by"] = str(identity.user_id)
    elif params.get("created_by"):
        query["created_by"] = str(params["created_by"])
    if params.get("status"):
        if params["status"] not in STATUSES:
            raise ValidationError(f"Unknown ticket status '{params['status']}'.")
        query["status"] = params["status"]
    if params.get("priority"):
        query["priority"] = params["priority"]
    if params.get("category"):
        query["category"] = params["category"]
    if params.get("assigned_to"):
        query["assigned_to"] = str(params["assigned_to"])
    if params.get("unassigned") in ("true", "1"):
        query["assigned_to"] = None
    if params.get("search"):
        query["subject"] = {"$regex": sanitize_text(params["search"], max_length=60), "$options": "i"}
    total = count("support_tickets", query)
    rows = find("support_tickets", query, sort=[("last_activity_at", -1)],
                skip=(page - 1) * page_size, limit=page_size, projection=_ticket_columns())
    return [document_to_api(row) for row in rows], total


def queue_counts(identity) -> dict[str, Any]:
    match: dict[str, Any] = {} if is_staff(identity) else {"created_by": str(identity.user_id)}
    rows = aggregate("support_tickets",
                     [{"$match": match}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    counts = {status: 0 for status in STATUSES}
    for row in rows:
        counts[str(row["_id"] or "unknown")] = row["count"]
    return {"by_status": counts, "scope": "all tickets" if is_staff(identity) else "own tickets"}


def get_ticket(ticket_id: str, identity) -> dict[str, Any]:
    ticket = _require_ticket(ticket_id)
    _assert_access(ticket, identity)
    payload = document_to_api(ticket)
    messages = find("ticket_messages", {"ticket_id": ticket["_id"]},
                    sort=[("created_at", 1)], limit=200)
    payload["messages"] = [document_to_api(message) for message in messages]
    return payload


def add_message(ticket: dict, author, body: str, *, system: bool = False) -> dict[str, Any]:
    if not body or not body.strip():
        raise ValidationError("A reply needs a message body.")
    staff = is_staff(author)
    moment = now_utc()
    document = {
        "ticket_id": ticket["_id"],
        "ticket_number": ticket.get("ticket_number"),
        "author": str(author.user_id),
        "author_name": author.full_name,
        "author_role": author.role,
        "body": sanitize_text(body, max_length=6000),
        "is_staff": bool(staff),
        "system": bool(system),
        "created_at": moment,
    }
    document["_id"] = oid(insert_one("ticket_messages", document))
    update: dict[str, Any] = {"updated_at": moment, "last_activity_at": moment}
    if staff:
        update["first_response_at"] = ticket.get("first_response_at") or moment
        update["unread_for_user"] = int(ticket.get("unread_for_user") or 0) + 1
    else:
        update["unread_for_staff"] = int(ticket.get("unread_for_staff") or 0) + 1
    update_one("support_tickets", {"_id": ticket["_id"]},
               {"$set": update, "$inc": {"message_count": 1}})
    ticket["message_count"] = int(ticket.get("message_count") or 0) + 1
    return document_to_api(document)


def notify_requester(ticket: dict, author, body: str) -> Optional[dict[str, Any]]:
    """Staff replies reach the requester through the alert notifier (in-app)."""
    recipient = str(ticket.get("created_by") or "")
    if not recipient or recipient == str(author.user_id):
        return None
    created = notifier.notify_user(
        recipient,
        f"Re: ticket {ticket.get('ticket_number')}",
        f"{author.full_name} ({author.role}) replied to your support ticket "
        f"'{ticket.get('subject')}':\n\n{sanitize_text(body, max_length=1200)}",
        kind="support_reply", severity="info", channels=["in_app"],
        link=f"/support/tickets/{ticket.get('_id')}",
        metadata={"ticket_id": str(ticket.get("_id")), "ticket_number": ticket.get("ticket_number"),
                  "author": str(author.user_id), "author_role": author.role},
    )
    return created[0] if created else None


def reply(ticket_id: str, identity, body: str) -> dict[str, Any]:
    """Append a reply to the thread.

    A staff reply also auto-assigns the ticket when it is unassigned and resets the
    requester's unread counter; a requester reply on an agent-held ticket moves it back
    to ``in_progress``. Only staff replies raise a notification.
    """
    ticket = _require_ticket(ticket_id)
    _assert_access(ticket, identity)
    staff = is_staff(identity)
    message = add_message(ticket, identity, body)
    notification = notify_requester(ticket, identity, body) if staff else None
    changes: dict[str, Any] = {}
    if staff:
        changes["unread_for_user"] = 0
        if not ticket.get("assigned_to"):
            changes["assigned_to"] = str(identity.user_id)
            changes["assigned_to_email"] = identity.email
    elif ticket.get("status") == "waiting_user":
        changes["status"] = "in_progress"
    changes["updated_at"] = now_utc()
    changes["last_activity_at"] = now_utc()
    update_one("support_tickets", {"_id": ticket["_id"]}, {"$set": changes})
    return {"message": message,
            "ticket": document_to_api(_require_ticket(str(ticket["_id"]))),
            "staff": staff,
            "notification": document_to_api(notification) if notification else None}


def _validate_assignee(assigned_to: str) -> dict:
    user = users.get_by_id(assigned_to)
    if not user:
        raise NotFound("The assignee account does not exist.")
    if not user.get("is_active", True):
        raise Conflict("Tickets cannot be assigned to a deactivated account.")
    return user


def patch_ticket(ticket_id: str, identity, payload: Any) -> dict[str, Any]:
    """Apply an operator update: status, assignee, priority and/or a reply body.

    Every branch is audited by the caller; this function only enforces the rules.
    """
    ticket = _require_ticket(ticket_id)
    staff = is_staff(identity)
    owner = str(ticket.get("created_by")) == str(identity.user_id)
    if not staff and not owner:
        raise Forbidden("Only the requester or support staff may change this ticket.")

    changes: dict[str, Any] = {}
    actions: list[str] = []

    if payload.assigned_to:
        if not staff:
            raise Forbidden("Only support staff can assign tickets.")
        assignee = _validate_assignee(payload.assigned_to)
        changes["assigned_to"] = str(assignee["_id"])
        changes["assigned_to_email"] = assignee.get("email")
        actions.append("assigned")

    if payload.priority:
        if not staff:
            raise Forbidden("Only support staff can change ticket priority.")
        changes["priority"] = payload.priority
        actions.append("priority")

    if payload.status:
        current = str(ticket.get("status"))
        target = payload.status
        allowed = TRANSITIONS.get(current, ())
        if target != current:
            if target not in allowed:
                raise Conflict(f"Cannot move a ticket from '{current}' to '{target}'. "
                               f"Allowed: {', '.join(allowed) or 'none'}.")
            if not staff and target != "closed":
                raise Forbidden("As the requester you may only close your own ticket; ask an "
                                "agent to move it forward.")
            if not staff and current == "resolved":
                raise Forbidden("Re-opening a resolved ticket requires support staff.")
            changes["status"] = target
            if target == "closed":
                changes["closed_at"] = now_utc()
            actions.append("status")

    notification = None
    if payload.reply:
        if not staff and changes.get("status") in (None, "waiting_user"):
            # A requester reply on an agent-held ticket moves it back to open work.
            if ticket.get("status") == "waiting_user":
                changes.setdefault("status", "in_progress")
        if staff and not changes.get("assigned_to") and not ticket.get("assigned_to"):
            changes["assigned_to"] = str(identity.user_id)
            changes["assigned_to_email"] = identity.email
        message = add_message(ticket, identity, payload.reply)
        actions.append("replied")
        if staff:
            notification = notify_requester(ticket, identity, payload.reply)
            changes["unread_for_user"] = 0

    if changes or actions:
        changes["updated_at"] = now_utc()
        changes["last_activity_at"] = now_utc()
        update_one("support_tickets", {"_id": ticket["_id"]}, {"$set": changes})
    ticket = _require_ticket(str(ticket["_id"]))
    return {"ticket": document_to_api(ticket), "actions": actions,
            "reply_notification": document_to_api(notification) if notification else None,
            "staff": staff}


# --------------------------------------------------------------------------- feedback


def create_feedback(payload: Any, identity) -> dict[str, Any]:
    document = {
        "rating": int(payload.rating),
        "message": sanitize_text(payload.message, max_length=4000),
        "page": sanitize_text(payload.page, max_length=200) if payload.page else None,
        "module": sanitize_text(payload.module, max_length=80) if payload.module else None,
        "created_by": str(identity.user_id),
        "created_by_email": identity.email,
        "created_by_role": identity.role,
        "created_at": now_utc(),
    }
    document["_id"] = oid(insert_one("feedback", document))
    return document_to_api(document)


def _feedback_query(params: dict[str, str]) -> dict[str, Any]:
    """Translate the shared feedback filter dict into a Mongo query."""
    query: dict[str, Any] = {}
    if params.get("created_by"):
        query["created_by"] = str(params["created_by"])
    if params.get("module"):
        query["module"] = params["module"]
    if params.get("page"):
        query["page"] = {"$regex": sanitize_text(params["page"], max_length=80), "$options": "i"}
    if params.get("rating"):
        try:
            query["rating"] = int(params["rating"])
        except ValueError:
            raise ValidationError("rating must be a whole number.") from None
    window: dict[str, Any] = {}
    _from = parse_datetime(params.get("from"))
    _to = parse_datetime(params.get("to"))
    if _from:
        window["$gte"] = _from
    if _to:
        window["$lte"] = _to
    if window:
        query["created_at"] = window
    return query


def list_feedback(params: dict[str, str], *, page: int = 1,
                  page_size: int = 25) -> tuple[list[dict], int]:
    query = _feedback_query(params)
    total = count("feedback", query)
    rows = find("feedback", query, sort=[("created_at", -1)], skip=(page - 1) * page_size,
                limit=page_size)
    return [document_to_api(row) for row in rows], total


def feedback_summary(params: dict[str, str]) -> dict[str, Any]:
    query = _feedback_query(params)
    overall = aggregate("feedback", [{"$match": query}, {"$group": {
        "_id": None, "total": {"$sum": 1}, "average_rating": {"$avg": "$rating"},
        "first": {"$min": "$created_at"}, "last": {"$max": "$created_at"}}}])
    distribution = aggregate("feedback", [
        {"$match": query}, {"$group": {"_id": "$rating", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}])
    by_module = aggregate("feedback", [
        {"$match": query},
        {"$group": {"_id": {"$ifNull": ["$module", "unspecified"]},
                    "total": {"$sum": 1}, "average_rating": {"$avg": "$rating"}}},
        {"$sort": {"total": -1}}, {"$limit": 30},
    ])
    row = overall[0] if overall else {"total": 0, "average_rating": None}
    return {
        "total": row.get("total", 0),
        "average_rating": round(row["average_rating"], 3) if row.get("average_rating") else None,
        "rating_distribution": {str(item["_id"]): item["count"] for item in distribution},
        "by_module": [{
            "module": item["_id"], "total": item["total"],
            "average_rating": round(item["average_rating"], 3) if item.get("average_rating") else None,
        } for item in by_module],
        "first_at": row.get("first").isoformat() if isinstance(row.get("first"), datetime) else None,
        "last_at": row.get("last").isoformat() if isinstance(row.get("last"), datetime) else None,
        "note": "averages are computed with $avg over the stored rating field; no imputation",
    }
