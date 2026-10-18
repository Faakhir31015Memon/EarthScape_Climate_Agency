"""``/api/v1/reports/`` routing.

``APPEND_SLASH`` is false, so the collection route is the empty path and every other
literal segment is registered before the ``<report_id>`` catch-all.
"""
from __future__ import annotations

from django.urls import path

from apps.reports import views

urlpatterns = [
    path("", views.ReportCollectionView.as_view(), name="report-collection"),
    path("types", views.ReportTypeListView.as_view(), name="report-types"),
    path("summary", views.ReportSummaryView.as_view(), name="report-summary"),
    path("<str:report_id>/download", views.ReportDownloadView.as_view(),
         name="report-download"),
    path("<str:report_id>/cancel", views.ReportCancelView.as_view(), name="report-cancel"),
    path("<str:report_id>", views.ReportDetailView.as_view(), name="report-detail"),
]
