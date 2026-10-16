"""Saved-analysis routes. Mount under ``analyses/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.climate import views

urlpatterns = [
    path("", views.SavedAnalysisListView.as_view(), name="analyses-list"),
    path("<str:analysis_id>", views.SavedAnalysisDetailView.as_view(), name="analyses-detail"),
]
