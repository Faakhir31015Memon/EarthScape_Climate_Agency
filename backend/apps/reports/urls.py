"""Report routes. Mount under ``reports/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.reports import views

urlpatterns = [
    path("", views.ReportListView.as_view(), name="reports-list"),
    path("<str:report_id>", views.ReportDetailView.as_view(), name="report-detail"),
    path("<str:report_id>/download/<str:fmt>", views.ReportDownloadView.as_view(),
         name="report-download"),
]
