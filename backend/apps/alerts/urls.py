"""URLs for alert rules and fired alerts, mounted at ``/api/v1/alerts/``."""
from django.urls import path

from apps.alerts import monitor, views

# Loading the API routes is the documented place where the bounded evaluation
# scheduler is armed (``ALERT_SCHEDULER_ENABLED``, default true; set it to false or
# ``ALERT_EVALUATION_SECONDS=0`` to run the engine only through
# ``POST /api/v1/alerts/rules/evaluate``). One daemon thread per process, and its
# real state is exposed by ``GET /api/v1/system/status``.
monitor.ensure_started()

urlpatterns = [
    path("", views.AlertListView.as_view(), name="alerts-list"),
    path("summary", views.AlertSummaryView.as_view(), name="alerts-summary"),
    path("templates", views.RuleTemplateView.as_view(), name="alert-templates"),
    # ``scan`` is the web client's name for the same immediate evaluation as
    # ``rules/evaluate``; both run the engine once and report real counts.
    path("scan", views.AlertScanView.as_view(), name="alerts-scan"),
    path("rules", views.RuleListView.as_view(), name="alert-rules"),
    path("rules/evaluate", views.RuleEvaluateView.as_view(), name="alert-rules-evaluate"),
    path("rules/<str:rule_id>", views.RuleDetailView.as_view(), name="alert-rule-detail"),
    path("rules/<str:rule_id>/enable", views.RuleEnableView.as_view(), name="alert-rule-enable"),
    path("rules/<str:rule_id>/disable", views.RuleDisableView.as_view(), name="alert-rule-disable"),
    path("rules/<str:rule_id>/active", views.RuleActiveView.as_view(), name="alert-rule-active"),
    path("rules/<str:rule_id>/test", views.RuleTestView.as_view(), name="alert-rule-test"),
    path("<str:alert_id>", views.AlertDetailView.as_view(), name="alert-detail"),
    path("<str:alert_id>/acknowledge", views.AlertAcknowledgeView.as_view(),
         name="alert-acknowledge"),
    path("<str:alert_id>/resolve", views.AlertResolveView.as_view(), name="alert-resolve"),
]
