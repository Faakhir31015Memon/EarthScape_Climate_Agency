from django.urls import path

from apps.alerts import monitor, views

# Arming the scheduler at URL-import time is deliberate: ALERT_SCHEDULER_ENABLED
# (default true) starts one daemon thread per process. Set it false, or
# ALERT_EVALUATION_SECONDS=0, to only run the engine from
# POST /api/v1/alerts/rules/evaluate.
monitor.ensure_started()

urlpatterns = [
    path("", views.AlertListView.as_view(), name="alerts-list"),
    path("summary", views.AlertSummaryView.as_view(), name="alerts-summary"),
    path("templates", views.RuleTemplateView.as_view(), name="alert-templates"),
    # 'scan' is the web client's name for the same pass as 'rules/evaluate'.
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
