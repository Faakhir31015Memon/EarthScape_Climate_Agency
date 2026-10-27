"""Turn a file (or normalised row stream) into ``climate_observations`` documents.

This module is the single conversion path shared by the synchronous ``/validate``
endpoint, the upload-wizard ``/preview`` endpoint and the background file-import task,
so the counts a user sees before importing are exactly the counts produced by the
import.

Conversion rules (see ``backend/common/validators.py`` - never re-implemented here):

* a value the source did not supply stays ``None`` (never coerced to 0);
* source ``MISSING_SENTINELS`` (e.g. ``-999``) are treated as "not supplied";
* every measurement is unit-converted to the canonical unit and range-checked;
* a row is only *rejected* when it cannot form a valid observation - i.e. it has no
  parsable observation timestamp or no valid latitude/longitude.  Every other problem
  becomes a recorded issue and the row is still stored (flagged);
* ``dedupe_key = dataset_id|location_code|observation_date`` deduplicates within the
  batch and is written with ``ReplaceOne(..., upsert=True)`` so re-runs are idempotent.

Counting model (documented so the numbers reconcile):

``fetched``            = data records read from the file (blank lines excluded).
``rejected``           = records that could not be converted into an observation.
``accepted``           = unique, valid observation documents produced and written.
``skipped``            = in-batch duplicate records dropped (only the first copy is kept).
``duplicate_count``    = ``skipped`` (in-batch) + ``updated`` (records that already
                         existed in the database before this run and were re-upserted).
``new`` / ``updated``  = split of ``accepted`` into freshly inserted vs overwritten.

Reconciliation: ``fetched == rejected + accepted + skipped``.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import ReplaceOne

from apps.datasets import file_import
from common import regions, validators
from common.repositories import (
    climate_observations,
    datasets as datasets_repository,
    maybe_object_id,
    utcnow,
)

logger = logging.getLogger(__name__)

MEASUREMENT_FIELDS: List[str] = list(validators.RANGE_LIMITS)
GEO_FIELDS = ("latitude", "longitude")
CONTEXT_FIELDS = ("observation_date", "location_code", "location_name", "region_code", "country")

MAX_AGGREGATED_ERRORS = 200
MAX_SAMPLE_ROWS = 50
DUPLICATE_PROBE_CHUNK = 500


class ImportError_(Exception):  # noqa: N801 - intentional, distinct from builtin ImportError
    """Raised for unrecoverable importer problems."""


@dataclass
class ImportResult:
    fetched: int = 0
    accepted: int = 0
    rejected: int = 0
    skipped: int = 0
    new: int = 0
    updated: int = 0
    format_detected: str = ""
    headers: List[str] = field(default_factory=list)
    mapping: Dict[str, str] = field(default_factory=dict)
    issues: List[Dict[str, Any]] = field(default_factory=list)      # aggregated
    accepted_sample: List[Dict[str, Any]] = field(default_factory=list)
    rejected_sample: List[Dict[str, Any]] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)      # per-column stats
    coverage: Dict[str, Any] = field(default_factory=dict)
    aborted_reason: Optional[str] = None

    @property
    def duplicate_count(self) -> int:
        return self.skipped + self.updated

    def summary_counts(self) -> Dict[str, int]:
        return {
            "records_fetched": self.fetched,
            "records_accepted": self.accepted,
            "records_rejected": self.rejected,
            "records_skipped": self.skipped,
            "duplicate_count": self.duplicate_count,
        }


# --------------------------------------------------------------------- row -> obs
def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _is_sentinel(value: Any) -> bool:
    number = _as_float(value)
    if number is None:
        return False
    return any(abs(number - sentinel) < 1e-9 for sentinel in regions.MISSING_SENTINELS)


def _get(row: Dict[str, Any], header: Optional[str]) -> Any:
    if header is None:
        return None
    if header in row:
        return row[header]
    # Tolerate a mapping that used the normalized form of the header.
    normalized = {validators.normalize_header(k): v for k, v in row.items()}
    return normalized.get(validators.normalize_header(header))


def resolve_location(row: Dict[str, Any], mapping: Dict[str, str], dataset: Dict[str, Any]
                     ) -> Dict[str, Any]:
    """Best-effort location identity, preferring the file, then the reference network."""
    defaults = dataset.get("source_metadata", {}).get("location_defaults", {}) \
        if isinstance(dataset.get("source_metadata"), dict) else {}
    code = (str(_get(row, mapping.get("location_code")) or "").strip().upper()) or \
        str(defaults.get("location_code") or "").strip().upper()
    reference = regions.location_by_code(code) if code else None

    lat = _get(row, mapping.get("latitude"))
    lon = _get(row, mapping.get("longitude"))
    if lat is None and reference is not None:
        lat = reference["latitude"]
    if lon is None and reference is not None:
        lon = reference["longitude"]
    if lat is None:
        lat = defaults.get("latitude")
    if lon is None:
        lon = defaults.get("longitude")

    name = (str(_get(row, mapping.get("location_name")) or "").strip()) or \
        (reference["name"] if reference else str(defaults.get("location_name") or code or ""))
    region = (str(_get(row, mapping.get("region_code")) or "").strip()) or \
        (reference["region_code"] if reference else str(defaults.get("region_code") or ""))
    country = (str(_get(row, mapping.get("country")) or "").strip()) or \
        (reference["country"] if reference else str(defaults.get("country") or ""))

    return {"code": code, "name": name, "region": region, "country": country,
            "latitude": lat, "longitude": lon,
            "source_type": defaults.get("source_type"), "reference": reference}


def row_to_observation(row: Dict[str, Any], *, dataset: Dict[str, Any], mapping: Dict[str, str],
                       row_index: int, run_id: str, job_id: str,
                       processing_mode: str, connector: str,
                       source_row_units: Optional[Dict[str, str]] = None
                       ) -> Tuple[Optional[Dict[str, Any]], List[validators.Issue], bool]:
    """Convert one raw row into an observation document.

    Returns ``(observation_or_None, issues, fatal)``.  ``fatal`` means the row could
    not form a valid observation at all (bad/absent timestamp or coordinates).
    """
    issues: List[validators.Issue] = []
    dataset_id = str(dataset["_id"])

    # -------------------------------------------------------------- timestamp
    raw_date = _get(row, mapping.get("observation_date"))
    if raw_date is None and mapping.get("observation_date") is None:
        return None, [validators.Issue("observation_date_unmapped", "error", "observation_date",
                                        "No column is mapped to the observation date.", row_index)], True
    moment, date_issues = validators.parse_timestamp(raw_date)
    for issue in date_issues:
        issue.row = row_index
    issues.extend(date_issues)
    if moment is None:
        return None, issues, True

    # -------------------------------------------------------------- location
    location = resolve_location(row, mapping, dataset)
    latitude, lat_issues = validators.parse_coordinate(location["latitude"], kind="latitude")
    longitude, lon_issues = validators.parse_coordinate(location["longitude"], kind="longitude")
    for issue in lat_issues + lon_issues:
        issue.row = row_index
    issues.extend(lat_issues + lon_issues)
    if latitude is None or longitude is None:
        return None, issues, True

    # -------------------------------------------------------------- units
    dataset_units = dataset.get("units", {}) if isinstance(dataset.get("units"), dict) else {}
    row_units = source_row_units or {}

    def unit_for(field_name: str, header: Optional[str]) -> Optional[str]:
        return row_units.get(field_name) or dataset_units.get(field_name) or \
            (validators.guess_unit(header) if header else None)

    location_identity = location["code"] or location["name"] or "unknown"
    observation: Dict[str, Any] = {
        "dedupe_key": f"{dataset_id}|{location_identity}|{moment:%Y-%m-%d}",
        "dataset_id": dataset_id,
        "observation_date": moment.strftime("%Y-%m-%d"),
        "observed_at": moment,
        "year": moment.year,
        "month": moment.month,
        "day": moment.day,
        "location_code": location["code"],
        "location_name": location["name"],
        "region_code": location["region"],
        "country": location["country"],
        "latitude": float(latitude),
        "longitude": float(longitude),
        "coordinates": {"type": "Point", "coordinates": [float(longitude), float(latitude)]},
    }

    source_units: Dict[str, str] = {}
    for field_name in MEASUREMENT_FIELDS:
        header = mapping.get(field_name)
        raw = _get(row, header)
        if _is_sentinel(raw):
            observation[field_name] = None
            issues.append(validators.Issue(f"{field_name}_sentinel", "info", field_name,
                        f"{field_name} carried a provider missing sentinel (-999 style).", row_index))
            continue
        unit = unit_for(field_name, header)
        value, measurement_issues = validators.validate_measurement(field_name, raw, unit, row_index)
        issues.extend(measurement_issues)
        observation[field_name] = value
        if unit and raw is not None and not validators.is_missing(raw):
            source_units[field_name] = unit

    observation["source_units"] = source_units
    observation["quality_flags"] = validators.quality_flags_for_row(issues)
    observation["source_name"] = dataset.get("source_name") or dataset.get("name") or "Uploaded file"
    observation["source_type"] = dataset.get("source_type") or "university_upload"
    observation["dataset_name"] = dataset.get("name", "")
    observation["provider_record_id"] = str(_get(row, mapping.get("provider_record_id"))) \
        if mapping.get("provider_record_id") and _get(row, mapping.get("provider_record_id")) is not None else None

    sanitised_parameters = _sanitised_request_parameters(dataset)
    observation["provenance"] = {
        "connector": connector,
        "endpoint": dataset.get("source_url") or dataset.get("file_name") or "uploaded-file",
        "run_id": run_id,
        "retrieved_at": utcnow(),
        "ingestion_job_id": job_id,
        "processing_mode": processing_mode,
        "request_parameters": sanitised_parameters,
    }
    observation["ingested_at"] = utcnow()
    observation["schema_version"] = validators.SCHEMA_VERSION
    return observation, issues, False


# --------------------------------------------------------------------- analysis
def analyse_rows(records: Iterable[Tuple[int, Dict[str, Any]]], *, dataset: Dict[str, Any],
                 mapping: Dict[str, str], run_id: str, job_id: str,
                 processing_mode: str = "uploaded-file", connector: str = "file_import",
                 collect_samples: bool = True, max_rows: Optional[int] = None) -> ImportResult:
    """Convert a row iterator into an :class:`ImportResult` (no database writes).

    In-batch dedupe is applied; ``accepted`` counts unique valid observations.
    """
    result = ImportResult()
    seen_keys: set[str] = set()
    prepared: List[Dict[str, Any]] = []
    aggregated: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}
    column_stats: Dict[str, Dict[str, Any]] = {}

    def note(issue: validators.Issue) -> None:
        key = (issue.code, issue.field)
        entry = aggregated.get(key)
        if entry is None:
            if len(aggregated) >= MAX_AGGREGATED_ERRORS:
                return
            aggregated[key] = {"code": issue.code, "field": issue.field,
                               "message": issue.message[:240], "row": issue.row, "count": 1}
        else:
            entry["count"] += 1

    for row_index, row in records:
        if max_rows is not None and result.fetched >= max_rows:
            result.aborted_reason = (f"Row limit of {max_rows} reached; remaining rows were not read.")
            break
        if all((value is None or str(value).strip() == "") for value in row.values()):
            continue
        result.fetched += 1

        observation, issues, fatal = row_to_observation(
            row, dataset=dataset, mapping=mapping, row_index=row_index, run_id=run_id,
            job_id=job_id, processing_mode=processing_mode, connector=connector)
        for issue in issues:
            note(issue)

        if fatal or observation is None:
            result.rejected += 1
            if collect_samples and len(result.rejected_sample) < MAX_SAMPLE_ROWS:
                result.rejected_sample.append({"row": row_index, "reason": [
                    i.to_dict() for i in issues if i.severity == "error"] or ["unconvertible"],
                    "raw": {k: _short(v) for k, v in row.items() if k != "__trailing__"}})
            continue

        key = observation["dedupe_key"]
        if key in seen_keys:
            result.skipped += 1
            continue
        seen_keys.add(key)
        prepared.append(observation)
        result.accepted += 1

        if collect_samples and len(result.accepted_sample) < MAX_SAMPLE_ROWS:
            result.accepted_sample.append({"row": row_index,
                                           "observation": _presentation(observation)})

    result.mapping = dict(mapping)
    result.issues = sorted(aggregated.values(), key=lambda item: -item["count"])
    result.columns = _column_profile(records=None, stats=column_stats)
    result.coverage = coverage_from_observations(prepared)
    result._prepared = prepared  # type: ignore[attr-defined]
    return result


# --------------------------------------------------------------------- writes
def import_file(path: str, dataset: Dict[str, Any], mapping: Dict[str, str], *,
                job_id: str, run_id: Optional[str] = None,
                processing_mode: str = "uploaded-file", connector: str = "file_import",
                max_rows: Optional[int] = None, batch_size: int = 500,
                progress: Optional[Any] = None, should_cancel: Optional[Any] = None) -> ImportResult:
    """Read, convert and idempotently upsert every observation in a file."""
    run_id = run_id or _run_id()
    detected = file_import.sniff_format(path)
    headers = file_import.read_headers(path, detected)
    max_rows = max_rows or _max_upload_rows()

    def stream() -> Iterable[Tuple[int, Dict[str, Any]]]:
        return file_import.open_file(path, detected, max_rows=max_rows + 1000)

    result = analyse_rows(
        stream(), dataset=dataset, mapping=mapping, run_id=run_id, job_id=job_id,
        processing_mode=processing_mode, connector=connector, collect_samples=False,
        max_rows=max_rows)
    result.format_detected = detected
    result.headers = headers
    result.columns = column_analysis(headers, mapping)
    prepared: List[Dict[str, Any]] = getattr(result, "_prepared", [])

    pre_existing = _keys_present([doc["dedupe_key"] for doc in prepared])
    written = 0
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start:start + batch_size]
        if should_cancel is not None and should_cancel():
            result.aborted_reason = "Cancelled by request; only completed batches were written."
            break
        operations = [ReplaceOne({"dedupe_key": doc["dedupe_key"]}, doc, upsert=True)
                      for doc in batch]
        written += _bulk_write(operations)
        if progress is not None:
            progress(processed=min(start + batch_size, len(prepared)), accepted=result.accepted)

    result.updated = len(pre_existing)
    result.new = result.accepted - result.updated
    return result


def upsert_observations(observations: Iterable[Dict[str, Any]], *, batch_size: int = 500,
                        should_cancel: Optional[Any] = None,
                        progress: Optional[Any] = None) -> ImportResult:
    """Idempotently write pre-built observation documents (used by API connectors)."""
    result = ImportResult()
    seen_keys: set[str] = set()
    prepared: List[Dict[str, Any]] = []
    for doc in observations:
        result.fetched += 1
        if doc is None:
            result.rejected += 1
            continue
        key = doc.get("dedupe_key")
        if not key:
            result.rejected += 1
            continue
        if key in seen_keys:
            result.skipped += 1
            continue
        seen_keys.add(key)
        prepared.append(doc)
        result.accepted += 1

    pre_existing = _keys_present([doc["dedupe_key"] for doc in prepared])
    processed = 0
    for start in range(0, len(prepared), batch_size):
        batch = prepared[start:start + batch_size]
        if should_cancel is not None and should_cancel():
            result.aborted_reason = "Cancelled by request; partial data was written."
            break
        operations = [ReplaceOne({"dedupe_key": doc["dedupe_key"]}, doc, upsert=True)
                      for doc in batch]
        _bulk_write(operations)
        processed += len(batch)
        if progress is not None:
            progress(processed=processed, accepted=result.accepted)

    result.updated = len(pre_existing)
    result.new = result.accepted - result.updated
    result.coverage = coverage_from_observations(prepared)
    return result


def update_dataset_after_import(dataset_id: str, result: ImportResult, *, final_status: str,
                                extra: Optional[Dict[str, Any]] = None) -> None:
    """Persist record_count / status / coverage / validation_report on the dataset."""
    report = build_validation_report(result)
    # A partial re-sync (one week of a multi-year dataset) accepts fewer rows than the
    # dataset holds, so the number shown on the dataset page is counted from storage.
    stored = climate_observations.count({"dataset_id": dataset_id})
    updates: Dict[str, Any] = {
        "status": final_status,
        "record_count": stored,
        "validation_report": report,
        "updated_at": utcnow(),
    }
    if result.coverage:
        # ``geographic_coverage`` is seeded from upload metadata before a single row has
        # been read, so a four-province file would otherwise keep advertising the one
        # fallback station named in the form. Coverage then grows with each append-only
        # run instead of being replaced by whichever window happened to run last.
        previous = datasets_repository.find_one({"_id": maybe_object_id(dataset_id)},
                                                {"temporal_coverage": 1, "geographic_coverage": 1})
        temporal = _merge_coverage(
            (previous or {}).get("temporal_coverage"),
            {"start": result.coverage.get("start"), "end": result.coverage.get("end"),
             "years": result.coverage.get("years", []),
             "frequency": result.coverage.get("frequency", "daily")}, ("years",))
        geographic = _merge_coverage(
            (previous or {}).get("geographic_coverage"),
            {"regions": result.coverage.get("regions", []),
             "location_codes": result.coverage.get("location_codes", []),
             "countries": result.coverage.get("countries", []),
             "bbox": result.coverage.get("bbox")},
            ("regions", "location_codes", "countries"))
        updates["temporal_coverage"] = temporal
        updates["geographic_coverage"] = geographic
    if extra:
        updates.update(extra)
    datasets_repository.update_by_id(dataset_id, updates)


def _merge_coverage(previous: Optional[Dict[str, Any]], current: Dict[str, Any],
                    set_keys: Tuple[str, ...]) -> Dict[str, Any]:
    """Union of what the dataset already advertised and what this run proved."""
    if not previous:
        return current
    merged = dict(current)
    for key in set_keys:
        merged[key] = sorted({*(previous.get(key) or []), *(current.get(key) or [])})
    starts = [value for value in (previous.get("start"), current.get("start")) if value]
    ends = [value for value in (previous.get("end"), current.get("end")) if value]
    if starts:
        merged["start"] = min(starts)
    if ends:
        merged["end"] = max(ends)
    if "frequency" in current and not merged.get("frequency"):
        merged["frequency"] = previous.get("frequency")
    boxes = [box for box in (previous.get("bbox"), current.get("bbox"))
             if isinstance(box, (list, tuple)) and len(box) == 4]
    if boxes:
        merged["bbox"] = [min(box[0] for box in boxes), min(box[1] for box in boxes),
                          max(box[2] for box in boxes), max(box[3] for box in boxes)]
    return merged


def build_validation_report(result: ImportResult) -> Dict[str, Any]:
    return {
        "generated_at": utcnow(),
        "format_detected": result.format_detected,
        "headers": result.headers[:100],
        "column_mapping": result.mapping,
        "counts": {
            "fetched": result.fetched,
            "accepted": result.accepted,
            "rejected": result.rejected,
            "skipped_in_batch_duplicates": result.skipped,
            "duplicates_against_database": result.updated,
            "new_records": result.new,
        },
        "reconciliation": "fetched == rejected + accepted + skipped",
        "column_profile": result.columns,
        "validation_errors": result.issues[:MAX_AGGREGATED_ERRORS],
        "error_count": len(result.issues),
        "coverage": result.coverage,
        "aborted_reason": result.aborted_reason,
    }


# --------------------------------------------------------------------- helpers
def _bulk_write(operations: List[ReplaceOne]) -> int:
    return climate_observations.bulk_write(operations)


def _keys_present(keys: List[str]) -> set[str]:
    """Which of these dedupe keys already exist in the database (chunked $in)."""
    present: set[str] = set()
    if not keys:
        return present
    for start in range(0, len(keys), DUPLICATE_PROBE_CHUNK):
        window = keys[start:start + DUPLICATE_PROBE_CHUNK]
        for document in climate_observations.find({"dedupe_key": {"$in": window}},
                                                  {"dedupe_key": 1}, limit=len(window)):
            found = document.get("dedupe_key")
            if found:
                present.add(str(found))
    return present


def _column_profile(records, *, stats: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    return []  # Filled by column_analysis(); kept for API stability.


def coverage_from_observations(observations: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Date span + geographic spread of the rows that were actually accepted."""
    dates: List[datetime] = []
    regions: set[str] = set()
    locations: set[str] = set()
    countries: set[str] = set()
    min_lat = min_lon = max_lat = max_lon = None
    for observation in observations:
        moment = observation.get("observed_at")
        if moment is None:
            continue
        dates.append(moment)
        if observation.get("region_code"):
            regions.add(observation["region_code"])
        if observation.get("location_code"):
            locations.add(observation["location_code"])
        if observation.get("country"):
            countries.add(observation["country"])
        latitude = observation.get("latitude")
        if latitude is not None:
            min_lat = _lower(min_lat, latitude)
            max_lat = _higher(max_lat, latitude)
        longitude = observation.get("longitude")
        if longitude is not None:
            min_lon = _lower(min_lon, longitude)
            max_lon = _higher(max_lon, longitude)
    return _coverage_document(dates, regions, locations, countries,
                              min_lat, min_lon, max_lat, max_lon)


