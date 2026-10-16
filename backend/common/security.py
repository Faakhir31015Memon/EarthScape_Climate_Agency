"""Password hashing, email normalisation, and input hardening utilities."""

from __future__ import annotations

import hashlib
import re
import unicodedata

import bcrypt

from django.conf import settings

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Operators that only ever appear in MongoDB query documents must never reach a query.
FORBIDDEN_KEY_PREFIX = re.compile(r"^[$.]")


def normalize_email(raw: str) -> str:
    text = (raw or "").strip().lower()
    local, _, domain = text.partition("@")
    if domain and "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}" if domain else text


def is_valid_email(raw: str) -> bool:
    value = normalize_email(raw)
    return bool(value) and len(value) <= 254 and bool(EMAIL_RE.match(value))


def hash_password(password: str) -> str:
    rounds = int(settings.APP["BCRYPT_ROUNDS"])
    # bcrypt truncates silently past 72 bytes; reject rather than weaken.
    encoded = (password or "").encode("utf-8")
    if len(encoded) > 72:
        raise ValueError("Password must not exceed 72 bytes.")
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=min(max(rounds, 10), 15])).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash or not password:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def password_problems(password: str) -> list[str]:
    minimum = int(settings.APP["PASSWORD_MIN_LENGTH"])
    problems: list[str] = []
    if not password or len(password) < minimum:
        problems.append(f"Password must be at least {minimum} characters.")
    if password and len(password) > 128:
        problems.append("Password must not exceed 128 characters.")
    if password and not re.search(r"[A-Za-z]", password):
        problems.append("Password must contain at least one letter.")
    if password and not re.search(r"[0-9]", password):
        problems.append("Password must contain at least one digit.")
    return problems


def sanitize_text(value, *, max_length: int = 400) -> str:
    """Strip control characters and bound length for any user-supplied string."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = CONTROL_CHARS.sub("", text)
    return text.strip()[:max_length]


def guard_query(document):
    """Recursively reject `$`/`.`-prefixed keys so user input cannot inject operators."""
    if isinstance(document, dict):
        clean = {}
        for key, value in document.items():
            if FORBIDDEN_KEY_PREFIX.match(str(key)):
                raise ValueError(f"Disallowed key '{key}' in submitted data.")
            clean[key] = guard_query(value)
        return clean
    if isinstance(document, list):
        return [guard_query(item) for item in document]
    return document


def fingerprint(*parts: str) -> str:
    joined = "|".join((p or "").strip().lower() for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


def mask_identifier(value: str) -> str:
    if not value:
        return "anonymous"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
