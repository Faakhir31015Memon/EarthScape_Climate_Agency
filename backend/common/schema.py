"""Applies index specs and collection validators from common.collections, then records
the version in platform_settings. Idempotent.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from pymongo.errors import OperationFailure, PyMongoError

from common import collections as schema
from common.database import get_database
from common.repositories import platform_settings, utcnow

logger = logging.getLogger(__name__)

SUPPORTED_ON_LOCAL_STORE = {
    # mongomock ignores these; the same rules are enforced in common/validators.py
    "$jsonSchema validators",
    "TTL indexes",
    "2dsphere indexes",
}


def _prune_stale_indexes(db, report: List[Dict[str, Any]]) -> None:
    """Drop indexes on managed collections that ``INDEX_SPECS`` no longer declares.

    An index left over from an earlier field naming (``model_name`` instead of
    ``name``, for example) indexes every document as ``null``, so its unique
    constraint rejects a perfectly legitimate second insert with a duplicate-key
    error. Removing them keeps the store and the declared contract comparable;
    ``_create_indexes`` then rebuilds the current definition under its own name.
    """
    for collection_name, specs in schema.INDEX_SPECS.items():
        declared = {spec["name"] for spec in specs}
        collection = db.collection(collection_name)
        try:
            existing = collection.index_information()
        except PyMongoError:
            continue
        for index_name in existing:
            if index_name == "_id_" or index_name in declared:
                continue
            try:
                collection.drop_index(index_name)
                report.append({"collection": collection_name, "index": index_name,
                               "action": "dropped (not declared)"})
            except PyMongoError as exc:
                logger.warning("Stale index %s on %s not dropped: %s",
                               index_name, collection_name, str(exc)[:120])


def _create_indexes(db, report: List[Dict[str, Any]]) -> None:
    for collection_name, specs in schema.INDEX_SPECS.items():
        collection = db.collection(collection_name)
        for spec in specs:
            payload = dict(spec)
            name = payload.pop("name")
            keys = payload.pop("keys")
            partial = payload.pop("partial", None)
            if partial is not None:
                payload["partialFilterExpression"] = partial
            try:
                # index_information() maps index name -> definition
                existing = collection.index_information()
            except PyMongoError:
                existing = {}
            if name in existing:
                report.append({"collection": collection_name, "index": name, "action": "unchanged"})
                continue
            try:
                collection.create_index(keys, name=name, **payload)
                report.append({"collection": collection_name, "index": name, "action": "created"})
            except (OperationFailure, PyMongoError) as exc:
                # 2dsphere and TTL are unsupported by the local-development store
                report.append({
                    "collection": collection_name,
                    "index": name,
                    "action": "skipped",
                    "reason": str(exc)[:200],
                })
                logger.warning("Index %s on %s skipped: %s", name, collection_name, str(exc)[:120])


def _apply_validators(db, report: List[Dict[str, Any]]) -> None:
    for collection_name, spec in schema.VALIDATORS.items():
        try:
            raw = db.collection(collection_name)
            if hasattr(raw, "database") and hasattr(raw.database, "command"):
                raw.database.command(
                    "collMod",
                    raw.name,
                    validator=spec["validator"],
                    validationLevel=spec["level"],
                    validationAction=spec["action"],
                )
                report.append({"collection": collection_name, "validator": "applied"})
        except (OperationFailure, PyMongoError, AttributeError, TypeError) as exc:
            report.append({"collection": collection_name, "validator": "server-side-unavailable"})
            logger.info("Validator not applied on %s (%s)", collection_name, str(exc)[:100])


def current_version() -> int:
    document = platform_settings.find_one({"key": "schema_version"})
    if not document:
        return 0
    try:
        return int(document.get("value", 0))
    except (TypeError, ValueError):
        # A store written by an older release recorded a dotted string here.
        return 0


def apply_schema() -> Dict[str, Any]:
    db = get_database()
    report: List[Dict[str, Any]] = []
    previous_version = current_version()
    _prune_stale_indexes(db, report)
    _create_indexes(db, report)
    _apply_validators(db, report)

    platform_settings.update_one(
        {"key": "schema_version"},
        {"$set": {"key": "schema_version", "value": schema.SCHEMA_VERSION,
                  "applied_at": utcnow(), "mode": db.mode}},
        upsert=True,
    )
    db.flush_if_dirty()
    return {
        "database": db.database_name,
        "mode": db.mode,
        "schema_version": schema.SCHEMA_VERSION,
        "previous_version": previous_version,
        "index_operations": report,
    }
