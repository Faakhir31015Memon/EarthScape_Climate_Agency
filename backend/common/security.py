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


# ------------------------------------------------------------------- text guards
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PADDED_WHITESPACE = re.compile(r"[ \t]{2,}")


def sanitize_text(value: Any, *, max_length: int = 500) -> str:
    """Bounded plain text for storage: no control characters, no padded whitespace."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = _CONTROL_CHARACTERS.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _PADDED_WHITESPACE.sub(" ", text)
    return text.strip()[:max(0, int(max_length))]


def guard_query(value: Any, _depth: int = 0) -> Any:
    """Return a JSON-shaped value with no MongoDB operator keys.

    API input here becomes query documents, ``view_state`` or stored settings, so any
    key starting with ``$`` or containing a dot is refused rather than silently escaped.
    """
    if _depth > 12:
        raise ValueError("Value is nested too deeply to guard.")
    if isinstance(value, dict):
        guarded = {}
        for key, item in value.items():
            name = str(key)
            if name.startswith("$") or "." in name:
                raise ValueError(f"Disallowed key: {name}.")
            guarded[name] = guard_query(item, _depth + 1)
        return guarded
    if isinstance(value, (list, tuple)):
        return [guard_query(item, _depth + 1) for item in value]
    return value


def fingerprint(*parts: Any) -> str:
    """Stable sha1 identity for dedupe keys, joined with the canonical ``|`` separator."""
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


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
