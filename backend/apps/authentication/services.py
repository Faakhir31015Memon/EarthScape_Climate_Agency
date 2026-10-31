"""Authentication domain services backed by the MongoDB users collection."""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from common import audit, security
from common.api import ADMINISTRATOR, ALL_ROLES, ApiError, VIEWER
from common.database import get_database
from common.repositories import refresh_tokens, users, utcnow
from config import env

logger = logging.getLogger(__name__)

MAX_FAILED_LOGINS = env.get_int("LOGIN_MAX_FAILED_ATTEMPTS", 8)
LOCKOUT_MINUTES = env.get_int("LOGIN_LOCKOUT_MINUTES", 15)


class AuthError(ApiError):
    """An ApiError so the response uses the platform envelope;
    a plain exception here would surface as an unhandled 500.
    """

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(code, message, status=status)


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    return users.find_one({"email_normalized": security.normalize_email(email)})


def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    return users.find_one({"_id": security.to_object_id(user_id)})


def create_user(*, full_name: str, email: str, password: str, role: str,
                created_by: Optional[str] = None, require_email_verification: Optional[bool] = None,
                organisation: Optional[str] = None, email_verified: bool = False) -> Dict[str, Any]:
    """The only place a password gets hashed."""
    normalized = security.normalize_email(email)
    if not security.valid_email(normalized):
        raise AuthError("email_invalid", "Enter a valid email address.")
    problems = security.password_problems(password)
    if problems:
        raise AuthError("password_weak", " ".join(problems))
    if role not in ALL_ROLES:
        raise AuthError("role_invalid", "Unknown role requested.")
    if get_user_by_email(normalized) is not None:
        raise AuthError("email_taken", "An account already exists for this email address.", 409)

    now = utcnow()
    document = {
        "full_name": full_name.strip(),
        "email": normalized,
        "email_normalized": normalized,
        "password_hash": security.hash_password(password),
        "role": role,
        "is_active": True,
        "email_verified": email_verified if require_email_verification is None
        else (True if not require_email_verification else email_verified),
        "organisation": organisation,
        "preferences": {
            "theme": env.get_str("DEFAULT_THEME", "light"),
            "units": {"temperature": "celsius", "precipitation": "millimetres", "wind": "m/s"},
            "notification_channels": {"in_app": True, "email": env.get_bool("ENABLE_EMAIL_NOTIFICATIONS")},
            "dashboard_region": None,
        },
        "failed_login_count": 0,
        "locked_until": None,
        "password_changed_at": now,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
        "last_login_at": None,
    }
    document["_id"] = security.to_object_id(users.insert_one(document))
    get_database().mark_dirty("users")
    return document


def public_profile(document: Dict[str, Any]) -> Dict[str, Any]:
    """Omits password_hash and the login counters."""
    return {
        "id": str(document["_id"]),
        "full_name": document.get("full_name"),
        "email": document.get("email"),
        "role": document.get("role"),
        "is_active": document.get("is_active", False),
        "email_verified": bool(document.get("email_verified")),
        "organisation": document.get("organisation"),
        "preferences": document.get("preferences", {}),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "last_login_at": document.get("last_login_at"),
        "password_changed_at": document.get("password_changed_at"),
    }



def authenticate(email: str, password: str, *, request=None) -> Dict[str, Any]:
    document = get_user_by_email(email)
    if document is None:
        # dummy verify keeps the response time and message identical for unknown emails
        security.verify_password(password or "x", "$2b$12$" + "x" * 53)
        audit.for_request(request, action="auth.login", resource_type="session",
                          outcome="failure", metadata={"email_domain": _domain(email)})
        raise AuthError("credentials_invalid", "Email address or password is incorrect.", 401)

    locked_until = document.get("locked_until")
    if isinstance(locked_until, datetime) and locked_until > utcnow():
        audit.for_request(request, action="auth.login", resource_type="session",
                          outcome="denied", actor_user_id=str(document["_id"]),
                          metadata={"reason": "throttled"})
        raise AuthError("account_locked",
                        "Too many failed attempts. Try again later.", 429)

    if not security.verify_password(password, document.get("password_hash")):
        failures = int(document.get("failed_login_count", 0)) + 1
        updates: Dict[str, Any] = {"failed_login_count": failures, "updated_at": utcnow()}
        if failures >= MAX_FAILED_LOGINS:
            updates["locked_until"] = utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            updates["failed_login_count"] = 0
        users.update_by_id(str(document["_id"]), updates)
        audit.for_request(request, action="auth.login", resource_type="session",
                          outcome="failure", actor_user_id=str(document["_id"]),
                          metadata={"attempt": failures})
        raise AuthError("credentials_invalid", "Email address or password is incorrect.", 401)

    if not document.get("is_active", False):
        audit.for_request(request, action="auth.login", resource_type="session",
                          outcome="denied", actor_user_id=str(document["_id"]),
                          metadata={"reason": "inactive"})
        raise AuthError("account_disabled", "This account is deactivated. Contact an administrator.", 401)

    users.update_by_id(str(document["_id"]), {
        "failed_login_count": 0,
        "locked_until": None,
        "last_login_at": utcnow(),
        "last_login_ip": _source_ip(request),
        "updated_at": utcnow(),
    })
    audit.for_request(request, action="auth.login", resource_type="session",
                      outcome="success", actor_user_id=str(document["_id"]),
                      actor_role=document.get("role"))
    return issue_token_pair(document)


