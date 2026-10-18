"""Support ticket API, mounted at ``/api/v1/support/``.

Authorisation model (enforced server-side on every route):

* a viewer sees only their own tickets;
* a climate analyst or administrator (``staff``) sees every ticket and may triage
  status, priority and category;
* assignment is administrator only;
* a comment flagged ``internal`` is stored on the ticket but stripped from every
  response a non-staff account receives - including the ticket's own author.

Ticket creation is rate limited by ``common.middleware.RateLimitMiddleware`` (the
policy for ``/api/v1/support/tickets``), so nothing here needs its own counter.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import notifications  # noqa: E402
from apps.support import serializers as support_serializers  # noqa: E402
from apps.support import services as feedback_service  # noqa: E402
from common import audit  # noqa: E402


# The serializers module is shared between tickets and feedback - aliased here so
# the feedback views read a little more clearly.
feedback_serializers = support_serializers
from common.api import (  # noqa: E402
    ADMINISTRATOR,
    CLIMATE_ANALYST,
    ApiError,
    AdministratorOrAnalyst,
    IsAdministrator,
    IsAuthenticatedAccount,
    MongoJWTAuthentication,
)
from common.repositories import (  # noqa: E402
    RepositoryError,
    support_tickets as tickets_repo,
    to_object_id,
    utcnow,
)
from common.views import (  # noqa: E402
    AuthenticatedView,
    bool_param,
    int_param,
    list_param,
    str_param,
)

logger = logging.getLogger(__name__)

STAFF_ROLES = (ADMINISTRATOR, CLIMATE_ANALYST)
CATEGORIES = support_serializers.CATEGORIES
PRIORITIES = support_serializers.PRIORITIES
STATUSES = support_serializers.TICKET_STATUSES
OPEN_STATUSES = ("open", "in_progress")
MAX_MEDIAN_SAMPLE = 2000


class SupportView(AuthenticatedView):
    """Authenticated base: the JWT authenticator is declared, not inherited empty."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]


class StaffView(SupportView):
    permission_classes = [AdministratorOrAnalyst]


class AdministratorView(SupportView):
    permission_classes = [IsAdministrator]


def is_staff(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False) and user.role in STAFF_ROLES)


def require_object_id(value: str, label: str) -> str:
    from bson import ObjectId

    if not ObjectId.is_valid(value or ""):
        raise ApiError("not_found", f"No such {label}.", status=404)
    return value


