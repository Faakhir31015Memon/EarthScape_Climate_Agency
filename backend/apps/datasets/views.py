"""Dataset endpoints: catalogue, metadata, upload, validate-preview, commit."""

from __future__ import annotations

from django.conf import settings

from apps.datasets import jobs as dataset_jobs
from apps.datasets import mapping
from apps.datasets import repository as datasets
from apps.datasets import storage as uploads
from apps.ingestion import service as ingestion
from common.audit import audit
from common.db import oid
from common.exceptions import Forbidden, ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.tasks import submit
from common.validators import DatasetCreate, UploadCommitRequest, UploadValidateRequest, parse
from common.views import ApiView


class DatasetListView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = datasets.list_datasets(
            search=params.get("search", ""), status=params.get("status", ""),
            source_type=params.get("source_type", ""), page=page, page_size=size,
            order=params.get("order", "-created_at"),
        )
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"status_counts": datasets.storage_stats()}, request=request)

    def post(self, request):
        payload = parse(DatasetCreate, self.body(request))
        document = datasets.create(
            name=payload.name, description=payload.description or "", source_type=payload.source_type,
            source_name=payload.source_name, source_url=payload.source_url or "",
            fmt=payload.format, uploaded_by=request.auth_identity.user_id,
            geographic_coverage=payload.geographic_coverage, temporal_coverage=payload.temporal_coverage,
            tags=payload.tags,
        )
        audit(request, "dataset.create", resource_type="datasets", resource_id=str(document["_id"]))
        return self.created(request, document)


class DatasetDetailView(ApiView):
    required_capability = "platform.view"

    def get(self, request, dataset_id: str):
        card = datasets.summary_card(dataset_id)
        params = self.params(request)
        recent_jobs = ingestion.recent_jobs(dataset_id=dataset_id, limit=int(params.get("jobs", 10) or 10))
        card["recent_ingestion_jobs"] = recent_jobs
        card["data_lake"] = _lake_reference(card["dataset"])
        return self.ok(request, card)

    def patch(self, request, dataset_id: str):
        body = self.body(request)
        allowed = {"name", "description", "tags", "status", "geographic_coverage",
                   "temporal_coverage", "source_url", "default_location", "granularity",
                   "column_map", "units_hint"}
        changes = {k: v for k, v in body.items() if k in allowed}
        if not changes:
            raise ValidationError("No updatable fields were provided.",
                                  details=[f"allowed fields: {', '.join(sorted(allowed))}"])
        updated = datasets.patch(dataset_id, changes)
        audit(request, "dataset.update", resource_type="datasets", resource_id=dataset_id,
              metadata={"fields": sorted(changes)})
        return self.ok(request, updated)

    def delete(self, request, dataset_id: str):
        params = self.params(request)
        include = params.get("include_observations", "").lower() == "true"
        if request.auth_identity.role != "administrator":
            raise Forbidden("Only an administrator can delete a dataset.",
                            code="permission_denied")
        result = datasets.delete(dataset_id, include_observations=include)
        audit(request, "dataset.delete", resource_type="datasets", resource_id=dataset_id,
              metadata=result)
        return self.ok(request, result)


class DatasetUploadView(ApiView):
    """Accept a CSV/Excel/JSON/JSONL file and return detected shape plus mapping hints."""

    required_capability = "dataset.upload"
    throttle_scope = "ingest"

    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError("Submit the file under the multipart field 'file'.",
                                  code="missing_file")
        stored = uploads.store_upload(upload)
        header = uploads.read_header(stored)
        suggested = mapping.suggest(header.columns)
        units = mapping.suggest_units(header.columns, suggested)
        missing = mapping.missing_required(suggested)
        audit(request, "dataset.upload_stage", resource_type="datasets", resource_id=None,
              metadata={"bytes": stored.size_bytes, "format": stored.fmt})
        return self.ok(request, {
            "upload": stored.as_dict(),
            "columns": [
                {"name": column, "detected_type": header.detected_types.get(column),
                 "suggested_field": suggested.get(column, "ignore")}
                for column in header.columns
            ],
            "sample_rows": header.sample_rows,
            "suggested_mapping": suggested,
            "suggested_units": units,
            "target_fields": mapping.available_targets(),
            "unmapped_required": missing,
            "rows_sampled": header.row_estimate,
            "limits": {
                "max_bytes": int(settings.APP["MAX_UPLOAD_BYTES"]),
                "max_rows": int(settings.APP["MAX_UPLOAD_ROWS"]),
                "preview_rows": int(settings.APP["PREVIEW_ROWS"]),
            },
            "notes": header.notes,
            "next": "Confirm or correct the column mapping, then call "
                    "/api/v1/datasets/upload/validate for a dry run and /commit to import.",
        })


