"""Endpoints for /api/v1/climate/. A too-thin record comes back with a reason and the
available vs required counts instead of a series. Write and export endpoints are
role-gated server-side and audited via common.audit.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from django.http import HttpResponse
from rest_framework.response import Response

from apps.climate import explorer, services
from apps.climate.serializers import (
    ANOMALY_METHODS,
    AnomalyScanSerializer,
    metric_or_400,
)
from common import analytics, audit
from common.api import (
    ApiError,
    AdministratorOrAnalyst,
    IsAuthenticatedAccount,
    MongoJWTAuthentication,
)
from common.views import (
    AuthenticatedView,
    BaseView,
    PublicView,
    bool_param,
    date_param,
    int_param,
    list_param,
    str_param,
)

logger = logging.getLogger(__name__)

EXPORT_FILENAME = "climate-observations.csv"


def _float_param(request, name: str, default: float, *, minimum: float,
                 maximum: float) -> float:
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", f"{name} must be a number.")
    if value < minimum or value > maximum:
        raise ApiError("validation_failed", f"{name} must be between {minimum} and {maximum}.")
    return value


def _optional_float(request, name: str) -> float | None:
    """An unbounded decimal filter: absent means "no threshold supplied"."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ApiError("validation_failed", f"{name} must be a number.")


def _quality(request) -> str:
    return str_param(request, "quality", "any", choices=services.QUALITY_OPTIONS) or "any"


def _frequency(request, default: str = "monthly") -> str:
    return str_param(request, "frequency", default, choices=services.FREQUENCIES) or default


def _group_by(request, default: str = "none") -> str:
    return str_param(request, "group_by", default, choices=services.GROUP_BY_OPTIONS) or default


def _page(request) -> Any:
    page = int_param(request, "page", 1, minimum=1, maximum=100000)
    page_size = int_param(request, "page_size", services.DEFAULT_PAGE_SIZE,
                          minimum=1, maximum=services.MAX_PAGE_SIZE)
    return page, page_size


START_WINDOW_KEYS = ("start", "from", "date_start")
END_WINDOW_KEYS = ("end", "to", "date_end")


def _window(request, *, default_days: int | None = None) -> tuple:
    """Read the inclusive YYYY-MM-DD window, accepting every alias.

    common.views.date_filter pairs one start key with one end key, so the aliases are
    folded here to avoid dropping ?end=.
    """
    start = None
    for key in START_WINDOW_KEYS:
        start = date_param(request, key, start)
    end = None
    for key in END_WINDOW_KEYS:
        end = date_param(request, key, end)
    if start and end and start > end:
        raise ApiError("validation_failed", "start must be on or before end.")
    if default_days and not (start or end):
        return services.default_window(days=default_days, end=end)
    return start, end


class ObservationListView(AuthenticatedView):
    """Bounded, indexed-friendly raw observation listing."""

    authentication_classes = [MongoJWTAuthentication]

    def get(self, request):
        page, page_size = _page(request)
        start, end = _window(request)
        metric = str_param(request, "metric")
        if metric:
            metric = metric_or_400(metric)
        else:
            metric = None
        payload = services.observation_list(
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            datasets_list=list_param(request, "datasets")[:100],
            countries=list_param(request, "countries")[:100],
            metric=metric,
            quality=_quality(request),
            sort=str_param(request, "sort", services.DEFAULT_OBSERVATION_SORT,
                           choices=tuple(sorted(services.OBSERVATION_SORTS))) or
                 services.DEFAULT_OBSERVATION_SORT,
            page=page,
            page_size=page_size,
            include_provenance=bool_param(request, "include_provenance"),
        )
        return Response(payload)


class TrendView(PublicView):
    """analytics.trend_series with an insufficient-data state."""

    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        start, end = _window(request)
        payload = services.trend_payload(
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            countries=list_param(request, "countries")[:100],
            metric=metric,
            frequency=_frequency(request),
            group_by=_group_by(request),
            require_baseline=bool_param(request, "require_baseline", True),
        )
        return Response(payload)


