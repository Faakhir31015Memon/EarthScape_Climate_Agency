"""HTTP surface for ``/api/v1/processing/``.

Authorisation model (mirrors apps/alerts/views.py, apps/reports/views.py):

* reads require an authenticated account (any role);
* writes (job submit, job cancel) are restricted to administrator + climate
  analyst;
* every mutation is audited with :mod:`common.audit` - metadata is scrubbed
  inside the audit helper so parameters never leak secrets.

No endpoint accepts a command, module path, callable name or file path from the
client. The workflow registry is the only source of executable names; every
free-form parameter is checked against the workflow's spec before submission.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.processing import engines
from apps.processing import jobs as jobs_service
from apps.processing import serializers as processing_serializers
from apps.processing import workflows  # noqa: E402
from common import audit  # noqa: E402
from common.api import (  # noqa: E402
    ADMINISTRATOR,
    CLIMATE_ANALYST,
    ApiError,
    AdministratorOrAnalyst,
    IsAuthenticatedAccount,
    MongoJWTAuthentication,
)
from common.data_lake import LAYERS, get_data_lake  # noqa: E402
from common.repositories import datasets as datasets_repo  # noqa: E402
from common.views import AuthenticatedView, bool_param, int_param, str_param  # noqa: E402


logger = logging.getLogger(__name__)

STAFF_ROLES = (ADMINISTRATOR, CLIMATE_ANALYST)


class ProcessingView(AuthenticatedView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]


class ProcessingStaffView(ProcessingView):
    permission_classes = [AdministratorOrAnalyst]


# ------------------------------------------------------------------- catalog views
class WorkflowListView(ProcessingView):
    """``GET /api/v1/processing/workflows`` - registry + real availability."""

    def get(self, request):
        entries: List[Dict[str, Any]] = []
        for workflow_id, entry in workflows.WORKFLOWS.items():
            available, reason = engines.workflow_availability(entry["engine"], entry)
            shaped = {**entry, "id": workflow_id}
            entries.append(processing_serializers.present_workflow(
                shaped, {"available": available, "reason": reason}))
        return Response(entries)


class EngineHealthView(ProcessingView):
    """``GET /api/v1/processing/engines`` - composite EngineHealth, wrapped in a list."""

    def get(self, request):
        return Response([engines.engine_health()])


class DataLakeView(ProcessingView):
    """``GET /api/v1/processing/data-lake`` - honest lake inventory."""

    def get(self, request):
        lake = get_data_lake()
        roots: List[Dict[str, Any]] = []
        total_partitions = 0
        if lake.mode == "local":
            root = Path(lake.local_root)
            for layer in LAYERS:
                directory = root / layer
                files = 0
                bytes_total = 0
                partitions = 0
                if directory.is_dir():
                    for handle in directory.rglob("*"):
                        if handle.is_file():
                            files += 1
                            try:
                                bytes_total += handle.stat().st_size
                            except OSError:
                                continue
                            if handle.name.endswith((".parquet", ".json", ".jsonl", ".csv")):
                                partitions = max(partitions, len(
                                    [part for part in handle.parent.relative_to(directory).parts
                                     if "=" in part]))
                total_partitions += partitions
                roots.append({"path": layer, "files": files, "bytes": bytes_total})
            message = (f"data lake reports mode={lake.mode_label}; "
                       f"root={lake.local_root}")
        else:
            for layer in LAYERS:
                listing = lake.list_partition(layer, None, recursive=True)
                entries = listing.get("entries") or []
                roots.append({"path": layer, "files": len(entries), "bytes": 0})
            message = (f"data lake reports mode={lake.mode_label} at "
                       f"{lake.endpoint or 'unconfigured endpoint'}")
        return Response({
            "mode": lake.mode_label,
            "roots": roots,
            "partitions": total_partitions,
            "message": message,
        })


# ------------------------------------------------------------------------ job views
class JobListView(ProcessingView):
    """``GET/POST /api/v1/processing/jobs``."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=1000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=200)
        active_first = bool_param(request, "active_first", False)
        workflow = str_param(request, "workflow", max_length=80)
        status_value = str_param(request, "status",
                                  choices=("queued", "running", "completed", "failed", "cancelled"))
        dataset_id = str_param(request, "dataset_id", max_length=24)
        submitted_by = str_param(request, "submitted_by", max_length=24)
        result = jobs_service.list_jobs(
            workflow=workflow, status=status_value, dataset_id=dataset_id,
            submitted_by=submitted_by, active_first=active_first,
            page=page, page_size=page_size)
        result["items"] = [processing_serializers.present_job(document) for document in result["items"]]
        return Response(result)

    def post(self, request):
        serializer = processing_serializers.StartProcessingJobSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        user = request.user
        workflow_id = serializer.validated_data["workflow"]
        entry = workflows.require_workflow(workflow_id)
        dataset_id = serializer.validated_data.get("dataset_id") or None
        if dataset_id:
            if datasets_repo.find_one({"_id": _oid(dataset_id)}, {"_id": 1}) is None:
                raise ApiError("not_found", "The referenced dataset does not exist.", status=404)
        parameters = processing_serializers.validate_parameters(
            workflow_id, serializer.validated_data.get("parameters") or {})
        processing_mode = engines.engine_health().get("current_processing_mode")
        document = jobs_service.create_job(
            workflow=workflow_id,
            engine=entry["engine"],
            dataset_id=dataset_id,
            parameters=parameters,
            submitted_by=user.id,
            submitted_by_role=user.role,
            workflow_label=entry["label"],
            dataset_name=_dataset_name(dataset_id),
            processing_mode=processing_mode,
        )
        from apps.processing import runner

        try:
            executor = runner.submit(workflow_id, document["id"], parameters)
        except Exception as exc:  # noqa: BLE001 - dispatch failure must fail the job honestly
            logger.error("Processing dispatch failed: %s", type(exc).__name__)
            jobs_service.fail_job(document["id"],
                                    f"background dispatch failed: {type(exc).__name__}")
            raise ApiError("service_unavailable",
                           "No background executor accepted the processing job.") from exc
        jobs_service.processing_jobs.update_by_id(document["id"], {"executor": executor})
        audit.for_request(request, action="processing.job.submitted",
                          resource_type="processing_job", resource_id=document["id"],
                          metadata={"workflow": workflow_id, "engine": entry["engine"],
                                     "dataset_id": dataset_id,
                                     "parameters": _safe_parameters(parameters)})
        stored = jobs_service.processing_jobs.find_one({"_id": _oid(document["id"])})
        return Response(processing_serializers.present_job(stored), status=202)


