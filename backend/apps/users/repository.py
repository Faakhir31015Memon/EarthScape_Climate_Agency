"""MongoDB user repository: the single source of truth for accounts.

Django's `contrib.auth` model layer is unused by design; authentication, role
assignment and lockout state all live in the `users` collection.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from django.conf import settings
from pymongo.errors import DuplicateKeyError

from common.db import collection, find, insert_one, now_utc, oid, update_one
from common.exceptions import Conflict, NotFound, Unauthorized, ValidationError
from common.security import hash_password, normalize_email, password_problems, sanitize_text, valid_email

ROLES = ("administrator", "climate_analyst", "viewer")
ROLE_ALIASES = {
    "admin": "administrator",
    "administrator": "administrator",
    "analyst": "climate_analyst",
    "climate_analyst": "climate_analyst",
    "viewer": "viewer",
    "stakeholder": "viewer",
}


def resolve_role(raw: str | None, default: str = "viewer") -> str:
    candidate = (raw or "").strip().lower()
    if not candidate:
        return default
    if candidate not in ROLE_ALIASES:
        raise ValidationError(f"Unknown role '{raw}'. Allowed roles: {', '.join(ROLES)}.")
    return ROLE_ALIASES[candidate]


def public_view(user: dict) -> dict:
    return {
        "id": str(user["_id"]),
        "full_name": user.get("full_name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "is_active": user.get("is_active", True),
        "email_verified": user.get("email_verified", False),
        "must_change_password": user.get("must_change_password", False),
        "locked_until": user["locked_until"].isoformat() if user.get("locked_until") else None,
        "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        "last_login_at": user["last_login_at"].isoformat() if user.get("last_login_at") else None,
        "preferences": user.get("preferences", {}),
    }


class UserIdentity:
    """Lightweight authenticated principal attached to every request."""

    def __init__(self, user: dict) -> None:
        self.user = user
        self.user_id = str(user["_id"])
        self.email = user.get("email", "")
        self.full_name = user.get("full_name", "")
        self.role = user.get("role", "viewer")
        self.is_authenticated = True
        self.token_jti = user.get("_token_jti")

    @property
    def is_admin(self) -> bool:
        return self.role == "administrator"

    @property
    def is_analyst(self) -> bool:
        return self.role in ("administrator", "climate_analyst")

    def to_jwt_payload(self) -> dict:
        return {
            "sub": self.user_id,
            "email": self.email,
            "role": self.role,
            "name": self.full_name,
        }


def get_by_email(email: str) -> Optional[dict]:
    normalized = normalize_email(email)
    if not normalized:
        return None
    return collection("users").find_one({"email": normalized})


def get_by_id(user_id: str) -> Optional[dict]:
    parsed = oid(user_id)
    if not parsed:
        return None
    return collection("users").find_one({"_id": parsed})


def require(user_id: str) -> dict:
    user = get_by_id(user_id)
    if not user:
        raise NotFound("User account not found.")
    return user


def authenticate_identity(user_id: str) -> Optional[UserIdentity]:
    user = get_by_id(user_id)
    if not user or not user.get("is_active", True):
        return None
    return UserIdentity(user)


def create_user(*, full_name: str, email: str, password: str, role: str = "viewer",
                email_verified: bool = False, must_change_password: bool = False,
                created_by: str | None = None, preferences: dict | None = None) -> dict:
    clean_name = sanitize_text(full_name, max_length=120)
    normalized = normalize_email(email)
    if len(clean_name) < 2:
        raise ValidationError("Full name must contain at least 2 characters.")
    if not valid_email(normalized):
        raise ValidationError("Enter a valid email address.")
    problems = password_problems(password)
    if problems:
        raise ValidationError("; ".join(problems))

    now = now_utc()
    document = {
        "full_name": clean_name,
        "email": normalized,
        "password_hash": hash_password(password),
        "role": resolve_role(role, default=settings.APP["DEFAULT_ROLE"]),
        "is_active": True,
        "email_verified": bool(email_verified),
        "must_change_password": bool(must_change_password),
        "failed_login_count": 0,
        "locked_until": None,
        "preferences": preferences or {"theme": "light", "units": "metric", "digest": "daily"},
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
        "last_login_at": None,
    }
    try:
        document["_id"] = insert_one("users", document)
    except DuplicateKeyError:
        raise Conflict("An account with this email address already exists.") from None
    document.pop("password_hash", None)
    return document


def set_password(user_id: str, password: str) -> dict:
    problems = password_problems(password)
    if problems:
        raise ValidationError("; ".join(problems))
    user = require(user_id)
    update_one(
        "users",
        {"_id": oid(user_id)},
        {"$set": {
            "password_hash": hash_password(password),
            "must_change_password": False,
            "failed_login_count": 0,
            "locked_until": None,
            "updated_at": now_utc(),
        }},
    )
    return user


def set_role(user_id: str, role: str) -> str:
    resolved = resolve_role(role)
    require(user_id)
    update_one("users", {"_id": oid(user_id)}, {"$set": {"role": resolved, "updated_at": now_utc()}})
    return resolved


def set_active(user_id: str, is_active: bool) -> dict:
    user = require(user_id)
    update_one("users", {"_id": oid(user_id)},
               {"$set": {"is_active": bool(is_active), "updated_at": now_utc()}})
    if not is_active:
        from apps.authentication import tokens

        tokens.revoke_all_for_user(user_id, reason="account_deactivated")
    return user


def update_profile(user_id: str, *, full_name: str | None = None,
                   preferences: dict | None = None) -> dict:
    require(user_id)
    update: dict[str, Any] = {"updated_at": now_utc()}
    if full_name is not None:
        clean = sanitize_text(full_name, max_length=120)
        if len(clean) < 2:
            raise ValidationError("Full name must contain at least 2 characters.")
        update["full_name"] = clean
    if preferences:
        allowed = {"theme", "units", "digest", "notifications", "default_region", "chart_range"}
        clean_prefs = {k: sanitize_text(v, max_length=60) for k, v in preferences.items() if k in allowed}
        update["preferences"] = clean_prefs
    update_one("users", {"_id": oid(user_id)}, {"$set": update})
    return require(user_id)


def register_failed_login(user_id: str) -> Optional[datetime]:
    user = require(user_id)
    attempts = int(user.get("failed_login_count", 0)) + 1
    update: dict[str, Any] = {"failed_login_count": attempts, "updated_at": now_utc()}
    lockout_until = None
    if attempts >= int(settings.APP["LOGIN_MAX_FAILURES"]):
        lockout_until = now_utc() + timedelta(minutes=int(settings.APP["LOGIN_LOCKOUT_MINUTES"]))
        update["locked_until"] = lockout_until
        update["failed_login_count"] = 0
    update_one("users", {"_id": oid(user_id)}, {"$set": update})
    return lockout_until


def register_successful_login(user_id: str) -> None:
    update_one("users", {"_id": oid(user_id)},
               {"$set": {"last_login_at": now_utc(), "failed_login_count": 0,
                         "locked_until": None, "updated_at": now_utc()}})


def list_users(*, search: str = "", role: str = "", active: str = "", page: int = 1,
               page_size: int = 25) -> tuple[list[dict], int]:
    query: dict[str, Any] = {}
    if search:
        text = sanitize_text(search, max_length=60)
        query["$or"] = [
            {"full_name": {"$regex": text, "$options": "i"}},
            {"email": {"$regex": text, "$options": "i"}},
        ]
    if role:
        query["role"] = resolve_role(role)
    if active in ("true", "false"):
        query["is_active"] = active == "true"
    total = collection("users").count_documents(query)
    docs = find("users", query, sort=[("created_at", -1)], skip=(page - 1) * page_size, limit=page_size)
    return [public_view(d) for d in docs], total


def count_by_role() -> dict[str, int]:
    pipeline = [{"$group": {"_id": "$role", "count": {"$sum": 1}}}]
    rows = collection("users").aggregate(pipeline)
    counts = {role: 0 for role in ROLES}
    for row in rows:
        counts[row["_id"] or "unknown"] = row["count"]
    return counts


def administrator_exists() -> bool:
    return collection("users").count_documents({"role": "administrator", "is_active": True}) > 0


def change_password(user_id: str, *, current_password: str, new_password: str) -> None:
    from common.security import verify_password

    user = require(user_id)
    if not verify_password(current_password, user.get("password_hash")):
        raise Unauthorized("Current password is incorrect.")
    set_password(user_id, new_password)
