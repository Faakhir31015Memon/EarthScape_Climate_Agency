"""Document repositories - the only way application code touches MongoDB.

Every write goes through :class:`Repository`, which marks the collection dirty so
local-development snapshots stay consistent, and returns BSON ``_id`` values
converted to strings at the boundary.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import BulkWriteError, DuplicateKeyError, OperationFailure, PyMongoError

from common.database import get_database

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGE_SIZE = 500
HARD_RESULT_LIMIT = 20000


class RepositoryError(RuntimeError):
    """Raised for database problems that callers should surface as errors."""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_object_id(value: str) -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise RepositoryError("Invalid document identifier.") from exc


def maybe_object_id(value: Any) -> Any:
    """Convert a string id to ObjectId, leaving anything else untouched."""
    if isinstance(value, str):
        try:
            return ObjectId(value)
        except (InvalidId, TypeError):
            return value
    return value


def _normalise(value: Any) -> Any:
    """Replace BSON-only types with JSON-safe equivalents, at any depth."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise(item) for item in value]
    return value


def _utc_moments(value: Any) -> Any:
    """Mark every naive datetime coming out of storage as UTC, at any depth.

    MongoDB keeps datetimes as UTC milliseconds and both the BSON codec and the
    local-development store decode them without a tzinfo, while this platform stamps
    every timestamp from ``utcnow()`` (aware). Comparing the two is a ``TypeError``,
    so the conversion happens once here instead of at each call site that has to
    discover it at runtime. Already-aware values are left exactly as they are, which
    is what an Atlas connection with ``tz_aware`` codecs produces.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, dict):
        return {key: _utc_moments(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_utc_moments(item) for item in value]
    return value


def serialize(document: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if document is None:
        return None
    out = {key: _normalise(value) for key, value in document.items()}
    if "_id" in out:
        out["id"] = out.pop("_id")
    return out


def serialize_many(documents: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [serialize(document) for document in documents]


class Repository:
    """Thin, explicit wrapper around one MongoDB collection."""

    def __init__(self, collection: str):
        self.collection_name = collection

    # ------------------------------------------------------------------ helpers
    @property
    def coll(self):
        return get_database().collection(self.collection_name)

    def _after_write(self) -> None:
        get_database().mark_dirty(self.collection_name)

    # -------------------------------------------------------------------- read
    def find_one(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        try:
            return _utc_moments(self.coll.find_one(query, projection))
        except PyMongoError as exc:
            logger.error("find_one failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database read failed.") from exc

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None,
             sort: Optional[Sequence[Tuple[str, int]]] = None, limit: int = 1000,
             batch_size: int = 1000) -> List[Dict[str, Any]]:
        try:
            cursor = self.coll.find(query, projection)
            if sort:
                cursor = cursor.sort(list(sort))
            cursor = cursor.limit(min(int(limit), HARD_RESULT_LIMIT))
            return [_utc_moments(document) for document in cursor.batch_size(batch_size)]
        except PyMongoError as exc:
            logger.error("find failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database read failed.") from exc

    def stream(self, query: Dict[str, Any], projection: Optional[Dict[str, Any]] = None,
               sort: Optional[Sequence[Tuple[str, int]]] = None, batch_size: int = 2000):
        """Server-side cursor for large exports / ML feature builds."""
        cursor = self.coll.find(query, projection, no_cursor_timeout=True)
        if sort:
            cursor = cursor.sort(list(sort))
        return (_utc_moments(document) for document in cursor.batch_size(batch_size))

    def count(self, query: Optional[Dict[str, Any]] = None) -> int:
        try:
            return int(self.coll.count_documents(query or {}))
        except PyMongoError as exc:
            raise RepositoryError("Database count failed.") from exc

    def distinct(self, field: str, query: Optional[Dict[str, Any]] = None) -> List[Any]:
        try:
            return [_utc_moments(value) for value in self.coll.distinct(field, query or {})]
        except PyMongoError as exc:
            raise RepositoryError("Database distinct failed.") from exc

    def paginate(self, query: Dict[str, Any], *, page: int = 1, page_size: int = 50,
                 sort: Optional[Sequence[Tuple[str, int]]] = None,
                 projection: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Bounded pagination with a stable contract consumed by the frontend."""
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 50), DEFAULT_MAX_PAGE_SIZE))
        total = self.count(query)
        documents = self.find(query, projection=projection, sort=sort or [("created_at", -1)],
                              limit=page * page_size)
        window = documents[(page - 1) * page_size: page * page_size]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": serialize_many(window),
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1,
        }

    def aggregate(self, pipeline: List[Dict[str, Any]], *, allow_disk_use: bool = False) -> List[Dict[str, Any]]:
        try:
            if allow_disk_use:
                rows = list(self.coll.aggregate(pipeline, allowDiskUse=True))
            else:
                rows = list(self.coll.aggregate(pipeline))
            return [_utc_moments(document) for document in rows]
        except (OperationFailure, PyMongoError) as exc:
            logger.error("aggregate failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database aggregation failed.") from exc

    # ------------------------------------------------------------------- write
    def insert_one(self, document: Dict[str, Any]) -> str:
        try:
            result = self.coll.insert_one(dict(document))
        except DuplicateKeyError:
            raise
        except PyMongoError as exc:
            logger.error("insert failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database write failed.") from exc
        self._after_write()
        return str(result.inserted_id)

    def insert_many(self, documents: Sequence[Dict[str, Any]], *, ordered: bool = False,
                    chunk_size: int = 500) -> Tuple[int, int]:
        """Chunked bulk insert. Returns ``(accepted, rejected)``."""
        accepted = 0
        rejected = 0
        for start in range(0, len(documents), chunk_size):
            chunk = [dict(document) for document in documents[start:start + chunk_size]]
            try:
                result = self.coll.insert_many(chunk, ordered=ordered)
                accepted += len(result.inserted_ids)
            except DuplicateKeyError as exc:
                inserted = int(getattr(exc, "details", {}).get("nInserted", 0))
                accepted += inserted
                rejected += len(chunk) - inserted
            except BulkWriteError as exc:
                accepted += int(exc.details.get("nInserted", 0))
                rejected += len(exc.details.get("writeErrors", []))
            except PyMongoError as exc:
                logger.error("bulk insert failed on %s: %s", self.collection_name, _clean(exc))
                rejected += len(chunk)
        if accepted:
            self._after_write()
        return accepted, rejected

    def update_by_id(self, document_id: str, updates: Dict[str, Any], *,
                     upsert: bool = False,
                     return_document: bool = True) -> Optional[Dict[str, Any]]:
        """Update one document by id, returning the stored document by default.

        Every service in ``apps/`` writes and then re-reads the same row (acknowledged
        alerts, delivered notifications, patched tickets), so the refreshed document -
        not a count - is the useful return value here. ``update_one`` deliberately
        defaults the other way because its callers test match counts.
        """
        return self.update_one({"_id": to_object_id(document_id)}, updates,
                               upsert=upsert, return_document=return_document)

    def update_one(self, query: Dict[str, Any], updates: Dict[str, Any], *,
                   upsert: bool = False, return_document: bool = False) -> Optional[Dict[str, Any]]:
        operators = dict(updates)
        if not any(key.startswith("$") for key in operators):
            operators = {"$set": operators}
        try:
            result = self.coll.update_one(query, operators, upsert=upsert)
        except PyMongoError as exc:
            logger.error("update failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database write failed.") from exc
        if result.matched_count or result.upserted_id:
            self._after_write()
        if return_document:
            document_id = result.upserted_id or query.get("_id")
            if document_id is None:
                return None
            return self.find_one({"_id": document_id})
        return None

    def update_many(self, query: Dict[str, Any], updates: Dict[str, Any]) -> int:
        operators = dict(updates)
        if not any(key.startswith("$") for key in operators):
            operators = {"$set": operators}
        try:
            result = self.coll.update_many(query, operators)
        except PyMongoError as exc:
            raise RepositoryError("Database write failed.") from exc
        if result.modified_count:
            self._after_write()
        return int(result.modified_count)

    def find_one_and_update(self, query: Dict[str, Any], updates: Dict[str, Any],
                            *, sort: Optional[Sequence[Tuple[str, int]]] = None,
                            return_before: bool = False) -> Optional[Dict[str, Any]]:
        """Atomic single-document update - used for token rotation and job claims."""
        operators = dict(updates)
        if not any(key.startswith("$") for key in operators):
            operators = {"$set": operators}
        try:
            result = self.coll.find_one_and_update(
                query,
                operators,
                sort=list(sort) if sort else None,
                return_document=ReturnDocument.BEFORE if return_before else ReturnDocument.AFTER,
            )
        except PyMongoError as exc:
            logger.error("atomic update failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database write failed.") from exc
        if result is not None:
            self._after_write()
        return _utc_moments(result)

    def delete_by_id(self, document_id: str) -> int:
        try:
            result = self.coll.delete_one({"_id": to_object_id(document_id)})
        except PyMongoError as exc:
            raise RepositoryError("Database delete failed.") from exc
        if result.deleted_count:
            self._after_write()
        return int(result.deleted_count)

    def delete_many(self, query: Dict[str, Any]) -> int:
        try:
            result = self.coll.delete_many(query)
        except PyMongoError as exc:
            raise RepositoryError("Database delete failed.") from exc
        if result.deleted_count:
            self._after_write()
        return int(result.deleted_count)

    def bulk_write(self, operations: Sequence[Any]) -> int:
        if not operations:
            return 0
        try:
            result = self.coll.bulk_write(list(operations), ordered=False)
        except PyMongoError as exc:
            logger.error("bulk_write failed on %s: %s", self.collection_name, _clean(exc))
            raise RepositoryError("Database bulk write failed.") from exc
        self._after_write()
        return int(result.modified_count + result.inserted_count + result.deleted_count)

    def exists(self, query: Dict[str, Any]) -> bool:
        return self.find_one(query, {"_id": 1}) is not None


