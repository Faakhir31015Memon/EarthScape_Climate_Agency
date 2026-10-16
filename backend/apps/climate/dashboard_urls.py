"""Dashboard routes. Mount under ``dashboard/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.climate import views

urlpatterns = [
    path("summary", views.DashboardSummaryView.as_view(), name="dashboard-summary"),
]
