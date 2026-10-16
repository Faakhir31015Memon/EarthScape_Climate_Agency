"""Request-scoped middleware: correlation ids, timing, security headers, audit trail."""

from __future__ import annotations

import re
import time
import uuid

from django.utils.deprecation import MiddlewareMixin

from common.audit import record_from_request
from common.logging_config import get_logger

ACCESS_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{20,400}$")
SENSITIVE_PATHS = ("/api/v1/auth/", "/api/v1/support/", "/api/v1/feedback")

logger = get_logger("earthscape.http")


def _bearer_token(request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if ACCESS_TOKEN_RE.match(token):
            return token
    return None


class RequestContextMiddleware(MiddlewareMixin):
    def process_request(self, request):
        request.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        request.started_at = time.perf_counter()
        request.auth_identity = None

    def process_response(self, request, response):
        duration = (time.perf_counter() - getattr(request, "started_at", time.perf_counter())) * 1000
        response["X-Correlation-Id"] = getattr(request, "correlation_id", "")
        response["X-Content-Type-Options"] = "nosniff"
        response["Referrer-Policy"] = "same-origin"
        response["Cache-Control"] = "no-store"
        if request.path.startswith("/api/"):
            logger.info(
                "%s %s -> %s",
                request.method,
                request.path,
                response.status_code,
                extra={
                    "event": "http_request",
                    "correlation_id": getattr(request, "correlation_id", None),
                    "path": request.path,
                    "method": request.method,
                    "status": response.status_code,
                    "duration_ms": round(duration, 1),
                    "user_id": getattr(getattr(request, "auth_identity", None), "user_id", None),
                },
            )
        return response


class SecurityHeadersMiddleware(MiddlewareMixin):
    """Baseline hardening independent of Django's optional middleware stack."""

    def process_response(self, request, response):
        if request.is_secure():
            response["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.setdefault("X-Frame-Options", "DENY")
        return response


class AuditTrailMiddleware(MiddlewareMixin):
    """Persists an audit record for every state-changing API call."""

    def process_response(self, request, response):
        if request.method in ("POST", "PATCH", "PUT", "DELETE") and request.path.startswith("/api/"):
            if any(marker in request.path for marker in ("/auth/refresh", "/auth/login")):
                # Login is audited explicitly by the service so failure reasons can be stored.
                return response
            record_from_request(request, response)
        return response