class RegionListView(PublicView):
    """The region catalogue enriched with real per-region aggregates."""

    def get(self, request):
        start, end = _window(request)
        codes = services.regions_list(request)
        stored = services.stored_regions()
        wanted = codes or [str(item.get("code")) for item in stored if item.get("code")]
        criteria = services.build_filters(start=start, end=end, regions_filter=codes or None,
                                          limit=analytics.MAX_QUERY_ROWS)
        aggregates = services.regional_aggregates(criteria, limit=200)
        items = []
        for document in stored:
            code = str(document.get("code") or "")
            if wanted and code not in wanted:
                continue
            stats = aggregates.get(code)
            items.append({
                "code": code,
                "name": document.get("name") or code,
                "country": document.get("country"),
                "climate_zone": document.get("climate_zone"),
                "centroid": document.get("centroid"),
                "location_count": len(document.get("locations") or []),
                "locations": [
                    {"code": location.get("code"), "name": location.get("name"),
                     "latitude": location.get("latitude"), "longitude": location.get("longitude"),
                     "station_id": location.get("station_id")}
                    for location in (document.get("locations") or [])[:50]
                ],
                "statistics": stats or {
                    "observation_count": 0,
                    "first_date": None,
                    "last_date": None,
                    "metrics": {},
                    "reason": "No observations exist for this region yet.",
                },
                "available": bool(stats and stats["observation_count"]),
                "geography_source": document.get("source", "reference"),
            })
        items.sort(key=lambda item: (-item["statistics"]["observation_count"], item["code"]))
        return Response({
            "regions": items,
            "region_count": len(items),
            "observed_region_codes": services.region_codes_present(),
            "catalogue_source": ("regions_collection"
                                 if any(item["geography_source"] == "regions_collection"
                                        for item in items) else "reference"),
            "aggregation_pipeline": ["$match", "$group", "$sort", "$limit"],
            "scope": {"start": start, "end": end, "regions": codes},
            "available": bool(aggregates),
            "reason": None if aggregates else (
                "The regions catalogue is available, but climate_observations holds no documents "
                "yet, so no region aggregate can be computed."),
            "generated_at": analytics.now_utc().isoformat(),
        })


class RegionDetailView(PublicView):
    def get(self, request, code: str):
        clean = (code or "").strip()[:80]
        if not clean:
            raise ApiError("validation_failed", "A region code is required.")
        start, end = _window(request)
        return Response(services.region_summary(clean, start=start, end=end))


class AnomalyListView(PublicView):
    """Anomaly detection over the stored record (analytics.detect_anomalies)."""

    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        start, end = _window(request)
        payload = services.anomaly_result(
            start=start,
            end=end,
            metric=metric,
            method=str_param(request, "method", "statistical_baseline",
                             choices=ANOMALY_METHODS) or "statistical_baseline",
            threshold_sigma=_float_param(request, "threshold_sigma", 2.5, minimum=1.0,
                                         maximum=10.0),
            absolute_threshold=_optional_float(request, "absolute_threshold"),
            direction=str_param(request, "direction", "both",
                                choices=services.ANOMALY_DIRECTIONS) or "both",
            region_code=str_param(request, "region") or None,
            location_code=(str_param(request, "location") or "").upper() or None,
        )
        return Response(payload)


