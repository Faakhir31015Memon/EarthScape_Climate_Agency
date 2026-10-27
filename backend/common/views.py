from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.views import APIView

from common.api import ApiError, IsAuthenticatedAccount, MongoJWTAuthentication, error_response


class BaseView(APIView):
    permission_classes: List[Any] = [AllowAny]
    authentication_classes: List[Any] = [MongoJWTAuthentication]

    def handle_exception(self, exc):  # noqa: D401 - DRF contract
        return error_response(exc, {"request": self.request, "view": self, "args": (), "kwargs": {}})

    def handle500(self, exc):
        return self.handle_exception(exc)


class PublicView(BaseView):
    pass


class AuthenticatedView(BaseView):
    permission_classes = [IsAuthenticatedAccount]

    def current_user(self, request: Request):
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            raise ApiError("permission_denied", "Authentication required.", status=401)
        return user


def int_param(request, name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", f"{name} must be an integer.")
    if value < minimum or value > maximum:
        raise ApiError("validation_failed", f"{name} must be between {minimum} and {maximum}.")
    return value


def bool_param(request, name: str, default: bool = False) -> bool:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def str_param(request, name: str, default: Optional[str] = None, *, max_length: int = 200,
              choices: Optional[Tuple[str, ...]] = None) -> Optional[str]:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    value = str(raw).strip()[:max_length]
    if choices and value not in choices:
        raise ApiError("validation_failed", f"{name} must be one of: {', '.join(choices)}.")
    return value


def date_param(request, name: str, default: Optional[str] = None) -> Optional[str]:
    value = str_param(request, name, default, max_length=10)
    if value is None:
        return None
    from datetime import datetime

    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ApiError("validation_failed", f"{name} must use the YYYY-MM-DD format.")
    return value


def list_param(request, name: str) -> List[str]:
    raw = request.query_params.getlist(name) if hasattr(request.query_params, "getlist") else []
    values: List[str] = []
    for item in raw:
        values.extend([part.strip() for part in str(item).split(",") if part.strip()])
    return values[:200]