def _clean(exc: Exception) -> str:
    text = str(exc)
    for token in ("mongodb://", "mongodb+srv://"):
        if token in text:
            return text.split(token)[0] + token + "***"
    return text[:300]


# --------------------------------------------------------------------- registry
from common.collections import COLLECTION_NAMES  # noqa: E402

users = Repository("users")
refresh_tokens = Repository("refresh_tokens")
regions = Repository("regions")
datasets = Repository("datasets")
ingestion_jobs = Repository("ingestion_jobs")
climate_observations = Repository("climate_observations")
analytics_summaries = Repository("analytics_summaries")
processing_jobs = Repository("processing_jobs")
model_registry = Repository("model_registry")
predictions = Repository("predictions")
alerts = Repository("alerts")
notifications = Repository("notifications")
reports = Repository("reports")
saved_analyses = Repository("saved_analyses")
audit_logs = Repository("audit_logs")
support_tickets = Repository("support_tickets")
feedback = Repository("feedback")
platform_settings = Repository("platform_settings")

ALL = {
    "users": users,
    "refresh_tokens": refresh_tokens,
    "regions": regions,
    "datasets": datasets,
    "ingestion_jobs": ingestion_jobs,
    "climate_observations": climate_observations,
    "analytics_summaries": analytics_summaries,
    "processing_jobs": processing_jobs,
    "model_registry": model_registry,
    "predictions": predictions,
    "alerts": alerts,
    "notifications": notifications,
    "reports": reports,
    "saved_analyses": saved_analyses,
    "audit_logs": audit_logs,
    "support_tickets": support_tickets,
    "feedback": feedback,
    "platform_settings": platform_settings,
}

assert set(ALL) == set(COLLECTION_NAMES), "Repository registry and collection list are out of sync"
