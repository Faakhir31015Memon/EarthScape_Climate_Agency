"""BSON-safe conversion of Mongo documents into JSON-friendly API payloads."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from bson import Int64, ObjectId
from bson.timestamp import Timestamp


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def jsonable(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, Timestamp):
        return int(value.time)
    if isinstance(value, (datetime, date)):
        return iso(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (Int64,)):
        return int(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): jsonable(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v, depth=depth + 1) for v in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    return str(value)


def document_to_api(doc: dict | None, *, id_field: str = "id") -> dict | None:
    """Convert `_id` to a stable string id and normalise every date to ISO-8601 UTC."""
    if not doc:
        return None
    payload = jsonable(dict(doc))
    raw_id = payload.pop("_id", None)
    if raw_id is not None:
        payload[id_field] = str(raw_id)
    return payload


def documents_to_api(docs) -> list[dict]:
    return [document_to_api(d) for d in docs]


def parse_datetime(value: Any) -> datetime | None:
    """Accept ISO strings, epoch numbers and datetimes; always return aware UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
