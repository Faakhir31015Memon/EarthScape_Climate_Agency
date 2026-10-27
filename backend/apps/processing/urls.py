from __future__ import annotations

from django.urls import path

from apps.processing import runner  # noqa: F401 - importing registers the tasks
from apps.processing import views

urlpatterns = [
    path("workflows", views.WorkflowListView.as_view(), name="processing-workflows"),
    path("engines", views.EngineHealthView.as_view(), name="processing-engines"),
    path("data-lake", views.DataLakeView.as_view(), name="processing-data-lake"),
    path("monitor", views.JobMonitorView.as_view(), name="processing-monitor"),
    path("", views.JobListView.as_view(), name="processing-jobs"),
    path("<str:job_id>/cancel", views.JobCancelView.as_view(), name="processing-job-cancel"),
    path("<str:job_id>/logs", views.JobLogsView.as_view(), name="processing-job-logs"),
    path("<str:job_id>", views.JobDetailView.as_view(), name="processing-job-detail"),
]