# ----------------------------------------------------------------------- tickets
class TicketListView(SupportView):
    """``GET/POST /api/v1/support/tickets``."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=200)
        staff = is_staff(request)

        criteria: Dict[str, Any] = {}
        mine = bool_param(request, "mine", False)
        if not staff or mine:
            # A non-staff account can never widen the scope with a filter parameter.
            criteria["created_by"] = request.user.id
        else:
            created_by = str_param(request, "created_by", max_length=24)
            if created_by:
                criteria["created_by"] = created_by
        status_value = str_param(request, "status", choices=STATUSES)
        if status_value:
            criteria["status"] = status_value
        elif str_param(request, "open_only", None):
            criteria["status"] = {"$in": list(OPEN_STATUSES)}
        category = str_param(request, "category", choices=CATEGORIES)
        if category:
            criteria["category"] = category
        priority = str_param(request, "priority", choices=PRIORITIES)
        if priority:
            criteria["priority"] = priority
        assigned = str_param(request, "assigned_to", max_length=24)
        if assigned and staff:
            criteria["assigned_to"] = "unassigned" if assigned == "none" else assigned
        search = str_param(request, "search", max_length=80)
        if search:
            pattern = re.escape(search)
            criteria["$or"] = [
                {"subject": {"$options": "i", "$regex": pattern}},
                {"description": {"$options": "i", "$regex": pattern}},
            ]

        result = tickets_repo.paginate(criteria, page=page, page_size=page_size,
                                       sort=[("created_at", -1)])
        names = _display_names(result["items"])
        result["items"] = [present(document, request, names) for document in result["items"]]
        result["scope"] = "all_tickets" if staff and not mine else "own_tickets"
        result["staff_view"] = staff
        return Response(result)

    def post(self, request):
        serializer = support_serializers.TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        attached = serializer.validated_data.get("attached_dataset_id") or None
        if attached:
            require_object_id(attached, "dataset")
            from common.repositories import datasets

            if datasets.find_one({"_id": to_object_id(attached)}, {"_id": 1}) is None:
                raise ApiError("not_found", "The attached dataset does not exist.", status=404)

        moment = utcnow()
        document = {
            "subject": serializer.validated_data["subject"],
            "description": serializer.validated_data["description"],
            "category": serializer.validated_data.get("category") or "other",
            "priority": serializer.validated_data.get("priority") or "medium",
            "status": "open",
            "created_by": user.id,
            "created_by_role": user.role,
            "assigned_to": None,
            "page_context": serializer.validated_page()[:200] or None,
            "attached_dataset_id": attached,
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
            ticket_id = tickets_repo.insert_one(document)
        except RepositoryError as exc:
            logger.error("Support ticket could not be stored: %s", exc)
            raise ApiError("internal_error", "The ticket could not be stored.") from exc

        audit.for_request(request, action="support_ticket.created", resource_type="support_ticket",
                          resource_id=ticket_id,
                          metadata={"category": document["category"],
                                    "priority": document["priority"]})
        _notify_staff_of_new_ticket(document, ticket_id)
        stored = tickets_repo.find_one({"_id": to_object_id(ticket_id)})
        return Response(present(stored, request, _display_names([stored])), status=201)


class TicketDetailView(SupportView):
    def get(self, request, ticket_id: str):
        document = _owned_or_staff(ticket_id, request)
        return Response(present(document, request, _display_names([document])))

    def patch(self, request, ticket_id: str):
        document = _owned_or_staff(ticket_id, request)
        serializer = support_serializers.TicketUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        payload = {key: value for key, value in serializer.validated_data.items()}
        if not payload:
            raise ApiError("validation_failed", "No updatable ticket fields were supplied.")

        staff = is_staff(request)
        administrator = request.user.role == ADMINISTRATOR
        if not staff:
            # The author may only correct their own description while the ticket is open.
            if set(payload) - {"description"} or document.get("status") not in OPEN_STATUSES:
                raise ApiError("permission_denied",
                               "Only staff can change status, priority, category or assignment.",
                               status=403)
        if "assigned_to" in payload and not administrator:
            raise ApiError("permission_denied", "Only an administrator can assign a ticket.", status=403)

        updates: Dict[str, Any] = {"updated_at": utcnow()}
        if "description" in payload:
            updates["description"] = str(payload["description"]).strip()[:8000]
        if "subject" in payload and staff:
            updates["subject"] = payload["subject"]
        if "category" in payload and staff:
            updates["category"] = payload["category"]
        if "priority" in payload and staff:
            updates["priority"] = payload["priority"]
        if "status" in payload and staff:
            status_value = payload["status"]
            updates["status"] = status_value
            if status_value == "resolved":
                updates["resolved_at"] = utcnow()
            elif status_value == "closed":
                updates["closed_at"] = utcnow()
                updates["resolved_at"] = updates.get("resolved_at") or utcnow()
            elif status_value in OPEN_STATUSES:
                updates["resolved_at"] = None
                updates["closed_at"] = None
        if "assigned_to" in payload:
            assignee = payload["assigned_to"]
            if assignee:
                _require_account(assignee)
            updates["assigned_to"] = assignee
            updates["assigned_at"] = utcnow() if assignee else None
            updates["assigned_by"] = request.user.id

        updated = tickets_repo.update_by_id(str(document["_id"]), updates, return_document=True)
        audit.for_request(request, action="support_ticket.updated", resource_type="support_ticket",
                          resource_id=str(document["_id"]),
                          metadata={"fields": sorted(key for key in updates
                                                     if key not in ("updated_at",))})
        if "status" in updates:
            _notify_author(updated, f"Ticket '{updated.get('subject')}' is now "
                                   f"{updated.get('status')}.", updated.get("status"))
        names = _display_names([updated])
        return Response(present(updated, request, names))

    def delete(self, request, ticket_id: str):
        """Spam control: administrators only, and the audit trail keeps the reference."""
        if request.user.role != ADMINISTRATOR:
            raise ApiError("permission_denied", "Only an administrator can delete a ticket.", status=403)
        document = _read(ticket_id)
        deleted = tickets_repo.delete_by_id(str(document["_id"]))
        audit.for_request(request, action="support_ticket.deleted",
                          resource_type="support_ticket", resource_id=str(document["_id"]),
                          metadata={"subject": str(document.get("subject"))[:120]})
        return Response({"status": "deleted", "deleted": bool(deleted)})


class TicketCommentView(SupportView):
    """``POST /api/v1/support/tickets/<id>/comments`` (also mounted as ``/messages``)."""

    def post(self, request, ticket_id: str):
        document = _owned_or_staff(ticket_id, request)
        serializer = support_serializers.TicketCommentSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        body = serializer.validated_body()
        internal = bool(serializer.validated_data.get("internal"))
        staff = is_staff(request)
        if internal and not staff:
            raise ApiError("permission_denied",
                           "Internal notes are restricted to staff.", status=403)
        if document.get("status") == "closed" and not staff:
            raise ApiError("conflict", "This ticket is closed. Open a new ticket instead.", status=409)

        user = request.user
        comment = {
            "author_user_id": user.id,
            "author_name": user.full_name or user.email,
            "author_role": user.role,
            "body": body[:8000],
            "created_at": utcnow(),
            "internal": bool(internal),
        }
        moment = utcnow()
        set_fields: Dict[str, Any] = {"updated_at": moment}
        if staff and not document.get("first_response_at"):
            set_fields["first_response_at"] = comment["created_at"]
        if staff and document.get("status") == "open":
            # A staff reply moves the ticket into progress; recorded with the reply time.
            set_fields["status"] = "in_progress"
            set_fields.setdefault("first_response_at", comment["created_at"])
        elif not staff and document.get("status") in ("resolved", "closed"):
            set_fields["status"] = "open"
            set_fields["reopened_at"] = moment
            set_fields["reopened_by"] = user.id
        operations: Dict[str, Any] = {"$push": {"comments": comment}, "$set": set_fields}
        updated = tickets_repo.update_by_id(str(document["_id"]), operations,
                                            return_document=True)
        audit.for_request(request, action="support_ticket.commented",
                          resource_type="support_ticket", resource_id=str(document["_id"]),
                          metadata={"internal": bool(internal),
                                    "length": len(body)})
        if staff and not internal:
            _notify_author(updated, f"Staff replied to your ticket "
                                    f"'{(updated or {}).get('subject')}'.", "ticket")
        names = _display_names([updated])
        return Response(present(updated, request, names), status=201)


class TicketCloseView(SupportView):
    """``POST /api/v1/support/tickets/<id>/close`` - author or staff."""

    def post(self, request, ticket_id: str):
        document = _owned_or_staff(ticket_id, request)
        serializer = support_serializers.TicketCloseSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_note()
        if not note and not is_staff(request):
            raise ApiError("validation_failed",
                           "Tell us how it was resolved before closing this ticket.")
        status_value = serializer.validated_data.get("status") or "closed"
        if status_value == "resolved" and not is_staff(request):
            status_value = "closed"
        moment = utcnow()
        updates = {
            "status": status_value,
            "resolution_note": note[:4000] or None,
            "closed_at": moment,
            "resolved_at": document.get("resolved_at") or (moment if status_value == "resolved" else None),
            "updated_at": moment,
            "closed_by": request.user.id,
        }
        updated = tickets_repo.update_by_id(str(document["_id"]), updates, return_document=True)
        audit.for_request(request, action=f"support_ticket.{status_value}",
                          resource_type="support_ticket", resource_id=str(document["_id"]),
                          metadata={"note_length": len(note),
                                    "by_role": request.user.role})
        _notify_author(updated, f"Ticket '{(updated or {}).get('subject')}' was "
                                f"{status_value}.", status_value)
        names = _display_names([updated])
        return Response(present(updated, request, names))


class TicketCategoryListView(SupportView):
    """Enumeration the UI needs, straight from the server-side contract."""

    def get(self, request):
        counts = {category: tickets_repo.count({"category": category}) for category in CATEGORIES}
        return Response({
            "categories": list(CATEGORIES),
            "priorities": list(PRIORITIES),
            "statuses": list(STATUSES),
            "category_labels": {
                "bug": "Something is broken",
                "data_request": "Data request",
                "access": "Account or access",
                "feature": "Feature idea",
                "other": "Other",
            },
            "open_by_category": {category: tickets_repo.count({"category": category,
                                                              "status": {"$in": list(OPEN_STATUSES)}})
                                  for category in CATEGORIES},
            "ticket_counts_by_category": counts,
            "staff_view": is_staff(request),
        })


class SupportSummaryView(SupportView):
    """Open counts by status and priority plus the median first response time."""

    def get(self, request):
        staff = is_staff(request)
        scope = {} if staff else {"created_by": request.user.id}
        by_status = {status: tickets_repo.count({**scope, "status": status})
                     for status in STATUSES}
        by_priority = {priority: tickets_repo.count({**scope, "status": {"$in": list(OPEN_STATUSES)},
                                                     "priority": priority})
                       for priority in PRIORITIES}
        responded = tickets_repo.find(
            {**scope, "first_response_at": {"$ne": None}},
            projection={"created_at": 1, "first_response_at": 1},
            sort=[("created_at", -1)], limit=MAX_MEDIAN_SAMPLE)
        hours: List[float] = []
        for document in responded:
            created = _as_datetime(document.get("created_at"))
            first = _as_datetime(document.get("first_response_at"))
            if created and first and first >= created:
                hours.append(round((first - created).total_seconds() / 3600.0, 2))
        median = round(statistics.median(hours), 2) if hours else None
        unassigned = None
        if staff:
            unassigned = tickets_repo.count({
                "status": {"$in": list(OPEN_STATUSES)},
                "$or": [{"assigned_to": None}, {"assigned_to": {"$exists": False}}],
            })
        return Response({
            "open": by_status["open"],
            "in_progress": by_status["in_progress"],
            "resolved": by_status["resolved"],
            "closed": by_status["closed"],
            "open_total": by_status["open"] + by_status["in_progress"],
            "by_status": by_status,
            "by_priority": by_priority,
            "unassigned": unassigned,
            "median_first_response_hours": median,
            "first_response_sample": len(hours),
            "first_response_sampled": len(responded) >= MAX_MEDIAN_SAMPLE,
            "scope": "all_tickets" if staff else "own_tickets",
            "generated_at": utcnow(),
            "note": "Median is computed from stored created/first-reply timestamps; "
                    "it is null when no ticket has been answered yet.",
        })


# ---------------------------------------------------------------------- feedback
def _present_feedback(document: Dict[str, Any], request, names: Dict[str, str]) -> Dict[str, Any]:
    user = getattr(request, "user", None)
    administrator = bool(user and user.role == ADMINISTRATOR)
    payload = {
        "id": str(document.get("id") or document.get("_id")),
        "rating": document.get("rating"),
        "category": document.get("category"),
        "message": document.get("message"),
        "page_context": document.get("page_context"),
        "created_by": document.get("created_by"),
        "created_by_name": names.get(str(document.get("created_by"))),
        "status": document.get("status"),
        "administrator_reply": document.get("administrator_reply"),
        "replied_by": document.get("replied_by"),
        "replied_at": document.get("replied_at"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }
    if not administrator:
        payload["replied_by"] = None
    return payload


# ------------------------------------------------------------------- presentation
def present(document: Dict[str, Any], request, names: Dict[str, str]) -> Dict[str, Any]:
    staff = is_staff(request)
    comments = [comment for comment in (document.get("comments") or [])
                if staff or not comment.get("internal")]
    ticket_id = str(document.get("id") or document.get("_id"))
    return {
        "id": ticket_id,
        "subject": document.get("subject"),
        "description": document.get("description"),
        "category": document.get("category"),
        "priority": document.get("priority"),
        "status": document.get("status"),
        "created_by": document.get("created_by"),
        "created_by_name": names.get(str(document.get("created_by"))),
        "created_by_role": document.get("created_by_role"),
        "assigned_to": document.get("assigned_to"),
        "assigned_to_name": names.get(str(document.get("assigned_to")))
        if document.get("assigned_to") else None,
        "comments": comments,
        "comment_count_total": len(document.get("comments") or []),
        "comment_count_visible": len(comments),
        "attached_dataset_id": document.get("attached_dataset_id"),
        "page_context": document.get("page_context"),
        "resolution_note": document.get("resolution_note"),
        "first_response_at": document.get("first_response_at"),
        "resolved_at": document.get("resolved_at"),
        "closed_at": document.get("closed_at"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "can_reply": bool(staff or document.get("created_by") == request.user.id),
        "can_triage": bool(staff),
        "can_assign": bool(request.user.role == ADMINISTRATOR),
    }


# ------------------------------------------------------------------------ helpers
def _read(ticket_id: str) -> Dict[str, Any]:
    require_object_id(ticket_id, "ticket")
    try:
        document = tickets_repo.find_one({"_id": to_object_id(ticket_id)})
    except RepositoryError as exc:
        raise ApiError("not_found", "No such ticket.", status=404) from exc
    if document is None:
        raise ApiError("not_found", "No such ticket.", status=404)
    return document


def _owned_or_staff(ticket_id: str, request) -> Dict[str, Any]:
    document = _read(ticket_id)
    if is_staff(request) or document.get("created_by") == request.user.id:
        return document
    # Same answer as a missing ticket: existence of other accounts' tickets is not leaked.
    raise ApiError("not_found", "No such ticket.", status=404)


def _require_account(account_id: str) -> Dict[str, Any]:
    from common.api import find_user_by_id

    document = find_user_by_id(account_id)
    if document is None:
        raise ApiError("validation_failed", "assigned_to does not match an account.")
    if not document.get("is_active", False):
        raise ApiError("validation_failed", "That account is deactivated and cannot be assigned.")
    return document


def _display_names(documents: List[Dict[str, Any]]) -> Dict[str, str]:
    ids = set()
    for document in documents or []:
        if not document:
            continue
        for key in ("created_by", "assigned_to"):
            value = document.get(key)
            if value:
                ids.add(str(value))
    if not ids:
        return {}
    try:
        from common.repositories import users

        found = users.find({"_id": {"$in": [_to_oid(item) for item in ids]}},
                           {"full_name": 1, "email": 1})
    except RepositoryError:
        return {}
    names: Dict[str, str] = {}
    for item in found:
        names[str(item["_id"])] = item.get("full_name") or item.get("email") or "Unknown"
    return names


def _to_oid(value: str):
    from bson import ObjectId

    return ObjectId(value) if ObjectId.is_valid(value) else value


def _notify_staff_of_new_ticket(document: Dict[str, Any], ticket_id: str) -> None:
    title = f"New support ticket: {document.get('subject')}"
    body = (f"{document.get('category')} / {document.get('priority')} priority. "
            f"Submitted by {document.get('created_by_name') or document.get('created_by_role')} "
            f"account.")
    _queue(["role:administrator"], title, body, ticket_id, document.get("priority"))


def _notify_author(document: Optional[Dict[str, Any]], message: str, kind: str) -> None:
    if not document:
        return
    author = document.get("created_by")
    if not author:
        return
    ticket_id = str(document.get("id") or document.get("_id"))
    _queue([str(author)], f"Support ticket update: {document.get('subject')}",
           message, ticket_id, None)


def _queue(recipients: List[str], title: str, body: str, ticket_id: str,
           priority: Optional[str]) -> None:
    """Ticket notices must never break the ticket write, so failures are logged only."""
    try:
        notifications.queue_for_recipients(
            _expand(recipients),
            type="ticket",
            title=title[:200],
            body=body[:4000],
            dedupe_key=f"ticket:{ticket_id}:{title[:60]}",
            related_resource={"type": "support_ticket", "id": ticket_id},
            severity={"urgent": "critical", "high": "warning"}.get(str(priority)),
        )
    except Exception as exc:  # noqa: BLE001 - notification fan-out is best effort
        logger.warning("Ticket notification could not be queued (%s)", type(exc).__name__)


def _expand(recipients: List[str]) -> List[str]:
    from common.repositories import users

    expanded: List[str] = []
    for token in recipients:
        if token.startswith("role:"):
            role = token.split(":", 1)[1]
            expanded.extend(str(item["_id"]) for item in
                            users.find({"role": role, "is_active": True}, {"_id": 1}, limit=500))
        else:
            expanded.append(token)
    return expanded


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


# -------------------------------------------------------------------------- feedback
class FeedbackListView(SupportView):
    """``GET/POST /api/v1/feedback`` - paginated list + create.

    A viewer only sees their own submissions; staff see every record. The
    feedback text itself is stored as-is and rendered by the React client
    (which escapes it) - the backend never injects HTML or evaluates it.
    """

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=200)
        status_value = str_param(request, "status", choices=feedback_serializers.FEEDBACK_STATUSES)
        category = str_param(request, "category", choices=CATEGORIES)
        staff = is_staff(request)
        created_by_filter = None
        if not staff:
            created_by_filter = request.user.id
        else:
            created_by_filter = str_param(request, "created_by", max_length=24) or None
        result = feedback_service.list_feedback(
            page=page, page_size=page_size, status=status_value,
            category=category, created_by=created_by_filter)
        names = _display_names(result["items"])
        result["items"] = [
            {**feedback_service.present_feedback(document, request),
             "created_by_name": document.get("created_by_name")
             or names.get(str(document.get("created_by")))}
            for document in result["items"]
        ]
        result["scope"] = "all" if staff else "own"
        return Response(result)

    def post(self, request):
        serializer = feedback_serializers.FeedbackCreateSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        message = serializer.validated_message()
        category = serializer.validated_category()
        document = feedback_service.create_feedback(
            rating=int(serializer.validated_data["rating"]),
            category=category,
            message=message,
            page_context=str(serializer.validated_data.get("page_context") or "")[:200],
            created_by=request.user.id,
            created_by_name=request.user.full_name or request.user.email)
        audit.for_request(request, action="feedback.created", resource_type="feedback",
                          resource_id=str(document["_id"]),
                          metadata={"rating": document["rating"], "category": category,
                                    "length": len(message)})
        payload = feedback_service.present_feedback(document, request)
        payload["created_by_name"] = document.get("created_by_name")
        return Response(payload, status=201)


class FeedbackDetailView(SupportView):
    """``GET/PATCH /api/v1/feedback/<id>`` - PATCH is administrator-only triage."""

    def get(self, request, feedback_id: str):
        document = feedback_service.get_feedback(feedback_id)
        staff = is_staff(request)
        owner = str(document.get("created_by") or "") == str(getattr(request.user, "id", ""))
        if not staff and not owner:
            raise ApiError("not_found", "No such feedback.", status=404)
        return Response(feedback_service.present_feedback(document, request))

    def patch(self, request, feedback_id: str):
        if request.user.role != ADMINISTRATOR:
            raise ApiError("permission_denied",
                           "Only an administrator can triage feedback.", status=403)
        document = feedback_service.get_feedback(feedback_id)
        serializer = feedback_serializers.FeedbackUpdateSerializer(data=request.data or {},
                                                                          partial=True)
        serializer.is_valid(raise_exception=True)
        reply = serializer.validated_reply() or None
        status_value = serializer.validated_data.get("status")
        category = serializer.validated_data.get("category")
        updated = feedback_service.triage_feedback(
            feedback_id=str(document["_id"]),
            status=status_value, reply=reply, category=category,
            actor_id=request.user.id)
        audit.for_request(request, action="feedback.triaged", resource_type="feedback",
                          resource_id=str(document["_id"]),
                          metadata={"status": status_value, "category": category,
                                    "reply_length": len(reply) if reply else 0})
        return Response(feedback_service.present_feedback(updated, request))


class FeedbackSummaryView(SupportView):
    """``GET /api/v1/feedback/summary`` - average rating + counts."""

    def get(self, request):
        payload = feedback_service.feedback_summary()
        payload["scope"] = "all" if is_staff(request) else "own_only_summary_reported_globally"
        return Response(payload)

