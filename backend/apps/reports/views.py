"""Report endpoints: request a report, read status, stream an authenticated download."""

from __future__ import annotations

from django.http import FileResponse

from apps.authentication.permissions import role_allows
from apps.reports import services
from common.audit import audit
from common.db import find_one, oid
from common.exceptions import Forbidden, NotFound, ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.validators import ReportRequest, parse
from common.views import ApiView


class ReportListView(ApiView):
    """``GET`` lists visible reports; ``POST`` requests generation (needs report.generate).

    The route is shared by both verbs, so the view is opened at ``export.download``
    (every role) and the create path enforces ``report.generate`` explicitly below.
    """

    required_capability = "export.download"
    throttle_scope = "reports"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = services.list_reports(request.auth_identity, params,
                                            page=page, page_size=size)
        extra: dict = {"report_types": list(services.REPORT_TYPES), "limits": services.LIMITS}
        if role_allows(request.auth_identity.role, "report.view_any"):
            extra["storage"] = services.storage_state()
        return paginated(items, total=total, page=page, page_size=size, extra=extra, request=request)

    def post(self, request):
        if not role_allows(request.auth_identity.role, "report.generate"):
            raise Forbidden("Generating reports requires the report.generate capability.")
        payload = parse(ReportRequest, self.body(request))
        document = services.create_report(payload, request.auth_identity)
        audit(request, "report.request", resource_type="reports",
              resource_id=str(document["_id"]),
              metadata={"report_type": document["report_type"], "formats": document["formats"],
                        "job_ref": document.get("job_ref")})
        return self.created(request, services.summarise(document) | {
            "note": "Generation runs in the background; poll this report for status "
                    "and read job transitions from the linked processing job."})


class ReportDetailView(ApiView):
    required_capability = "export.download"
    http_method_names = ["get"]

    def get(self, request, report_id: str):
        report = services.get_report(report_id, request.auth_identity,
                                     include_sections=str(
                                         self.params(request).get("sections", "true")).lower()
                                     != "false")
        return self.ok(request, report)


class ReportDownloadView(ApiView):
    """Streams a stored export from the server path - never a public URL."""

    required_capability = "export.download"
    throttle_scope = "burst"
    http_method_names = ["get"]

    def get(self, request, report_id: str, fmt: str):
        report = find_one("reports", {"_id": oid(report_id)})
        if not report:
            raise NotFound("Report not found.")
        if not services.can_view(report, request.auth_identity):
            raise Forbidden("You can only download reports you requested, unless you hold "
                            "report.view_any.")
        if report.get("status") != "completed":
            raise ValidationError(f"Report is '{report.get('status')}'; only completed reports "
                                  "can be downloaded.")
        # The requested format is validated against the allowlist recorded on the document,
        # so a URL can never select an arbitrary file.
        path, entry = services.resolve_output(report, fmt)
        audit(request, "report.download", resource_type="reports", resource_id=report_id,
              metadata={"format": fmt, "file": entry.get("path")})
        filename = f"{report.get('report_type', 'report')}-{report_id[:8]}.{fmt}"
        response = FileResponse(open(path, "rb"), as_attachment=True, filename=filename,
                               content_type=entry.get("media_type")
                               or services.MEDIA_TYPES.get(fmt, "application/octet-stream"))
        response["X-Report-Generated-At"] = str(report.get("completed_at") or "")
        return response
