"""Feedback routes, mounted by config/urls.py at /api/v1/feedback/."""

from __future__ import annotations

from django.urls import path

from apps.support import views

urlpatterns = [
    path("", views.FeedbackView.as_view(), name="feedback"),
    path("admin", views.FeedbackAdminView.as_view(), name="feedback-admin"),
]
