"""Shared DRF plumbing: authentication against MongoDB, RBAC, error envelope."""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional, Tuple, Type

from django.http import HttpRequest
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission

import jwt
from common import security
from common.database import get_database
from common.repositories import RepositoryError, audit_logs, maybe_object_id, users, utcnow

logger = logging.getLogger(__name__)

ADMINISTRATOR = "administrator"
CLIMATE_ANALYST = "climate_analyst"
VIEWER = "viewer"
ALL_ROLES: Tuple[str, str, str] = (ADMINISTRATOR, CLIMATE_ANALYST, VIEWER)


class AuthenticationFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentUser:
    """A MongoDB user document presented to the rest of the request cycle."""

    __slots__ = ("document",)

    def __init__(self, document: Dict[str, Any]):
        self.document = document

    @property
    def id(self) -> str:
        return str(self.document["_id"])

    @property
    def role(self) -> str:
        return self.document.get("role", VIEWER)

    @property
    def email(self) -> str:
        return self.document.get("email", "")

    @property
    def full_name(self) -> str:
        return self.document.get("full_name", "")

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return bool(self.document.get("is_active", False))

    def is_administrator(self) -> bool:
        return self.role == ADMINISTRATOR

    def can_ingest(self) -> bool:
        return self.role in (ADMINISTRATOR, CLIMATE_ANALYST)

    def to_public(self) -> Dict[str, Any]:
        document = self.document
        return {
            "id": str(document["_id"]),
            "full_name": document.get("full_name"),
            "email": document.get("email"),
            "role": document.get("role"),
            "is_active": document.get("is_active"),
            "email_verified": document.get("email_verified"),
            "preferences": document.get("preferences", {}),
            "created_at": document.get("created_at"),
            "last_login_at": document.get("last_login_at"),
            "last_seen_at": document.get("last_seen_at"),
        }


def find_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        return users.find_one({"_id": maybe_object_id(user_id)})
    except RepositoryError:
        logger.warning("User lookup failed for a supplied identifier")
        return None


class MongoJWTAuthentication(BaseAuthentication):
    """Validates an access token and loads the account from MongoDB."""

    keyword = "Bearer"

    def authenticate(self, request: HttpRequest) -> Optional[Tuple[DocumentUser, Dict[str, Any]]]:
        header = request.headers.get("Authorization", "")
        if not header.startswith(f"{self.keyword} "):
            return None
        token = header[len(self.keyword) + 1:].strip()
        if not token:
            return None
        try:
            payload = security.decode_token(token)
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed("token_expired", "Access token has expired.")
        except jwt.PyJWTError:
            raise AuthenticationFailed("token_invalid", "Access token is not valid.")

        if payload.get("typ") != "access":
            raise AuthenticationFailed("token_invalid", "Access token is not valid.")

        document = find_user_by_id(payload.get("sub", ""))
        if document is None:
            raise AuthenticationFailed("account_unknown", "Account no longer exists.")
        if not document.get("is_active", False):
            raise AuthenticationFailed("account_disabled", "This account is deactivated.")

        user = DocumentUser(document)
        request.token_payload = payload
        return user, payload

    def authenticate_header(self, request: HttpRequest) -> str:
        return self.keyword


class IsAuthenticatedAccount(BasePermission):
    message = "Authentication required."

    def has_permission(self, request, view) -> bool:
        return bool(getattr(request, "user", None) and request.user.is_authenticated)


class HasRole(BasePermission):
    """Server-side role enforcement. Frontend route guards are not trusted."""

    roles: Iterable[str] = ALL_ROLES

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return user.role in set(self.roles)


class IsAdministrator(BasePermission):
    message = "Administrator role required."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False) and user.role == ADMINISTRATOR)


class AdministratorOrAnalyst(BasePermission):
    message = "Climate analyst or administrator role required."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False)
                    and user.role in (ADMINISTRATOR, CLIMATE_ANALYST))


def require_roles(*roles: str) -> Type[BasePermission]:
    return type(
        f"RolePermission_{'_'.join(roles)}",
        (HasRole,),
        {"roles": tuple(roles), "message": f"Required role: {', '.join(roles)}."},
    )


ERROR_STATUS = {
    "token_expired": 401,
    "token_invalid": 401,
    "account_unknown": 401,
    "account_disabled": 401,
    "credentials_invalid": 401,
    "permission_denied": 403,
    "not_found": 404,
    "validation_failed": 400,
    "conflict": 409,
    "rate_limited": 429,
    "service_unavailable": 503,
    "internal_error": 500,
}


class ApiError(Exception):
    """A deliberate, user-safe error with a stable code."""

    def __init__(self, code: str, message: str, *, status: Optional[int] = None,
                 details: Optional[Any] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status or ERROR_STATUS.get(code, 400)
        self.details = details


def error_response(exc: Exception, context: Dict[str, Any]) -> Any:
    """DRF exception handler producing the platform error envelope."""
    from rest_framework.response import Response
    from rest_framework.views import exception_handler as drf_handler

    if isinstance(exc, AuthenticationFailed):
        return Response(
            {"error": {"code": exc.code, "message": exc.message, "request_id": _request_id(context)}},
            status=401,
        )
    if isinstance(exc, ApiError):
        payload: Dict[str, Any] = {"code": exc.code, "message": exc.message}
        if exc.details is not None:
            payload["details"] = exc.details
        payload["request_id"] = _request_id(context)
        return Response({"error": payload}, status=exc.status)

    response = drf_handler(exc, context)
    if response is not None:
        status = response.status_code
        code = {400: "validation_failed", 401: "token_invalid", 403: "permission_denied",
                404: "not_found", 429: "rate_limited"}.get(status, "request_failed")
        detail = response.data
        message = "The request could not be completed."
        if isinstance(detail, dict) and "detail" in detail:
            message = str(detail["detail"])
        payload = {"code": code, "message": message, "request_id": _request_id(context)}
        if isinstance(detail, dict) and code == "validation_failed":
            payload["details"] = {key: _first(value) for key, value in detail.items() if key != "non_field_errors"}
            if "non_field_errors" in detail:
                payload["message"] = str(_first(detail["non_field_errors"]))
        elif isinstance(detail, list) and detail:
            payload["details"] = detail
        response.data = {"error": payload}
        return response

    logger.exception("Unhandled server error: %s", exc)
    return Response(
        {"error": {"code": "internal_error", "message": "An unexpected server error occurred.",
                   "request_id": _request_id(context)}},
        status=500,
    )


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return str(value[0])
    return str(value)


def _request_id(context: Dict[str, Any]) -> Optional[str]:
    request = context.get("request")
    return getattr(request, "request_id", None) if request is not None else None


def record_denied(request, view, message: str = "Permission denied") -> None:
    """Audit a permission denial; never includes tokens or secrets."""
    user = getattr(request, "user", None)
    audit_logs.insert_one({
        "actor_user_id": getattr(user, "id", None),
        "actor_role": getattr(user, "role", "anonymous"),
        "action": "api.denied",
        "resource_type": type(view).__name__,
        "resource_id": None,
        "outcome": "denied",
        "metadata": {"path": request.path, "method": request.method, "reason": message},
        "created_at": utcnow(),
    })
    get_database().flush_if_dirty()