class AnomalyScanView(BaseView):
    """Run detection and persist it: analyst or administrator only."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request):
        data = services.validated_body(AnomalyScanSerializer(data=request.data or {}))
        start = data["start"].isoformat() if data.get("start") else None
        end = data["end"].isoformat() if data.get("end") else None
        result = services.anomaly_result(
            start=start,
            end=end,
            metric=data["metric"],
            method=data["method"],
            threshold_sigma=data["threshold_sigma"],
            absolute_threshold=data.get("absolute_threshold"),
            direction=data["direction"],
            region_code=(data.get("region") or None),
            location_code=(data.get("location") or "").upper() or None,
            row_limit=data.get("limit_rows") or services.ANALYTICS_ROW_LIMIT,
        )
        summary = services.persist_anomaly_scan(
            result,
            start=start,
            end=end,
            metric=data["metric"],
            method=data["method"],
            direction=data["direction"],
            threshold_sigma=data["threshold_sigma"],
            absolute_threshold=data.get("absolute_threshold"),
            region_code=(data.get("region") or None),
            location_code=(data.get("location") or "").upper() or None,
            persist_limit=min(int(data.get("persist_limit") or 200),
                                services.MAX_ALERTS_PERSISTED),
            dry_run=bool(data.get("dry_run")),
            actor_user_id=getattr(request.user, "id", None),
            actor_role=getattr(request.user, "role", None),
        )
        audit.for_request(request, action="climate.anomaly_scan",
                          resource_type="anomaly_scan",
                          resource_id=summary.get("summary_key"),
                          metadata={
                              "metric": data["metric"], "method": data["method"],
                              "anomalies_detected": summary.get("anomalies_detected"),
                              "alerts_persisted": summary.get("alerts_persisted"),
                              "dry_run": summary.get("dry_run"),
                          })
        return Response({
            "scan": summary,
            "detection": {
                "metric": result.get("metric"),
                "method": result.get("method"),
                "anomaly_count": result.get("anomaly_count", 0),
                "severity_counts": result.get("severity_counts"),
                "evaluated_observations": result.get("evaluated_observations"),
                "baseline": result.get("baseline"),
                "date_range": result.get("date_range"),
                "available": result.get("available"),
                "reason": result.get("reason"),
                "caveat": result.get("caveat"),
            },
            "sample_anomalies": (result.get("anomalies") or [])[:20],
        })


class MapDataView(PublicView):
    """Live map layer: most recent stored measurement per monitored point."""

    def get(self, request):
        requested = list_param(request, "metrics") or list_param(request, "metric")
        metrics = [metric for metric in (requested or services.DEFAULT_MAP_METRICS)
                   if metric in analytics.METRICS]
        start, end = _window(request)
        payload = services.map_data(
            metrics=metrics or services.DEFAULT_MAP_METRICS,
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            countries=list_param(request, "countries")[:100],
            datasets_list=list_param(request, "datasets")[:100],
            quality=_quality(request),
            max_points=int_param(request, "max_points", 5000, minimum=1, maximum=10000),
        )
        return Response(payload)


class CorrelationView(PublicView):
    def get(self, request):
        start, end = _window(request)
        payload = services.correlation_payload(
            metrics=list_param(request, "metrics") or list_param(request, "metric"),
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            datasets_list=list_param(request, "datasets")[:100],
            countries=list_param(request, "countries")[:100],
        )
        return Response(payload)


class ComparisonView(PublicView):
    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        start, end = _window(request)
        payload = services.comparison_payload(
            metric=metric,
            frequency=_frequency(request),
            regions_filter=services.regions_list(request) or services.region_codes_present(),
            start=start,
            end=end,
            location_codes=list_param(request, "location_codes")[:200],
        )
        return Response(payload)


class HistoryView(PublicView):
    """Historical explorer: normals, per-year anomalies and change deltas."""

    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        start, end = _window(request)
        base_from = str_param(request, "base_from") or str_param(request, "base_start") or "1961"
        base_to = str_param(request, "base_to") or str_param(request, "base_end") or "1990"
        payload = services.history_payload(
            metric=metric,
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            countries=list_param(request, "countries")[:100],
            datasets_list=list_param(request, "datasets")[:100],
            start=start,
            end=end,
            base_from=explorer.normalise_period(base_from, default="1961"),
            base_to=explorer.normalise_period(base_to, default="1990"),
            group_by=_group_by(request, "region"),
        )
        return Response(payload)


class QualityView(BaseView):
    """Data-quality report; authenticated, because it describes the platform."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]

    def get(self, request):
        start, end = _window(request)
        payload = services.quality_report(
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            datasets_list=list_param(request, "datasets")[:100],
            countries=list_param(request, "countries")[:100],
            scope_detail=bool_param(request, "scope_detail", True),
        )
        return Response(payload)


