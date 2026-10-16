"""HTTP layer for support tickets and ticket threads.

Access control is delegated to ``apps.support.services`` (which knows the difference
between the requester and ``support.manage``); the views only parse input, run the
operation and record the audit entry for it.
"""

from __future__ import annotations

from apps.support import services
from common.audit import audit
from common.pagination import normalize_pagination
from common.responses import paginated
from common.validators import (FeedbackRequest, TicketMessageRequest, TicketPatchRequest,
                               TicketRequest, parse)
from common.views import ApiView


class TicketListView(ApiView):
    """Own tickets for every role; every ticket for support staff."""

    required_capability = "support.create"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = services.list_tickets(request.auth_identity, params,
                                             page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"scope": "all" if services.is_staff(request.auth_identity)
                                else "own_tickets",
                                "counts": services.queue_counts(request.auth_identity)["by_status"]},
                         request=request)

    def post(self, request):
        payload = parse(TicketRequest, self.body(request))
        ticket = services.create_ticket(payload, request.auth_identity)
        audit(request, "ticket.create", resource_type="support_tickets",
              resource_id=ticket.get("id"),
              metadata={"ticket_number": ticket.get("ticket_number"),
                        "category": ticket.get("category"), "priority": ticket.get("priority")})
        return self.created(request, ticket)


class TicketQueueView(ApiView):
    """Status histogram for the staff queue (or the requester's own slice)."""

    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, services.queue_counts(request.auth_identity))


class TicketDetailView(ApiView):
    required_capability = "platform.view"

    def get(self, request, ticket_id: str):
        return self.ok(request, services.get_ticket(ticket_id, request.auth_identity))

    def patch(self, request, ticket_id: str):
        payload = parse(TicketPatchRequest, self.body(request))
        result = services.patch_ticket(ticket_id, request.auth_identity, payload)
        identity = request.auth_identity
        for action in result["actions"]:
            audit(request, f"ticket.{action}", resource_type="support_tickets",
                  resource_id=result["ticket"].get("id"),
                  metadata={"ticket_number": result["ticket"].get("ticket_number"),
                            "actor_role": identity.role, "staff": result["staff"],
                            "status": result["ticket"].get("status"),
                            "assigned_to": result["ticket"].get("assigned_to"),
                            "priority": result["ticket"].get("priority"),
                            "notification": bool(result.get("reply_notification"))})
        return self.ok(request, result["ticket"])


class TicketMessageView(ApiView):
    """Append a reply to the thread; staff replies notify the requester in-app."""

    required_capability = "platform.view"
    http_method_names = ["post"]

    def post(self, request, ticket_id: str):
        payload = parse(TicketMessageRequest, self.body(request))
        result = services.reply(ticket_id, request.auth_identity, payload.body)
        ticket = result["ticket"]
        audit(request, "ticket.reply", resource_type="ticket_messages",
              resource_id=result["message"].get("id"),
              metadata={"ticket_id": ticket.get("id"), "ticket_number": ticket.get("ticket_number"),
                        "is_staff": result["message"].get("is_staff"),
                        "actor_role": request.auth_identity.role,
                        "notified": bool(result.get("notification"))})
        return self.created(request, {"message": result["message"], "ticket": ticket,
                                      "requester_notified": bool(result.get("notification"))})


class FeedbackView(ApiView):
    """POST accepts a rating; GET returns the caller's own submissions."""

    required_capability = "feedback.create"
    throttle_scope = "feedback"

    def post(self, request):
        payload = parse(FeedbackRequest, self.body(request))
        feedback = services.create_feedback(payload, request.auth_identity)
        audit(request, "feedback.create", resource_type="feedback", resource_id=feedback.get("id"),
              metadata={"rating": feedback.get("rating"), "module": feedback.get("module"),
                        "page": feedback.get("page")})
        return self.created(request, feedback)

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        filters = {key: params[key] for key in ("module", "page", "rating", "from", "to")
                   if params.get(key)}
        filters["created_by"] = str(request.auth_identity.user_id)
        items, total = services.list_feedback(filters, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"scope": "own_feedback",
                                "summary": services.feedback_summary(
                                    {key: value for key, value in filters.items()
                                     if key != "created_by"})},
                         request=request)


class FeedbackAdminView(ApiView):
    """Administrator roll-up: every submission plus real rating averages."""

    required_capability = "feedback.view"
    throttle_scope = "feedback"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        filters = {key: params[key] for key in ("module", "page", "rating", "from", "to")
                   if params.get(key)}
        items, total = services.list_feedback(filters, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"scope": "all_feedback",
                                "summary": services.feedback_summary(filters)},
                         request=request)
