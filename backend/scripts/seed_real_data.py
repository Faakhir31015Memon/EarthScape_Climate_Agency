"""Seed the platform with REAL NASA POWER daily observations.

Run from the ``backend`` directory::

    python scripts/seed_real_data.py                       # default: last 4 complete years
    python scripts/seed_real_data.py --locations KHI,LHE --start 2024-01-01 --end 2024-01-31

It bootstraps Django, ensures the reference regions exist, creates (or reuses) the
dataset "NASA POWER daily observations (Pakistan + global reference network)", then
fetches real data for every location in :data:`common.regions.LOCATIONS` over a
configurable, monthly-chunked date range and ingests it through the SAME code path the
API uses (``NasaPowerConnector`` + ``apps.datasets.importer`` idempotent upsert).

Nothing is fabricated: if the network is unreachable the script aborts loudly with a
non-zero exit code and writes no invented rows. It is idempotent - re-running upserts
the same ``dedupe_key`` documents rather than duplicating them.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

DATASET_NAME = "NASA POWER daily observations (Pakistan + global reference network)"
DEFAULT_PARAMETERS = ["T2M", "T2M_MIN", "T2M_MAX", "PRECTOTCORR", "RH2M", "WS10M", "WD10M", "PS"]


def configure_django() -> None:
    import django

    django.setup()


# --------------------------------------------------------------------- dataset
def ensure_regions() -> None:
    from common import regions as region_reference
    from common.repositories import regions as regions_repository, utcnow

    for document in region_reference.region_documents():
        payload = dict(document)
        payload["updated_at"] = utcnow()
        if regions_repository.find_one({"code": document["code"]}) is None:
            payload["created_at"] = utcnow()
            regions_repository.insert_one(payload)


def ensure_dataset(user_id: Optional[str]) -> Dict[str, Any]:
    from common import validators
    from common.repositories import datasets as datasets_repository, maybe_object_id, utcnow
    from common import regions as region_reference

    existing = datasets_repository.find_one({"name": DATASET_NAME, "source_type": "nasa_power"})
    if existing is not None:
        return existing

    now = utcnow()
    resolved = region_reference.LOCATIONS
    document = {
        "name": DATASET_NAME,
        "description": ("Real daily observations pulled from NASA POWER for the Pakistan network "
                        "and a global reference set, ingested idempotently per dataset+location+day."),
        "source_type": "nasa_power",
        "source_name": "NASA POWER",
        "source_url": os.environ.get("NASA_POWER_BASE_URL",
                                     "https://power.larc.nasa.gov/api/temporal/daily/point"),
        "file_name": None,
        "storage_uri": None,
        "format": "api",
        "schema_version": validators.SCHEMA_VERSION,
        "geographic_coverage": {
            "regions": sorted({item["region_code"] for item in resolved}),
            "countries": sorted({item["country"] for item in resolved}),
            "bbox": _bbox(resolved),
            "location_codes": sorted(item["code"] for item in resolved),
        },
        "temporal_coverage": {"start": None, "end": None, "years": [], "frequency": "daily"},
        "record_count": 0,
        "status": "ingesting",
        "uploaded_by": user_id,
        "created_at": now,
        "updated_at": now,
        "source_metadata": {"request_parameters": {"community": "AG", "parameters": DEFAULT_PARAMETERS}},
        "validation_report": {},
        "ingestion_job_ids": [],
        "tags": ["nasa_power", "real-data", "seed"],
        "units": {meta["field"]: meta["unit"] for meta in region_reference.NASA_PARAMETERS.values()},
        "is_public": True,
    }
    inserted = datasets_repository.insert_one(document)
    document["_id"] = maybe_object_id(inserted)
    return document


def _bbox(locations: List[Dict[str, Any]]) -> List[float]:
    lats = [item["latitude"] for item in locations]
    lons = [item["longitude"] for item in locations]
    return [min(lons), min(lats), max(lons), max(lats)]


# --------------------------------------------------------------------- seeding
def resolve_locations(codes: Optional[str]) -> List[Dict[str, Any]]:
    from common import regions as region_reference

    if not codes:
        return list(region_reference.LOCATIONS)
    wanted = [code.strip().upper() for code in codes.split(",") if code.strip()]
    resolved: List[Dict[str, Any]] = []
    unknown: List[str] = []
    for code in wanted:
        reference = region_reference.location_by_code(code)
        if reference:
            resolved.append(reference)
        else:
            unknown.append(code)
    if unknown:
        raise SystemExit(f"ABORT: unknown location code(s): {', '.join(unknown)}. "
                         f"Valid codes: {', '.join(item['code'] for item in region_reference.LOCATIONS)}")
    return resolved


def date_chunks(start: datetime, end: datetime, months: int) -> List[Tuple[str, str]]:
    """Split [start, end] into monthly windows, returning YYYY-MM-DD pairs."""
    chunks: List[Tuple[str, str]] = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=months * 31 - 1), end)
        chunks.append((cursor.strftime("%Y-%m-%d"), window_end.strftime("%Y-%m-%d")))
        cursor = window_end + timedelta(days=1)
    return chunks


def default_range(years: int, end_offset_days: int) -> Tuple[datetime, datetime]:
    today = datetime.now(timezone.utc).date()
    end = today - timedelta(days=end_offset_days)
    start_year = end.year - years
    start = datetime(start_year, end.month, end.day, tzinfo=timezone.utc)
    return start, datetime(end.year, end.month, end.day, tzinfo=timezone.utc)


def seed(args: argparse.Namespace) -> Dict[str, Any]:
    from apps.datasets import importer
    from apps.ingestion.connectors import get_connector
    from apps.ingestion.connectors import base as connector_base
    from common import regions as region_reference
    from common.repositories import (
        datasets as datasets_repository, ingestion_jobs, maybe_object_id, utcnow)

    connector = get_connector("nasa_power")
    if not connector.is_configured():
        raise SystemExit("ABORT: the NASA POWER connector reports itself unconfigured.")

    locations = resolve_locations(args.locations)
    start, end = _resolve_dates(args)
    chunks = date_chunks(start, end, args.chunk_months)
    parameters = [item.strip().upper() for item in args.parameters.split(",")] if args.parameters \
        else DEFAULT_PARAMETERS

    print(f"NASA POWER seed: {len(locations)} location(s), "
          f"{start:%Y-%m-%d} .. {end:%Y-%m-%d}, {len(chunks)} monthly chunk(s) each.")
    print(f"Parameters: {', '.join(parameters)}")

    dataset = ensure_dataset(args.user_id)
    print(f"Dataset id: {dataset['_id']}  name: {dataset['name']}")

    # Create a real ingestion job that this run updates with genuine counts.
    job_doc = _new_seed_job(str(dataset["_id"]), locations, start, end, parameters, args.user_id)
    job_id = ingestion_jobs.insert_one(job_doc)
    ingestion_jobs.update_by_id(job_id, {"status": "running", "executor": "script"})
    datasets_repository.update_by_id(str(dataset["_id"]),
                                     {"$push": {"ingestion_job_ids": maybe_object_id(job_id)},
                                      "$set": {"status": "ingesting", "updated_at": utcnow()}})

    totals = ImporterTotals()
    first_error: Optional[str] = None

    try:
        for location_index, location in enumerate(locations, start=1):
            for chunk_index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
                request = connector_base.IngestionRequest(
                    dataset_id=str(dataset["_id"]), run_id=f"seed-{location['code']}",
                    job_id=str(job_id), source_name="NASA POWER", source_type="nasa_power",
                    dataset_name=DATASET_NAME, parameters={"parameters": parameters,
                                                           "community": "AG"},
                    locations=[location], start=chunk_start, end=chunk_end,
                    processing_mode="live-api")
                if args.dry_run:
                    observations = _count_only(connector, request)
                    print(f"  [dry-run] {location['code']} {chunk_start}..{chunk_end}: "
                          f"{observations} observation(s) would be produced")
                    totals.accepted += observations
                    continue
                try:
                    observations = list(connector.run(request))
                except connector_base.Unreachable as exc:
                    raise RuntimeError(f"Network failure reaching NASA POWER for "
                                       f"{location['code']} {chunk_start}..{chunk_end}: {exc.message}")
                result = importer.upsert_observations(observations)
                totals.add(result)
                _touch_job(job_id, totals)
                if chunk_index == 1 or location_index == len(locations):
                    print(f"  {location['code']:<4} {chunk_start}..{chunk_end}: "
                          f"accepted={result.accepted} new={result.new} "
                          f"duplicates={result.duplicate_count} rejected={result.rejected}")
    except RuntimeError as exc:
        first_error = str(exc)

    if args.dry_run:
        ingestion_jobs.update_by_id(job_id, {"status": "cancelled",
                                             "error_message": "dry-run", "completed_at": utcnow()})
        print("\nDry run: nothing was persisted to climate_observations.")
        _flush()
        return {"accepted": totals.accepted}

    final_status = "completed"
    if first_error:
        final_status = "failed" if totals.accepted == 0 else "completed_with_errors"
    ingestion_jobs.update_by_id(job_id, {
        "status": final_status,
        "records_fetched": totals.fetched,
        "records_accepted": totals.accepted,
        "records_rejected": totals.rejected,
        "records_skipped": totals.skipped,
        "duplicate_count": totals.duplicate_count,
        "validation_errors": totals.issues[:200],
        "error_message": first_error,
        "completed_at": utcnow(),
    })
    _update_dataset_coverage(dataset_id=str(dataset["_id"]))

    print("\n==== REAL NASA POWER ingest totals ====")
    print(f"  status              : {final_status}")
    print(f"  records fetched     : {totals.fetched}")
    print(f"  records accepted    : {totals.accepted}")
    print(f"  new records         : {totals.new}")
    print(f"  duplicates (idempotent re-runs) : {totals.duplicate_count}")
    print(f"  records rejected    : {totals.rejected}")
    print(f"  records skipped     : {totals.skipped}")
    if first_error:
        print(f"  ERROR               : {first_error}")
    _flush()
    return {"accepted": totals.accepted, "new": totals.new, "error": first_error}


class ImporterTotals:
    def __init__(self) -> None:
        self.fetched = self.accepted = self.rejected = self.skipped = self.new = self.duplicate_count = 0
        self.issues: List[Dict[str, Any]] = []

    def add(self, result) -> None:
        self.fetched += result.fetched
        self.accepted += result.accepted
        self.rejected += result.rejected
        self.skipped += result.skipped
        self.new += result.new
        self.duplicate_count += result.duplicate_count

    def merge_issues(self, issues: List[Dict[str, Any]]) -> None:
        self.issues.extend(issues)


def _count_only(connector, request) -> int:
    return sum(1 for _ in connector.run(request))


def _touch_job(job_id: str, totals: ImporterTotals) -> None:
    from common.repositories import ingestion_jobs

    ingestion_jobs.update_by_id(job_id, {
        "records_fetched": totals.fetched, "records_accepted": totals.accepted,
        "records_rejected": totals.rejected, "records_skipped": totals.skipped,
        "duplicate_count": totals.duplicate_count})


def _new_seed_job(dataset_id: str, locations: List[Dict[str, Any]], start: datetime,
                  end: datetime, parameters: List[str], requested_by: Optional[str]) -> Dict[str, Any]:
    from common import regions as region_reference
    from common.repositories import utcnow, maybe_object_id
    from apps.ingestion.connectors import base as connector_base

    resolved_codes = [item["code"] for item in locations]
    regions_list = sorted({item["region_code"] for item in locations})
    return {
        "source_name": "NASA POWER", "source_type": "nasa_power",
        "source_name": "NASA POWER", "source_type": "nasa_power",
        "dataset_id": maybe_object_id(dataset_id), "job_type": "api_sync", "status": "queued",
        "created_at": utcnow(), "updated_at": utcnow(),
        "started_at": utcnow(), "completed_at": None, "records_fetched": 0, "records_accepted": 0,
        "records_rejected": 0, "records_skipped": 0, "duplicate_count": 0, "validation_errors": [],
        "retry_count": 0, "max_retries": 0,
        "data_period": {"start": start.strftime("%Y-%m-%d"), "end": end.strftime("%Y-%m-%d")},
        "geographic_coverage": {"regions": regions_list, "location_codes": resolved_codes,
                                "countries": sorted({item["country"] for item in locations})},
        "requested_by": requested_by, "triggered_by": "manual", "error_message": None,
        "processing_mode": "live-api",
        "parameters": connector_base.sanitise_parameters(
            {"parameters": parameters, "community": "AG", "source": "seed_script"}),
        "log_lines": [{"at": utcnow(), "level": "info", "message": "Created by scripts/seed_real_data.py"}],
        "executor": "script",
    }


def _update_dataset_coverage(*, dataset_id: str) -> None:
    from common.repositories import (
        climate_observations, datasets as datasets_repository, maybe_object_id, utcnow)

    # Observations are persisted with ``dataset_id`` as its string form (see
    # ``apps.datasets.importer`` / ``connectors.base.assemble_observation``), but match
    # the ObjectId too so this stays correct if a run ever stores the BSON form.
    scope = {"dataset_id": {"$in": [str(dataset_id), maybe_object_id(dataset_id)]}}
    dates = climate_observations.distinct("observation_date", scope)
    if not dates:
        return
    ordered = sorted(str(item) for item in dates)
    years = sorted({int(item[:4]) for item in ordered})
    region_codes = climate_observations.distinct("region_code", scope)
    location_codes = climate_observations.distinct("location_code", scope)
    countries = climate_observations.distinct("country", scope)
    count = climate_observations.count(scope)
    datasets_repository.update_by_id(dataset_id, {
        "record_count": count,
        "status": "ready",
        "temporal_coverage": {"start": ordered[0], "end": ordered[-1], "years": years,
                              "frequency": "daily"},
        "geographic_coverage.regions": sorted(filter(None, region_codes)),
        "geographic_coverage.location_codes": sorted(filter(None, location_codes)),
        "geographic_coverage.countries": sorted(filter(None, countries)),
        "updated_at": utcnow(),
    })


def _flush() -> None:
    try:
        from common.database import get_database

        flushed = get_database().flush_if_dirty()
        if flushed:
            print(f"\nPersisted local-development snapshots: {', '.join(flushed)} "
                  f"(under backend/.data/localdb). These are the MongoDB documents in local mode.")
    except Exception as exc:  # noqa: BLE001
        print(f"Warning: snapshot flush failed: {exc}")


def _resolve_dates(args) -> Tuple[datetime, datetime]:
    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    elif args.start or args.end:
        raise SystemExit("ABORT: provide both --start and --end, or neither (defaults are used).")
    else:
        start, end = default_range(args.years, args.end_offset_days)
    if start > end:
        raise SystemExit("ABORT: --start is after --end.")
    return start, end


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed real NASA POWER observations.")
    parser.add_argument("--start", help="YYYY-MM-DD (default: 4 complete years before the end)")
    parser.add_argument("--end", help="YYYY-MM-DD (default: today minus --end-offset-days)")
    parser.add_argument("--years", type=int, default=4, help="Years of history when no dates given")
    parser.add_argument("--end-offset-days", type=int, default=10,
                        help="How many days back from today the end date sits (NASA lag)")
    parser.add_argument("--locations", help="Comma-separated codes (default: all reference locations)")
    parser.add_argument("--parameters", help="Comma-separated NASA parameters (default verified set)")
    parser.add_argument("--chunk-months", type=int, default=1, help="Days per request window (months)")
    parser.add_argument("--user-id", help="Optional dataset owner user id")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + count only; persist nothing")
    args = parser.parse_args(argv)

    configure_django()
    ensure_regions()

    summary = seed(args)
    if summary.get("error"):
        print(f"\nSEED ABORTED WITH NETWORK ERROR: {summary['error']}")
        return 1
    if summary.get("accepted", 0) == 0:
        print("\nSEED PRODUCED ZERO OBSERVATIONS - check the date range / parameters.")
        return 2
    print("\nSeed complete.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
