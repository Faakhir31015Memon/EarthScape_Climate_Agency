"""Ingestion routing (mounted at ``/api/v1/ingestion/``)."""
from django.urls import path

from apps.ingestion import scheduler, views

scheduler.start_if_enabled()

urlpatterns = [
    path("jobs", views.JobsView.as_view(), name="ingestion-jobs"),
    path("jobs/<str:job_id>", views.JobDetailView.as_view(), name="ingestion-job-detail"),
    path("jobs/<str:job_id>/retry", views.JobRetryView.as_view(), name="ingestion-job-retry"),
    path("jobs/<str:job_id>/cancel", views.JobCancelView.as_view(), name="ingestion-job-cancel"),
    path("connectors", views.ConnectorsView.as_view(), name="ingestion-connectors"),
    path("connectors/<str:name>/test", views.ConnectorTestView.as_view(), name="ingestion-connector-test"),
    path("coverage", views.CoverageView.as_view(), name="ingestion-coverage"),
]
