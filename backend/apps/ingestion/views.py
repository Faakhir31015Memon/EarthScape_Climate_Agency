"""Ingestion endpoints: provider catalogue, job creation, job monitoring, retry."""

from __future__ import annotations

from apps.authentication.permissions import role_allows
from apps.datasets import repository as datasets
from apps.ingestion import jobs as ingestion_jobs
from apps.ingestion import service as ingestion
from apps.ingestion import source_clients
from common.audit import audit
from common.exceptions import ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.tasks import submit
from common.validators import IngestionJobRequest, parse
from common.views import ApiView

PROVIDER_LABELS = {"nasa_power": "NASA POWER", "noaa_cdc": "NOAA Climate Data Online",
                   "open_meteo": "Open-Meteo"}


class ProviderCatalogueView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        options = source_clients.provider_options()
        for key, entry in options.items():
            entry["display_name"] = PROVIDER_LABELS.get(key, key)
        return self.ok(request, {
            "providers": options,
            "granularities": ["hourly", "daily", "monthly", "yearly"],
            "plausible_ranges_note": "Values outside documented physical ranges are rejected and "
                                     "reported per row; missing values are never stored as zero.",
        })


class IngestionJobListView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        page, size, _skip = normalize_pagination(request.query_params)
        params = self.params(request)
        items, total = ingestion.list_jobs(
            status=params.get("status", ""), source=params.get("source", ""),
            dataset_id=params.get("dataset_id", ""), kind=params.get("kind", ""),
            date_from=params.get("date_from", ""), date_to=params.get("date_to", ""),
            page=page, page_size=size,
        )
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"status_totals": ingestion.status_totals()}, request=request)

    def post(self, request):
        if not role_allows(request.auth_identity.role, "ingestion.run"):
            raise ValidationError("Your role cannot run ingestion jobs.", code="permission_denied")
        payload: IngestionJobRequest = parse(IngestionJobRequest, self.body(request))
        option = source_clients.provider_options().get(payload.provider)
        if not option or not option.get("available"):
            missing = (option or {}).get("missing_config") or "provider configuration"
            raise ValidationError(
                f"The {PROVIDER_LABELS.get(payload.provider, payload.provider)} connector is not "
                f"available in this deployment.",
                details=[f"Set {missing} in the backend environment to enable this source."])

        dataset = _dataset_for_pull(payload, request.auth_identity.user_id)
        job = ingestion.create_job(
            kind="provider_pull", source=payload.provider, dataset_id=str(dataset["_id"]),
            params={"coordinates": payload.coordinates, "date_range": payload.date_range,
                    "variables": payload.variables, "granularity": payload.granularity,
                    "station_id": payload.station_id or "", "dataset_code": payload.dataset_code or "",
                    "location_code": payload.location_code or "",
                    "location_name": payload.location_name or ""},
            created_by=request.auth_identity.user_id,
            label=f"{PROVIDER_LABELS.get(payload.provider)} pull ({payload.coordinates['latitude']:.2f}, "
                  f"{payload.coordinates['longitude']:.2f})",
        )
        submit(
            "provider_pull",
            ingestion_jobs.run_provider_ingest,
            kwargs={
                "provider": payload.provider, "dataset_id": str(dataset["_id"]),
                "job_id_str": str(job["_id"]), "coordinates": payload.coordinates,
                "start": (payload.date_range or {}).get("start", ""),
                "end": (payload.date_range or {}).get("end", ""),
                "variables": payload.variables, "granularity": payload.granularity,
                "station_id": payload.station_id or "", "dataset_code": payload.dataset_code or "",
                "location_code": payload.location_code or "",
                "location_name": payload.location_name or _coord_label(payload),
            },
            created_by=request.auth_identity.user_id,
            payload={"dataset_id": str(dataset["_id"]), "provider": payload.provider},
            label=job["label"],
        )
        audit(request, "ingestion.job_create", resource_type="ingestion_jobs",
              resource_id=str(job["_id"]), metadata={"provider": payload.provider,
                                                     "dataset_id": str(dataset["_id"])})
        return self.created(request, {
            "ingestion_job_id": str(job["_id"]),
            "dataset_id": str(dataset["_id"]),
            "status": job["status"],
            "poll": f"/api/v1/ingestion/jobs/{job['_id']}",
            "note": "The pull runs on the background task engine and calls the live provider API; "
                    "exact fetched/accepted/rejected counts appear on the job.",
        })


class IngestionJobDetailView(ApiView):
    required_capability = "platform.view"

    def get(self, request, job_id: str):
        return self.ok(request, ingestion.job_detail(job_id))


class IngestionJobRetryView(ApiView):
    required_capability = "ingestion.run"
    throttle_scope = "ingest"

    def post(self, request, job_id: str):
        result = ingestion.retry_job(job_id)
        audit(request, "ingestion.job_retry", resource_type="ingestion_jobs", resource_id=job_id)
        return self.ok(request, result)


class IngestionStatusView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        params = self.params(request)
        limit = int(params.get("recent", 8) or 8)
        return self.ok(request, {
            **ingestion.status_totals(),
            "recent_jobs": ingestion.recent_jobs(limit=min(limit, 30)),
            "providers": source_clients.provider_options(),
        })


def _dataset_for_pull(payload: IngestionJobRequest, user_id: str) -> dict:
    """Reuse an identical provider dataset so repeated pulls accumulate into one dataset."""
    label = PROVIDER_LABELS.get(payload.provider, payload.provider)
    found = datasets.find_by_name(payload.dataset_name, label)
    if found:
        return found
    return datasets.create(
        name=payload.dataset_name, description=f"Pulled from {label} for "
        f"({payload.coordinates['latitude']:.2f}, {payload.coordinates['longitude']:.2f}).",
        source_type="api", source_name=label, fmt="api", uploaded_by=user_id,
        geographic_coverage={"bounding_box": {"min_lat": payload.coordinates["latitude"],
                                              "max_lat": payload.coordinates["latitude"],
                                              "min_lon": payload.coordinates["longitude"],
                                              "max_lon": payload.coordinates["longitude"]}},
        temporal_coverage=payload.date_range or {},
        tags=[payload.provider, payload.granularity],
    )


def _coord_label(payload: IngestionJobRequest) -> str:
    return f"({payload.coordinates['latitude']:.2f}, {payload.coordinates['longitude']:.2f})"
