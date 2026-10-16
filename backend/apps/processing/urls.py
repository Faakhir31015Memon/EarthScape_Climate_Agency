"""Processing workspace routes, mounted by the project at ``/api/v1/processing/``."""

from __future__ import annotations

from apps.processing import views
from django.urls import path

urlpatterns = [
    path("workflows", views.WorkflowListView.as_view(), name="processing-workflows"),
    path("workflows/<str:workflow_id>", views.WorkflowDetailView.as_view(),
         name="processing-workflow-detail"),
    path("jobs", views.JobListView.as_view(), name="processing-jobs"),
    path("jobs/<str:job_id>", views.JobDetailView.as_view(), name="processing-job-detail"),
    path("jobs/<str:job_id>/retry", views.JobRetryView.as_view(), name="processing-job-retry"),
    path("summaries", views.SummaryListView.as_view(), name="processing-summaries"),
    path("mapreduce", views.MapReduceCatalogueView.as_view(), name="processing-mapreduce"),
    path("stream/status", views.StreamStatusView.as_view(), name="processing-stream-status"),
    path("sweep", views.StaleJobSweepView.as_view(), name="processing-sweep"),
]