class DatasetValidateView(ApiView):
    """Dry-run validation: exact accepted/rejected counts without writing observations."""

    required_capability = "dataset.upload"
    throttle_scope = "ingest"

    def post(self, request):
        payload = parse(UploadValidateRequest, self.body(request))
        stored = uploads.resolve(payload.file_ref)
        if not payload.column_map:
            raise ValidationError("A column mapping is required before validation.",
                                  details=["column_map must map at least timestamp, latitude, longitude"])
        missing = mapping.missing_required(payload.column_map)
        if missing:
            raise ValidationError("Mapping is incomplete.", details=[f"missing required field: {f}"
                                                                      for f in missing])
        result = ingestion.ingest_row_stream(
            dataset_id="preview",
            row_stream=uploads.iter_rows(stored, max_rows=payload.dry_run_limit),
            column_map=payload.column_map,
            units_hint=payload.units_hint,
            granularity=payload.granularity,
            provider="university_upload",
            kind="file",
            job_id=None,
            source_file=stored.original_name,
            dry_run=True,
            limit=payload.dry_run_limit,
            preview_rows=int(settings.APP["PREVIEW_ROWS"]),
        )
        counts = result["counts"]
        accepted = int(counts.get("records_accepted", 0))
        fetched = int(counts.get("records_fetched", 0))
        result["quality_score"] = round(accepted / fetched, 3) if fetched else 0.0
        result["would_write_rows"] = accepted
        result["file"] = stored.as_dict()
        return self.ok(request, result)


class DatasetCommitView(ApiView):
    """Create the dataset record and import the validated file (background for large files)."""

    required_capability = "dataset.upload"
    throttle_scope = "ingest"

    def post(self, request):
        payload = parse(UploadCommitRequest, self.body(request))
        stored = uploads.resolve(payload.file_ref)
        missing = mapping.missing_required(payload.column_map)
        if missing:
            raise ValidationError("Mapping is incomplete.", details=[f"missing required field: {f}"
                                                                      for f in missing])

        if payload.dataset_id:
            dataset = datasets.get(payload.dataset_id)
        else:
            if payload.dataset is None:
                raise ValidationError("Provide either dataset_id or a dataset definition.",
                                      details=["dataset.name, dataset.source_name, dataset.format"])
            dataset = datasets.create(
                name=payload.dataset.name, description=payload.dataset.description or "",
                source_type=payload.dataset.source_type or "file",
                source_name=payload.dataset.source_name, source_url=payload.dataset.source_url or "",
                fmt=payload.dataset.format or stored.fmt, uploaded_by=request.auth_identity.user_id,
                file_name=stored.original_name, storage_uri=stored.path.name,
            )
        datasets.patch(str(dataset["_id"]), {"column_map": payload.column_map,
                                             "units_hint": payload.units_hint,
                                             "granularity": payload.granularity,
                                             "file_name": stored.original_name,
                                             "status": "uploaded"})

        job = ingestion.create_job(
            kind="file_import", source=stored.original_name, dataset_id=str(dataset["_id"]),
            params={"file_ref": payload.file_ref, "rows_previewed": payload.dry_run_limit,
                    "duplicate_policy": payload.overwrite_duplicates,
                    "column_map": payload.column_map, "units_hint": payload.units_hint,
                    "granularity": payload.granularity, "timezone": payload.timezone},
            created_by=request.auth_identity.user_id, label=f"Import {stored.original_name}",
        )
        ingestion.finish_job(job["_id"], status="running")

        submit(
            "file_import",
            dataset_jobs.run_file_import,
            kwargs={
                "upload_ref": payload.file_ref,
                "dataset_id": str(dataset["_id"]),
                "job_id_str": str(job["_id"]),
                "column_map": payload.column_map,
                "units_hint": payload.units_hint,
                "granularity": payload.granularity,
                "timezone_name": payload.timezone,
                "duplicate_policy": payload.overwrite_duplicates,
                "max_rows": None,
            },
            created_by=request.auth_identity.user_id,
            payload={"dataset_id": str(dataset["_id"]), "file": stored.original_name},
            label=f"Import {stored.original_name}",
        )
        audit(request, "dataset.import_commit", resource_type="datasets",
              resource_id=str(dataset["_id"]), metadata={"ingestion_job_id": str(job["_id"])})
        return self.created(request, {
            "dataset_id": str(dataset["_id"]),
            "ingestion_job_id": str(job["_id"]),
            "status": "running",
            "poll": f"/api/v1/ingestion/jobs/{job['_id']}",
            "note": "The import is running in the background; poll the ingestion job for exact "
                    "accepted, rejected and duplicate counts.",
        })


class DatasetRegionsView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        return self.ok(request, {"regions": datasets.distinct_regions()})


def _lake_reference(dataset: dict) -> dict:
    from common import datalake

    slug = datalake.slug(str(dataset.get("name", "dataset")))
    return {
        "engine": datalake.engine(),
        "storage_uri": dataset.get("storage_uri"),
        "raw_path": "raw/" + slug,
        "status": datalake.status(),
    }
