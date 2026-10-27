"""Domain exceptions and the DRF exception handler.

Driver errors and tracebacks stay in the log; clients get a code, a message and a
correlation id.
"""

from __future__ import annotations

import logging

from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.http import Http404
from pymongo.errors import PyMongoError
from rest_framework import status
from rest_framework.exceptions import APIException, ValidationError as DRFValidationError

from common.logging_config import redact

logger = logging.getLogger("earthscape.errors")


class DomainError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "domain_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None,
                 details=None):
        super().__init__(message)
        if code:
            self.default_code = code
        if status_code:
            self.status_code = status_code
        self.details = details or []


class NotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"


class Forbidden(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "permission_denied"


class Conflict(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "conflict"


class Unauthorized(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "authentication_failed"


class ServiceUnavailableConfig(DomainError):
    """A required dependency is unconfigured or unreachable."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "dependency_unavailable"


class ValidationError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_code = "validation_failed"


class Throttled(APIException):
    """Rate limited or locked out; clients read `wait_seconds` from details."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    default_code = "rate_limited"

    def __init__(self, message: str, *, wait_seconds: int | None = None, details=None):
        super().__init__(message)
        self.details = details or ([f"retry after {wait_seconds}s"] if wait_seconds else [])
        self.wait_seconds = wait_seconds


def _payload(code: str, message: str, details, correlation_id: str) -> dict:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": redact(details) if details else []},
        "data": None,
        "meta": {"correlation_id": correlation_id},
    }


def api_exception_handler(exc, context):
    # deferred import: rest_framework.views is only initialised after Django settings
    # load, and this module sits on the authentication import path
    from rest_framework.response import Response
    from rest_framework.views import exception_handler as drf_exception_handler

    request = context.get("request")
    correlation_id = getattr(request, "correlation_id", "") if request else ""

    response = drf_exception_handler(exc, context)

    if isinstance(exc, (PyMongoError, ImproperlyConfigured)):
        logger.error("database/config failure: %s", exc, exc_info=True,
                     extra={"correlation_id": correlation_id})
        return Response(
            _payload("dependency_unavailable",
                     "The application database is unavailable. Check MONGODB_URI and network access.",
                     None, correlation_id),
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if isinstance(exc, Http404):
        exc = NotFound("Resource not found.")

    if isinstance(exc, PermissionDenied):
        exc = Forbidden("You do not have permission to perform this action.")

    if isinstance(exc, DRFValidationError):
        details = exc.detail
        message = "Request validation failed."
        if isinstance(details, dict):
            flat = []
            for field, value in details.items():
                if isinstance(value, (list, tuple)):
                    flat.extend(f"{field}: {item}" for item in value)
                else:
                    flat.append(f"{field}: {value}")
            non_field = details.get("non_field_errors")
            if non_field:
                message = "; ".join(str(item) for item in non_field)
            details = flat
        return Response(_payload("validation_failed", message, details, correlation_id),
                        status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    if isinstance(exc, Throttled):
        return Response(
            _payload("rate_limited", str(exc),
                     exc.details or [f"retry after {exc.wait_seconds}s"], correlation_id),
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    if isinstance(exc, DomainError):
        return Response(_payload(exc.default_code, str(exc), exc.details, correlation_id),
                        status=exc.status_code)

    if response is not None:
        code = "request_failed"
        message = str(response.data) if not isinstance(response.data, dict) else "Request failed."
        if isinstance(response.data, dict) and "detail" in response.data:
            message = str(response.data["detail"])
            code = str(response.data.get("code", code))
        return Response(_payload(code, message, response.data, correlation_id),
                        status=response.status_code)

    logger.exception("unhandled server error", extra={"correlation_id": correlation_id})
    return Response(
        _payload("internal_error", "An unexpected server error occurred.", None, correlation_id),
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
