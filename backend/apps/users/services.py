"""User administration services (MongoDB ``users`` collection)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from common import audit, security
from common.api import ADMINISTRATOR, ALL_ROLES, ApiError
from common.database import get_database
from common.repositories import users, utcnow
from apps.authentication.services import (
    AuthError,
    create_user,
    public_profile,
    revoke_all_for_user,
)
from config import env

logger = logging.getLogger(__name__)

SORT_FIELDS = {
    "created_at": [("created_at", -1)],
    "name": [("full_name", 1)],
    "email": [("email_normalized", 1)],
    "last_login": [("last_login_at", -1), ("created_at", -1)],
    "role": [("role", 1), ("full_name", 1)],
}


def search(*, query: Optional[str], role: Optional[str], status_filter: Optional[str],
           sort: str, page: int, page_size: int) -> Dict[str, Any]:
    criteria: Dict[str, Any] = {}
    if query:
        escaped = _escape(query)
        criteria["$or"] = [
            {"full_name": {"$regex": escaped, "$options": "i"}},
            {"email_normalized": {"$regex": escaped, "$options": "i"}},
            {"organisation": {"$regex": escaped, "$options": "i"}},
        ]
    if role:
        criteria["role"] = role
    if status_filter == "active":
        criteria["is_active"] = True
    elif status_filter == "inactive":
        criteria["is_active"] = False
    elif status_filter == "locked":
        criteria["locked_until"] = {"$gt": utcnow()}

    result = users.paginate(criteria, page=page, page_size=page_size,
                            sort=SORT_FIELDS.get(sort, SORT_FIELDS["created_at"]))
    for item in result["items"]:
        item.pop("password_hash", None)
        item["locked_until"] = item.get("locked_until")
        item["is_locked"] = bool(item.get("locked_until") and item["locked_until"] > utcnow())
    return result


def detail(user_id: str) -> Dict[str, Any]:
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise ApiError("not_found", "No such account.", status=404)
    public = public_profile(document)
    public["is_locked"] = bool(document.get("locked_until") and document["locked_until"] > utcnow())
    public["sessions_active"] = get_database().collection("refresh_tokens").count_documents(
        {"user_id": user_id, "kind": {"$ne": "password_reset"}, "revoked_at": None,
         "expires_at": {"$gt": utcnow()}}
    )
    return public


def create(*, full_name: str, email: str, password: str, role: str,
           organisation: Optional[str], actor_id: str, request=None,
           email_verified: bool = True) -> Dict[str, Any]:
    if role not in ALL_ROLES:
        raise ApiError("role_invalid", "Unknown role requested.")
    try:
        document = create_user(
            full_name=full_name, email=email, password=password, role=role,
            created_by=actor_id, organisation=organisation, email_verified=email_verified,
        )
    except AuthError as exc:
        # ``apps.authentication.services`` signals with its own error type; the API
        # contract is the platform error envelope, so translate before it leaves.
        raise ApiError(exc.code, exc.message, status=exc.status) from exc
    audit.for_request(request, action="user.created", resource_type="user",
                      resource_id=str(document["_id"]), actor_user_id=actor_id,
                      metadata={"role": role, "email_domain": _domain(email)})
    return public_profile(document)


def update(user_id: str, updates: Dict[str, Any], *, actor_id: str, request=None) -> Dict[str, Any]:
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise ApiError("not_found", "No such account.", status=404)

    allowed = {"full_name", "organisation", "preferences"}
    payload = {key: value for key, value in updates.items() if key in allowed}
    if "email" in updates and updates["email"]:
        normalized = security.normalize_email(str(updates["email"]))
        if not security.valid_email(normalized):
            raise ApiError("email_invalid", "Enter a valid email address.")
        clash = users.find_one({"email_normalized": normalized, "_id": {"$ne": document["_id"]}})
        if clash is not None:
            raise ApiError("email_taken", "Another account already uses that email address.", status=409)
        payload.update({"email": normalized, "email_normalized": normalized})

    if "role" in updates and updates["role"]:
        role = updates["role"]
        if role not in ALL_ROLES:
            raise ApiError("role_invalid", "Unknown role requested.")
        if document.get("role") == ADMINISTRATOR and role != ADMINISTRATOR:
            _guard_last_administrator(exclude_user_id=user_id, request=request, actor_id=actor_id,
                                      action="role_change")
        payload["role"] = role
        # A role change must invalidate already-issued sessions.
        revoke_all_for_user(user_id, reason="role_change")

    if not payload:
        raise ApiError("validation_failed", "No updatable fields were supplied.")

    payload["updated_at"] = utcnow()
    updated = users.update_by_id(user_id, payload, return_document=True)
    audit.for_request(request, action="user.updated", resource_type="user", resource_id=user_id,
                      actor_user_id=actor_id, metadata={"fields": sorted(payload)})
    return public_profile(updated)


def set_active(user_id: str, is_active: bool, *, actor_id: str, request=None) -> Dict[str, Any]:
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise ApiError("not_found", "No such account.", status=404)
    if not is_active:
        if str(document["_id"]) == actor_id:
            raise ApiError("self_deactivation", "You cannot deactivate your own account.", status=400)
        if document.get("role") == ADMINISTRATOR:
            _guard_last_administrator(exclude_user_id=user_id, request=request, actor_id=actor_id,
                                      action="deactivation")
        revoke_all_for_user(user_id, reason="deactivated")

    updated = users.update_by_id(user_id, {"is_active": bool(is_active), "updated_at": utcnow()},
                                 return_document=True)
    audit.for_request(request, action="user.activated" if is_active else "user.deactivated",
                      resource_type="user", resource_id=user_id, actor_user_id=actor_id)
    return public_profile(updated)


def admin_set_password(user_id: str, new_password: str, *, actor_id: str, request=None) -> Dict[str, Any]:
    """Direct administrator password reset (no email required)."""
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise ApiError("not_found", "No such account.", status=404)
    problems = security.password_problems(new_password)
    if problems:
        raise ApiError("password_weak", " ".join(problems))
    users.update_by_id(user_id, {
        "password_hash": security.hash_password(new_password),
        "password_changed_at": utcnow(),
        "updated_at": utcnow(),
        "failed_login_count": 0,
        "locked_until": None,
    })
    revoke_all_for_user(user_id, reason="administrator_password_reset")
    audit.for_request(request, action="user.password_set_by_administrator", resource_type="user",
                      resource_id=user_id, actor_user_id=actor_id)
    return {"status": "password_updated",
            "note": "The account's sessions were revoked. Share the new password out of band."}


def unlock(user_id: str, *, actor_id: str, request=None) -> Dict[str, Any]:
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise ApiError("not_found", "No such account.", status=404)
    users.update_by_id(user_id, {"locked_until": None, "failed_login_count": 0,
                                 "updated_at": utcnow()})
    audit.for_request(request, action="user.unlocked", resource_type="user", resource_id=user_id,
                      actor_user_id=actor_id)
    return {"status": "unlocked"}


def _guard_last_administrator(*, exclude_user_id: str, request, actor_id: str, action: str) -> None:
    active_admins = users.count({"role": ADMINISTRATOR, "is_active": True})
    others = users.count({"role": ADMINISTRATOR, "is_active": True,
                          "_id": {"$ne": security.to_object_id(exclude_user_id)}})
    if others == 0:
        audit.for_request(request, action=f"user.{action}", resource_type="user",
                          resource_id=exclude_user_id, outcome="denied", actor_user_id=actor_id,
                          metadata={"reason": "last_administrator"})
        raise ApiError(
            "last_administrator",
            f"Refusing to {action} the only active administrator account. Provision another "
            "administrator first.",
            status=409,
        )
    _ = active_admins


def role_catalogue() -> List[Dict[str, Any]]:
    from apps.authentication.views import capabilities_for

    descriptions = {
        ADMINISTRATOR: "Full platform control: users, roles, datasets, pipelines, alert rules, "
                       "settings, audit logs and support.",
        "climate_analyst": "Uploads and validates datasets, runs ingestion and processing jobs, "
                           "trains and evaluates models, produces forecasts, reports and alerts.",
        "viewer": "Reads dashboards, maps, forecasts and authorised reports; submits feedback and "
                  "support tickets.",
    }
    counts = {role: users.count({"role": role}) for role in ALL_ROLES}
    return [
        {
            "role": role,
            "label": role.replace("_", " ").title(),
            "description": descriptions[role],
            "capabilities": capabilities_for(role),
            "account_count": counts[role],
            "assignable_by_administrators": True,
            "self_registerable": role == "viewer"
            or (role == "climate_analyst" and env.get_bool("ALLOW_ANALYST_SELF_REGISTRATION")),
        }
        for role in ALL_ROLES
    ]


def _escape(text: str) -> str:
    import re

    return re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", text)[:60]


def _domain(email: str) -> str:
    return (email or "").split("@")[-1].lower()