def issue_token_pair(document: Dict[str, Any]) -> Dict[str, Any]:
    access_token, access_expires_at = security.issue_access_token(document)
    raw_refresh = security.generate_refresh_token()
    expires_at = security.refresh_expiry()
    refresh_tokens.insert_one({
        "user_id": str(document["_id"]),
        "token_hash": security.hash_refresh_token(raw_refresh),
        "family_id": secrets.token_hex(8),
        "issued_at": utcnow(),
        "expires_at": expires_at,
        "revoked_at": None,
        "replaced_by": None,
        "user_agent": None,
        "source_ip": None,
    })
    get_database().mark_dirty("refresh_tokens")
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "token_type": "Bearer",
        "access_expires_at": access_expires_at,
        "refresh_expires_at": expires_at,
        "user": public_profile(document),
    }


def rotate_refresh_token(raw_refresh: str, *, request=None) -> Dict[str, Any]:
    """Reusing an already-rotated token revokes every token in its family."""
    token_hash = security.hash_refresh_token(raw_refresh)
    stored = refresh_tokens.find_one({"token_hash": token_hash})
    if stored is None:
        raise AuthError("refresh_unknown", "Refresh token is not recognised.", 401)

    if stored.get("revoked_at"):
        refresh_tokens.update_many(
            {"family_id": stored.get("family_id"), "revoked_at": None},
            {"$set": {"revoked_at": utcnow(), "revocation_reason": "token_reuse_detected"}},
        )
        get_database().mark_dirty("refresh_tokens")
        audit.for_request(request, action="auth.refresh_reuse", resource_type="refresh_token",
                          resource_id=str(stored["_id"]), outcome="denied",
                          actor_user_id=stored.get("user_id"))
        raise AuthError("refresh_reused", "This refresh token was already used. Sign in again.", 401)

    if stored.get("expires_at") and stored["expires_at"] < utcnow():
        raise AuthError("refresh_expired", "Session expired. Sign in again.", 401)

    document = users.find_one({"_id": security.to_object_id(stored["user_id"])})
    if document is None or not document.get("is_active", False):
        raise AuthError("account_disabled", "This account is no longer active.", 401)

    tokens = issue_token_pair(document)
    refresh_tokens.update_by_id(str(stored["_id"]), {
        "revoked_at": utcnow(),
        "revocation_reason": "rotated",
        "replaced_by": security.hash_refresh_token(tokens["refresh_token"]),
    })
    get_database().mark_dirty("refresh_tokens")
    return tokens


def logout(raw_refresh: Optional[str], *, request=None, user_id: Optional[str] = None) -> int:
    count = 0
    if raw_refresh:
        stored = refresh_tokens.find_one({"token_hash": security.hash_refresh_token(raw_refresh)})
        if stored is not None and not stored.get("revoked_at"):
            count = refresh_tokens.update_by_id(
                str(stored["_id"]), {"revoked_at": utcnow(), "revocation_reason": "logout"}) or 1
            count = 1
    if user_id:
        refresh_tokens.update_many(
            {"user_id": user_id, "revoked_at": None},
            {"$set": {"revoked_at": utcnow(), "revocation_reason": "logout_all"}},
        )
    get_database().mark_dirty("refresh_tokens")
    audit.for_request(request, action="auth.logout", resource_type="session",
                      actor_user_id=user_id, outcome="success",
                      metadata={"tokens_revoked": count})
    return count


