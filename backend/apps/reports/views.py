from __future__ import annotations

from typing import Any, Dict

from django.http import FileResponse
from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common.api import ApiError, MongoJWTAuthentication  # noqa: E402
from common.views import (  # noqa: E402
    AuthenticatedView,
    bool_param,
    int_param,
    str_param,
)

from apps.reports import generators, services  # noqa: E402


class ReportView(AuthenticatedView):
    """Base view; the JWT authenticator is declared here, not inherited."""

    authentication_classes = [MongoJWTAuthentication]


class ReportCollectionView(ReportView):
    """GET/POST /api/v1/reports/."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 20, minimum=1, maximum=200)
        status = str_param(request, "status", None, choices=services.STATUSES)
        report_type = str_param(request, "report_type", None,
                                choices=tuple(generators.REPORT_TYPES))
        include_expired = bool_param(request, "include_expired", False)
        data = services.list_reports(
            request, page=page, page_size=page_size, status=status,
            report_type=report_type, include_expired=include_expired,
        )
        data["scope"] = "all_reports" if request.user.role == "administrator" else "own_reports"
        data["retention_days"] = services.retention_days()
        return Response(data)

    def post(self, request):
        payload = _payload(request)
        report_type = str(payload.get("report_type") or "").strip()
        if not report_type:
            raise ApiError("validation_failed", "report_type is required.")
        fmt = str(payload.get("format") or payload.get("file_format") or "pdf").strip().lower()
        filters = payload.get("filters")
        if filters is not None and not isinstance(filters, dict):
            raise ApiError("validation_failed", "filters must be an object.")
        data = services.create_report(request, report_type=report_type, fmt=fmt,
                                      filters=filters or {})
        return Response(data, status=202)


class ReportTypeListView(ReportView):
    """Catalogue plus a computable-now flag."""

    def get(self, request):
        return Response({
            "types": services.catalogue(request),
            "formats": list(services.FORMATS),
            "statuses": list(services.STATUSES),
            "filter_help": {
                "metric": "measured variable, e.g. temperature_c",
                "frequency": "daily | weekly | monthly | annual",
                "start_date": "YYYY-MM-DD",
                "end_date": "YYYY-MM-DD",
                "regions": "list of region codes",
                "location_codes": "list of station codes",
                "countries": "list of country names or codes",
                "datasets": "list of dataset ids",
                "sigma": "deviation threshold for the anomaly report",
                "limit": "maximum rows (1-1000)",
            },
            "storage": services.storage_report(),
        })


class ReportSummaryView(ReportView):
    def get(self, request):
        data = services.counts(request)
        data["storage"] = services.storage_report()
        return Response(data)


class ReportDetailView(ReportView):
    def get(self, request, report_id: str):
        return Response(services.detail(report_id, request))

    def delete(self, request, report_id: str):
        return Response(services.delete_report(report_id, request))


class ReportCancelView(ReportView):
    """POST /api/v1/reports/<id>/cancel - withdraw a queued report."""

    def post(self, request, report_id: str):
        return Response(services.cancel_report(report_id, request))


class ReportDownloadView(ReportView):
    def get(self, request, report_id: str):
        path, filename, content_type, document = services.artefact_for_download(report_id, request)
        try:
            handle = path.open("rb")
        except OSError as exc:  # noqa: BLE001 - lost race with the cleanup sweep
            raise ApiError("not_found", "The artefact could not be opened.", status=410) from exc
        response = FileResponse(handle, as_attachment=True, filename=filename,
                                content_type=content_type)
        response["X-Report-Row-Count"] = str(document.get("row_count"))
        response["X-Report-Status"] = str(document.get("status"))
        response["Cache-Control"] = "private, no-store"
        return response


def _payload(request) -> Dict[str, Any]:
    data = getattr(request, "data", None)
    if isinstance(data, dict):
        return data
    if data is None:
        return {}
    raise ApiError("validation_failed", "A JSON object body is required.")
