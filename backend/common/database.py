"""MongoDB access layer.

MongoDB Atlas is the primary application database for EarthScape Climate Agency.
Django's ORM is deliberately **not** used for application data: every record is a
MongoDB document accessed through PyMongo (``backend/common/repositories.py``).

Two connection modes exist:

``atlas``
    Selected whenever ``MONGODB_URI`` is configured and the server answers a ping.
    This is the production path.

``local-development``
    Selected when ``MONGODB_URI`` is absent or unreachable and
    ``FORCE_ATLAS_CONNECTION`` is not set. It uses an in-process document store
    (``mongomock``) with JSON snapshots under ``backend/.data/localdb`` so the
    platform is fully usable - with the same collection, document and query
    semantics - on a machine that has no Atlas credentials. The active mode is
    always reported by ``GET /api/v1/health`` and never silently faked as Atlas.

Set ``FORCE_ATLAS_CONNECTION=true`` to make an unavailable Atlas cluster a hard
startup failure instead of a fallback (recommended for production).
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from bson import json_util
from bson.codec_options import CodecOptions
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from config import env

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOCALDB_DIR = BACKEND_DIR / ".data" / "localdb"

# Collections used by the platform (authoritative list: common/collections.py).
# Order matters for snapshot restore so that reference data exists first.
from common.collections import COLLECTION_NAMES as COLLECTIONS  # noqa: E402


class DocumentStoreUnavailable(RuntimeError):
    """Raised when neither Atlas nor the local development store can be used."""


class _LocalDocumentStore:
    """File-backed document store used only in local-development mode."""

    def __init__(self, database_name: str, directory: Path):
        import mongomock

        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._client = mongomock.MongoClient()
        self._db = self._client[database_name]
        self._restore()

    def _path(self, collection: str) -> Path:
        return self.directory / f"{collection}.json"

    def _restore(self) -> None:
        for collection in COLLECTIONS:
            path = self._path(collection)
            if not path.is_file():
                continue
            try:
                documents = json_util.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.warning("Could not restore %s: %s", collection, exc)
                continue
            if documents:
                self._db[collection].insert_many(documents, ordered=False)
                logger.info("Restored %d document(s) into %s", len(documents), collection)

    def snapshot(self, collection: str) -> None:
        documents = list(self._db[collection].find({}))
        payload = json_util.dumps(documents, ensure_ascii=False)
        self._path(collection).write_text(payload, encoding="utf-8")

    def collection(self, name: str):
        return self._db[name]

    def collection_names(self) -> List[str]:
        return list(self._db.list_collection_names())

    def ping(self) -> Dict[str, Any]:
        return {"ok": 1}


class Database:
    """Thread-safe MongoDB gateway shared by every repository."""

    def __init__(self, uri: Optional[str], database_name: str, force_atlas: bool = False):
        self.database_name = database_name
        self.force_atlas = force_atlas
        self._lock = threading.RLock()
        self._dirty: set[str] = set()
        self._client: Any = None
        self._store: Any = None
        self.mode: str = "uninitialised"
        self.error: Optional[str] = None
        self._connect(uri)

    # ------------------------------------------------------------------ connect
    def _connect(self, uri: Optional[str]) -> None:
        if uri:
            try:
                client = MongoClient(
                    uri,
                    maxPoolSize=env.get_int("MONGODB_MAX_POOL_SIZE", 50),
                    minPoolSize=env.get_int("MONGODB_MIN_POOL_SIZE", 0),
                    serverSelectionTimeoutMS=env.get_int("MONGODB_SERVER_TIMEOUT_MS", 5000),
                    connectTimeoutMS=env.get_int("MONGODB_CONNECT_TIMEOUT_MS", 5000),
                    socketTimeoutMS=env.get_int("MONGODB_SOCKET_TIMEOUT_MS", 30000),
                    codec_options=CodecOptions(tz_aware=True, tzinfo=timezone.utc),
                    retryWrites=True,
                )
                client.admin.command("ping")
                self._client = client
                self.mode = "atlas"
                logger.info("Connected to MongoDB (Atlas) database %s", self.database_name)
                return
            except (ServerSelectionTimeoutError, PyMongoError, ValueError) as exc:
                self.error = _safe_error(exc)
                if self.force_atlas:
                    raise DocumentStoreUnavailable(
                        "MongoDB Atlas is required but unreachable: " + self.error
                    ) from exc
                logger.warning("MongoDB Atlas unavailable (%s); using local-development mode", self.error)

        if self.force_atlas:
            raise DocumentStoreUnavailable("MONGODB_URI is required when FORCE_ATLAS_CONNECTION=true.")

        self._store = _LocalDocumentStore(self.database_name, LOCALDB_DIR)
        self.mode = "local-development"

    # --------------------------------------------------------------- collections
    def collection(self, name: str):
        with self._lock:
            if self._store is not None:
                return self._store.collection(name)
            return self._client[self.database_name][name]

    def flush_if_dirty(self) -> List[str]:
        """Persist local-development snapshots after writes. No-op on Atlas."""
        if self._store is None:
            return []
        with self._lock:
            if not self._dirty:
                return []
            flushed = sorted(self._dirty)
            self._dirty.clear()
        for name in flushed:
            try:
                self._store.snapshot(name)
            except OSError as exc:  # pragma: no cover - disk failure is not silent
                logger.error("Could not snapshot %s: %s", name, exc)
        return flushed

    def mark_dirty(self, name: str) -> None:
        if self._store is not None:
            with self._lock:
                self._dirty.add(name)

    # ------------------------------------------------------------------- status
    def ping(self) -> Dict[str, Any]:
        try:
            result = self._store.ping() if self._store else self._client.admin.command("ping")
            return {"mode": self.mode, "ok": bool(result), "database": self.database_name}
        except Exception as exc:  # noqa: BLE001 - health checks never raise
            return {"mode": self.mode, "ok": False, "database": self.database_name, "error": _safe_error(exc)}

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for name in COLLECTIONS:
            try:
                out[name] = self.collection(name).estimated_document_count()
            except Exception:  # noqa: BLE001
                try:
                    out[name] = self.collection(name).count_documents({})
                except Exception:  # noqa: BLE001
                    out[name] = 0
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def drop_collection(self, name: str) -> None:
        """Used by administrative reset tooling only."""
        self.collection(name).drop()
        self.mark_dirty(name)


def _safe_error(exc: Exception) -> str:
    """Strip credentials that PyMongo can embed in connection error strings."""
    text = str(exc)
    for token in ("mongodb://", "mongodb+srv://"):
        if token in text:
            scheme, _, remainder = text.partition(token)
            host = remainder.split("@")[-1]
            text = f"{scheme}{token}***@{host}"
            break
    return text[:400]


_DATABASE: Optional[Database] = None
_INIT_LOCK = threading.Lock()


def get_database() -> Database:
    global _DATABASE
    if _DATABASE is None:
        with _INIT_LOCK:
            if _DATABASE is None:
                _DATABASE = Database(
                    uri=env.get_raw("MONGODB_URI"),
                    database_name=env.get_str("MONGODB_DATABASE", "earthscape_climate"),
                    force_atlas=env.get_bool("FORCE_ATLAS_CONNECTION"),
                )
    return _DATABASE


def reset_database_singleton() -> None:
    """Test/utility hook: force reconnection with the current environment."""
    global _DATABASE
    with _INIT_LOCK:
        if _DATABASE is not None:
            _DATABASE.close()
        _DATABASE = None


def iter_documents(collection: str, query: Dict[str, Any], *, projection: Optional[Dict[str, Any]] = None,
                   sort: Optional[Iterable[Any]] = None, limit: int = 0,
                   batch_size: int = 1000) -> Iterable[Dict[str, Any]]:
    """Stream documents with a bounded server-side cursor."""
    cursor = get_database().collection(collection).find(query, projection)
    if sort:
        cursor = cursor.sort(list(sort))
    if limit:
        cursor = cursor.limit(limit)
    return cursor.batch_size(batch_size)
