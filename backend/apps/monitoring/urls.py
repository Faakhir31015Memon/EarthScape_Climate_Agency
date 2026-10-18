"""``/api/v1/system/`` routing.

Health and readiness are mounted directly by :mod:`config.urls` at ``/api/v1/health``
and ``/api/v1/ready``; the JSON API index lives at ``/api/`` and ``/``. This module
carries every other operational surface.
"""
from __future__ import annotations

from django.urls import path

from apps.monitoring import views

urlpatterns = [
    path("status", views.SystemStatusView.as_view(), name="system-status"),
    path("metrics", views.SystemMetricsView.as_view(), name="system-metrics"),
    path("endpoints", views.EndpointCatalogView.as_view(), name="system-endpoints"),
    path("audit", views.AuditLogView.as_view(), name="system-audit"),
    path("settings", views.SettingsView.as_view(), name="system-settings"),
    path("backups", views.BackupsView.as_view(), name="system-backups"),
    path("dependencies", views.DependenciesView.as_view(), name="system-dependencies"),
    path("services", views.ServicesView.as_view(), name="system-services"),
]
