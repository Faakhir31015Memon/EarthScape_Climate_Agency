"""Ingestion job lifecycle and registered background tasks.

Job counters and ``error_message`` values come from the connectors and the file
importer.  A source that is unconfigured or unreachable produces a ``failed`` job
carrying the reason the connector returned, not a success.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apps.datasets import importer
from apps.ingestion.connectors import base as connector_base
from apps.ingestion.connectors import get_connector, resolve_name
from common import audit, regions, storage_paths, tasks, validators
from common.repositories import (
    climate_observations,
    datasets as datasets_repository,
    ingestion_jobs,
    maybe_object_id,
    serialize,
    utcnow,
)
from config import env

logger = logging.getLogger(__name__)

MAX_LOG_LINES = 200
MAX_VALIDATION_ERRORS = 200

# --------------------------------------------------------------------- job model
JOB_TYPES = ("api_sync", "file_import", "live_provider")
TERMINAL_STATUSES = ("completed", "completed_with_errors", "failed", "cancelled")


def _run_id() -> str:
    return secrets.token_hex(8)


def new_job_document(*, source_name: str, source_type: str, job_type: str,
                     dataset_id: Optional[str], requested_by: Optional[str],
                     triggered_by: str, data_period: Dict[str, Any],
                     geographic_coverage: Dict[str, Any], parameters: Dict[str, Any],
                     processing_mode: str) -> Dict[str, Any]:
    return {
        "source_name": source_name,
        "source_type": source_type,
        "dataset_id": maybe_object_id(dataset_id) if dataset_id else None,
        "job_type": job_type,
        "status": "queued",
        "started_at": utcnow(),
        "completed_at": None,
        "records_fetched": 0,
        "records_accepted": 0,
        "records_rejected": 0,
        "records_skipped": 0,
        "duplicate_count": 0,
        "validation_errors": [],
        "retry_count": 0,
        "max_retries": env.get_int("INGESTION_MAX_RETRIES", 3),
        "data_period": data_period,
        "geographic_coverage": geographic_coverage,
        "requested_by": requested_by,
        "triggered_by": triggered_by,
        "error_message": None,
        "processing_mode": processing_mode,
        "parameters": connector_base.sanitise_parameters(parameters),
        "log_lines": [],
        "executor": None,
    }


def create_job(**kwargs) -> str:
    document = new_job_document(**kwargs)
    job_id = ingestion_jobs.insert_one(document)
    append_log(job_id, "info", f"Job queued for {kwargs['source_name']} ({kwargs['job_type']}).")
    return job_id


# ------------------------------------------------------------- job doc helpers
def append_log(job_id: str, level: str, message: str) -> None:
    entry = {"at": utcnow(), "level": level, "message": _clean(message)[:300]}
    ingestion_jobs.update_one({"_id": maybe_object_id(job_id)},
                              {"$push": {"log_lines": {"$each": [entry], "$slice": -MAX_LOG_LINES}}})


def set_status(job_id: str, status: str, **extra) -> None:
    updates: Dict[str, Any] = {"status": status}
    updates.update(extra)
    if status in TERMINAL_STATUSES:
        updates.setdefault("completed_at", utcnow())
    ingestion_jobs.update_by_id(job_id, updates)


def _clean(message: str) -> str:
    text = str(message)
    for token in ("mongodb://", "mongodb+srv://"):
        if token in text:
            text = text.split(token)[0] + token + "***"
    # Strip any accidental key material patterns.
    return text[:500]


def job_detail(job_id: str) -> Optional[Dict[str, Any]]:
    document = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)})
    return serialize(document)


def is_cancel_requested(job_id: str) -> bool:
    document = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)},
                                       {"_id": 1, "cancel_requested": 1, "status": 1})
    if not document:
        return True
    return bool(document.get("cancel_requested")) or document.get("status") == "cancelled"


# ------------------------------------------------------------------- api sync
@tasks.register("ingestion.run_api_sync")
def run_api_sync(job_id: str, **_) -> Dict[str, Any]:
    job = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)})
    if job is None:
        logger.error("run_api_sync: job %s not found", job_id)
        return {"ok": False, "reason": "job_missing"}

    set_status(job_id, "running", executor=tasks.backend(),
               started_at=job.get("started_at") or utcnow())
    run_id = _run_id()
    parameters = job.get("parameters", {}) or {}
    source_name = job.get("source_name", "live source")
    append_log(job_id, "info", f"Starting API sync (run {run_id}).")

    try:
        connector = get_connector(parameters.get("connector") or job.get("source_type") or source_name)
    except connector_base.SourceValidationError as exc:
        set_status(job_id, "failed", error_message=_clean(exc.message))
        append_log(job_id, "error", exc.message)
        return {"ok": False, "reason": "unknown_connector"}

    # Configuration gate - an unconfigured connector fails the job.
    if not connector.is_configured():
        message = connector.setup_hint or "The connector is not configured."
        set_status(job_id, "failed", error_message=f"needs_configuration: {message}")
        append_log(job_id, "error", f"Aborted: {message}")
        return {"ok": False, "reason": "needs_configuration"}

    dataset = _resolve_dataset(job)
    if dataset is None:
        set_status(job_id, "failed", error_message="No target dataset could be resolved.")
        append_log(job_id, "error", "No target dataset resolved.")
        return {"ok": False, "reason": "dataset_missing"}

    request = connector_base.IngestionRequest(
        dataset_id=str(dataset["_id"]), run_id=run_id, job_id=str(job_id),
        source_name=job.get("source_name") or connector.name,
        source_type=dataset.get("source_type") or connector.source_type,
        dataset_name=dataset.get("name", ""),
        parameters=parameters,
        locations=_resolve_locations(parameters),
        start=(job.get("data_period") or {}).get("start"),
        end=(job.get("data_period") or {}).get("end"),
        processing_mode=job.get("processing_mode", "live-api"))

    try:
        observations = list(connector.run(request))
    except connector_base.NotConfigured as exc:
        set_status(job_id, "failed", error_message=f"needs_configuration: {_clean(exc.message)}")
        append_log(job_id, "error", exc.message)
        return {"ok": False, "reason": "needs_configuration"}
    except connector_base.ConnectorError as exc:
        set_status(job_id, "failed", error_message=_clean(exc.message))
        append_log(job_id, "error", f"Connector error: {exc.message}")
        return {"ok": False, "reason": "connector_error"}

    if not observations:
        set_status(job_id, "completed_with_errors",
                   error_message="The source responded but produced no usable observations.",
                   records_fetched=0, records_accepted=0)
        append_log(job_id, "warning", "No observations produced by the source.")
        return {"ok": True, "reason": "empty", "accepted": 0}

    result = importer.upsert_observations(
        observations, should_cancel=lambda: is_cancel_requested(job_id))

    return _finalise(job_id, dataset, result, run_id=run_id, connector_name=connector.name)


# ----------------------------------------------------------------- file import
@tasks.register("ingestion.run_file_import")
def run_file_import(job_id: str, **_) -> Dict[str, Any]:
    job = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)})
    if job is None:
        logger.error("run_file_import: job %s not found", job_id)
        return {"ok": False, "reason": "job_missing"}

    dataset_id = job.get("dataset_id")
    dataset = datasets_repository.find_one({"_id": maybe_object_id(str(dataset_id))}) \
        if dataset_id else None
    if dataset is None:
        set_status(job_id, "failed", error_message="The target dataset no longer exists.")
        append_log(job_id, "error", "Dataset missing.")
        return {"ok": False, "reason": "dataset_missing"}

    path = storage_paths.stored_path(dataset.get("storage_uri"))
    if path is None:
        set_status(job_id, "failed", error_message="No stored file for this dataset.")
        append_log(job_id, "error", "Dataset has no stored file.")
        return {"ok": False, "reason": "file_missing"}
    path = str(path)

    run_id = _run_id()
    set_status(job_id, "running", executor=tasks.backend(),
               started_at=job.get("started_at") or utcnow())
    datasets_repository.update_by_id(str(dataset["_id"]), {"status": "ingesting", "updated_at": utcnow()})
    append_log(job_id, "info", f"Importing stored file (run {run_id}).")

    mapping = _ensure_mapping(dataset, path)

    try:
        result = importer.import_file(
            path, dataset, mapping, job_id=str(job_id), run_id=run_id,
            processing_mode=dataset.get("processing_mode", "uploaded-file"),
            should_cancel=lambda: is_cancel_requested(job_id))
    except importer.file_import.FileImportError as exc:
        set_status(job_id, "failed", error_message=_clean(str(exc)))
        datasets_repository.update_by_id(str(dataset["_id"]), {"status": "failed", "updated_at": utcnow()})
        append_log(job_id, "error", str(exc))
        return {"ok": False, "reason": "file_error"}
    except Exception as exc:  # noqa: BLE001 - background boundary
        logger.exception("file import crashed")
        set_status(job_id, "failed", error_message=_clean(f"Unexpected import failure: {exc}"))
        datasets_repository.update_by_id(str(dataset["_id"]), {"status": "failed", "updated_at": utcnow()})
        append_log(job_id, "error", _clean(str(exc)))
        return {"ok": False, "reason": "crash"}

    return _finalise(job_id, dataset, result, run_id=run_id, connector_name="file_import")


# ------------------------------------------------------------------- finalise
def _finalise(job_id: str, dataset: Dict[str, Any], result: importer.ImportResult, *,
              run_id: str, connector_name: str) -> Dict[str, Any]:
    counts = result.summary_counts()
    status = "completed"
    if result.aborted_reason:
        status = "cancelled" if "Cancel" in result.aborted_reason else "completed_with_errors"
    elif result.rejected or result.issues:
        status = "completed_with_errors"

    ingestion_jobs.update_by_id(job_id, {
        **counts,
        "validation_errors": result.issues[:MAX_VALIDATION_ERRORS],
        "status": status if status != "cancelled" else "cancelled",
        "completed_at": utcnow(),
        "error_message": result.aborted_reason,
    })
    append_log(job_id, "info",
               f"Run {run_id} finished: fetched={counts['records_fetched']} "
               f"accepted={counts['records_accepted']} rejected={counts['records_rejected']} "
               f"duplicates={counts['duplicate_count']} status={status}.")

    importer.update_dataset_after_import(str(dataset["_id"]), result,
                                         final_status="ready" if counts["records_accepted"] else "failed")
    get_database_flush()
    return {"ok": True, "status": status, **counts}


def get_database_flush() -> None:
    try:
        from common.database import get_database

        get_database().flush_if_dirty()
    except Exception as exc:  # noqa: BLE001
        logger.error("flush after job failed: %s", exc)


# --------------------------------------------------------------------- dataset
def _resolve_dataset(job: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    dataset_id = job.get("dataset_id")
    if dataset_id:
        return datasets_repository.find_one({"_id": maybe_object_id(str(dataset_id))})
    return get_or_create_source_dataset(
        source_name=job.get("source_name", "live source"),
        source_type=job.get("source_type", "live_api"),
        created_by=job.get("requested_by"))


def get_or_create_source_dataset(*, source_name: str, source_type: str,
                                 created_by: Optional[str] = None) -> Dict[str, Any]:
    name = f"{source_name} feed"
    existing = datasets_repository.find_one({"name": name, "source_type": source_type})
    if existing:
        return existing
    now = utcnow()
    document = {
        "name": name, "description": f"Live ingestion feed for {source_name}.",
        "source_type": source_type if source_type in
        ("nasa_power", "noaa", "live_api", "university_upload", "sample") else "live_api",
        "source_name": source_name, "source_url": None, "file_name": None, "storage_uri": None,
        "format": "api", "schema_version": validators.SCHEMA_VERSION,
        "geographic_coverage": {"regions": [], "countries": [], "bbox": None, "location_codes": []},
        "temporal_coverage": {"start": None, "end": None, "years": [], "frequency": "daily"},
        "record_count": 0, "status": "ingesting", "uploaded_by": created_by,
        "created_at": now, "updated_at": now, "source_metadata": {"feed": True},
        "validation_report": {}, "ingestion_job_ids": [], "tags": ["live", "auto"],
        "units": {}, "is_public": True,
    }
    document["_id"] = maybe_object_id(datasets_repository.insert_one(document))
    return document


# -------------------------------------------------------------------- locations
def _resolve_locations(parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
    locations: List[Dict[str, Any]] = []
    codes = parameters.get("location_codes") or []
    if isinstance(codes, str):
        codes = [codes]
    for code in codes:
        reference = regions.location_by_code(str(code))
        if reference:
            locations.append(_reference_location(reference))
    latitude = validators.to_float(parameters.get("latitude"))
    longitude = validators.to_float(parameters.get("longitude"))
    if latitude is not None and longitude is not None and not locations:
        locations.append({"code": parameters.get("location_code", "CUSTOM"),
                          "name": parameters.get("location_name", "Custom point"),
                          "region_code": parameters.get("region_code", ""),
                          "country": parameters.get("country", ""),
                          "latitude": latitude, "longitude": longitude})
    if not locations:
        cap = env.get_int("NASA_POWER_MAX_POINTS_PER_RUN", 24)
        locations = [_reference_location(item) for item in regions.LOCATIONS[:cap]]
    return locations


def _reference_location(reference: Dict[str, Any]) -> Dict[str, Any]:
    return {"code": reference["code"], "name": reference["name"],
            "region_code": reference["region_code"], "country": reference["country"],
            "latitude": reference["latitude"], "longitude": reference["longitude"]}


# ---------------------------------------------------------------------- mapping
def _ensure_mapping(dataset: Dict[str, Any], path: str) -> Dict[str, str]:
    metadata = dataset.get("source_metadata", {}) if isinstance(dataset.get("source_metadata"), dict) else {}
    mapping = metadata.get("column_mapping") or {}
    if mapping:
        return dict(mapping)
    from apps.datasets import file_import

    headers = file_import.read_headers(path)
    mapping = validators.auto_map_columns(headers)
    # Persist the suggestion so retries are deterministic.
    datasets_repository.update_by_id(str(dataset["_id"]),
                                     {"source_metadata.column_mapping": mapping, "updated_at": utcnow()})
    return mapping


# ------------------------------------------------------------------ submit helpers
def submit_api_sync(job_id: str) -> str:
    executor = tasks.submit("ingestion.run_api_sync", job_id=str(job_id),
                            job_type="ingestion", payload={})
    ingestion_jobs.update_by_id(job_id, {"executor": executor})
    return executor


def submit_file_import(job_id: str) -> str:
    executor = tasks.submit("ingestion.run_file_import", job_id=str(job_id),
                            job_type="ingestion", payload={})
    ingestion_jobs.update_by_id(job_id, {"executor": executor})
    return executor


# ------------------------------------------------------------------ retry/cancel
def retry_job(job_id: str, *, requested_by: Optional[str] = None) -> Dict[str, Any]:
    job = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)})
    if job is None:
        return {"ok": False, "reason": "not_found"}
    if job.get("status") not in ("failed", "cancelled"):
        return {"ok": False, "reason": "not_retryable", "status": job.get("status")}
    if int(job.get("retry_count", 0)) >= int(job.get("max_retries", 3)):
        return {"ok": False, "reason": "max_retries", "status": job.get("status")}

    ingestion_jobs.update_by_id(job_id, {
        "status": "queued", "retry_count": int(job.get("retry_count", 0)) + 1,
        "error_message": None, "cancel_requested": False, "completed_at": None,
        "started_at": utcnow(), "records_fetched": 0, "records_accepted": 0,
        "records_rejected": 0, "records_skipped": 0, "duplicate_count": 0})
    append_log(job_id, "info", "Retry requested; job re-queued.")
    task_name = "ingestion.run_file_import" if job.get("job_type") == "file_import" \
        else "ingestion.run_api_sync"
    executor = tasks.submit(task_name, job_id=str(job_id), job_type="ingestion", payload={})
    ingestion_jobs.update_by_id(job_id, {"executor": executor})
    audit.record(action="ingestion.job_retried", resource_type="ingestion_job", resource_id=str(job_id),
                 actor_user_id=requested_by, metadata={"task": task_name})
    return {"ok": True, "executor": executor, "status": "queued"}


def cancel_job(job_id: str, *, requested_by: Optional[str] = None) -> Dict[str, Any]:
    job = ingestion_jobs.find_one({"_id": maybe_object_id(job_id)})
    if job is None:
        return {"ok": False, "reason": "not_found"}
    if job.get("status") in TERMINAL_STATUSES:
        return {"ok": False, "reason": "already_finished", "status": job.get("status")}
    ingestion_jobs.update_by_id(job_id, {"cancel_requested": True})
    if job.get("status") == "queued":
        set_status(job_id, "cancelled", completed_at=utcnow(), error_message="Cancelled before start.")
    else:
        append_log(job_id, "warning", "Cancellation requested; the running job stops at the next batch.")
    audit.record(action="ingestion.job_cancelled", resource_type="ingestion_job", resource_id=str(job_id),
                 actor_user_id=requested_by)
    return {"ok": True, "status": "cancel_requested"}


# --------------------------------------------------------------------- schedule
def scheduled_sources() -> List[Dict[str, Any]]:
    """Sources the scheduler polls: NASA POWER + the configured live provider, when enabled."""
    lookback_days = env.get_int("INGESTION_SCHEDULE_LOOKBACK_DAYS", 7)
    end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    sources = [
        {"connector": "nasa_power", "source_name": "NASA POWER", "source_type": "nasa_power"},
        {"connector": resolve_name(env.get_str("LIVE_CLIMATE_PROVIDER", "open_meteo")),
         "source_name": "Live Climate API", "source_type": "live_api"},
    ]
    return [{**source, "start": start, "end": end} for source in sources]


def sync_all_scheduled(*, triggered_by: str = "schedule", requested_by: Optional[str] = None) -> List[Dict[str, Any]]:
    """Queue a bounded recent-window sync for every configured scheduled source."""
    results: List[Dict[str, Any]] = []
    for source in scheduled_sources():
        connector = get_connector(source["connector"])
        if not connector.is_configured():
            results.append({"source": source["connector"], "queued": False,
                            "reason": "needs_configuration"})
            continue
        try:
            dataset = get_or_create_source_dataset(source_name=source["source_name"],
                                                   source_type=source["source_type"])
        except Exception as exc:  # noqa: BLE001
            results.append({"source": source["connector"], "queued": False,
                            "reason": _clean(str(exc))})
            continue
        job_id = create_job(
            source_name=source["source_name"], source_type=source["source_type"],
            job_type="api_sync", dataset_id=str(dataset["_id"]), requested_by=requested_by,
            triggered_by=triggered_by,
            data_period={"start": source["start"], "end": source["end"]},
            geographic_coverage={"regions": [], "location_codes": []},
            parameters={"connector": source["connector"]},
            processing_mode="live-api")
        executor = tasks.submit("ingestion.run_api_sync", job_id=job_id, job_type="ingestion", payload={})
        ingestion_jobs.update_by_id(job_id, {"executor": executor})
        results.append({"source": source["connector"], "queued": True, "job_id": job_id,
                        "executor": executor,
                        "period": {"start": source["start"], "end": source["end"]}})
    return results
