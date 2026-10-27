from __future__ import annotations

from django.urls import path

from apps.monitoring import views

urlpatterns = [
    path("status", views.SystemStatusView.as_view(), name="system-status"),
    path("metrics", views.SystemMetricsView.as_view(), name="system-metrics"),
    path("endpoints", views.EndpointCatalogView.as_view(), name="system-endpoints"),
    path("backups", views.BackupsView.as_view(), name="system-backups"),
    path("dependencies", views.DependenciesView.as_view(), name="system-dependencies"),
    path("services", views.ServicesView.as_view(), name="system-services"),
]
