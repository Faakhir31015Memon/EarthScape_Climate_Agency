"""JWT access tokens plus rotating refresh tokens, revoked server-side.
Refresh tokens are random strings stored only as SHA-256 hashes.
Reuse of a rotated token revokes the whole family.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any, Optional

import jwt
from django.conf import settings

from common.db import collection, find_one, insert_one, now_utc, oid
from common.exceptions import Forbidden, Unauthorized
from common.logging_config import get_logger

logger = get_logger("earthscape.tokens")
ALGORITHM = settings.JWT["ALGORITHM"]
SIGNING_KEY = settings.JWT["SIGNING_KEY"]
ACCESS_MINUTES = int(settings.JWT["ACCESS_MINUTES"])
REFRESH_DAYS = int(settings.JWT["REFRESH_DAYS"])
ISSUER = settings.JWT["ISSUER"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_access(identity) -> tuple[str, int]:
    issued = now_utc()
    expiry = issued + timedelta(minutes=ACCESS_MINUTES)
    payload = {
        **identity.to_jwt_payload(),
        "jti": secrets.token_hex(12),
        "iat": int(issued.timestamp()),
        "exp": int(expiry.timestamp()),
        "iss": ISSUER,
        "typ": "access",
    }
    token = jwt.encode(payload, SIGNING_KEY, algorithm=ALGORITHM)
    return token, ACCESS_MINUTES * 60


def issue_refresh(identity, *, family: str | None = None, device_hint: str = "") -> tuple[str, dict]:
    raw = secrets.token_urlsafe(48)
    expiry = now_utc() + timedelta(days=REFRESH_DAYS)
    document = {
        "user_id": oid(identity.user_id),
        "token_hash": _hash(raw),
        "family": family or secrets.token_hex(8),
        "rotated_from": None,
        "revoked_at": None,
        "revoke_reason": None,
        "device_hint": device_hint[:120],
        "issued_at": now_utc(),
        "expires_at": expiry,
        "last_used_at": now_utc(),
    }
    insert_one("refresh_tokens", document)
    return raw, document


def rotate_refresh(presented: str) -> tuple[dict, str]:
    record = find_one("refresh_tokens", {"token_hash": _hash(presented or "")})
    if not record:
        raise Unauthorized("Refresh token is not recognised. Please sign in again.")
    if record.get("revoked_at"):
        # reuse of a rotated token means it leaked, kill the family
        revoke_family(record["family"], reason="refresh_reuse_detected")
        logger.warning("refresh token reuse detected", extra={"event": "token_reuse"})
        raise Forbidden("This session was ended because a refresh token was reused.")
    if record["expires_at"] < now_utc():
        raise Unauthorized("Refresh token has expired. Please sign in again.")
    user_id = str(record["user_id"])
    successor_raw, _ = issue_refresh(_identity_for(user_id), family=record["family"])
    collection("refresh_tokens").update_one(
        {"_id": record["_id"]},
        {"$set": {"revoked_at": now_utc(), "revoke_reason": "rotated", "last_used_at": now_utc()}},
    )
    collection("refresh_tokens").update_one(
        {"token_hash": _hash(successor_raw)}, {"$set": {"rotated_from": record["token_hash"][:16]}}
    )
    return record, successor_raw


def revoke(raw: str, *, reason: str = "logout") -> bool:
    result = collection("refresh_tokens").update_one(
        {"token_hash": _hash(raw or ""), "revoked_at": None},
        {"$set": {"revoked_at": now_utc(), "revoke_reason": reason}},
    )
    return result.modified_count > 0


def revoke_family(family: str, *, reason: str) -> int:
    result = collection("refresh_tokens").update_many(
        {"family": family, "revoked_at": None},
        {"$set": {"revoked_at": now_utc(), "revoke_reason": reason}},
    )
    return result.modified_count


def revoke_all_for_user(user_id: str, *, reason: str) -> int:
    result = collection("refresh_tokens").update_many(
        {"user_id": oid(user_id), "revoked_at": None},
        {"$set": {"revoked_at": now_utc(), "revoke_reason": reason}},
    )
    return result.modified_count


def active_sessions(user_id: str) -> list[dict]:
    docs = collection("refresh_tokens").find(
        {"user_id": oid(user_id), "revoked_at": None, "expires_at": {"$gt": now_utc()}},
        {"token_hash": 0},
    ).sort("issued_at", -1).limit(50)
    return [
        {
            "id": str(d["_id"]),
            "issued_at": d["issued_at"].isoformat(),
            "expires_at": d["expires_at"].isoformat(),
            "device_hint": d.get("device_hint", ""),
        }
        for d in docs
    ]


def decode_access(token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            SIGNING_KEY,
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise Unauthorized("Access token has expired.") from exc
    except jwt.InvalidIssuerError as exc:
        raise Unauthorized("Access token issuer is not recognised.") from exc
    except jwt.InvalidTokenError as exc:
        raise Unauthorized("Access token is invalid.") from exc
    if payload.get("typ") != "access":
        raise Unauthorized("Expected an access token.")
    return payload


def _identity_for(user_id: str):
    from apps.users.repository import authenticate_identity

    identity = authenticate_identity(user_id)
    if not identity:
        raise Unauthorized("Account is inactive or no longer exists.")
    return identity


def purge_expired() -> int:
    return collection("refresh_tokens").delete_many({"expires_at": {"$lt": now_utc()}}).deleted_count
