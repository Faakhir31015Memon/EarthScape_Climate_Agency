"""Compact API view base: every handler returns the standard success envelope."""

from __future__ import annotations

from rest_framework.views import APIView

from common.responses import created, ok


class ApiView(APIView):
    """Adds the correlation id to every response and a sane default capability."""

    required_capability = "platform.view"
    throttle_scope = "burst"

    def ok(self, request, data=None, *, meta=None, http_status=200):
        return ok(data, meta=meta, http_status=http_status, request=request)

    def created(self, request, data=None, *, meta=None):
        return created(data, meta=meta, request=request)

    def body(self, request):
        return request.data if isinstance(request.data, dict) else {}

    def params(self, request):
        return {k: v[0] for k, v in request.query_params.lists()}


class PublicView(ApiView):
    """Unauthenticated endpoint (landing widgets, health, legal pages)."""

    authentication_classes: list = []
    permission_classes: list = []
    required_capability = ""
