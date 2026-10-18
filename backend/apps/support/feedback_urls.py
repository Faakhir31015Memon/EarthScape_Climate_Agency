"""``/api/v1/feedback/`` routing.

Mounts the feedback views added to :mod:`apps.support.views`. ``APPEND_SLASH``
is false so the collection route is the empty path.
"""
from __future__ import annotations

from django.urls import path

from apps.support import views

urlpatterns = [
    path("", views.FeedbackListView.as_view(), name="feedback-list"),
    path("summary", views.FeedbackSummaryView.as_view(), name="feedback-summary"),
    path("<str:feedback_id>", views.FeedbackDetailView.as_view(), name="feedback-detail"),
]
