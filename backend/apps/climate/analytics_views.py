"""Analytics endpoints (``/api/v1/analytics/``).

Exposes the aggregates stored in ``analytics_summaries`` - whether they were
produced by the MapReduce/PySpark workflows or recomputed here from MongoDB - plus
the trend-slope and one-call matrix helpers the analytics page needs.
"""
from __future__ import annotations

import logging

from rest_framework.response import Response

from apps.climate import services
from apps.climate.serializers import (
    FREQUENCY_CHOICES,
    GROUP_BY_CHOICES,
    RecomputeSerializer,
    metric_or_400,
)
from common import analytics, audit
from common.api import AdministratorOrAnalyst, MongoJWTAuthentication
from common.views import (
    AuthenticatedView,
    PublicView,
    int_param,
    list_param,
    str_param,
)

logger = logging.getLogger(__name__)


class SummaryListView(PublicView):
    """Stored aggregates, newest first."""

    def get(self, request):
        payload = services.list_summaries(
            metric=str_param(request, "metric"),
            workflow=str_param(request, "workflow"),
            processing_mode=str_param(request, "processing_mode"),
            prefix=str_param(request, "key_prefix"),
            limit=int_param(request, "limit", 50, minimum=1, maximum=200),
        )
        return Response(payload)


class SummaryDetailView(PublicView):
    """One aggregate by its ``summary_key``."""

    def get(self, request, key: str):
        return Response(services.summary_by_key(key))


class RecomputeView(AuthenticatedView):
    """Recompute the core aggregates from MongoDB and store them (analyst/admin)."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request):
        data = services.validated_body(RecomputeSerializer(data=request.data or {}))
        start = data["start"].isoformat() if data.get("start") else None
        end = data["end"].isoformat() if data.get("end") else None
        result = services.recompute_core_aggregates(
            start=start,
            end=end,
            metric=data.get("metric") or None,
            limit=data.get("limit_rows") or services.ANALYTICS_ROW_LIMIT,
            processing_mode=data.get("processing_mode") or "mongodb",
        )
        audit.for_request(request, action="analytics.aggregates_recomputed",
                          resource_type="analytics_summary",
                          metadata={
                              "stored_count": result["stored_count"],
                              "skipped_count": result["skipped_count"],
                              "observations_in_scope": result["observations_in_scope"],
                              "duration_seconds": result["duration_seconds"],
                              "processing_mode": result["processing_mode"],
                          })
        return Response(result)


class TrendSlopeView(PublicView):
    """``analytics.trend_slope`` per region/location with base-period deltas."""

    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        start, end = services.date_filter(request, "start", "end")
        base_from = str_param(request, "base_from") or str_param(request, "base_start")
        base_to = str_param(request, "base_to") or str_param(request, "base_end")
        payload = services.trend_slope_report(
            metric=metric,
            group_by=str_param(request, "group_by", "region", choices=GROUP_BY_CHOICES) or "region",
            frequency=str_param(request, "frequency", "annual", choices=FREQUENCY_CHOICES)
            or "annual",
            regions_filter=services.regions_list(request),
            location_codes=list_param(request, "location_codes")[:200],
            start=start,
            end=end,
            base_from=base_from,
            base_to=base_to,
        )
        return Response(payload)


class AnalyticsMatrixView(PublicView):
    """Correlation + regional comparison + trend in one call for the analytics page."""

    def get(self, request):
        metric = metric_or_400(str_param(request, "metric", "temperature_c"))
        comparison = str_param(request, "comparison_metric")
        start, end = services.date_filter(request, "start", "end")
        payload = services.analytics_matrix(
            metric=metric,
            comparison_metric=metric_or_400(comparison) if comparison else None,
            correlation_metrics=[item for item in list_param(request, "metrics")
                                 if item in analytics.METRICS] or None,
            frequency=str_param(request, "frequency", "monthly", choices=FREQUENCY_CHOICES)
            or "monthly",
            regions_filter=services.regions_list(request),
            start=start,
            end=end,
        )
        return Response(payload)
