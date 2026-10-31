"""Datasets API: listing, upload, validation, preview, ingest, mapping, download."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import uuid
from datetime import timedelta
from itertools import islice
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.http import FileResponse
from rest_framework import status
from rest_framework.response import Response

from apps.datasets import file_import, importer, serializers as dataset_serializers
from common import audit, storage_paths, validators
from common.api import ADMINISTRATOR, CLIMATE_ANALYST, ApiError, IsAdministrator, \
    IsAuthenticatedAccount
from common.repositories import datasets as datasets_repository, ingestion_jobs, maybe_object_id, \
    serialize, utcnow
from common.views import AuthenticatedView, bool_param, int_param, str_param

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("nasa_power", "noaa", "live_api", "university_upload", "sample")
STATUSES = ("draft", "validating", "validated", "ingesting", "ready", "failed", "archived")
FORMATS = ("csv", "json", "xlsx", "xlsm", "xltx", "xltm", "xls", "xlsb", "ods",
           "parquet", "api")

VALIDATE_ROW_CAP = 50000
PREVIEW_READ_CAP = 400

SORT_FIELDS = {
    "created_at": [("created_at", -1)],
    "name": [("name", 1)],
    "updated_at": [("updated_at", -1)],
    "record_count": [("record_count", -1), ("created_at", -1)],
    "status": [("status", 1), ("created_at", -1)],
}

FORBIDDEN_EXTENSIONS = {"", ".exe", ".dll", ".sh", ".bat", ".cmd", ".ps1", ".py", ".html",
                        ".htm", ".svg", ".js", ".com", ".scr", ".msi", ".jar"}


class DatasetListView(AuthenticatedView):
    def get(self, request):
        user = self.current_user(request)
        criteria = _visibility_criteria(user)
        search = str_param(request, "search")
        if search:
            escaped = _escape_regex(search)
            criteria["$and"] = criteria.get("$and", []) + [{
                "$or": [{"name": {"$regex": escaped, "$options": "i"}},
                        {"description": {"$regex": escaped, "$options": "i"}},
                        {"tags": {"$regex": escaped, "$options": "i"}}]}]
        source_type = str_param(request, "source_type", choices=SOURCE_TYPES)
        if source_type:
            criteria["source_type"] = source_type
        status_filter = str_param(request, "status", choices=STATUSES)
        if status_filter:
            criteria["status"] = status_filter
        fmt = str_param(request, "format", choices=FORMATS)
        if fmt:
            criteria["format"] = fmt
        region = str_param(request, "region")
        if region:
            criteria["geographic_coverage.regions"] = region
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)
        sort_key = str_param(request, "sort", default="created_at")
        if sort_key.lstrip("-") not in SORT_FIELDS:
            raise ApiError("validation_failed", "Unknown sort field.")
        sort = SORT_FIELDS[sort_key]
        if sort_key.startswith("-"):
            sort = [(field, -direction) for field, direction in sort]
        result = datasets_repository.paginate(criteria, page=page, page_size=page_size, sort=sort)
        result["items"] = [_present(dataset, user) for dataset in result["items"]]
        return Response(result)


class DatasetUploadView(AuthenticatedView):
    # Bringing your own data file is the platform's core user action, so every signed-in
    # role may do it; archive/delete and other administrative writes stay role-gated.
    permission_classes = [IsAuthenticatedAccount]

    def post(self, request):
        user = self.current_user(request)
        upload = request.FILES.get("file")
        if upload is None:
            raise ApiError("validation_failed", "No file was supplied under 'file'.")

        extension = Path(upload.name or "").suffix.lower()
        allowed_extensions = {ext.lower() for ext in settings.ALLOWED_UPLOAD_EXTENSIONS}
        if extension in FORBIDDEN_EXTENSIONS or extension not in allowed_extensions:
            raise ApiError("validation_failed",
                           f"File extension '{extension or 'none'}' is not permitted. Allowed: "
                           f"{', '.join(sorted(allowed_extensions))}.")

        declared_size = int(getattr(upload, "size", 0) or 0)
        if declared_size and declared_size > settings.MAX_UPLOAD_BYTES:
            raise ApiError("validation_failed",
                           f"File exceeds the maximum upload size "
                           f"({settings.MAX_UPLOAD_BYTES} bytes).")

        stored = _persist_upload(upload, extension)
        try:
            metadata = _validate_metadata(request)
            detected = file_import.sniff_format(stored["path"], extension)
            headers = _safe_headers(stored["path"], detected)
            mapping = _resolve_mapping(metadata, headers)
            dataset = _create_dataset_document(user, metadata, stored, detected, headers, mapping)
        except file_import.FileImportError as exc:
            _delete_quietly(stored["path"])
            raise ApiError("validation_failed", str(exc))
        except Exception:
            _delete_quietly(stored["path"])
            raise

        from apps.ingestion import services

        job_id = services.create_job(
            source_name=dataset["source_name"], source_type=dataset["source_type"],
            job_type="file_import", dataset_id=str(dataset["_id"]), requested_by=user.id,
            triggered_by="manual",
            data_period={"start": None, "end": None},
            geographic_coverage=dataset.get("geographic_coverage", {}),
            parameters={"file_name": dataset["file_name"], "format": detected},
            processing_mode="uploaded-file")
        datasets_repository.update_by_id(str(dataset["_id"]),
                                         {"$push": {"ingestion_job_ids": maybe_object_id(job_id)},
                                          "$set": {"status": "ingesting", "updated_at": utcnow()}})
        executor = services.submit_file_import(job_id)
        audit.for_request(request, action="dataset.uploaded", resource_type="dataset",
                          resource_id=str(dataset["_id"]),
                          metadata={"format": detected, "size_bytes": stored["size"],
                                    "file_name": _basename(dataset["file_name"])})
        return Response({
            "dataset": _present(serialize(datasets_repository.find_one({"_id": dataset["_id"]})), user),
            "ingestion_job_id": job_id,
            "status": "ingesting",
            "executor": executor,
            "detected_format": detected,
            "column_mapping": mapping,
            "headers": headers[:100],
        }, status=status.HTTP_202_ACCEPTED)


class DatasetDetailView(AuthenticatedView):
    def get(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_read(user, dataset)
        return Response({"dataset": _present(serialize(dataset), user)})

    def patch(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_write(user, dataset)
        serializer = dataset_serializers.DatasetUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updates = {key: value for key, value in serializer.validated_data.items()
                   if key in {"name", "description", "tags", "is_public", "units",
                              "geographic_coverage"}}
        if not updates:
            raise ApiError("validation_failed", "No updatable fields were supplied.")
        updates["updated_at"] = utcnow()
        updated = datasets_repository.update_by_id(dataset_id, updates, return_document=True)
        audit.for_request(request, action="dataset.updated", resource_type="dataset",
                          resource_id=dataset_id, metadata={"fields": sorted(updates)})
        return Response({"dataset": _present(serialize(updated), user)})

    def delete(self, request, dataset_id: str):
        # Archive is administrator-only and soft (the file and data are retained).
        user = self.current_user(request)
        if user.role != ADMINISTRATOR:
            raise ApiError("permission_denied", "Only an administrator can archive a dataset.", status=403)
        dataset = _load(dataset_id)
        updated = datasets_repository.update_by_id(dataset_id,
                                                   {"status": "archived", "archived_at": utcnow(),
                                                    "updated_at": utcnow()}, return_document=True)
        audit.for_request(request, action="dataset.archived", resource_type="dataset",
                          resource_id=dataset_id)
        return Response({"dataset": _present(serialize(updated), user), "archived": True})


class DatasetValidateView(AuthenticatedView):
    permission_classes = [IsAuthenticatedAccount]

    def post(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_write(user, dataset)
        path = _storage_path(dataset)
        if path is None:
            raise ApiError("validation_failed", "This dataset has no stored file to validate.")
        cap = min(int_param(request, "max_rows", VALIDATE_ROW_CAP, minimum=1,
                            maximum=VALIDATE_ROW_CAP), VALIDATE_ROW_CAP)
        mapping = _dataset_mapping(dataset)
        report = _run_validation(path, dataset, mapping, cap)
        datasets_repository.update_by_id(dataset_id, {
            "validation_report": report,
            "status": "validated" if report["counts"]["accepted"] else "failed",
            "updated_at": utcnow()})
        return Response(report)


class DatasetPreviewView(AuthenticatedView):
    def get(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_read(user, dataset)
        path = _storage_path(dataset)
        if path is None:
            raise ApiError("validation_failed", "This dataset has no stored file to preview.")
        count = int_param(request, "rows", 20, minimum=1, maximum=100)
        mapping = _dataset_mapping(dataset)
        detected = file_import.sniff_format(path)
        headers = file_import.read_headers(path, detected)
        result = importer.analyse_file(path, dataset, mapping, job_id="preview",
                                       max_rows=max(PREVIEW_READ_CAP, count * 10))
        return Response({
            "format_detected": detected,
            "headers": headers[:100],
            "column_mapping": result.mapping,
            "accepted": result.accepted_sample[:count],
            "rejected": result.rejected_sample[:count],
            "counts_preview": {"fetched": result.fetched, "accepted": result.accepted,
                               "rejected": result.rejected},
        })


class DatasetIngestView(AuthenticatedView):
    permission_classes = [IsAuthenticatedAccount]

    def post(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_write(user, dataset)
        if dataset.get("status") == "ingesting":
            raise ApiError("conflict", "This dataset is already being ingested.", status=409)
        if not _storage_path(dataset):
            raise ApiError("validation_failed", "This dataset has no stored file to import.")
        # Idempotent re-run: mapping is reused or re-suggested, upsert de-duplicates.
        mapping = _ensure_dataset_mapping(dataset)
        datasets_repository.update_by_id(dataset_id, {"status": "ingesting", "updated_at": utcnow()})

        from apps.ingestion import services

        job_id = services.create_job(
            source_name=dataset.get("source_name") or dataset.get("name") or "Uploaded file",
            source_type=dataset.get("source_type", "university_upload"),
            job_type="file_import", dataset_id=dataset_id, requested_by=user.id,
            triggered_by="manual", data_period={"start": None, "end": None},
            geographic_coverage=dataset.get("geographic_coverage", {}),
            parameters={"file_name": dataset.get("file_name"), "re_run": True},
            processing_mode="uploaded-file")
        datasets_repository.update_by_id(dataset_id,
                                         {"$push": {"ingestion_job_ids": maybe_object_id(job_id)},
                                          "$set": {"updated_at": utcnow()}})
        executor = services.submit_file_import(job_id)
        audit.for_request(request, action="dataset.ingest_queued", resource_type="dataset",
                          resource_id=dataset_id, metadata={"job_id": job_id})
        return Response({"dataset_id": dataset_id, "ingestion_job_id": job_id,
                         "status": "ingesting", "executor": executor},
                        status=status.HTTP_202_ACCEPTED)


class DatasetMappingView(AuthenticatedView):
    def get(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_read(user, dataset)
        path = _storage_path(dataset)
        headers: List[str] = []
        if path is not None:
            try:
                headers = file_import.read_headers(path)
            except file_import.FileImportError:
                headers = []
        suggestion = validators.auto_map_columns(headers) if headers else {}
        current = _dataset_mapping(dataset)
        return Response({"headers": headers[:200], "suggested_mapping": suggestion,
                         "current_mapping": current,
                         "units": dataset.get("units", {}),
                         "mappable_fields": dataset_serializers.MAPPABLE_FIELDS})

    def post(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_write(user, dataset)
        serializer = dataset_serializers.ColumnMappingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        updates = {
            "source_metadata.column_mapping": data["mapping"],
            "units": data.get("units", dataset.get("units", {})),
            "updated_at": utcnow(),
        }
        if isinstance(data.get("location_defaults"), dict):
            updates["source_metadata.location_defaults"] = data["location_defaults"]
        datasets_repository.update_by_id(dataset_id, updates)
        audit.for_request(request, action="dataset.mapping_set", resource_type="dataset",
                          resource_id=dataset_id, metadata={"fields": sorted(data["mapping"])})
        return Response({"dataset_id": dataset_id, "mapping": data["mapping"],
                         "units": updates["units"]})


class DatasetDownloadView(AuthenticatedView):
    def get(self, request, dataset_id: str):
        user = self.current_user(request)
        dataset = _load(dataset_id)
        _require_read(user, dataset, allow_public=True)
        path = _storage_path(dataset)
        if path is None or not path.is_file():
            raise ApiError("not_found", "The original uploaded file is not available.", status=404)
        response = FileResponse(open(path, "rb"), content_type="application/octet-stream",
                                as_attachment=True, filename=dataset.get("file_name") or path.name)
        # Served only through this access-controlled endpoint, never a public URL.
        response["Content-Disposition"] = f'attachment; filename="{_safe_filename(dataset.get("file_name") or path.name)}"'
        response["X-Content-Type-Options"] = "nosniff"
        return response


class ColumnSuggestionView(AuthenticatedView):
    permission_classes = [IsAuthenticatedAccount]

    def post(self, request):
        serializer = dataset_serializers.ColumnSuggestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        headers = serializer.validated_data["headers"]
        mapping = validators.auto_map_columns(headers)
        units = {}
        for field_name, header in mapping.items():
            if field_name in validators.RANGE_LIMITS:
                guessed = validators.guess_unit(header)
                if guessed:
                    units[field_name] = guessed
        return Response({"headers": headers, "suggested_mapping": mapping,
                         "suggested_units": units,
                         "unmapped_headers": [header for header in headers
                                              if header not in mapping.values()],
                         "mappable_fields": dataset_serializers.MAPPABLE_FIELDS})


class DatasetFormatsView(AuthenticatedView):
    def get(self, request):
        from common import regions
        return Response({
            "formats": _format_documentation(),
            "canonical_fields": {
                "measurement_fields": list(validators.RANGE_LIMITS),
                "context_fields": ["observation_date", "latitude", "longitude",
                                   "location_code", "location_name", "region_code", "country"],
            },
            "nasa_power_parameters": regions.NASA_PARAMETERS,
        })


def _visibility_criteria(user) -> Dict[str, Any]:
    """Administrator + analyst see everything; viewer sees public/own only."""
    if user.role in (ADMINISTRATOR, CLIMATE_ANALYST):
        return {}
    return {"$or": [{"is_public": True}, {"uploaded_by": user.id}]}


def _present(dataset: Optional[Dict[str, Any]], user) -> Dict[str, Any]:
    if dataset is None:
        return {}
    role = user.role
    include_storage = role in (ADMINISTRATOR, CLIMATE_ANALYST) or \
        dataset.get("uploaded_by") == user.id
    if not include_storage:
        dataset = {key: value for key, value in dataset.items() if key != "storage_uri"}
    dataset["is_owner"] = str(dataset.get("uploaded_by")) == str(user.id)
    dataset["can_edit"] = role in (ADMINISTRATOR, CLIMATE_ANALYST) or dataset["is_owner"]
    return dataset


def _load(dataset_id: str) -> Dict[str, Any]:
    try:
        oid = maybe_object_id(dataset_id)
    except Exception:
        raise ApiError("validation_failed", "Invalid dataset identifier.")
    dataset = datasets_repository.find_one({"_id": oid})
    if dataset is None:
        raise ApiError("not_found", "No such dataset.", status=404)
    return dataset


def _require_read(user, dataset, *, allow_public: bool = False) -> None:
    if user.role in (ADMINISTRATOR, CLIMATE_ANALYST):
        return
    if str(dataset.get("uploaded_by")) == str(user.id):
        return
    if dataset.get("is_public"):
        return
    if allow_public:
        raise ApiError("permission_denied", "You do not have access to this dataset.", status=403)
    raise ApiError("permission_denied", "You do not have access to this dataset.", status=403)


def _require_write(user, dataset) -> None:
    if user.role in (ADMINISTRATOR, CLIMATE_ANALYST):
        return
    # A viewer owns the import they started: correcting its mapping, validating or
    # re-running it must not require an analyst role.
    if str(dataset.get("uploaded_by")) == str(user.id):
        return
    raise ApiError("permission_denied",
                   "Only an administrator, a climate analyst or the account that uploaded "
                   "this dataset can change it.", status=403)


def _storage_path(dataset: Dict[str, Any]) -> Optional[Path]:
    path = storage_paths.stored_path(dataset.get("storage_uri"))
    return path if path is not None and path.exists() else None


def _persist_upload(upload, extension: str) -> Dict[str, Any]:
    target_dir = Path(settings.UPLOAD_ROOT)
    target_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    final_path = target_dir / f"{file_id}{extension}"
    digest = hashlib.sha256()
    size = 0
    limit = settings.MAX_UPLOAD_BYTES
    with open(final_path, "wb") as handle:
        for chunk in upload.chunks():
            size += len(chunk)
            if size > limit:
                handle.close()
                final_path.unlink(missing_ok=True)
                raise ApiError("validation_failed",
                               f"File exceeds the maximum upload size ({limit} bytes).")
            digest.update(chunk)
            handle.write(chunk)
    return {"path": final_path, "size": size, "sha256": digest.hexdigest(),
            "original_name": upload.name, "content_type": getattr(upload, "content_type", None)}


def _safe_headers(path: Path, detected: str) -> List[str]:
    try:
        return file_import.read_headers(str(path), detected)
    except file_import.FileImportError:
        return []


def _validate_metadata(request) -> Dict[str, Any]:
    data = request.data
    coerced = {
        "name": _first(data, "name"),
        "description": _first(data, "description", ""),
        "source_type": _first(data, "source_type", "university_upload"),
        "source_name": _first(data, "source_name", ""),
        "source_url": _first(data, "source_url", ""),
        "tags": dataset_serializers.coerce_list(_first(data, "tags")),
        "units": dataset_serializers.coerce_json_object(_first(data, "units")),
        "mapping": dataset_serializers.coerce_json_object(_first(data, "mapping")),
        "is_public": str(_first(data, "is_public", "false")).lower() in ("1", "true", "yes", "on"),
        "default_location_code": _first(data, "default_location_code", ""),
        "default_region_code": _first(data, "default_region_code", ""),
        "default_country": _first(data, "default_country", ""),
    }
    # An omitted coordinate must stay absent: FloatField rejects the empty string.
    for field in ("default_latitude", "default_longitude"):
        value = str(_first(data, field, "") or "").strip()
        if value:
            coerced[field] = value
    serializer = dataset_serializers.DatasetUploadMetadataSerializer(data=coerced)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _first(data, key: str, default=None):
    if hasattr(data, "getlist"):
        values = data.getlist(key)
        if values:
            return values if len(values) > 1 else values[0]
        return default
    value = data.get(key)
    return default if value is None else value


def _resolve_mapping(metadata: Dict[str, Any], headers: List[str]) -> Dict[str, str]:
    mapping = dict(metadata.get("mapping") or {})
    if not mapping and headers:
        mapping = validators.auto_map_columns(headers)
    # Keep only headers that actually exist in the file.
    header_set = set(headers)
    cleaned = {field_name: header for field_name, header in mapping.items()
               if header in header_set}
    return cleaned


def _stored_format(detected: str, file_name: str) -> str:
    """Label a dataset with the format a reader will use.

    An encrypted container hides its real type, so the declared extension carries it;
    ``sniff_format`` already rejects an extension that cannot hold the sniffed content.
    """
    if detected == "encrypted":
        extension = Path(file_name).suffix.lstrip(".").lower()
        if extension in FORMATS:
            return extension
    return detected if detected in FORMATS else "csv"


def _create_dataset_document(user, metadata, stored, detected, headers, mapping) -> Dict[str, Any]:
    now = utcnow()
    location_defaults = {
        "location_code": metadata.get("default_location_code") or None,
        "region_code": metadata.get("default_region_code") or None,
        "country": metadata.get("default_country") or None,
        "latitude": metadata.get("default_latitude"),
        "longitude": metadata.get("default_longitude"),
        "source_type": metadata.get("source_type"),
    }
    coverage = _coverage_from_mapping(metadata, location_defaults)
    manifest = {
        "dataset_upload": True,
        "original_file_name": stored["original_name"],
        "size_bytes": stored["size"],
        "sha256": stored["sha256"],
        "declared_content_type": stored["content_type"],
        "detected_format": detected,
        "headers": headers[:200],
        "uploaded_by_user_id": user.id,
        "uploaded_at": now.isoformat(),
        "column_mapping": mapping,
    }
    manifest_path = Path(str(stored["path"]) + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    document = {
        "name": metadata["name"],
        "description": metadata.get("description", ""),
        "source_type": metadata.get("source_type", "university_upload"),
        "source_name": metadata.get("source_name") or metadata["name"],
        "source_url": metadata.get("source_url") or None,
        "file_name": Path(stored["original_name"]).name,
        "storage_uri": storage_paths.stored_key(stored["path"]),
        "format": _stored_format(detected, stored["original_name"]),
        "schema_version": validators.SCHEMA_VERSION,
        "geographic_coverage": coverage,
        "temporal_coverage": {"start": None, "end": None, "years": [], "frequency": "unknown"},
        "record_count": 0,
        "status": "validating",
        "uploaded_by": user.id,
        "created_at": now,
        "updated_at": now,
        "source_metadata": {
            "column_mapping": mapping,
            "location_defaults": {k: v for k, v in location_defaults.items() if v is not None},
            "storage": {"sha256": stored["sha256"], "size_bytes": stored["size"],
                        "manifest": storage_paths.stored_key(manifest_path)},
        },
        "validation_report": {},
        "ingestion_job_ids": [],
        "tags": metadata.get("tags", []),
        "units": metadata.get("units", {}),
        "is_public": metadata.get("is_public", False),
    }
    dataset_id = datasets_repository.insert_one(document)
    document["_id"] = maybe_object_id(dataset_id)
    return document


def _coverage_from_mapping(metadata, location_defaults) -> Dict[str, Any]:
    from common import regions
    codes = []
    if location_defaults.get("location_code"):
        codes = [str(location_defaults["location_code"]).upper()]
    region = location_defaults.get("region_code")
    resolved = [regions.location_by_code(code) for code in codes]
    resolved = [item for item in resolved if item]
    region_list = [region] if region else sorted({item["region_code"] for item in resolved})
    countries = sorted({item["country"] for item in resolved})
    bbox = None
    if resolved:
        lats = [item["latitude"] for item in resolved]
        lons = [item["longitude"] for item in resolved]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
    return {"regions": [r for r in region_list if r], "countries": countries,
            "bbox": bbox, "location_codes": sorted({item["code"] for item in resolved})}


def _dataset_mapping(dataset: Dict[str, Any]) -> Dict[str, str]:
    metadata = dataset.get("source_metadata", {}) if isinstance(dataset.get("source_metadata"), dict) else {}
    return dict(metadata.get("column_mapping") or {})


def _ensure_dataset_mapping(dataset: Dict[str, Any]) -> Dict[str, str]:
    mapping = _dataset_mapping(dataset)
    if mapping:
        return mapping
    path = _storage_path(dataset)
    if path is None:
        return {}
    headers = file_import.read_headers(str(path))
    mapping = validators.auto_map_columns(headers)
    datasets_repository.update_by_id(str(dataset["_id"]),
                                     {"source_metadata.column_mapping": mapping,
                                      "updated_at": utcnow()})
    return mapping


def _run_validation(path: Path, dataset: Dict[str, Any], mapping: Dict[str, str], cap: int) -> Dict[str, Any]:
    detected = file_import.sniff_format(str(path))
    headers = file_import.read_headers(str(path), detected)
    try:
        sample = [record for _, record in islice(
            file_import.open_file(str(path), detected, max_rows=cap), cap)]
    except file_import.FileImportError as exc:
        return {"error": str(exc), "format_detected": detected, "headers": headers,
                "counts": {"fetched": 0, "accepted": 0, "rejected": 0}}

    rows = [(index + 1, record) for index, record in enumerate(sample)]
    result = importer.analyse_rows(iter(rows), dataset=dataset, mapping=mapping,
                                   run_id="validate", job_id="validate", collect_samples=True)
    result.format_detected = detected
    result.headers = headers
    result.columns = importer.column_analysis(headers, mapping)
    importer.enrich_missing_percentages(result, sample)

    report = importer.build_validation_report(result)
    report["truncated"] = len(sample) >= cap
    report["duplicate_key_estimate"] = _duplicate_estimate(result, len(sample))
    report["geospatial"] = _geospatial_section(result)
    report["sample_accepted"] = result.accepted_sample[:20]
    report["sample_rejected"] = result.rejected_sample[:20]
    return report


def _duplicate_estimate(result, total: int) -> Dict[str, Any]:
    unique = result.accepted
    repeat = result.skipped
    return {"records_sampled": total, "unique_keys": unique,
            "in_sample_duplicates": repeat,
            "duplicate_percentage": round(100.0 * repeat / total, 1) if total else 0.0}


def _geospatial_section(result) -> Dict[str, Any]:
    coverage = result.coverage or {}
    zero_warnings = [issue for issue in result.issues
                     if issue.get("code") in ("latitude_zero", "longitude_zero")]
    bad_coords = [issue for issue in result.issues
                  if str(issue.get("code", "")).endswith(("_missing", "_out_of_range"))]
    return {"bbox": coverage.get("bbox"), "regions": coverage.get("regions", []),
            "location_codes": coverage.get("location_codes", []),
            "zero_coordinate_warnings": sum(issue.get("count", 0) for issue in zero_warnings),
            "coordinate_errors": sum(issue.get("count", 0) for issue in bad_coords)}


def _delete_quietly(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
        Path(str(path) + ".manifest.json").unlink(missing_ok=True)
    except OSError:
        pass


def _basename(name: Any) -> str:
    return Path(str(name or "")).name


def _safe_filename(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9._\-]', "_", str(name))[:120]


def _escape_regex(text: str) -> str:
    return re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", text)[:60]


def _format_documentation() -> List[Dict[str, Any]]:
    from common import regions

    required_core = ["observation_date", "latitude", "longitude"]
    return [
        {"format": "csv", "delimiter": "auto-detected (, ; tab |)",
         "notes": "Chunked streaming reader; header row optional of units in the column name.",
         "required_columns": required_core, "optional_columns": dataset_serializers.MAPPABLE_FIELDS,
         "unit_rules": _unit_rules()},
        {"format": "json", "shapes": ["array of objects", "NDJSON (object per line)"],
         "required_columns": required_core, "optional_columns": dataset_serializers.MAPPABLE_FIELDS,
         "unit_rules": _unit_rules(),
         "notes": "Arrays and NDJSON stream without loading the whole file; other shapes are size-guarded."},
        {"format": "xlsx", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "First worksheet, header on the first row. Cell values are read as stored; formulas are not evaluated. The "
                  "same reader also accepts .xlsm, .xltx and .xltm."},
        {"format": "xlsm", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "Macro-enabled workbook. Cell values are read; the macro stream is not executed."},
        {"format": "xltx", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "OOXML worksheet template; .xltm is read the same way."},
        {"format": "xltm", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "Macro-enabled template workbook: only cell values are read."},
        {"format": "xls", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "Legacy BIFF workbook via xlrd; capped at 256 MB because it is parsed whole."},
        {"format": "xlsb", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "Excel binary workbook streamed with pyxlsb, first sheet."},
        {"format": "ods", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "LibreOffice/OpenDocument spreadsheet; repeated empty columns are capped by "
                  "ODS_MAX_COLUMN_REPEAT."},
        {"format": "parquet", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS, "unit_rules": _unit_rules(),
         "notes": "Streamed row-group batches via PyArrow when available."},
        {"format": "api", "required_columns": required_core,
         "optional_columns": dataset_serializers.MAPPABLE_FIELDS,
         "unit_rules": _unit_rules(),
         "notes": f"Live sources map provider parameters to canonical fields, e.g. NASA POWER "
                  f"{', '.join(sorted(regions.NASA_PARAMETERS))}."},
    ]


def _unit_rules() -> Dict[str, List[str]]:
    return {field: sorted(units) for field, units in validators.UNIT_CONVERSIONS.items()}
