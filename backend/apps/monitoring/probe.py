"""Operational probes: what is genuinely reachable right now."""

from __future__ import annotations

import os
import platform
import shutil
import time
from datetime import datetime, timezone

from django.conf import settings

from common.db import COLLECTIONS, count, db, ping
from common.env import ENV, startup_status, storage_root
from common.paths import ensure_paths

BOOT_TIME = time.time()


def _collection_counts() -> dict[str, int]:
    probe = db()
    existing = set(probe.list_collection_names())
    return {name: (probe[name].estimated_document_count() if name in existing else 0)
            for name in COLLECTIONS}


def data_lake_state() -> dict:
    root = storage_root()
    layout = ["raw", "validated", "processed", "analytics", "ml-features", "exports"]
    present = {layer: (root / layer).is_dir() for layer in layout}
    total_bytes = 0
    file_count = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total_bytes += os.path.getsize(os.path.join(dirpath, name))
                file_count += 1
            except OSError:
                continue
    usage = shutil.disk_usage(str(root))
    return {
        "root": str(root),
        "layers_present": present,
        "files": file_count,
        "bytes": total_bytes,
        "free_disk_gb": round(usage.free / 1e9, 2),
        "engine": "webhdfs" if ENV.exists("HDFS_WEBHDFS_URL") else "local",
    }


def last_sync() -> dict:
    try:
        rows = list(db()["ingestion_jobs"].find(
            {"status": "completed"}, {"started_at": 1, "finished_at": 1, "source": 1,
                                      "records_accepted": 1}
        ).sort("finished_at", -1).limit(1))
    except Exception:
        rows = []
    if not rows:
        return {"status": "no_successful_sync", "detail": "No ingestion job has completed yet."}
    row = rows[0]
    finished = row.get("finished_at")
    age_seconds = (datetime.now(timezone.utc) - finished).total_seconds() if finished else None
    return {
        "status": "available",
        "finished_at": finished.isoformat() if finished else None,
        "age_seconds": int(age_seconds) if age_seconds is not None else None,
        "source": row.get("source"),
        "records_accepted": row.get("records_accepted"),
    }


def snapshot() -> dict:
    ensure_paths()
    mongo = ping()
    counts: dict[str, int] = {}
    error = None
    if mongo.get("reachable"):
        try:
            counts = _collection_counts()
        except Exception as exc:
            error = type(exc).__name__
    return {
        "database": mongo,
        "collection_counts": counts,
        "collection_counts_error": error,
        "services": [s.as_dict() for s in startup_status()],
        "data_lake": data_lake_state(),
        "last_sync": last_sync(),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "uptime_seconds": int(time.time() - BOOT_TIME),
            "debug": settings.DEBUG,
            "signing_key_persisted": not settings.SIGNING_KEY_IS_EPHEMERAL,
            "background_engine": ENV.raw("CELERY_ENABLED") and ENV.raw("REDIS_URL") and "celery" or "threaded",
            "self_registration": settings.APP["ALLOW_SELF_REGISTRATION"],
        },
    }
