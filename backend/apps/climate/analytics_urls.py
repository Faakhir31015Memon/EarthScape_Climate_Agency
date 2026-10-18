"""Analytics routing (mounted at ``/api/v1/analytics/``)."""
from django.urls import path

from apps.climate import analytics_views

urlpatterns = [
    path("summaries", analytics_views.SummaryListView.as_view(), name="analytics-summaries"),
    path("summaries/<str:key>", analytics_views.SummaryDetailView.as_view(),
         name="analytics-summary-detail"),
    path("recompute", analytics_views.RecomputeView.as_view(), name="analytics-recompute"),
    path("trends-slope", analytics_views.TrendSlopeView.as_view(), name="analytics-trends-slope"),
    path("matrix", analytics_views.AnalyticsMatrixView.as_view(), name="analytics-matrix"),
]
