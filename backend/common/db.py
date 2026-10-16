"""PyMongo access layer.

MongoDB is the primary application database. Django's ORM is intentionally unused, so
every persistence path in this project goes through the repositories built on top of
the helpers here.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.errors import ConfigurationError, ConnectionFailure, OperationFailure, PyMongoError

from common.env import ENV

COLLECTIONS: dict[str, str] = {
    "users": "users",
    "refresh_tokens": "refresh_tokens",
    "password_resets": "password_resets",
    "datasets": "datasets",
    "ingestion_jobs": "ingestion_jobs",
    "climate_observations": "climate_observations",
    "analytics_summaries": "analytics_summaries",
    "processing_jobs": "processing_jobs",
    "model_registry": "model_registry",
    "predictions": "predictions",
    "alert_rules": "alert_rules",
    "alert_events": "alert_events",
    "notifications": "notifications",
    "reports": "reports",
    "saved_analyses": "saved_analyses",
    "audit_logs": "audit_logs",
    "support_tickets": "support_tickets",
    "ticket_messages": "ticket_messages",
    "feedback": "feedback",
    "platform_settings": "platform_settings",
    "rate_limits": "rate_limits",
    "stream_checkpoints": "stream_checkpoints",
}

_state: dict[str, Any] = {"client": None, "failure": None}
_lock = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def client() -> MongoClient:
    """Return a lazily created, process-wide pooled client."""
    with _lock:
        if _state["client"] is None:
            uri = ENV.raw("MONGODB_URI")
            if not uri:
                raise ConfigurationError(
                    "MONGODB_URI is not configured. Copy .env.example to .env and set a MongoDB "
                    "Atlas or local mongod connection string."
                )
            _state["client"] = MongoClient(
                uri,
                maxPoolSize=ENV.integer("MONGODB_MAX_POOL_SIZE", 50),
                minPoolSize=ENV.integer("MONGODB_MIN_POOL_SIZE", 1),
                serverSelectionTimeoutMS=int(ENV.integer("MONGODB_SERVER_TIMEOUT_MS", 4000)),
                connectTimeoutMS=int(ENV.integer("MONGODB_CONNECT_TIMEOUT_MS", 5000)),
                tz_aware=True,
                uuidRepresentation="standard",
                readPreference="primaryPreferred",
                appname="earthscape-climate-agency",
            )
            _state["failure"] = None
        return _state["client"]


def database_name() -> str:
    return ENV.raw("MONGODB_DATABASE", "earthscape_climate")


def db():
    return client()[database_name()]


def collection(name: str):
    if name not in COLLECTIONS:
        raise KeyError(f"Unknown collection '{name}'. Register it in common.db.COLLECTIONS.")
    return db()[name]


def ping(timeout_ms: int = 2500) -> dict[str, Any]:
    """Non-raising connectivity probe; never echoes the connection string."""
    try:
        c = client()
        info = c.admin.command("ping")
        build = c[database_name()].command("buildinfo")
        return {
            "reachable": True,
            "database": database_name(),
            "ping": bool(info.get("ok")),
            "server_version": build.get("version", "unknown"),
            "atlas": bool(build.get("modules")),
            "collections": len(list(c[database_name()].list_collection_names())),
        }
    except (ConnectionFailure, OperationFailure, ConfigurationError) as exc:
        return {"reachable": False, "database": database_name(), "error": type(exc).__name__,
                "detail": str(exc)[:300]}
    except PyMongoError as exc:  # pragma: no cover - defensive
        return {"reachable": False, "database": database_name(), "error": type(exc).__name__,
                "detail": str(exc)[:300]}


def oid(value: Any) -> Optional[ObjectId]:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError, ValueError):
        return None


def oid_str(value: Any) -> str:
    parsed = oid(value)
    return str(parsed) if parsed else str(value)


def count(collection_name: str, query: Optional[dict] = None) -> int:
    return collection(collection_name).count_documents(query or {})


def insert_one(collection_name: str, document: dict) -> str:
    result = collection(collection_name).insert_one(document)
    return str(result.inserted_id)


def insert_many(collection_name: str, documents: Iterable[dict], ordered: bool = False) -> int:
    docs = list(documents)
    if not docs:
        return 0
    result = collection(collection_name).insert_many(docs, ordered=ordered)
    return len(result.inserted_ids)


def find(collection_name: str, query: dict, *, sort: Optional[list] = None, skip: int = 0,
         limit: int = 100, projection: Optional[dict] = None) -> list[dict]:
    cursor = collection(collection_name).find(query, projection)
    if sort:
        cursor = cursor.sort(sort)
    return list(cursor.skip(skip).limit(min(limit, 5000)))


def find_one(collection_name: str, query: dict, projection: Optional[dict] = None) -> Optional[dict]:
    return collection(collection_name).find_one(query, projection)


def update_one(collection_name: str, query: dict, update: dict, *, upsert: bool = False) -> int:
    result = collection(collection_name).update_one(query, update, upsert=upsert)
    return result.modified_count + (1 if result.upserted_id else 0)


def delete_many(collection_name: str, query: dict) -> int:
    return collection(collection_name).delete_many(query).deleted_count


def aggregate(collection_name: str, pipeline: list, *, allow_disk_use: bool = True,
              max_time_ms: int = 20000) -> list[dict]:
    return list(
        collection(collection_name).aggregate(
            pipeline, allowDiskUse=allow_disk_use, maxTimeMS=max_time_ms
        )
    )
