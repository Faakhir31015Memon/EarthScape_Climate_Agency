"""HTTP layer for the climate analytics app: explorer, trends, maps, quality, saved analyses."""

from __future__ import annotations

from apps.authentication.permissions import role_allows
from apps.climate import analytics, dashboard, repository
from common.audit import audit
from common.exceptions import Forbidden, ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.security import sanitize_text
from common.serialization import document_to_api
from common.validators import SavedAnalysisPatchRequest, SavedAnalysisRequest, parse
from common.views import ApiView

EXPLORER_MAX_PAGE_SIZE = 100
MAP_MAX_POINTS = 5000


def _clamp_size(request, *, hard_max: int, default: int) -> tuple[int, int, int]:
    page, size, skip = normalize_pagination(request.query_params, default_size=default)
    if size > hard_max:
        size = hard_max
        skip = (page - 1) * size
    return page, size, skip


def _bool_param(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int_param(value, default: int, *, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return default


class DashboardSummaryView(ApiView):
    """Single overview payload assembled from real stored documents."""

    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, dashboard.build_overview())


class ObservationListView(ApiView):
    required_capability = "platform.view"

    def get(self, request):
        params = self.params(request)
        variables = request.query_params.getlist("variable")
        match = repository.build_observation_match(params, variables)
        page, size, skip = _clamp_size(request, hard_max=EXPLORER_MAX_PAGE_SIZE, default=25)
        sort = sanitize_text(params.get("sort", "timestamp"), max_length=24)
        rows, total = repository.list_observations(match, sort=sort, skip=skip, limit=size)
        return paginated([document_to_api(row) for row in rows], total=total, page=page,
                         page_size=size,
                         extra={"filter_required": True, "max_page_size": EXPLORER_MAX_PAGE_SIZE},
                         request=request)


class ObservationDetailView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request, observation_id: str):
        document = repository.get_observation(observation_id)
        payload = document_to_api(document)
        payload["provenance_complete"] = bool(document.get("provenance"))
        return self.ok(request, payload)


class TrendsView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        variable = params.get("variable", "temperature")
        bucket = "year" if params.get("bucket") == "year" else "month"
        return self.ok(request, analytics.trends(
            variable,
            region=params.get("region") or None,
            bucket=bucket,
            granularity=params.get("granularity") or None,
            date_from=params.get("date_from") or None,
            date_to=params.get("date_to") or None,
        ))


class RegionsView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, analytics.regions_summary())


class MapDataView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        mode = params.get("mode", "markers")
        cap = _int_param(params.get("limit"), 500, low=1, high=MAP_MAX_POINTS)
        return self.ok(request, analytics.map_points(mode=mode, cap=cap))


class CorrelationsView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        variable_a = params.get("variable_a") or params.get("x")
        variable_b = params.get("variable_b") or params.get("y")
        if not variable_a or not variable_b:
            raise ValidationError("Both variable_a and variable_b are required.")
        return self.ok(request, analytics.correlations(
            variable_a, variable_b,
            region=params.get("region") or None,
            date_from=params.get("date_from") or None,
            date_to=params.get("date_to") or None,
            min_samples=_int_param(params.get("min_samples"), 8, low=2, high=1000),
        ))


class QualityView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        report = repository.quality_report()
        report["by_status"] = repository.count_by_quality()
        report["variables_available"] = repository.distinct_variable_counts()
        return self.ok(request, report)


class VariableAvailabilityView(ApiView):
    """Which metric fields actually exist in stored data — drives honest chart gating."""

    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, {"observed_range": repository.observed_range(),
                                 "variables": repository.distinct_variable_counts()})


class AnomalySummaryView(ApiView):
    """Read-only roll-up of pipeline alert events and ML anomaly summaries; ML owns detection."""

    required_capability = "anomaly.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        limit = _int_param(params.get("limit"), 20, low=1, high=200)
        return self.ok(request, repository.anomaly_summary(limit=limit))


class AnalyticsSummaryListView(ApiView):
    """Rows written by the PySpark/pandas jobs, exposing the engine that produced each figure."""

    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params, default_size=25)
        rows, total = repository.analytics_summaries(params, page=page, page_size=size)
        return paginated(rows, total=total, page=page, page_size=size,
                         extra={"engines": repository.analytics_summary_overview()},
                         request=request)


class SavedAnalysisListView(ApiView):
    """Owned + shared analyses. Reads are open to any role; writes require analysis.save."""

    required_capability = "platform.view"

    def get(self, request):
        params = self.params(request)
        scope = "all" if params.get("scope") == "all" else "mine"
        page, size, _skip = normalize_pagination(request.query_params, default_size=25)
        rows, total = repository.list_analyses(
            request.auth_identity.user_id, scope=scope, page=page, page_size=size)
        return paginated(rows, total=total, page=page, page_size=size,
                         extra={"scope": scope}, request=request)

    def post(self, request):
        _require_capability(request, "analysis.save")
        payload = parse(SavedAnalysisRequest, self.body(request))
        document = repository.create_analysis(
            name=payload.name, description=payload.description or "",
            view_state=payload.view_state, owner_id=request.auth_identity.user_id)
        result = document_to_api(document)
        audit(request, "analysis.create", resource_type="saved_analyses",
              resource_id=result["id"], metadata={"name": result.get("name")})
        return self.created(request, result)


class SavedAnalysisDetailView(ApiView):
    required_capability = "platform.view"

    def get(self, request, analysis_id: str):
        document = repository.get_analysis(analysis_id)
        if not repository.can_read(document, request.auth_identity):
            raise Forbidden("This analysis is private to its owner.")
        return self.ok(request, document_to_api(document))

    def patch(self, request, analysis_id: str):
        _require_capability(request, "analysis.save")
        payload = parse(SavedAnalysisPatchRequest, _patch_payload(self.body(request)))
        changes = {k: v for k, v in payload.model_dump().items() if v is not None}
        result = repository.patch_analysis(analysis_id, request.auth_identity, changes)
        audit(request, "analysis.update", resource_type="saved_analyses", resource_id=analysis_id)
        return self.ok(request, result)

    def delete(self, request, analysis_id: str):
        _require_capability(request, "analysis.save")
        result = repository.delete_analysis(analysis_id, request.auth_identity)
        audit(request, "analysis.delete", resource_type="saved_analyses", resource_id=analysis_id,
              metadata=result)
        return self.ok(request, result)


def _patch_payload(body: dict) -> dict:
    allowed = ("name", "description", "view_state")
    return {key: body[key] for key in allowed if key in body}


def _require_capability(request, capability: str) -> None:
    if not role_allows(request.auth_identity.role, capability):
        raise Forbidden(f"Role '{request.auth_identity.role}' is not permitted to: {capability}.")
