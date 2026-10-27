"""Business logic for tickets and feedback.

``views.py`` in this package holds the HTTP layer, :mod:`apps.support.services`
holds everything the API and background workers share:

* Ticket status transitions follow a fixed graph: open → in_progress → resolved →
  closed, plus administrator-only reopen. The rules are enforced in
  :func:`validate_transition`, never on the client.
* Comments are appended (``$push`` on ``comments``) and immutable - there is no
  edit or delete endpoint. ``first_response_at`` is written the first time a
  staff account replies.
* Feedback records are user-authored rows that only administrators can triage;
  every reply is stamped with the triaging account and the moment.
* Summary endpoints compute counts and, when the timestamps support it, the mean
  and median first-response time. Values are ``None`` when they cannot be computed.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.support import serializers as support_serializers  # noqa: E402
from common.api import ADMINISTRATOR, CLIMATE_ANALYST, ApiError  # noqa: E402
from common.repositories import (  # noqa: E402
    RepositoryError,
    feedback as feedback_repo,
    support_tickets as tickets_repo,
    to_object_id,
    utcnow,
)


logger = logging.getLogger(__name__)

STAFF_ROLES = (ADMINISTRATOR, CLIMATE_ANALYST)
CATEGORIES = support_serializers.CATEGORIES
PRIORITIES = support_serializers.PRIORITIES
TICKET_STATUSES = support_serializers.TICKET_STATUSES
FEEDBACK_STATUSES = support_serializers.FEEDBACK_STATUSES
OPEN_STATUSES = ("open", "in_progress")
MAX_MEDIAN_SAMPLE = 2000

# ----------------------------------------------------------------------- transitions
# Legal moves for non-admin accounts, keyed by current status:
_TRANSITIONS: Dict[str, Dict[str, Iterable[str]]] = {
    "open": {"staff": ("in_progress", "resolved", "closed"),
              "author": ("closed",)},
    "in_progress": {"staff": ("open", "resolved", "closed"),
                     "author": ("open", "closed")},
    "resolved": {"staff": ("closed",), "author": ("closed",)},
    "closed": {"staff": (), "author": ()},
}


def is_staff(role: str) -> bool:
    return role in STAFF_ROLES


def validate_transition(document: Dict[str, Any], requested: str, *, actor_role: str,
                         actor_id: str, administrator: bool) -> str:
    """Return the status to apply or raise ``ApiError``.

    * Ordinary authors may always close their own open tickets.
    * Only administrators can reopen a closed ticket.
    * Staff may move a ticket anywhere legal on the graph.
    """
    current = str(document.get("status") or "open")
    if requested == current:
        return current
    if administrator and requested in OPEN_STATUSES and current in ("resolved", "closed"):
        return requested
    bucket = _TRANSITIONS.get(current, {})
    allowed = set()
    if actor_role in STAFF_ROLES:
        allowed |= set(bucket.get("staff", ()))
    if str(document.get("created_by") or "") == actor_id:
        allowed |= set(bucket.get("author", ()))
    if requested not in allowed:
        raise ApiError("validation_failed",
                       f"Cannot move a {current} ticket to {requested}.")
    return requested


# -------------------------------------------------------------------------- tickets
def create_ticket(*, subject: str, description: str, category: str, priority: str,
                  page_context: Optional[str], attached_dataset_id: Optional[str],
                  created_by: str, created_by_role: str) -> str:
    moment = utcnow()
    document = {
        "subject": subject,
        "description": description,
        "category": category,
        "priority": priority,
        "status": "open",
        "created_by": created_by,
        "created_by_role": created_by_role,
        "assigned_to": None,
        "page_context": page_context,
        "attached_dataset_id": attached_dataset_id,
        "comments": [],
        "resolution_note": None,
        "first_response_at": None,
        "resolved_at": None,
        "closed_at": None,
        "created_at": moment,
        "updated_at": moment,
        "schema_version": 3,
    }
    try:
        return tickets_repo.insert_one(document)
    except RepositoryError as exc:
        logger.error("Support ticket could not be stored: %s", exc)
        raise ApiError("internal_error", "The ticket could not be stored.") from exc


def get_ticket(ticket_id: str) -> Dict[str, Any]:
    try:
        document = tickets_repo.find_one({"_id": to_object_id(ticket_id)})
    except RepositoryError as exc:
        raise ApiError("not_found", "No such ticket.") from exc
    if document is None:
        raise ApiError("not_found", "No such ticket.")
    return document


def append_message(*, ticket_id: str, body: str, internal: bool, actor_id: str,
                    actor_name: str, actor_role: str) -> Dict[str, Any]:
    """Append-only thread comment. Returns the refreshed ticket document."""
    document = get_ticket(ticket_id)
    moment = utcnow()
    comment = {
        "author_user_id": actor_id,
        "author_name": actor_name,
        "author_role": actor_role,
        "body": body[:8000],
        "created_at": moment,
        "internal": bool(internal),
    }
    staff = is_staff(actor_role)
    set_fields: Dict[str, Any] = {"updated_at": moment}
    if staff and not document.get("first_response_at"):
        set_fields["first_response_at"] = moment
    if staff and document.get("status") == "open":
        set_fields["status"] = "in_progress"
        set_fields.setdefault("first_response_at", moment)
    elif not staff and document.get("status") in ("resolved", "closed"):
        # A non-staff reply after resolution reopens for the author (a legal transition).
        set_fields["status"] = "open"
        set_fields["reopened_at"] = moment
        set_fields["reopened_by"] = actor_id
    try:
        return tickets_repo.update_by_id(str(document["_id"]),
                                                   {"$push": {"comments": comment},
                                                    "$set": set_fields}) or document
    except RepositoryError as exc:
        logger.error("Ticket comment write failed: %s", exc)
        raise ApiError("internal_error", "The comment could not be stored.") from exc


def close_ticket(*, ticket_id: str, note: str, status: str,
                  actor_id: str, actor_role: str) -> Dict[str, Any]:
    if status not in ("resolved", "closed"):
        raise ApiError("validation_failed", "A ticket closes to 'resolved' or 'closed'.")
    document = get_ticket(ticket_id)
    moment = utcnow()
    resolved_at = document.get("resolved_at")
    if status == "resolved" and not resolved_at:
        resolved_at = moment
    elif status == "closed" and not resolved_at:
        resolved_at = moment
    try:
        return tickets_repo.update_by_id(str(document["_id"]), {
            "status": status,
            "resolution_note": (note or "")[:4000] or None,
            "closed_at": moment if status == "closed" else document.get("closed_at"),
            "resolved_at": resolved_at,
            "updated_at": moment,
            "closed_by": actor_id,
        }, return_document=True) or document
    except RepositoryError as exc:
        raise ApiError("internal_error", "The ticket close could not be stored.") from exc


def assign_ticket(*, ticket_id: str, assignee_id: Optional[str],
                   actor_id: str) -> Dict[str, Any]:
    document = get_ticket(ticket_id)
    moment = utcnow()
    return tickets_repo.update_by_id(str(document["_id"]), {
        "assigned_to": assignee_id or None,
        "assigned_at": moment if assignee_id else None,
        "assigned_by": actor_id,
        "updated_at": moment,
    }, return_document=True) or document


# ----------------------------------------------------------------------- summary
def ticket_summary(request) -> Dict[str, Any]:
    """Ticket counts + median/mean first-response time (``None`` when uncomputable)."""
    user = getattr(request, "user", None)
    staff = bool(user and user.role in STAFF_ROLES)
    scope: Dict[str, Any] = {} if staff else {"created_by": getattr(user, "id", None)}
    by_status = {status: tickets_repo.count({**scope, "status": status}) for status in TICKET_STATUSES}
    by_priority = {priority: tickets_repo.count({**scope, "status": {"$in": list(OPEN_STATUSES)},
                                                   "priority": priority})
                    for priority in PRIORITIES}
    responded = tickets_repo.find({**scope, "first_response_at": {"$ne": None}},
                                        projection={"created_at": 1, "first_response_at": 1},
                                        sort=[("created_at", -1)], limit=MAX_MEDIAN_SAMPLE)
    hours: List[float] = []
    for document in responded:
        created = _as_datetime(document.get("created_at"))
        first = _as_datetime(document.get("first_response_at"))
        if created and first and first >= created:
            hours.append(round((first - created).total_seconds() / 3600.0, 2))
    median = round(statistics.median(hours), 2) if hours else None
    mean = round(statistics.mean(hours), 2) if len(hours) >= 2 else None
    unassigned = None
    if staff:
        unassigned = tickets_repo.count({
            "status": {"$in": list(OPEN_STATUSES)},
            "$or": [{"assigned_to": None}, {"assigned_to": {"$exists": False}}],
        })
    return {
        "open": by_status["open"],
        "in_progress": by_status["in_progress"],
        "resolved": by_status["resolved"],
        "closed": by_status["closed"],
        "open_total": by_status["open"] + by_status["in_progress"],
        "by_status": by_status,
        "by_priority": by_priority,
        "median_first_response_hours": median,
        "mean_first_response_hours": mean,
        "unassigned": unassigned,
        "first_response_sample": len(hours),
        "scope": "all_tickets" if staff else "own_tickets",
        "generated_at": utcnow(),
        "note": "Median/mean are null when no ticket has been answered yet.",
    }


# ----------------------------------------------------------------------- feedback
def create_feedback(*, rating: int, category: str, message: str,
                     page_context: Optional[str], created_by: str,
                     created_by_name: Optional[str]) -> Dict[str, Any]:
    moment = utcnow()
    document = {
        "rating": int(rating),
        "category": category,
        "message": message[:8000],
        "page_context": (page_context or "")[:200] or None,
        "created_by": created_by,
        "created_by_name": created_by_name,
        "status": "new",
        "administrator_reply": None,
        "replied_by": None,
        "replied_at": None,
        "created_at": moment,
        "updated_at": moment,
        "schema_version": 3,
    }
    try:
        document["_id"] = to_object_id(feedback_repo.insert_one(document))
    except RepositoryError as exc:
        logger.error("Feedback could not be stored: %s", exc)
        raise ApiError("internal_error", "Feedback could not be stored.") from exc
    return document


def list_feedback(*, page: int, page_size: int, status: Optional[str] = None,
                   category: Optional[str] = None,
                   created_by: Optional[str] = None) -> Dict[str, Any]:
    query: Dict[str, Any] = {}
    if status:
        query["status"] = status
    if category:
        query["category"] = category
    if created_by:
        query["created_by"] = created_by
    return feedback_repo.paginate(query, page=page, page_size=page_size,
                                       sort=[("created_at", -1)])


def get_feedback(feedback_id: str) -> Dict[str, Any]:
    try:
        document = feedback_repo.find_one({"_id": to_object_id(feedback_id)})
    except RepositoryError as exc:
        raise ApiError("not_found", "No such feedback.") from exc
    if document is None:
        raise ApiError("not_found", "No such feedback.")
    return document


def triage_feedback(*, feedback_id: str, status: Optional[str], reply: Optional[str],
                     category: Optional[str], actor_id: str) -> Dict[str, Any]:
    document = get_feedback(feedback_id)
    updates: Dict[str, Any] = {"updated_at": utcnow()}
    if status:
        updates["status"] = status
    if category:
        updates["category"] = category
    if reply:
        updates["administrator_reply"] = reply[:4000]
        updates["replied_at"] = utcnow()
        updates["replied_by"] = actor_id
    updated = feedback_repo.update_by_id(str(document["_id"]), updates, return_document=True)
    return updated or document


def feedback_summary() -> Dict[str, Any]:
    documents = feedback_repo.find({}, projection={"rating": 1, "category": 1, "status": 1},
                                          limit=5000)
    ratings = [int(row.get("rating")) for row in documents
                  if row.get("rating") is not None]
    by_category = {category: sum(1 for row in documents if row.get("category") == category)
                    for category in CATEGORIES}
    by_status = {status: sum(1 for row in documents if row.get("status") == status)
                  for status in FEEDBACK_STATUSES}
    average = round(sum(ratings) / len(ratings), 3) if ratings else None
    return {
        "average_rating": average,
        "count": int(len(documents)),
        "by_category": by_category,
        "by_status": by_status,
        "generated_at": utcnow(),
    }


def present_feedback(document: Dict[str, Any], request) -> Dict[str, Any]:
    user = getattr(request, "user", None)
    administrator = bool(user and user.role == ADMINISTRATOR)
    payload = {
        "id": str(document.get("id") or document.get("_id")),
        "rating": document.get("rating"),
        "category": document.get("category"),
        "message": document.get("message"),
        "page_context": document.get("page_context"),
        "created_by": document.get("created_by"),
        "created_by_name": document.get("created_by_name"),
        "status": document.get("status"),
        "administrator_reply": document.get("administrator_reply"),
        "replied_by": document.get("replied_by") if administrator else None,
        "replied_at": document.get("replied_at"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }
    return payload


# ------------------------------------------------------------------------ utility
def _as_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


__all__ = [
    "create_ticket", "get_ticket", "append_message", "close_ticket", "assign_ticket",
    "validate_transition", "ticket_summary",
    "create_feedback", "list_feedback", "get_feedback", "triage_feedback",
    "feedback_summary", "present_feedback",
    "CATEGORIES", "PRIORITIES", "TICKET_STATUSES", "FEEDBACK_STATUSES",
    "is_staff",
]
