"""Audit trail writes to `audit_logs`; secret-shaped keys never reach metadata."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from common.repositories import audit_logs, utcnow
from config import env

logger = logging.getLogger(__name__)

REDACTED_KEYS = {
    "password", "password_hash", "current_password", "new_password", "token",
    "access", "refresh", "refresh_token", "authorization", "api_key", "apikey",
    "secret", "uri", "connection_string", "cookie",
}


def scrub(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not metadata:
        return {}
    cleaned: Dict[str, Any] = {}
    for key, value in metadata.items():
        if key.lower() in REDACTED_KEYS:
            cleaned[key] = "[redacted]"
        elif isinstance(value, dict):
            cleaned[key] = scrub(value)
        elif isinstance(value, str) and any(token in value for token in ("mongodb://", "mongodb+srv://")):
            cleaned[key] = "[redacted-connection-string]"
        elif isinstance(value, (str, int, float, bool, type(None))):
            cleaned[key] = value[:200] if isinstance(value, str) else value
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [str(item)[:80] for item in list(value)[:20]]
    return cleaned


def record(
    *,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    outcome: str = "success",
    actor_user_id: Optional[str] = None,
    actor_role: Optional[str] = None,
    request=None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    document = {
        "actor_user_id": actor_user_id,
        "actor_role": actor_role or "system",
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "metadata": scrub(metadata),
        "request_id": getattr(request, "request_id", None),
        "source_ip": _client_ip(request) if request is not None else None,
        "environment": env.get_str("DEPLOYMENT_ENVIRONMENT", "development"),
        "created_at": utcnow(),
    }
    try:
        return audit_logs.insert_one(document)
    except Exception as exc:  # noqa: BLE001 - auditing must not break the request
        logger.error("Audit write failed for %s: %s", action, exc)
        return ""


def for_request(request, **kwargs) -> str:
    user = getattr(request, "user", None)
    kwargs.setdefault("actor_user_id", getattr(user, "id", None))
    kwargs.setdefault("actor_role", getattr(user, "role", "anonymous"))
    kwargs.setdefault("request", request)
    return record(**kwargs)


def _client_ip(request) -> Optional[str]:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
