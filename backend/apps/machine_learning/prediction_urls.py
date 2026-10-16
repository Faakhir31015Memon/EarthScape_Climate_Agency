"""Prediction endpoints, mounted at /api/v1/predictions/."""

from django.urls import path

from apps.machine_learning import views

urlpatterns = [
    path("", views.PredictionListView.as_view()),
    path("forecast", views.ForecastSubmitView.as_view()),
    path("latest", views.PredictionLatestView.as_view()),
    path("<str:prediction_id>", views.PredictionDetailView.as_view()),
]
