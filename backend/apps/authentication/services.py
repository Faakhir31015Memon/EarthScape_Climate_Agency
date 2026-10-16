"""Authentication services: login with lockout, registration policy, password recovery."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.exceptions import ImproperlyConfigured

from apps.authentication import tokens
from apps.users import repository
from apps.users.repository import UserIdentity, resolve_role
from common.db import collection, find_one, now_utc, oid
from common.exceptions import (Forbidden, ServiceUnavailableConfig, Throttled, Unauthorized,
                               ValidationError)
from common.logging_config import get_logger
from common.security import normalize_email, sanitize_text, verify_password

logger = get_logger("earthscape.auth.services")


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "") if hasattr(request, "META") else ""
    return (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")) or ""


def login(request, *, email: str, password: str, device_hint: str = "") -> dict:
    """Verify credentials, enforce lockout, and issue a token pair."""
    normalized = normalize_email(email)
    user = repository.get_by_email(normalized)

    # Uniform failure message: do not reveal whether an account exists.
    generic = "Email or password is incorrect."
    if user is None:
        _audit(request, "auth.login", outcome="denied", detail="unknown_email")
        raise Unauthorized(generic)

    locked_until = user.get("locked_until")
    if locked_until and locked_until > now_utc():
        remaining = int((locked_until - now_utc()).total_seconds())
        _audit(request, "auth.login", outcome="blocked", detail="locked_out", user_id=str(user["_id"]))
        raise Throttled(
            f"Too many failed attempts. Try again in {max(remaining, 1)} seconds.",
        )

    if not user.get("is_active", True):
        _audit(request, "auth.login", outcome="denied", detail="inactive_account", user_id=str(user["_id"]))
        raise Forbidden("This account has been deactivated. Contact an administrator.")

    if not verify_password(password, user.get("password_hash")):
        lockout = repository.register_failed_login(str(user["_id"]))
        _audit(request, "auth.login", outcome="denied", detail="bad_password", user_id=str(user["_id"]))
        if lockout:
            raise Throttled(
                f"Account temporarily locked after repeated failures. Retry after "
                f"{lockout.isoformat()}."
            )
        raise Unauthorized(generic)

    repository.register_successful_login(str(user["_id"]))
    identity = UserIdentity(user)
    access, expires_in = tokens.issue_access(identity)
    refresh, _record = tokens.issue_refresh(identity, device_hint=device_hint or _client_ip(request))
    _audit(request, "auth.login", outcome="success", user_id=identity.user_id, role=identity.role)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "user": repository.public_view(repository.require(identity.user_id)),
    }


def refresh(request, *, refresh_token: str) -> dict:
    record, successor = tokens.rotate_refresh(refresh_token)
    identity = repository.authenticate_identity(str(record["user_id"]))
    if identity is None:
        tokens.revoke(successor, reason="account_inactive")
        raise Unauthorized("Account is inactive or no longer exists.")
    access, expires_in = tokens.issue_access(identity)
    _audit(request, "auth.refresh", outcome="success", user_id=identity.user_id)
    return {
        "access_token": access,
        "refresh_token": successor,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "user": repository.public_view(repository.require(identity.user_id)),
    }


def logout(request, *, refresh_token: str | None, all_sessions: bool, identity) -> dict:
    if all_sessions:
        revoked = tokens.revoke_all_for_user(identity.user_id, reason="logout_all_sessions")
    elif refresh_token:
        revoked = 1 if tokens.revoke(refresh_token, reason="logout") else 0
    else:
        revoked = 0
    _audit(request, "auth.logout", outcome="success", user_id=identity.user_id,
           metadata={"revoked_sessions": revoked})
    return {"revoked_sessions": revoked}


def register(request, *, full_name: str, email: str, password: str, organisation: str = "") -> dict:
    """Self-service sign-up is closed by default; accounts are otherwise provisioned."""
    if not settings.APP["ALLOW_SELF_REGISTRATION"]:
        _audit(request, "auth.register", outcome="denied", detail="registration_disabled")
        raise Forbidden(
            "Self-service registration is disabled on this deployment. An administrator "
            "provisions accounts; use scripts/provision_admin.py for the first one."
        )
    user = repository.create_user(
        full_name=full_name,
        email=email,
        password=password,
        role=settings.APP["DEFAULT_ROLE"],
        email_verified=False,
    )
    if organisation:
        collection("users").update_one(
            {"_id": user["_id"]},
            {"$set": {"organisation": sanitize_text(organisation, max_length=140)}},
        )
    _audit(request, "auth.register", outcome="success", user_id=str(user["_id"]))
    return repository.public_view(user)


def request_password_reset(request, *, email: str) -> dict:
    """Create a single-use reset token and deliver it through the configured channel."""
    normalized = normalize_email(email)
    user = repository.get_by_email(normalized)
    if user is None:
        # Respond identically so the endpoint cannot be used to enumerate accounts.
        return {"accepted": True, "channel": "email",
                "note": "If an account matches that address, a recovery link has been sent."}

    raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expires = now_utc() + timedelta(minutes=int(settings.APP["PASSWORD_RESET_MINUTES"]))
    collection("password_resets").update_many({"user_id": user["_id"], "used_at": None},
                                              {"$set": {"revoked_at": now_utc()}})
    collection("password_resets").insert_one({
        "user_id": user["_id"],
        "token_hash": token_hash,
        "issued_at": now_utc(),
        "expires_at": expires,
        "used_at": None,
        "revoked_at": None,
        "requested_from": _client_ip(request),
    })

    if not settings.EMAIL_ENABLED:
        _audit(request, "auth.password_reset", outcome="deferred", user_id=str(user["_id"]),
               metadata={"reason": "smtp_not_configured"})
        raise ServiceUnavailableConfig(
            "Password recovery email is not configured on this deployment. An administrator "
            "can reset the password from User management, or set EMAIL_HOST, EMAIL_HOST_USER, "
            "EMAIL_HOST_PASSWORD and DEFAULT_FROM_EMAIL to enable self-service recovery.",
            code="email_not_configured",
        )

    link = f"{settings.APP['PUBLIC_BASE_URL']}/reset-password?token={raw}"
    try:
        send_mail(
            subject="EarthScape Climate Agency password recovery",
            message=(
                "Use this secure link to choose a new password. It expires at "
                f"{expires.isoformat()} and can be used once.\n\n{link}\n\n"
                "If you did not request this, no action is needed."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[normalized],
            fail_silently=False,
        )
    except (ImproperlyConfigured, Exception) as exc:
        logger.error("recovery email failed: %s", type(exc).__name__)
        collection("password_resets").update_one(
            {"token_hash": token_hash}, {"$set": {"revoked_at": now_utc()}}
        )
        raise ServiceUnavailableConfig("The email provider rejected the recovery message. "
                                       "Try again later or contact an administrator.",
                                       code="email_send_failed") from exc

    _audit(request, "auth.password_reset", outcome="success", user_id=str(user["_id"]))
    return {"accepted": True, "channel": "email",
            "note": "A recovery link has been sent. It expires in "
                    f"{settings.APP['PASSWORD_RESET_MINUTES']} minutes."}


def confirm_password_reset(request, *, token: str, new_password: str) -> dict:
    token_hash = hashlib.sha256((token or "").encode("utf-8")).hexdigest()
    record = find_one("password_resets", {"token_hash": token_hash})
    if not record or record.get("used_at") or record.get("revoked_at"):
        raise Unauthorized("This recovery link is not valid. Request a new one.")
    if record["expires_at"] < now_utc():
        raise Unauthorized("This recovery link has expired. Request a new one.")
    user_id = str(record["user_id"])
    repository.set_password(user_id, new_password)
    collection("password_resets").update_one({"_id": record["_id"]}, {"$set": {"used_at": now_utc()}})
    tokens.revoke_all_for_user(user_id, reason="password_reset")
    _audit(request, "auth.password_reset.confirm", outcome="success", user_id=user_id)
    return {"password_changed": True, "sessions_revoked": True}


def current_session(request) -> dict:
    from apps.authentication.permissions import CAPABILITIES, role_allows

    identity = request.auth_identity
    user = repository.require(identity.user_id)
    return {
        "user": repository.public_view(user),
        "capabilities": sorted(c for c in CAPABILITIES if role_allows(identity.role, c)),
        "role": identity.role,
        "sessions": tokens.active_sessions(identity.user_id),
    }


def _audit(request, action: str, *, outcome: str, user_id: str | None = None,
           role: str | None = None, detail: str | None = None, metadata: dict | None = None):
    from common.audit import record as write_audit

    payload = dict(metadata or {})
    if detail:
        payload["reason"] = detail
    write_audit(
        user_id or "anonymous",
        action,
        resource_type="authentication",
        resource_id=user_id,
        outcome=outcome,
        metadata=payload,
        actor_role=role,
        source_ip=_client_ip(request) if request is not None else None,
        correlation_id=getattr(request, "correlation_id", None),
        label=action,
    )
