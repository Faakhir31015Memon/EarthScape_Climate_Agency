"""File import job executed on the background task engine."""

from __future__ import annotations

from datetime import timezone

from apps.datasets import repository as datasets
from apps.datasets import storage as uploads
from apps.ingestion import service as ingestion
from common.canonical import SCHEMA_VERSION
from common.db import find_one, oid
from common.logging_config import get_logger

logger = get_logger("earthscape.import")


def run_file_import(*, upload_ref: str, dataset_id: str, job_id_str: str, column_map: dict,
                    units_hint: dict, granularity: str, timezone_name: str | None,
                    duplicate_policy: str = "skip", max_rows: int | None = None) -> dict:
    """Stream a stored upload into `climate_observations` and finalise both records."""
    job = find_one("ingestion_jobs", {"_id": oid(job_id_str)})
    if not job:
        raise RuntimeError("ingestion job record is missing")

    stored = uploads.resolve(upload_ref)
    source_tz = None
    if timezone_name:
        try:
            from zoneinfo import ZoneInfo

            source_tz = ZoneInfo(timezone_name)
        except Exception:
            source_tz = timezone.utc

    dataset = datasets.get(dataset_id)
    result = ingestion.ingest_row_stream(
        dataset_id=str(dataset["_id"]),
        row_stream=uploads.iter_rows(stored, max_rows=max_rows),
        column_map=column_map,
        units_hint=units_hint,
        granularity=granularity,
        provider="university_upload",
        kind="file",
        job_id=str(job["_id"]),
        source_file=stored.original_name,
        default_location=dataset.get("default_location") or None,
        source_timezone=source_tz,
        dry_run=False,
    )

    counts = result["counts"]
    fetched = int(counts.get("records_fetched", 0))
    accepted = int(counts.get("records_accepted", 0))
    rejected = int(counts.get("records_rejected", 0))
    duplicates = int(counts.get("records_duplicate", 0))
    score = round(accepted / fetched, 3) if fetched else 0.0
    status = "ready" if accepted and not rejected else ("partial" if accepted else "failed")

    ingestion.finish_job(
        job["_id"],
        status=status,
        counts=counts,
        errors=result["errors"],
        coverage=result["coverage"],
    )
    datasets.record_ingestion_result(
        dataset_id,
        records_added=accepted,
        coverage=result["coverage"],
        status=status,
        storage_uri=stored.path.name,
        rejected=rejected,
        duplicates=duplicates,
        quality_score=score,
    )
    datasets.patch(dataset_id, {"column_map": column_map, "units_hint": units_hint,
                                "granularity": granularity, "schema_version": SCHEMA_VERSION})

    # Mirror the raw file into the data lake so distributed jobs can see the same input.
    from common.datalake import publish_raw_file

    lake = publish_raw_file(stored.path, dataset_name=dataset["name"], original_name=stored.original_name)

    return {
        "dataset_id": dataset_id,
        "job_id": str(job["_id"]),
        **counts,
        "quality_score": score,
        "status": status,
        "coverage": result["coverage"],
        "data_lake": lake,
    }
