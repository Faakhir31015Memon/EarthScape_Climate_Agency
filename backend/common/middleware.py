"""Request middleware: correlation ids, bounded rate limiting, write flushing,
and security headers.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from django.conf import settings
from django.http import JsonResponse

from common.database import get_database
from config import env

logger = logging.getLogger(__name__)


class CORSAllowListMiddleware:
    """Restrictive CORS: only origins explicitly listed in FRONTEND_ORIGIN."""

    ALLOWED_METHODS = "GET, POST, PATCH, PUT, DELETE, OPTIONS"
    ALLOWED_HEADERS = "Authorization, Content-Type, X-Request-Id, X-Requested-With"

    def __init__(self, get_response):
        self.get_response = get_response
        self.allowed = {origin.rstrip("/") for origin in settings.CORS_ALLOWED_ORIGINS}

    def __call__(self, request):
        origin = (request.headers.get("Origin") or "").rstrip("/")
        is_api = request.path.startswith("/api/")

        if request.method == "OPTIONS" and is_api:
            response = JsonResponse({}, status=200)
            if origin in self.allowed:
                response["Access-Control-Allow-Origin"] = origin
                response["Vary"] = "Origin"
                response["Access-Control-Allow-Methods"] = self.ALLOWED_METHODS
                response["Access-Control-Allow-Headers"] = self.ALLOWED_HEADERS
                response["Access-Control-Max-Age"] = "600"
                response["Access-Control-Allow-Credentials"] = "false"
            else:
                response["X-CORS-Rejected"] = "origin-not-allow-listed"
            return response

        response = self.get_response(request)
        if is_api and origin in self.allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Vary"] = "Origin"
            response["Access-Control-Allow-Credentials"] = "false"
        return response


class CorrelationIdMiddleware:
    """Attach a request id to every log line and error envelope."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:16]
        request.request_id = request_id
        start = time.perf_counter()
        response = self.get_response(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response["X-Request-Id"] = request_id
        response["X-Response-Time-Ms"] = str(duration_ms)
        logger.info(
            "request method=%s path=%s status=%s duration_ms=%s request_id=%s",
            request.method, request.path, response.status_code, duration_ms, request_id,
        )
        return response


class SecurityHeadersMiddleware:
    """Baseline hardening headers. TLS itself is terminated by the reverse proxy."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault("X-Content-Type-Options", "nosniff")
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("Referrer-Policy", "same-origin")
        response.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        response.setdefault("Cache-Control", "no-store")
        if not settings.DEBUG:
            response.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class LocalWriteFlushMiddleware:
    """Persist local-development snapshots after any request that wrote documents."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            get_database().flush_if_dirty()
        except Exception as exc:  # noqa: BLE001 - never break a response over persistence
            logger.error("Snapshot flush failed: %s", exc)
        return response


class SlidingWindowRateLimiter:
    """Process-local limiter. Use a shared store (Redis) when running multiple workers."""

    def __init__(self):
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: Tuple[str, str], limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def retry_after(self, key: Tuple[str, str], window_seconds: int) -> int:
        with self._lock:
            bucket = self._hits.get(key)
            if not bucket:
                return window_seconds
            return max(1, int(window_seconds - (time.monotonic() - bucket[0])) + 1)


_rate_limiter = SlidingWindowRateLimiter()

# path prefix -> (requests per window, window seconds)
POLICIES: Dict[str, Tuple[int, int]] = {
    "/api/v1/auth/login": (env.get_int("RATE_LIMIT_LOGIN", 10), 60),
    "/api/v1/auth/register": (env.get_int("RATE_LIMIT_REGISTER", 5), 300),
    "/api/v1/auth/password-reset": (env.get_int("RATE_LIMIT_PASSWORD_RESET", 5), 900),
    "/api/v1/datasets/upload": (env.get_int("RATE_LIMIT_UPLOAD", 20), 300),
    "/api/v1/feedback": (env.get_int("RATE_LIMIT_FEEDBACK", 10), 300),
    "/api/v1/support/tickets": (env.get_int("RATE_LIMIT_TICKETS", 10), 300),
}
DEFAULT_POLICY = (env.get_int("RATE_LIMIT_DEFAULT", 600), 60)


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if not path.startswith("/api/"):
            return self.get_response(request)

        limit, window = DEFAULT_POLICY
        matched = None
        for prefix, policy in POLICIES.items():
            if path.startswith(prefix):
                limit, window = policy
                matched = prefix
                break

        identity = _client_ip(request)
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            identity = f"user:{user.id}"
        key = (matched or "default", identity)

        if not _rate_limiter.allow(key, limit, window):
            retry_after = _rate_limiter.retry_after(key, window)
            logger.warning("Rate limit hit key=%s limit=%s window=%s", key, limit, window)
            response = JsonResponse(
                {"error": {"code": "rate_limited",
                           "message": "Too many requests. Please slow down and retry.",
                           "request_id": getattr(request, "request_id", None)}},
                status=429,
            )
            response["Retry-After"] = str(retry_after)
            return response

        response = self.get_response(request)
        response["X-RateLimit-Limit"] = str(limit)
        response["X-RateLimit-Window-Seconds"] = str(window)
        return response


def _client_ip(request) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
