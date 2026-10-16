"""Ingestion service: one code path for file uploads and provider pulls.

Every run writes an `ingestion_jobs` document with honest counts (fetched, accepted,
rejected, skipped-duplicate), validation errors, timing and retry state. Duplicate
detection relies on the unique `dedupe_key` index, so re-running an import is safe.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

from pymongo.errors import BulkWriteError, DuplicateKeyError

from apps.datasets import repository as datasets
from common.canonical import SCHEMA_VERSION, build_observation, validate_document
from common.db import collection, find_one, insert_one, now_utc, oid, update_one
from common.exceptions import NotFound, ValidationError
from common.logging_config import get_logger
from common.security import sanitize_text

logger = get_logger("earthscape.ingestion")

BATCH_SIZE = 500
MAX_RECORDED_ERRORS = 40


def create_job(*, kind: str, source: str, dataset_id: str | None, params: dict,
               created_by: str, label: str = "") -> dict:
    document = {
        "job_ref": f"ing_{int(time.time() * 1000) % 10**15:015d}{kind[:2]}",
        "kind": kind,
        "source": sanitize_text(source, max_length=60),
        "label": sanitize_text(label or source, max_length=140),
        "dataset_id": dataset_id,
        "parameters": {k: _bounded(v) for k, v in (params or {}).items()},
        "status": "running",
        "records_fetched": 0,
        "records_accepted": 0,
        "records_rejected": 0,
        "records_duplicate": 0,
        "validation_errors": [],
        "coverage": {},
        "retry_count": 0,
        "started_at": now_utc(),
        "completed_at": None,
        "duration_seconds": None,
        "error": None,
        "created_by": created_by,
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    document["_id"] = insert_one("ingestion_jobs", document)
    return document


def _bounded(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value, max_length=300)
    if isinstance(value, (list, tuple)):
        return [sanitize_text(str(v), max_length=120) for v in list(value)[:25]]
    if isinstance(value, dict):
        return {sanitize_text(str(k), max_length=60): _bounded(v) for k, v in list(value.items())[:20]}
    return value


def finish_job(job_id, *, status: str, counts: dict | None = None, errors: list | None = None,
               coverage: dict | None = None, error_message: str | None = None) -> dict:
    completed = now_utc()
    job = find_one("ingestion_jobs", {"_id": job_id}) or {}
    started = job.get("started_at") or completed
    update = {
        "status": status,
        "completed_at": completed,
        "duration_seconds": round((completed - started).total_seconds(), 2),
        "updated_at": completed,
    }
    update.update(counts or {})
    if errors is not None:
        update["validation_errors"] = errors[:MAX_RECORDED_ERRORS]
    if coverage:
        update["coverage"] = coverage
    if error_message:
        update["error"] = sanitize_text(error_message, max_length=500)
    update_one("ingestion_jobs", {"_id": job_id}, {"$set": update})
    return find_one("ingestion_jobs", {"_id": job_id}) or {}


def mark_job_failed(job_id, message: str) -> None:
    finish_job(job_id, status="failed", error_message=message)


def write_observations(documents: Iterable[dict], *, duplicate_policy: str = "skip") -> dict:
    """Batch-insert observations; returns exact accepted/duplicate/rejected counts."""
    table = collection("climate_observations")
    accepted = 0
    duplicates = 0
    rejected = 0
    errors: list[str] = []
    batch: list[dict] = []

    def flush() -> None:
        nonlocal accepted, duplicates
        if not batch:
            return
        try:
            result = table.insert_many(list(batch), ordered=False)
            accepted += len(result.inserted_ids)
        except BulkWriteError as exc:
            accepted += int(exc.details.get("nInserted", 0)) if isinstance(exc.details, dict) else 0
            for detail in (exc.details or {}).get("writeErrors", []) if isinstance(exc.details, dict) else []:
                code = detail.get("code")
                if code == 11000:
                    if duplicate_policy == "replace":
                        key = detail.get("dupKey", {}).get("dedupe_key")
                        if key:
                            table.update_one({"dedupe_key": key},
                                             {"$set": {"superseded_at": now_utc()}}, upsert=False)
                    duplicates += 1
                else:
                    errors.append(f"write error: {detail.get('errmsg', 'unknown')}"[:200])
        batch.clear()

    for document in documents:
        problems = validate_document(document)
        if problems:
            rejected += 1
            if len(errors) < MAX_RECORDED_ERRORS:
                errors.extend(problems)
            continue
        batch.append(document)
        if len(batch) >= BATCH_SIZE:
            flush()
    flush()
    return {
        "records_accepted": accepted,
        "records_duplicate": duplicates,
        "records_rejected": rejected,
        "errors": errors,
    }


def ingest_row_stream(*, dataset_id: str, row_stream: Iterator[dict], column_map: dict,
                      units_hint: dict | None, granularity: str, provider: str, kind: str,
                      job_id, source_file: str | None = None,
                      default_location: dict | None = None,
                      source_timezone: tzinfo | None = None,
                      dry_run: bool = False, limit: int | None = None,
                      preview_rows: int = 20) -> dict:
    """Shared pipeline for uploads and API payloads.

    With `dry_run` nothing is written: accepted/rejected previews come back instead.
    """
    accepted_preview: list[dict] = []
    rejected_preview: list[dict] = []
    documents: list[dict] = []
    fetched = 0
    flagged = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    regions: set[str] = set()
    coords: list[tuple[float, float]] = []

    for row in row_stream:
        if limit and fetched >= limit:
            break
        fetched += 1
        document, issues = build_observation(
            dataset_id=dataset_id,
            row=row,
            column_map=column_map,
            units_hint=units_hint,
            granularity=granularity,
            provider=provider,
            kind=kind,
            ingestion_job_id=str(job_id) if job_id else None,
            source_file=source_file,
            row_number=fetched,
            default_location=default_location,
            source_timezone=source_timezone,
        )
        if document is None:
            if len(rejected_preview) < preview_rows:
                rejected_preview.append({"row": fetched, "reasons": issues,
                                         "values": {k: v for k, v in list(row.items())[:8]}})
            continue
        if issues and len(rejected_preview) < preview_rows:
            rejected_preview.append({"row": fetched, "reasons": issues, "warning_only": True,
                                     "values": {k: v for k, v in list(row.items())[:8]}})
        if document["quality"]["flags"]:
            flagged += 1
        documents.append(document)
        timestamp = document["timestamp"]
        earliest = timestamp if earliest is None or timestamp < earliest else earliest
        latest = timestamp if latest is None or timestamp > latest else latest
        region = document["location"].get("region")
        if region:
            regions.add(region)
        if len(coords) < 4000:
            coords.append((document["location"]["latitude"], document["location"]["longitude"]))

    counts = {"records_fetched": fetched}
    if dry_run:
        accepted = len(documents)
        result_counts = {**counts, "records_accepted": accepted,
                         "records_rejected": fetched - accepted, "records_duplicate": 0,
                         "records_previewed": min(accepted, preview_rows)}
        return {
            "dry_run": True,
            "counts": result_counts,
            "quality_flagged_rows": flagged,
            "coverage": {"temporal_start": earliest.isoformat() if earliest else None,
                         "temporal_end": latest.isoformat() if latest else None,
                         "regions": sorted(regions)[:40],
                         "bounding_box": _bounding_box(coords) if coords else None},
            "accepted_preview": [_shallow(d) for d in documents[:preview_rows]],
            "rejected_preview": rejected_preview,
            "errors": [],
        }

    outcomes = write_observations(documents)
    total = {**counts, **{k: v for k, v in outcomes.items() if k != "errors"}}
    return {
        "dry_run": False,
        "counts": total,
        "quality_flagged_rows": flagged,
        "coverage": {"temporal_start": earliest.isoformat() if earliest else None,
                     "temporal_end": latest.isoformat() if latest else None,
                     "regions": sorted(regions)[:40],
                     "bounding_box": _bounding_box(coords) if coords else None},
        "accepted_preview": [_shallow(d) for d in documents[:preview_rows]],
        "rejected_preview": rejected_preview,
        "errors": outcomes["errors"],
    }


def _bounding_box(coords: list[tuple[float, float]]) -> dict[str, float]:
    lats = [c[0] for c in coords]
    lons = [c[1] for c in coords]
    return {"min_lat": min(lats), "max_lat": max(lats), "min_lon": min(lons), "max_lon": max(lons)}


def _shallow(document: dict) -> dict:
    return {
        "timestamp": document["timestamp"].isoformat(),
        "granularity": document["granularity"],
        "location": document["location"],
        "metrics": document["metrics"],
        "quality": document["quality"],
    }


def apply_job_counts_to_dataset(dataset_id: str, *, records_added: int, coverage: dict,
                                status: str, storage_uri: str | None = None) -> None:
    datasets.record_ingestion_result(dataset_id, records_added=records_added, coverage=coverage,
                                     status=status, storage_uri=storage_uri)
