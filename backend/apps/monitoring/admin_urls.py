"""Administrator routes.

The orchestrator is expected to mount this as
``path("admin/", include("apps.monitoring.admin_urls"))`` inside the ``api_v1`` group,
so the public paths are ``/api/v1/admin/...``.
"""

from __future__ import annotations

from django.urls import path

from apps.monitoring import admin_views

urlpatterns = [
    path("audit-logs", admin_views.AuditLogListView.as_view(), name="admin-audit-logs"),
    path("audit-logs/<str:log_id>", admin_views.AuditLogDetailView.as_view(),
         name="admin-audit-log-detail"),
    path("settings", admin_views.PlatformSettingListView.as_view(), name="admin-settings"),
    path("settings/<str:key>", admin_views.PlatformSettingDetailView.as_view(),
         name="admin-setting-detail"),
    path("metrics", admin_views.OperationsMetricsView.as_view(), name="admin-metrics"),
    path("logs", admin_views.LogTailView.as_view(), name="admin-logs"),
]
