"""Anomaly detection endpoints, mounted at /api/v1/anomaly/."""

from django.urls import path

from apps.machine_learning import views

urlpatterns = [
    path("", views.AnomalyIndexView.as_view()),
    path("scan", views.AnomalyScanView.as_view()),
    path("events", views.AnomalyEventListView.as_view()),
    path("methods", views.AnomalyMethodsView.as_view()),
    path("baselines", views.AnomalyBaselineView.as_view()),
]
