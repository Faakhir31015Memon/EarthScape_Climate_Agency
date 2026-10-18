"""Password hashing and JWT token handling.

Authentication is backed by the MongoDB ``users`` collection - not by Django's ORM
``auth.User`` model - so ``django.contrib.auth`` is intentionally not installed.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

from config import env

ALGORITHM = "HS256"
SUPPORTED_ALGORITHMS = ["HS256"]


# ------------------------------------------------------------------------ identifiers
def to_object_id(value: str):
    """Parse a document id string, the way every service in ``apps/`` expects.

    The implementation belongs to the repository layer; it is re-exported here because
    the account services resolve ids through ``common.security``.
    """
    from common.repositories import to_object_id as parse

    return parse(value)


# ------------------------------------------------------------------------ hashing
def hash_password(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        # bcrypt truncates silently at 72 bytes; reject instead of weakening.
        raise ValueError("Password is too long (72 bytes maximum for the configured hash).")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


_PASSWORD_SPECIAL = re.compile(r"[^A-Za-z0-9@.$%&*_=+\-!?#/]")
_SYMBOL = re.compile(r"[^A-Za-z0-9]")


def password_problems(password: str) -> list[str]:
    """Baseline strength rules. Enforced server-side, mirrored client-side."""
    problems: list[str] = []
    value = password or ""
    if len(value) < env.get_int("PASSWORD_MIN_LENGTH", 10):
        problems.append(f"Use at least {env.get_int('PASSWORD_MIN_LENGTH', 10)} characters.")
    if not re.search(r"[A-Z]", value):
        problems.append("Add at least one uppercase letter.")
    if not re.search(r"[a-z]", value):
        problems.append("Add at least one lowercase letter.")
    if not re.search(r"[0-9]", value):
        problems.append("Add at least one number.")
    if env.get_bool("PASSWORD_REQUIRE_SYMBOL", True) and not _SYMBOL.search(value):
        problems.append("Add at least one symbol (@ . $ % & * _ = + - ! ? # /).")
    if _PASSWORD_SPECIAL.search(value):
        problems.append("Avoid unsupported characters.")
    return problems


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", normalize_email(email)))


# ---------------------------------------------------------------------------- JWT
def _signing_key() -> bytes:
    key = env.get_raw("JWT_SIGNING_KEY")
    if not key:
        raise RuntimeError("JWT_SIGNING_KEY is not configured.")
    return key.encode("utf-8")


def access_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=env.get_int("JWT_ACCESS_MINUTES", 20))


def issue_access_token(user: Dict[str, Any], *, jti: Optional[str] = None) -> tuple[str, datetime]:
    expires_at = access_expiry()
    payload = {
        "sub": str(user["_id"]),
        "email": user["email"],
        "role": user["role"],
        "name": user.get("full_name", ""),
        "typ": "access",
        "iat": datetime.now(timezone.utc),
        "exp": expires_at,
        "jti": jti or secrets.token_hex(8),
        "iss": env.get_str("JWT_ISSUER", "earthscape-climate-agency"),
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM), expires_at


def decode_token(token: str) -> Dict[str, Any]:
    return jwt.decode(
        token,
        _signing_key(),
        algorithms=SUPPORTED_ALGORITHMS,
        issuer=env.get_str("JWT_ISSUER", "earthscape-climate-agency"),
        options={"require": ["exp", "iat", "sub", "typ"]},
    )


# ----------------------------------------------------------------- refresh tokens
def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=env.get_int("JWT_REFRESH_DAYS", 7))


def seconds_since(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value).total_seconds()
