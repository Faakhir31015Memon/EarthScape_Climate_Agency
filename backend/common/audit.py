"""Audit trail persistence (best-effort, never breaks the request it observes)."""

from __future__ import annotations

from typing import Any

from common.db import collection, insert_one, now_utc
from common.logging_config import get_logger, redact
from common.security import sanitize_text

logger = get_logger("earthscape.audit")

ACTION_BY_SEGMENT = {
    "auth": "authentication",
    "users": "user_management",
    "datasets": "dataset",
    "ingestion": "ingestion",
    "processing": "processing",
    "models": "model",
    "predictions": "prediction",
    "anomaly": "anomaly",
    "alerts": "alert",
    "notifications": "notification",
    "reports": "report",
    "analyses": "saved_analysis",
    "support": "support",
    "feedback": "feedback",
}

OUTCOME_MAP = (("deleted", 204), ("failed", (400, 422)), ("denied", (401, 403)), ("blocked", 429))


def derive_action(path: str, method: str) -> tuple[str, str]:
    segments = [s for s in path.split("/") if s]
    try:
        api_index = segments.index("v1")
    except ValueError:
        api_index = 0
    relevant = segments[api_index + 1:] if len(segments) > api_index + 1 else segments
    resource = relevant[0] if relevant else "unknown"
    action = ACTION_BY_SEGMENT.get(resource, resource)
    verb = {"POST": "create", "PATCH": "update", "PUT": "replace", "DELETE": "delete"}.get(method, "write")
    sub = "_".join(relevant[1:3]) if len(relevant) > 1 else ""
    label = f"{action}.{verb}" + (f".{sanitize_text(sub, max_length=24)}" if sub and sub.isalpha() else "")
    return action, label


def classify_outcome(status_code: int) -> str:
    if status_code < 300:
        return "success"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "blocked"
    if status_code in (401, 403):
        return "denied"
    if status_code >= 500:
        return "error"
    return "failed"


def record(identity: str, action: str, *, resource_type: str, resource_id: str | None,
           outcome: str, metadata: dict[str, Any] | None = None, actor_role: str | None = None,
           source_ip: str | None = None, correlation_id: str | None = None, label: str | None = None):
    document = {
        "identity": sanitize_text(identity or "system", max_length=64),
        "actor_role": sanitize_text(actor_role or "service", max_length=24),
        "action": sanitize_text(label or action, max_length=96),
        "resource_type": sanitize_text(resource_type, max_length=48),
        "resource_id": sanitize_text(resource_id, max_length=64) if resource_id else None,
        "outcome": sanitize_text(outcome, max_length=24),
        "occurred_at": now_utc(),
        "source_ip": sanitize_text(source_ip, max_length=64) if source_ip else None,
        "correlation_id": sanitize_text(correlation_id, max_length=64) if correlation_id else None,
        "metadata": redact(metadata or {}),
    }
    try:
        insert_one("audit_logs", document)
    except Exception as exc:  # audit must never take down a live request
        logger.warning("audit write failed: %s", type(exc).__name__,
                       extra={"correlation_id": correlation_id})
    return document


def record_from_request(request, response) -> dict | None:
    identity = getattr(getattr(request, "auth_identity", None), "user_id", None)
    role = getattr(getattr(request, "auth_identity", None), "role", None)
    resource_type, action = derive_action(request.path, request.method)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return record(
        identity or "anonymous",
        resource_type,
        resource_type=resource_type,
        resource_id=None,
        outcome=classify_outcome(getattr(response, "status_code", 0)),
        metadata={"method": request.method, "path": request.path[:200]},
        actor_role=role,
        source_ip=ip,
        correlation_id=getattr(request, "correlation_id", None),
        label=action,
    )


def audit(request, action: str, *, resource_type: str, resource_id=None, outcome="success",
          metadata=None):
    """Explicit audit call for services that need precise labelling."""
    identity = getattr(getattr(request, "auth_identity", None), "user_id", None)
    role = getattr(getattr(request, "auth_identity", None), "role", None)
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    ip = forwarded.split(",")[0].strip() if forwarded else (request.META.get("REMOTE_ADDR") if hasattr(request, "META") else None)
    return record(
        identity or "anonymous",
        action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        metadata=metadata,
        actor_role=role,
        source_ip=ip,
        correlation_id=getattr(request, "correlation_id", None),
    )
