from __future__ import annotations

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.monitoring import views as monitoring_views

urlpatterns = [
    path("api/v1/auth/", include("apps.authentication.urls")),
    path("api/v1/users/", include("apps.users.urls")),
    path("api/v1/datasets/", include("apps.datasets.urls")),
    path("api/v1/ingestion/", include("apps.ingestion.urls")),
    path("api/v1/climate/", include("apps.climate.urls")),
    path("api/v1/processing/", include("apps.processing.urls")),
    path("api/v1/models/", include("apps.machine_learning.urls")),
    path("api/v1/predictions/", include("apps.machine_learning.prediction_urls")),
    path("api/v1/alerts/", include("apps.alerts.urls")),
    path("api/v1/notifications/", include("apps.alerts.notification_urls")),
    path("api/v1/reports/", include("apps.reports.urls")),
    path("api/v1/support/", include("apps.support.urls")),
    path("api/v1/feedback/", include("apps.support.feedback_urls")),
    path("api/v1/dashboard/", include("apps.climate.dashboard_urls")),
    path("api/v1/analytics/", include("apps.climate.analytics_urls")),

    path("api/v1/health", monitoring_views.HealthView.as_view()),
    path("api/v1/ready", monitoring_views.ReadinessView.as_view()),
    path("api/v1/system/", include("apps.monitoring.urls")),

    path("api/schema", SpectacularAPIView.as_view(), name="openapi-schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="openapi-schema"), name="api-docs"),
    path("api/", monitoring_views.ApiIndexView.as_view()),
    path("", monitoring_views.ApiIndexView.as_view()),
]