def revoke_all_for_user(user_id: str, reason: str = "administrative") -> int:
    revoked = refresh_tokens.update_many({"user_id": user_id, "revoked_at": None},
                                         {"$set": {"revoked_at": utcnow(),
                                                   "revocation_reason": reason}})
    get_database().mark_dirty("refresh_tokens")
    return revoked


def change_password(user_id: str, current_password: str, new_password: str, *, request=None) -> None:
    document = users.find_one({"_id": security.to_object_id(user_id)})
    if document is None:
        raise AuthError("account_unknown", "Account not found.", 404)
    if not security.verify_password(current_password, document.get("password_hash")):
        audit.for_request(request, action="auth.password_change", resource_type="user",
                          resource_id=user_id, outcome="failure", actor_user_id=user_id)
        raise AuthError("credentials_invalid", "The current password is incorrect.", 401)
    problems = security.password_problems(new_password)
    if problems:
        raise AuthError("password_weak", " ".join(problems))
    users.update_by_id(user_id, {
        "password_hash": security.hash_password(new_password),
        "password_changed_at": utcnow(),
        "updated_at": utcnow(),
    })
    revoke_all_for_user(user_id, reason="password_change")
    audit.for_request(request, action="auth.password_change", resource_type="user",
                      resource_id=user_id, outcome="success", actor_user_id=user_id)


def begin_password_reset(email: str, *, request=None) -> Dict[str, Any]:
    """Single-use token; self-service reset for user accounts with direct reset link."""
    if not env.get_bool("ENABLE_PASSWORD_RESET", True):
        raise AuthError("feature_disabled", "Password recovery is disabled on this deployment.", 503)

    document = get_user_by_email(email)
    smtp_ready = bool(env.get_raw("SMTP_HOST")) and env.get_bool("ENABLE_EMAIL_NOTIFICATIONS")
    if document is None:
        # same reply either way, so this cannot be used to probe for accounts
        return {"status": "accepted", "delivered": False,
                "note": "If an account exists for this address, recovery instructions were sent."}

    user_role = document.get("role", VIEWER)
    if user_role == ADMINISTRATOR and not env.get_bool("ALLOW_ADMIN_PASSWORD_RESET", False):
        logger.info("Password reset requested for administrator account (%s) - restricted to user accounts", document.get("email"))
        return {
            "status": "admin_restricted",
            "delivered": False,
            "note": "Password reset via email is strictly for user accounts. Administrator accounts cannot be reset from this form.",
        }

    raw_token = secrets.token_urlsafe(32)
    ttl_minutes = env.get_int("PASSWORD_RESET_TTL_MINUTES", 30)
    refresh_tokens.insert_one({
        "user_id": str(document["_id"]),
        "token_hash": security.hash_refresh_token(raw_token),
        "kind": "password_reset",
        "family_id": "reset",
        "issued_at": utcnow(),
        "expires_at": utcnow() + timedelta(minutes=ttl_minutes),
        "revoked_at": None,
        "used_at": None,
    })
    get_database().mark_dirty("refresh_tokens")
    audit.for_request(request, action="auth.password_reset_requested", resource_type="user",
                      resource_id=str(document["_id"]), outcome="success")

    if not smtp_ready:
        return {
            "status": "configuration_required",
            "delivered": False,
            "note": ("Password recovery is implemented but email delivery is not configured on this "
                     "deployment. Set SMTP_HOST/SMTP_USER and ENABLE_EMAIL_NOTIFICATIONS=true."),
        }

    from common.emailer import send_email

    frontend_origin = env.get_str('FRONTEND_ORIGIN', 'http://localhost:5173').split(',')[0].strip()
    reset_url = f"{frontend_origin}/reset-password?token={raw_token}"
    user_name = document.get("full_name") or "User"

    plain_body = (
        f"Hello {user_name},\n\n"
        "We received a request to reset your password for your EarthScape Climate Agency account.\n\n"
        f"Use the link below to set a new password. It expires in {ttl_minutes} minutes and can be used once:\n\n"
        f"{reset_url}\n\n"
        "If you did not request this, no action is needed.\n\n"
        "— EarthScape Climate Agency"
    )

    html_body = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Password Recovery</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f1f5f9; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background-color: #f1f5f9; padding: 40px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" style="max-width: 580px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); border: 1px solid #e2e8f0;">
          <tr>
            <td style="background-color: #064e3b; padding: 28px 32px; text-align: left;">
              <h1 style="margin: 0; color: #ffffff; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">EarthScape Climate Agency</h1>
              <p style="margin: 4px 0 0 0; color: #a7f3d0; font-size: 13px;">User Account Password Recovery</p>
            </td>
          </tr>
          <tr>
            <td style="padding: 36px 32px; color: #1e293b;">
              <h2 style="margin: 0 0 16px 0; font-size: 19px; font-weight: 600; color: #0f172a;">Password Reset Request</h2>
              <p style="margin: 0 0 16px 0; font-size: 15px; line-height: 24px; color: #334155;">Hello <strong>{user_name}</strong>,</p>
              <p style="margin: 0 0 24px 0; font-size: 15px; line-height: 24px; color: #334155;">
                We received a request to reset your password. You can set a new password directly by clicking the button below:
              </p>
              
              <table role="presentation" cellspacing="0" cellpadding="0" style="margin: 28px 0;">
                <tr>
                  <td align="center" style="border-radius: 8px; background-color: #059669;">
                    <a href="{reset_url}" target="_blank" style="display: inline-block; padding: 14px 28px; font-size: 15px; font-weight: 600; color: #ffffff; text-decoration: none; border-radius: 8px; background-color: #059669;">
                      Reset Your Password
                    </a>
                  </td>
                </tr>
              </table>

              <p style="margin: 0 0 12px 0; font-size: 13px; line-height: 20px; color: #64748b;">
                If the button above does not work, copy and paste this link into your web browser:
              </p>
              <p style="margin: 0 0 24px 0; font-size: 13px; line-height: 20px; word-break: break-all;">
                <a href="{reset_url}" style="color: #0284c7; text-decoration: underline;">{reset_url}</a>
              </p>

              <div style="background-color: #f8fafc; border-left: 4px solid #059669; padding: 12px 16px; border-radius: 4px; margin: 24px 0 0 0;">
                <p style="margin: 0; font-size: 13px; line-height: 20px; color: #475569;">
                  <strong>Note:</strong> This link is single-use and will expire in <strong>{ttl_minutes} minutes</strong>. If you did not request this, you can safely ignore this email &mdash; your password will remain unchanged.
                </p>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background-color: #f8fafc; padding: 20px 32px; border-top: 1px solid #e2e8f0; text-align: center;">
              <p style="margin: 0; font-size: 12px; color: #94a3b8;">
                &copy; EarthScape Climate Agency &bull; Automated System Notification
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    delivered = send_email(
        to=document["email"],
        subject="EarthScape Climate Agency - Password Recovery Link",
        body=plain_body,
        html_body=html_body,
    )
    return {"status": "sent" if delivered else "delivery_failed", "delivered": delivered,
            "note": "Recovery instructions were sent to the email address on file."}


