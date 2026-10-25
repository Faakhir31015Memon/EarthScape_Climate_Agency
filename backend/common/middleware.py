"""Request middleware: correlation ids, bounded rate limiting, request coalescing,
write flushing, and security headers.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from typing import Any, Deque, Dict, Optional, Tuple

from django.conf import settings
from django.http import HttpResponse, JsonResponse

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
    # Aggregate reads cost a collection scan, so a client that loops them is stopped
    # by count as well as by the coalescing and slot limits below.
    "/api/v1/climate/": (env.get_int("RATE_LIMIT_ANALYTICS", 60), 60),
    "/api/v1/dashboard/": (env.get_int("RATE_LIMIT_ANALYTICS", 60), 60),
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


# ------------------------------------------------------------------ request coalescing
#: Reads that scan or aggregate the collection. A duplicate of one of these is served
#: from the request that is already running instead of reading MongoDB a second time.
HEAVY_READS = tuple(env.get_raw(
    "ANALYTICS_HEAVY_PATHS",
    "/api/v1/climate/anomalies,/api/v1/climate/correlations,/api/v1/climate/history,"
    "/api/v1/climate/comparison,/api/v1/climate/trends,/api/v1/climate/map-data,"
    "/api/v1/dashboard/summary",
).split(","))

#: Paths whose completed JSON answer may be replayed to an identical duplicate.
COALESCE_PREFIXES = tuple(env.get_raw(
    "REQUEST_COALESCE_PREFIXES", "/api/v1/climate/,/api/v1/dashboard/"
).split(","))

#: How long an identical duplicate is answered from the read that already happened.
#: Shorter than the 5 s job-monitor poll, so live status never comes from the cache.
COALESCE_WINDOW = env.get_int("REQUEST_COALESCE_WINDOW_SECONDS", 3)
#: Longest a duplicate will wait for the leader it joined before running on its own.
COALESCE_MAX_WAIT = env.get_int("REQUEST_COALESCE_MAX_WAIT_SECONDS", 90)
COALESCE_MAX_ENTRIES = env.get_int("REQUEST_COALESCE_MAX_ENTRIES", 64)
COALESCE_MAX_BYTES = env.get_int("REQUEST_COALESCE_MAX_BYTES", 8_000_000)

#: Concurrent heavy reads per process. Beyond this a caller is asked to wait rather
#: than being allowed to queue an unbounded number of aggregation pipelines on Atlas.
HEAVY_MAX_CONCURRENT = env.get_int("ANALYTICS_MAX_CONCURRENT", 4)
HEAVY_SLOT_WAIT = env.get_int("ANALYTICS_SLOT_WAIT_SECONDS", 3)
HEAVY_RETRY_AFTER = env.get_int("ANALYTICS_RETRY_AFTER_SECONDS", 5)

_FLIGHT_LOCK = threading.Lock()
_FLIGHTS: Dict[str, "_Flight"] = {}
#: key -> {"record": response bytes + status, "finished": monotonic clock}
_RECENT: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_RECENT_BYTES = 0
_HEAVY_SLOTS = threading.BoundedSemaphore(max(1, HEAVY_MAX_CONCURRENT))


class _Flight:
    """One heavy read in progress. Late duplicates join it instead of reading again."""

    __slots__ = ("done", "record", "failed")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.record: Optional[Dict[str, Any]] = None
        self.failed = False


def _coalescable(path: str) -> bool:
    return any(path.startswith(prefix.strip()) for prefix in COALESCE_PREFIXES if prefix.strip())


def _is_heavy(path: str) -> bool:
    return any(path.startswith(prefix.strip()) for prefix in HEAVY_READS if prefix.strip())


def _coalesce_key(request) -> str:
    """Identity + endpoint + query. Two callers never share an answer they should not.

    The credential is hashed, never stored, so the bearer token cannot be recovered
    from anything this middleware keeps or logs.
    """
    credential = request.headers.get("Authorization", "")
    identity = hashlib.sha256(credential.encode()).hexdigest()[:16] if credential else _client_ip(request)
    query = "&".join(f"{name}={value}" for name, value in sorted(request.GET.items()))
    return f"{identity}|{request.path}|{query}"


def _record_from(response) -> Optional[Dict[str, Any]]:
    """Keep a replayable copy of a JSON answer; let anything else pass through."""
    if response.status_code != 200 or getattr(response, "streaming", False):
        return None
    content_type = response.get("Content-Type", "")
    if "json" not in content_type:
        return None
    payload = response.content
    if len(payload) > COALESCE_MAX_BYTES:
        return None
    return {"content": payload, "status": response.status_code, "content_type": content_type}


def _remember(record: Dict[str, Any]) -> None:
    global _RECENT_BYTES
    weight = len(record["content"])
    if weight > COALESCE_MAX_BYTES:
        return
    with _FLIGHT_LOCK:
        _RECENT[record["key"]] = {"record": record, "finished": time.monotonic()}
        _RECENT_BYTES += weight
        while len(_RECENT) > COALESCE_MAX_ENTRIES or _RECENT_BYTES > COALESCE_MAX_BYTES:
            _, evicted = _RECENT.popitem(last=False)
            _RECENT_BYTES -= len(evicted["record"]["content"])


def _replay(record: Dict[str, Any], via: str):
    response = HttpResponse(record["content"], status=record["status"])
    response["Content-Type"] = record["content_type"]
    response["X-Request-Coalesced"] = via
    return response


def _forget_after_write() -> None:
    """A write makes every cached read stale; drop them rather than serve the old answer."""
    global _RECENT_BYTES
    with _FLIGHT_LOCK:
        _RECENT.clear()
        _RECENT_BYTES = 0


class RequestCoalescingMiddleware:
    """Answer one request per identical read, and cap how many heavy reads run at once.

    A client that fires the same aggregate twice - a double-mounted component, an
    impatient refresh click, a scripted loop - costs the platform one MongoDB read.
    The first request runs and the others join it, then replay its JSON answer with
    ``X-Request-Coalesced``; a duplicate that arrives inside the short replay window
    is answered from memory without touching the database at all. Nothing is
    invented: the bytes are the same ones the aggregation produced.

    Sustained pressure on a *different* heavy endpoint is met by the slot limit,
    which returns ``429 server_busy`` with a ``Retry-After`` instead of queueing
    unbounded pipelines against Atlas.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            _forget_after_write()
            return self.get_response(request)
        if request.method != "GET" or not _coalescable(path):
            return self.get_response(request)
        if not _is_heavy(path):
            return self._window_only(request, path)
        return self._single_flight(request, path)

    # ------------------------------------------------------ duplicate replay window
    def _window_only(self, request, path: str):
        """Cheap reads are still answered from the window, without occupying a slot."""
        key = _coalesce_key(request)
        with _FLIGHT_LOCK:
            entry = _RECENT.get(key)
            fresh = entry and time.monotonic() - entry["finished"] < COALESCE_WINDOW
            if fresh:
                return _replay(entry["record"], "cached")
            response = self.get_response(request)
        record = _record_from(response)
        if record is not None:
            record["key"] = key
            _remember(record)
        return response

    # ----------------------------------------------------------------- heavy reads
    def _single_flight(self, request, path: str):
        key = _coalesce_key(request)
        with _FLIGHT_LOCK:
            entry = _RECENT.get(key)
            if entry and time.monotonic() - entry["finished"] < COALESCE_WINDOW:
                return _replay(entry["record"], "cached")
            flight = _FLIGHTS.get(key)
            leader = flight is None
            if leader:
                flight = _Flight()
                _FLIGHTS[key] = flight

        if not leader:
            if not flight.done.wait(COALESCE_MAX_WAIT):
                logger.warning("coalesce wait expired path=%s", path)
                return self._run(request)
            if flight.failed or flight.record is None:
                return self._run(request)
            return _replay(flight.record, "coalesced")

        try:
            response = self._run(request)
            record = _record_from(response)
            if record is None:
                flight.failed = True
            else:
                record["key"] = key
                flight.record = record
                _remember(record)
            return response
        finally:
            with _FLIGHT_LOCK:
                _FLIGHTS.pop(key, None)
            flight.done.set()

    def _run(self, request):
        """One heavy read, inside the process-wide slot."""
        if not _HEAVY_SLOTS.acquire(timeout=HEAVY_SLOT_WAIT):
            logger.warning("heavy read slot exhausted path=%s limit=%s", request.path, HEAVY_MAX_CONCURRENT)
            response = JsonResponse(
                {"error": {"code": "server_busy",
                           "message": ("Too many analytics requests are running at once. "
                                       "This one was not started; retry after a few seconds."),
                           "request_id": getattr(request, "request_id", None)}},
                status=429,
            )
            response["Retry-After"] = str(HEAVY_RETRY_AFTER)
            return response
        try:
            return self.get_response(request)
        finally:
            _HEAVY_SLOTS.release()
