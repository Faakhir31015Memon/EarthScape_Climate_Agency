from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.monitoring.views import HealthView, ReadinessView, ServiceStatusView

api_v1 = [
    path("auth/", include("apps.authentication.urls")),
    path("users/", include("apps.users.urls")),
    path("datasets/", include("apps.datasets.urls")),
    path("ingestion/", include("apps.ingestion.urls")),
    path("alerts/", include("apps.alerts.urls")),
    path("notifications/", include("apps.alerts.notification_urls")),
    path("health", HealthView.as_view()),
    path("ready", ReadinessView.as_view()),
    path("services/status", ServiceStatusView.as_view()),
]

# Wired as each module lands (phases 4-7 of the build plan):
#   path("dashboard/", include("apps.climate.dashboard_urls")),
#   path("climate/", include("apps.climate.urls")),
#   path("processing/", include("apps.processing.urls")),
#   path("models/", include("apps.machine_learning.model_urls")),
#   path("predictions/", include("apps.machine_learning.prediction_urls")),
#   path("anomaly/", include("apps.machine_learning.anomaly_urls")),
#   path("reports/", include("apps.reports.urls")),
#   path("analyses/", include("apps.climate.saved_urls")),
#   path("support/", include("apps.support.urls")),
#   path("feedback", include("apps.support.feedback_urls")),

urlpatterns = [
    path("api/v1/", include(api_v1)),
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
]