class JobDetailView(ProcessingView):
    def get(self, request, job_id: str):
        document = jobs_service.get_job(job_id)
        return Response(processing_serializers.present_job(document))


class JobCancelView(ProcessingStaffView):
    """``POST /api/v1/processing/jobs/<id>/cancel``.

    Only ``queued`` jobs can actually be cancelled - see :func:`apps.processing.jobs.cancel_job`.
    """

    def post(self, request, job_id: str):
        document = jobs_service.cancel_job(job_id, request.user.id)
        audit.for_request(request, action="processing.job.cancelled",
                          resource_type="processing_job", resource_id=job_id,
                          metadata={"workflow": document.get("workflow")})
        return Response(processing_serializers.present_job(document))


class JobLogsView(ProcessingView):
    """``GET /api/v1/processing/jobs/<id>/logs``.

    Response shape matches ``{ lines: {timestamp, level, message}[], job: ProcessingJob }``
    from ``frontend/src/services/api.ts`` - note the ``timestamp`` key used by the
    dedicated logs feed, distinct from the ``at`` key on ``ProcessingJob.log_lines``.
    """

    def get(self, request, job_id: str):
        document = jobs_service.get_job(job_id)
        lines: List[Dict[str, Any]] = []
        for line in (document.get("log_lines") or [])[-200:]:
            if isinstance(line, dict):
                lines.append({
                    "timestamp": line.get("at"),
                    "level": str(line.get("level") or "info"),
                    "message": jobs_service.sanitize_error(str(line.get("message") or "")),
                })
            elif isinstance(line, str):
                # Legacy documents stored bare strings per line; surface them as
                # unlabelled ``info`` rows rather than dropping user data.
                lines.append({
                    "timestamp": None,
                    "level": "info",
                    "message": jobs_service.sanitize_error(line),
                })
        return Response({"lines": lines, "job": processing_serializers.present_job(document)})


class JobMonitorView(ProcessingView):
    """``GET /api/v1/processing/monitor`` - active-first job stream for the dashboard."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=1000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=100)
        result = jobs_service.list_jobs(active_first=True, page=page, page_size=page_size)
        result["items"] = [processing_serializers.present_job(document) for document in result["items"]]
        result["active_counts"] = {"queued": jobs_service.queued_count(),
                                    "running": jobs_service.active_count() - jobs_service.queued_count()}
        return Response(result)


# --------------------------------------------------------------------- utilities
def _oid(value: str):
    from bson import ObjectId

    if not ObjectId.is_valid(value or ""):
        raise ApiError("validation_failed", "identifier is not a valid ObjectId")
    return ObjectId(value)


def _dataset_name(dataset_id: Optional[str]) -> Optional[str]:
    if not dataset_id:
        return None
    document = datasets_repo.find_one({"_id": _oid(dataset_id)}, {"name": 1, "label": 1})
    if not document:
        return None
    return str(document.get("name") or document.get("label") or "")[:120]


def _safe_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    """Trim before writing into the audit document so no huge blob ends up there."""
    out: Dict[str, Any] = {}
    for key, value in (parameters or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = (str(value)[:200] if isinstance(value, str) else value)
        else:
            out[key] = type(value).__name__
    return out
