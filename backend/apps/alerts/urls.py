"""Alert rule + event routes. Mount under ``alerts/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.alerts import views

urlpatterns = [
    path("", views.AlertEventListView.as_view(), name="alert-events-root"),
    path("rules", views.AlertRuleListView.as_view(), name="alert-rules"),
    path("rules/<str:rule_id>", views.AlertRuleDetailView.as_view(), name="alert-rule-detail"),
    path("rules/<str:rule_id>/evaluate", views.AlertRuleEvaluateView.as_view(),
         name="alert-rule-evaluate"),
    path("evaluate", views.AlertEvaluationSweepView.as_view(), name="alert-evaluate-all"),
    path("events", views.AlertEventListView.as_view(), name="alert-events"),
    path("events/<str:event_id>", views.AlertEventDetailView.as_view(), name="alert-event-detail"),
    path("events/<str:event_id>/acknowledge", views.AlertEventAcknowledgeView.as_view(),
         name="alert-event-acknowledge"),
    path("events/<str:event_id>/resolve", views.AlertEventResolveView.as_view(),
         name="alert-event-resolve"),
    path("events/<str:event_id>/reopen", views.AlertEventReopenView.as_view(),
         name="alert-event-reopen"),
    path("summary", views.AlertSummaryView.as_view(), name="alert-summary"),
]
