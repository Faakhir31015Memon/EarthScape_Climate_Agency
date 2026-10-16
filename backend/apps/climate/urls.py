"""Climate analytics routes. Mount under ``climate/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.climate import views

urlpatterns = [
    path("observations", views.ObservationListView.as_view(), name="climate-observations"),
    path("observations/<str:observation_id>", views.ObservationDetailView.as_view(),
         name="climate-observation-detail"),
    path("trends", views.TrendsView.as_view(), name="climate-trends"),
    path("regions", views.RegionsView.as_view(), name="climate-regions"),
    path("map-data", views.MapDataView.as_view(), name="climate-map-data"),
    path("correlations", views.CorrelationsView.as_view(), name="climate-correlations"),
    path("quality", views.QualityView.as_view(), name="climate-quality"),
    path("variables", views.VariableAvailabilityView.as_view(), name="climate-variables"),
    path("anomalies/summary", views.AnomalySummaryView.as_view(), name="climate-anomalies-summary"),
    path("analytics/summaries", views.AnalyticsSummaryListView.as_view(),
         name="climate-analytics-summaries"),
]
