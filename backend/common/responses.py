"""Uniform success envelope for every API response."""

from __future__ import annotations

from typing import Any

from rest_framework.response import Response
from rest_framework import status


def ok(data: Any = None, *, meta: dict | None = None, http_status: int = status.HTTP_200_OK,
       request=None) -> Response:
    payload: dict[str, Any] = {"success": True, "data": data, "error": None}
    merged_meta = dict(meta or {})
    if request is not None:
        merged_meta.setdefault("correlation_id", getattr(request, "correlation_id", ""))
    payload["meta"] = merged_meta
    return Response(payload, status=http_status)


def created(data: Any = None, *, meta: dict | None = None, request=None) -> Response:
    return ok(data, meta=meta, http_status=status.HTTP_201_CREATED, request=request)


def paginated(items: list, *, total: int, page: int, page_size: int, extra: dict | None = None,
              request=None) -> Response:
    pages = (total + page_size - 1) // page_size if page_size else 1
    meta = {
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        }
    }
    if extra:
        meta.update(extra)
    return ok(items, meta=meta, request=request)


def failure(code: str, message: str, *, details: Any = None, http_status: int = 400,
            request=None) -> Response:
    return Response(
        {
            "success": False,
            "data": None,
            "error": {"code": code, "message": message, "details": details or []},
            "meta": {"correlation_id": getattr(request, "correlation_id", "") if request else ""},
        },
        status=http_status,
    )
