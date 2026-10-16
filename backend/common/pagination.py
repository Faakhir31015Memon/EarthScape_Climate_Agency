"""Bounded pagination helpers shared by all Mongo-backed list endpoints."""

from __future__ import annotations

from rest_framework.pagination import PageNumberPagination

from common.env import ENV

MAX_PAGE_SIZE = ENV.integer("MAX_PAGE_SIZE", 200)
DEFAULT_PAGE_SIZE = ENV.integer("DEFAULT_PAGE_SIZE", 25)


def normalize_pagination(query_params, default_size: int = DEFAULT_PAGE_SIZE) -> tuple[int, int, int]:
    """Return (page, page_size, skip) with hard bounds so queries stay cheap."""
    try:
        page = max(1, int(query_params.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(query_params.get("page_size", default_size))
    except (TypeError, ValueError):
        size = int(default_size)
    size = max(1, min(size, MAX_PAGE_SIZE))
    return page, size, (page - 1) * size


class MongoPageNumberPagination(PageNumberPagination):
    page_size = DEFAULT_PAGE_SIZE
    page_size_query_param = "page_size"
    max_page_size = MAX_PAGE_SIZE