class StationListView(PublicView):
    def get(self, request):
        payload = services.stations(regions_filter=services.regions_list(request),
                                    countries=list_param(request, "countries")[:100])
        return Response(payload)


class MetricCatalogueView(PublicView):
    """The measurement contract the frontend filter widgets are built from."""

    def get(self, request):
        catalogue = services.metric_catalogue(with_counts=bool_param(request, "counts", True))
        return Response({
            "metrics": catalogue,
            "frequencies": list(services.FREQUENCIES),
            "group_by_options": list(services.GROUP_BY_OPTIONS),
            "quality_options": list(services.QUALITY_OPTIONS),
            "anomaly_methods": list(services.ANOMALY_METHODS),
            "sorts": sorted(services.OBSERVATION_SORTS),
            "aggregation_rules": {
                "sum": sorted(analytics.ADDITIVE_METRICS),
                "mean": sorted(metric for metric in analytics.METRICS
                               if metric not in analytics.ADDITIVE_METRICS),
            },
            "missing_value_policy": "A measurement the source did not supply is stored as null "
                                    "and reported as null - never as zero.",
            "available_count": sum(1 for item in catalogue if item["has_data"]),
            "generated_at": analytics.now_utc().isoformat(),
        })


class FilterOptionsView(PublicView):
    """One call that fills every explorer filter widget."""

    def get(self, request):
        return Response(services.filter_options(regions_filter=services.regions_list(request)))


class ObservationExportView(BaseView):
    """Streaming CSV export - analyst or administrator, audited, hard-bounded."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [AdministratorOrAnalyst]

    def get(self, request):
        start, end = _window(request)
        metric = str_param(request, "metric")
        if metric:
            metric = metric_or_400(metric)
        requested_rows = request.query_params.get("rows") or request.query_params.get("max_rows")
        try:
            requested_rows = int(requested_rows) if requested_rows else None
        except (TypeError, ValueError):
            raise ApiError("validation_failed", "rows must be an integer.")
        meta = services.export_plan(
            start=start,
            end=end,
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            datasets_list=list_param(request, "datasets")[:100],
            countries=list_param(request, "countries")[:100],
            metric=metric,
            quality=_quality(request),
            sort=str_param(request, "sort", services.DEFAULT_OBSERVATION_SORT)
            or services.DEFAULT_OBSERVATION_SORT,
            requested_rows=requested_rows,
            actor={"id": getattr(request.user, "id", None),
                   "role": getattr(request.user, "role", None),
                   "email": getattr(request.user, "email", None)},
        )
        audit.for_request(request, action="climate.observations_export",
                          resource_type="climate_observations",
                          metadata={
                              "rows_included": meta["exported_rows"],
                              "rows_matched": meta["matched_rows"],
                              "rows_limit": meta["max_rows"],
                              "truncated": meta["truncated"],
                              "date_range": f"{meta['first_date']}..{meta['last_date']}",
                              "filters": {key: services.compact(value)
                                          for key, value in sorted(meta["scope"].items())},
                          })
        response = HttpResponse(services.export_stream(meta),
                                content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="{EXPORT_FILENAME}"'
        response["X-Export-Rows"] = str(meta["exported_rows"])
        response["X-Export-Matched"] = str(meta["matched_rows"])
        response["X-Export-Truncated"] = "true" if meta["truncated"] else "false"
        response["X-Export-Limit"] = str(meta["max_rows"])
        response["Cache-Control"] = "no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response