def _coverage_document(dates: List[datetime], regions_: set, locations: set, countries: set,
                       min_lat, min_lon, max_lat, max_lon) -> Dict[str, Any]:
    if not dates:
        return {}
    ordered = sorted(dates)
    years = sorted({moment.year for moment in ordered})
    bbox = None
    if None not in (min_lat, min_lon, max_lat, max_lon):
        bbox = [min_lon, min_lat, max_lon, max_lat]
    return {
        "start": ordered[0].strftime("%Y-%m-%d"),
        "end": ordered[-1].strftime("%Y-%m-%d"),
        "years": years,
        "frequency": "daily",
        "regions": sorted(regions_),
        "location_codes": sorted(locations),
        "countries": sorted(countries),
        "bbox": bbox,
    }


def _presentation(observation: Dict[str, Any]) -> Dict[str, Any]:
    # Provenance request_parameters are already sanitised by
    # _sanitised_request_parameters(); nothing else is stripped for display.
    return dict(observation)


def _short(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:80]


def _lower(current, candidate):
    return candidate if current is None else min(current, candidate)


def _higher(current, candidate):
    return candidate if current is None else max(current, candidate)


def _sanitised_request_parameters(dataset: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dataset.get("source_metadata", {}) if isinstance(dataset.get("source_metadata"), dict) else {}
    parameters = metadata.get("request_parameters", {}) if isinstance(metadata, dict) else {}
    return {key: _short(value) for key, value in dict(parameters).items()
            if key.lower() not in validators.MISSING_TOKENS and key.lower() not in
            ("api_key", "token", "secret", "password", "connection_string", "uri")}


def _run_id() -> str:
    import secrets

    return secrets.token_hex(8)


def _max_upload_rows() -> int:
    from django.conf import settings

    return int(getattr(settings, "MAX_UPLOAD_ROWS", 500000))


# --------------------------------------------------------------------- analysis (public)
def analyse_file(path: str, dataset: Dict[str, Any], mapping: Dict[str, str], *,
                 run_id: Optional[str] = None, job_id: str = "preview",
                 max_rows: Optional[int] = None, collect_samples: bool = True) -> ImportResult:
    """Synchronous analysis WITHOUT persisting observations (validate/preview endpoints)."""
    run_id = run_id or _run_id()
    detected = file_import.sniff_format(path)
    headers = file_import.read_headers(path, detected)
    max_rows = max_rows or _max_upload_rows()
    result = analyse_rows(
        file_import.open_file(path, detected, max_rows=max_rows + 1000), dataset=dataset,
        mapping=mapping, run_id=run_id, job_id=job_id, collect_samples=collect_samples,
        max_rows=max_rows)
    result.format_detected = detected
    result.headers = headers
    result.columns = column_analysis(headers, mapping)
    return result


def column_analysis(headers: List[str], mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    """Per-column detection summary: canonical target, guessed dtype, unit and role."""
    reverse = {header: field_name for field_name, header in mapping.items()}
    profile: List[Dict[str, Any]] = []
    for header in headers:
        field_name = reverse.get(header)
        if field_name in MEASUREMENT_FIELDS:
            dtype, role = "float", "measurement"
        elif field_name == "observation_date":
            dtype, role = "datetime", "temporal key"
        elif field_name in GEO_FIELDS:
            dtype, role = "float", "geospatial"
        elif field_name in CONTEXT_FIELDS:
            dtype, role = "string", "context"
        else:
            dtype, role = "unknown", "unmapped"
        profile.append({
            "header": header,
            "canonical_field": field_name,
            "guessed_dtype": dtype,
            "role": role,
            "guessed_unit": validators.guess_unit(header) if field_name in MEASUREMENT_FIELDS else None,
            "missing_percentage_estimate": None,  # populated by the sample pass below
        })
    return profile


def enrich_missing_percentages(result: ImportResult, rows: List[Dict[str, Any]]) -> None:
    """Fill ``missing_percentage_estimate`` from a bounded sample of raw rows."""
    if not rows:
        return
    total = len(rows)
    reverse = {header: field_name for field_name, header in result.mapping.items()}
    counters = {column["header"]: 0 for column in result.columns}
    for row in rows:
        for column in result.columns:
            header = column["header"]
            value = row.get(header)
            if value is None or validators.is_missing(value) or _is_sentinel(value):
                counters[header] = counters.get(header, 0) + 1
    for column in result.columns:
        column["missing_percentage_estimate"] = round(100.0 * counters.get(column["header"], 0) / total, 1)