def complete_password_reset(raw_token: str, new_password: str, *, request=None) -> None:
    stored = refresh_tokens.find_one({"token_hash": security.hash_refresh_token(raw_token),
                                      "kind": "password_reset"})
    if stored is None:
        raise AuthError("reset_invalid", "This recovery link is not valid.", 400)
    if stored.get("used_at") or stored.get("revoked_at"):
        raise AuthError("reset_used", "This recovery link has already been used.", 400)
    if stored.get("expires_at") and stored["expires_at"] < utcnow():
        raise AuthError("reset_expired", "This recovery link has expired.", 400)

    problems = security.password_problems(new_password)
    if problems:
        raise AuthError("password_weak", " ".join(problems))

    user_id = str(stored["user_id"])
    users.update_by_id(user_id, {
        "password_hash": security.hash_password(new_password),
        "password_changed_at": utcnow(),
        "updated_at": utcnow(),
        "failed_login_count": 0,
        "locked_until": None,
    })
    refresh_tokens.update_by_id(str(stored["_id"]), {"used_at": utcnow(), "revoked_at": utcnow()})
    revoke_all_for_user(user_id, reason="password_reset")
    audit.for_request(request, action="auth.password_reset_completed", resource_type="user",
                      resource_id=user_id, outcome="success", actor_user_id=user_id)


def _domain(email: str) -> str:
    return (email or "").split("@")[-1].lower()


def _source_ip(request) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def self_registration_enabled() -> bool:
    """Default-on is safe: registration_role() clamps to the lowest role."""
    return env.get_bool("ENABLE_SELF_REGISTRATION", True)


def registration_role(role: Optional[str]) -> str:
    if env.get_bool("ALLOW_ANALYST_SELF_REGISTRATION") and role == "climate_analyst":
        return "climate_analyst"
    return VIEWER
