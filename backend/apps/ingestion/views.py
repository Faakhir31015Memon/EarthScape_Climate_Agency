"""Ingestion API endpoints (jobs, connectors, coverage)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from rest_framework import status
from rest_framework.response import Response

from apps.ingestion import services
from apps.ingestion.connectors import connector_status, get_connector, resolve_name
from apps.ingestion.connectors import base as connector_base
from common import audit, validators
from common.api import ADMINISTRATOR, AdministratorOrAnalyst, ApiError
from common.repositories import ingestion_jobs, maybe_object_id, serialize
from common.views import AuthenticatedView, int_param, str_param

logger = logging.getLogger(__name__)

JOB_STATUSES = ("queued", "running", "completed", "completed_with_errors", "failed", "cancelled")


class JobsView(AuthenticatedView):
    """Authenticated read; create requires administrator or climate analyst."""

    def get(self, request):
        criteria: Dict[str, Any] = {}
        status_filter = str_param(request, "status", choices=JOB_STATUSES)
        if status_filter:
            criteria["status"] = status_filter
        source_name = str_param(request, "source_name")
        if source_name:
            criteria["source_name"] = {"$regex": _escape(source_name), "$options": "i"}
        dataset_id = str_param(request, "dataset_id")
        if dataset_id:
            criteria["dataset_id"] = maybe_object_id(dataset_id)
        page = int_param(request, "page", 1, minimum=1, maximum=10000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)
        result = ingestion_jobs.paginate(criteria, page=page, page_size=page_size,
                                          sort=[("started_at", -1)])
        return Response(result)

    def post(self, request):
        user = self.current_user(request)
        from common.api import CLIMATE_ANALYST
        if user.role not in (ADMINISTRATOR, CLIMATE_ANALYST):
            raise ApiError("permission_denied", "Climate analyst or administrator role required.", status=403)
        body = request.data if isinstance(request.data, dict) else {}
        source = str(body.get("source", "")).strip()
        if not source:
            raise ApiError("validation_failed", "A 'source' is required.")
        try:
            connector = get_connector(source)
        except connector_base.SourceValidationError as exc:
            raise ApiError("validation_failed", exc.message, status=400)

        parameters = body.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise ApiError("validation_failed", "parameters must be an object.")

        # Configuration gate.
        if not connector.is_configured():
            raise ApiError("service_unavailable",
                           connector.setup_hint or "This source is not configured on the server.",
                           status=503, details=connector.configuration_status())

        dataset_id = body.get("dataset_id")
        if dataset_id:
            from common.repositories import datasets as datasets_repository
            dataset = datasets_repository.find_one({"_id": maybe_object_id(str(dataset_id))})
            if dataset is None:
                raise ApiError("not_found", "The referenced dataset does not exist.", status=404)
        else:
            dataset = services.get_or_create_source_dataset(
                source_name=connector.name, source_type=connector.source_type, created_by=user.id)

        start, end = _resolve_period(parameters)
        location_codes = _location_codes(parameters)
        geographic_coverage = _coverage_from_codes(location_codes)
        parameters = {**parameters, "connector": connector.name, "location_codes": location_codes}

        job_id = services.create_job(
            source_name=body.get("source_name") or connector.name,
            source_type=connector.source_type, job_type="api_sync",
            dataset_id=str(dataset["_id"]), requested_by=user.id,
            triggered_by=body.get("triggered_by", "manual") if body.get("triggered_by") in
            ("manual", "schedule") else "manual",
            data_period={"start": start, "end": end}, geographic_coverage=geographic_coverage,
            parameters=parameters, processing_mode="live-api")
        executor = services.submit_api_sync(job_id)
        audit.for_request(request, action="ingestion.job_created", resource_type="ingestion_job",
                          resource_id=job_id, metadata={"source": connector.name})
        return Response({"job_id": job_id, "status": "queued", "executor": executor,
                         "dataset_id": str(dataset["_id"]), "data_period": {"start": start, "end": end}},
                        status=status.HTTP_202_ACCEPTED)


class JobDetailView(AuthenticatedView):
    def get(self, request, job_id: str):
        detail = services.job_detail(job_id)
        if detail is None:
            raise ApiError("not_found", "No such ingestion job.", status=404)
        return Response(detail)


class JobRetryView(AuthenticatedView):
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request, job_id: str):
        result = services.retry_job(job_id, requested_by=self.current_user(request).id)
        if not result.get("ok"):
            reason = result.get("reason")
            if reason == "not_found":
                raise ApiError("not_found", "No such ingestion job.", status=404)
            if reason == "not_retryable":
                raise ApiError("conflict",
                               f"Only failed or cancelled jobs can be retried (status: "
                               f"{result.get('status')}).", status=409)
            if reason == "max_retries":
                raise ApiError("conflict", "This job has exhausted its retry allowance.", status=409)
            raise ApiError("internal_error", "Retry failed.", status=500)
        return Response(result)


class JobCancelView(AuthenticatedView):
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request, job_id: str):
        result = services.cancel_job(job_id, requested_by=self.current_user(request).id)
        if not result.get("ok"):
            if result.get("reason") == "not_found":
                raise ApiError("not_found", "No such ingestion job.", status=404)
            raise ApiError("conflict",
                           f"This job already finished (status: {result.get('status')}).",
                           status=409)
        return Response(result)


class ConnectorsView(AuthenticatedView):
    def get(self, request):
        check = str(request.query_params.get("reachability", "")).lower() in ("1", "true", "yes")
        return Response({"connectors": connector_status(check_reachability=check),
                         "note": "Configuration states come from environment variables; secret values are not returned."})


class ConnectorTestView(AuthenticatedView):
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request, name: str):
        user = self.current_user(request)
        try:
            connector = get_connector(name)
        except connector_base.SourceValidationError as exc:
            raise ApiError("validation_failed", exc.message, status=400)

        status_info = connector.configuration_status()
        if not connector.is_configured():
            return Response({"connector": connector.name, "success": False,
                             "state": "needs_configuration",
                             "detail": status_info.get("setup_hint", "Not configured."),
                             "records_fetched": 0, "records_accepted": 0},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)

        # A deliberately small live fetch: 1 point, 3 days.
        end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        start = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        location = _single_test_location(connector)
        from common.repositories import datasets as datasets_repository
        probe_dataset = datasets_repository.find_one({"source_metadata.feed": True})
        dataset_id = str(probe_dataset["_id"]) if probe_dataset else "connector-test"

        request_obj = connector_base.IngestionRequest(
            dataset_id=dataset_id, run_id="test-" + user.id[:6], job_id="connector-test",
            source_name=connector.name, source_type=connector.source_type,
            dataset_name="connector test", parameters={"parameters": _test_parameters(connector)},
            locations=[location], start=start, end=end, processing_mode="live-api")

        fetched = 0
        produced = 0
        error = None
        try:
            for raw in connector.fetch(request_obj):
                for observation in connector.normalize(raw, request_obj):
                    produced += 1
                fetched += 1
        except connector_base.ConnectorError as exc:
            error = {"code": exc.code, "message": exc.message}
        except Exception as exc:  # noqa: BLE001 - never let a probe crash the API
            error = {"code": "source_error", "message": str(exc)[:200]}

        success = produced > 0 and error is None
        payload = {
            "connector": connector.name,
            "success": success,
            "state": "reachable" if success else (error or {}).get("code", "degraded"),
            "points_requested": 1, "days_requested": 3,
            "raw_responses": fetched,
            "observations_produced": produced,
            "window": {"start": start, "end": end, "location": location.get("code")},
        }
        if error:
            payload["error"] = error
        audit.for_request(request, action="ingestion.connector_test", resource_type="connector",
                          resource_id=connector.name, outcome="success" if success else "failure",
                          metadata={"produced": produced})
        return Response(payload, status=status.HTTP_200_OK if success
                        else status.HTTP_502_BAD_GATEWAY)


class CoverageView(AuthenticatedView):
    def get(self, request):
        return Response({"sources": _coverage_summary(),
                         "computed_at": services.utcnow(),
                         "note": "Coverage is computed from stored observations, datasets and ingestion jobs."})


# --------------------------------------------------------------------- helpers
def _test_parameters(connector) -> List[str]:
    if connector.name == "nasa_power":
        return ["T2M", "PRECTOTCORR", "RH2M"]
    return []


def _single_test_location(connector) -> Dict[str, Any]:
    from common import regions
    reference = regions.LOCATIONS[0]
    return {"code": reference["code"], "name": reference["name"],
            "region_code": reference["region_code"], "country": reference["country"],
            "latitude": reference["latitude"], "longitude": reference["longitude"]}


def _resolve_period(parameters: Dict[str, Any]):
    end = parameters.get("end")
    start = parameters.get("start")
    if end:
        _validate_date(str(end))
    else:
        end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    if start:
        _validate_date(str(start))
    else:
        lookback = services.env.get_int("INGESTION_DEFAULT_LOOKBACK_DAYS", 7)
        start = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    if start > end:
        raise ApiError("validation_failed", "The start date is after the end date.")
    return start, end


def _validate_date(value: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ApiError("validation_failed", f"'{value[:40]}' must be a YYYY-MM-DD date.")


def _location_codes(parameters: Dict[str, Any]) -> List[str]:
    codes = parameters.get("location_codes") or parameters.get("location_code") or []
    if isinstance(codes, str):
        codes = [codes]
    return [str(code).strip().upper() for code in codes if str(code).strip()][:50]


def _coverage_from_codes(codes: List[str]) -> Dict[str, Any]:
    from common import regions
    resolved = [regions.location_by_code(code) for code in codes]
    resolved = [item for item in resolved if item]
    return {"regions": sorted({item["region_code"] for item in resolved}),
            "countries": sorted({item["country"] for item in resolved}),
            "location_codes": sorted({item["code"] for item in resolved})}


def _escape(text: str) -> str:
    import re
    return re.sub(r"[.*+?^${}()|[\]\\]", r"\\\g<0>", text)[:60]


def _coverage_summary() -> List[Dict[str, Any]]:
    """Temporal + geographic coverage per source, from real stored observations."""
    from common.repositories import climate_observations
    pipeline = [
        {"$group": {
            "_id": {"source_name": "$source_name", "source_type": "$source_type"},
            "observation_count": {"$sum": 1},
            "first_date": {"$min": "$observation_date"},
            "last_date": {"$max": "$observation_date"},
            "regions": {"$addToSet": "$region_code"},
            "countries": {"$addToSet": "$country"},
            "locations": {"$addToSet": "$location_code"},
            "datasets": {"$addToSet": "$dataset_id"},
        }},
        {"$sort": {"observation_count": -1}},
    ]
    try:
        rows = climate_observations.aggregate(pipeline)
    except Exception:  # noqa: BLE001 - local mongomock may not fully support $group
        rows = _coverage_fallback(climate_observations)
    sources: List[Dict[str, Any]] = []
    for row in rows:
        key = row.get("_id", {})
        sources.append({
            "source_name": key.get("source_name"),
            "source_type": key.get("source_type"),
            "observation_count": row.get("observation_count", 0),
            "temporal": {"start": row.get("first_date"), "end": row.get("last_date")},
            "regions": sorted(filter(None, row.get("regions", []))),
            "countries": sorted(filter(None, row.get("countries", []))),
            "location_codes": sorted(filter(None, row.get("locations", []))),
            "dataset_ids": [str(item) for item in row.get("datasets", [])],
        })
    return sources


def _coverage_fallback(collection) -> List[Dict[str, Any]]:
    grouped: Dict[Any, Dict[str, Any]] = {}
    for document in collection.find({}, {"source_name": 1, "source_type": 1, "observation_date": 1,
                                         "region_code": 1, "country": 1, "location_code": 1,
                                         "dataset_id": 1}, limit=20000):
        key = (document.get("source_name"), document.get("source_type"))
        bucket = grouped.setdefault(key, {"_id": {"source_name": key[0], "source_type": key[1]},
                                          "observation_count": 0, "dates": [], "regions": set(),
                                          "countries": set(), "locations": set(), "datasets": set()})
        bucket["observation_count"] += 1
        if document.get("observation_date"):
            bucket["dates"].append(str(document["observation_date"]))
        bucket["regions"].add(document.get("region_code"))
        bucket["countries"].add(document.get("country"))
        bucket["locations"].add(document.get("location_code"))
        bucket["datasets"].add(str(document.get("dataset_id")))
    rows: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        dates = sorted(filter(None, bucket["dates"]))
        rows.append({
            "_id": bucket["_id"], "observation_count": bucket["observation_count"],
            "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
            "regions": list(bucket["regions"]), "countries": list(bucket["countries"]),
            "locations": list(bucket["locations"]), "datasets": list(bucket["datasets"]),
        })
    return rows
