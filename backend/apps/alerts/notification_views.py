"""In-app notification endpoints, mounted at ``/api/v1/notifications/``.

Every read is scoped server-side to ``request.user.id``: the recipient filter is
added by the view, never taken from the client, so one account can never list or
mutate another account's notifications.
"""
from __future__ import annotations

import logging

from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import notifications  # noqa: E402
from apps.alerts import serializers as alert_serializers  # noqa: E402
from common import audit  # noqa: E402
from common.api import ApiError, MongoJWTAuthentication  # noqa: E402
from common.views import (  # noqa: E402
    AuthenticatedView,
    bool_param,
    int_param,
    str_param,
)

logger = logging.getLogger(__name__)

TYPES = ("alert", "report", "system", "ticket")
CHANNELS = ("in_app", "email")
DELIVERY_STATUSES = ("pending", "sent", "failed", "suppressed")


class NotificationView(AuthenticatedView):
    authentication_classes = [MongoJWTAuthentication]

    def recipient_id(self, request) -> str:
        return self.current_user(request).id


class NotificationListView(NotificationView):
    """``GET /api/v1/notifications/`` - the caller's own notifications."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)
        result = notifications.list_for_user(
            self.recipient_id(request),
            page=page,
            page_size=page_size,
            unread_only=bool_param(request, "unread", False),
            type_filter=str_param(request, "type", choices=TYPES),
            channel=str_param(request, "channel", choices=CHANNELS),
            # The web client's ``status`` filter means delivery status here; a recipient
            # can never filter by another account's id.
            delivery_status=str_param(request, "status", choices=DELIVERY_STATUSES),
        )
        result["unread"] = notifications.unread_counts(self.recipient_id(request))
        result["scoped_to"] = "authenticated_user"
        return Response(result)


class UnreadCountView(NotificationView):
    def get(self, request):
        counts = notifications.unread_counts(self.recipient_id(request))
        counts["email_delivery_configured"] = notifications.channels_for_user(
            self.recipient_id(request))["email_configured"]
        return Response(counts)


class NotificationDetailView(NotificationView):
    def get(self, request, notification_id: str):
        document = _read_owned(notification_id, self.recipient_id(request))
        return Response({"notification": document})


class NotificationReadView(NotificationView):
    def post(self, request, notification_id: str):
        _read_owned(notification_id, self.recipient_id(request))
        document = notifications.mark_read(notification_id, self.recipient_id(request))
        return Response({"notification": document})


class NotificationAcknowledgeView(NotificationView):
    def post(self, request, notification_id: str):
        _read_owned(notification_id, self.recipient_id(request))
        document = notifications.acknowledge(notification_id, self.recipient_id(request))
        audit.for_request(request, action="notification.acknowledged",
                         resource_type="notification", resource_id=document.get("id"),
                         metadata={"type": document.get("type")})
        return Response({"notification": document})


class ReadAllView(NotificationView):
    def post(self, request):
        type_filter = str_param(request, "type", choices=TYPES)
        updated = notifications.mark_all_read(self.recipient_id(request), type_filter=type_filter)
        audit.for_request(request, action="notifications.read_all", resource_type="notification",
                         metadata={"updated": updated, "type": type_filter})
        return Response({"status": "read", "updated": updated,
                         "remaining_unread": notifications.unread_counts(
                             self.recipient_id(request))["total"]})


class NotificationPreferencesView(NotificationView):
    """Per-user channel preferences (in-app and email)."""

    def get(self, request):
        return Response(notifications.get_preferences(self.recipient_id(request)))

    def patch(self, request):
        serializer = alert_serializers.NotificationPreferencesSerializer(
            data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        channels = {key: bool(value) for key, value in serializer.validated_data.items()}
        if not channels:
            raise ApiError("validation_failed", "No notification channels were supplied.")
        updated = notifications.set_preferences(self.recipient_id(request), channels)
        audit.for_request(request, action="notification.preferences_updated",
                         resource_type="user", resource_id=self.recipient_id(request),
                         metadata={"channels": sorted(channels)})
        return Response(updated)

    def put(self, request):
        return self.patch(request)


def _read_owned(notification_id: str, user_id: str) -> dict:
    from bson import ObjectId

    if not ObjectId.is_valid(notification_id or ""):
        raise ApiError("not_found", "No such notification for this account.", status=404)
    try:
        return notifications.detail(notification_id, user_id)
    except ValueError:
        raise ApiError("not_found", "No such notification for this account.", status=404)
