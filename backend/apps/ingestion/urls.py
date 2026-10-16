"""Ingestion endpoints."""

from apps.ingestion import views
from django.urls import path

urlpatterns = [
    path("providers", views.ProviderCatalogueView.as_view()),
    path("status", views.IngestionStatusView.as_view()),
    path("jobs", views.IngestionJobListView.as_view()),
    path("jobs/<str:job_id>", views.IngestionJobDetailView.as_view()),
    path("jobs/<str:job_id>/retry", views.IngestionJobRetryView.as_view()),
]
