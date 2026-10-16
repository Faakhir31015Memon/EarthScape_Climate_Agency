"""Mongo-backed rate limiting.

Uses the application database as the counter store so limits survive a restart and are
shared across worker processes without requiring Redis.
"""

from __future__ import annotations

import math
import re

from pymongo.errors import PyMongoError
from rest_framework.throttling import BaseThrottle

from common.db import collection, now_utc
from common.logging_config import get_logger

logger = get_logger("earthscape.throttle")

_RANGES = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_RATE_RE = re.compile(r"^(\d+)/([smhd])$")


def parse_rate(rate: str) -> tuple[int, int]:
    match = _RATE_RE.match((rate or "").strip())
    if not match:
        return 0, 1
    amount = int(match.group(1))
    window = _RANGES[match.group(2)]
    return amount, window


def _identities(request) -> list[str]:
    parts: list[str] = []
    identity = getattr(request, "auth_identity", None)
    if identity is not None and getattr(identity, "user_id", None):
        parts.append(f"user:{identity.user_id}")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    if ip:
        parts.append(f"ip:{ip}")
    return parts or ["ip:unknown"]


class ScopedRateThrottle(BaseThrottle):
    """Apply the throttle scope named by `throttle_scope` on the view."""

    def __init__(self, scope: str | None = None):
        self.override_scope = scope
        self.key = ""

    def get_scope(self, request, view) -> str:
        if self.override_scope:
            return self.override_scope
        return getattr(view, "throttle_scope", "") or "burst"

    def allow_request(self, request, view) -> bool:
        scope = self.get_scope(request, view)
        if not request.auth:  # authenticated requests are throttled, public ones by ip below
            pass
        from django.conf import settings

        rate = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].get(scope)
        if not rate:
            return True
        limit, window = parse_rate(rate)
        if limit <= 0:
            return True
        identities = _identities(request)
        primary = f"{scope}:{identities[0]}"
        now = now_utc()
        table = collection("rate_limits")
        try:
            doc = table.find_one_and_update(
                {"key": primary},
                {
                    "$inc": {"count": 1},
                    "$set": {"window_start": now, "updated_at": now, "scope": scope},
                    "$setOnInsert": {"window_seconds": window},
                },
                upsert=True,
                return_document=True,
            )
            if doc is None:
                doc = table.find_one({"key": primary}) or {}
            window_start = doc.get("window_start", now)
            count = doc.get("count", 1)
            if (now - window_start).total_seconds() > window:
                table.update_one(
                    {"key": primary},
                    {"$set": {"count": 1, "window_start": now, "updated_at": now}},
                )
                return True
            if count > limit:
                wait_seconds = max(1, math.ceil(window - (now - window_start).total_seconds()))
                request.throttle_wait = wait_seconds
                logger.warning(
                    "rate limit hit",
                    extra={"event": "rate_limited", "correlation_id": getattr(request, "correlation_id", None),
                           "path": request.path},
                )
                return False
            return True
        except PyMongoError as exc:
            # Fail open on a counter-store outage; the API must stay available.
            logger.warning("throttle store unavailable: %s", type(exc).__name__)
            return True

    def wait(self) -> int:
        return int(getattr(self, "_wait", 1) or 1)
