"""Dashboard endpoints (``/api/v1/dashboard/``).

Every tile is the result of a real query against MongoDB. Where the platform has
no data yet the field is ``null``/empty together with a human ``reason`` - the UI
can then show setup status instead of a decorative zero.
"""
from __future__ import annotations

import logging

from rest_framework.response import Response

from apps.climate import services
from common.api import ApiError, IsAuthenticatedAccount, MongoJWTAuthentication
from common.views import AuthenticatedView, bool_param, int_param, list_param, str_param

logger = logging.getLogger(__name__)


def account(request):
    """The authenticated account, or an honest 401 (never a fabricated viewer)."""
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        raise ApiError("permission_denied", "Authentication required.", status=401)
    return user


class DashboardSummaryView(AuthenticatedView):
    """Computed platform state for the signed-in account."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]

    def get(self, request):
        user = account(request)
        try:
            payload = services.dashboard_summary(user.id, role=user.role, name=user.full_name)
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dashboard must degrade, never fabricate
            logger.exception("Dashboard summary could not be computed")
            raise ApiError("service_unavailable",
                           "The dashboard could not be computed from the stored data. "
                           f"Reason: {str(exc)[:160]}")
        payload["account"] = {"id": user.id, "role": user.role}
        return Response(payload)


class DashboardActivityView(AuthenticatedView):
    """Recent audit events merged with ingestion/processing job activity."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=10000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=100)
        kinds = list_param(request, "kinds") or list_param(request, "kind") or []
        invalid = [kind for kind in kinds if kind not in services.ACTIVITY_KINDS]
        if invalid:
            raise ApiError("validation_failed",
                           f"kinds must be one of: {', '.join(services.ACTIVITY_KINDS)}.")
        payload = services.activity_stream(
            page=page,
            page_size=page_size,
            kinds=kinds,
            outcome=str_param(request, "outcome", choices=("success", "failure", "denied")),
            action=str_param(request, "action"),
            since=str_param(request, "since"),
        )
        return Response(payload)


class DashboardWidgetsView(AuthenticatedView):
    """Widget catalogue plus whether each widget is computable right now."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]

    def get(self, request):
        user = account(request)
        payload = services.widget_catalogue(user.id, role=user.role)
        if bool_param(request, "compact", False):
            payload["widgets"] = [
                {key: widget[key] for key in ("id", "title", "kind", "endpoint", "computable",
                                              "role_allowed")}
                for widget in payload["widgets"]
            ]
            payload["compact"] = True
        return Response(payload)
