"""Notification inbox routes. Mount under ``notifications/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.alerts import views

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notifications-list"),
    path("unread", views.NotificationUnreadView.as_view(), name="notifications-unread"),
    path("read-all", views.NotificationReadAllView.as_view(), name="notifications-read-all"),
    path("test", views.NotificationTestView.as_view(), name="notifications-test"),
    path("<str:notification_id>", views.NotificationDetailView.as_view(), name="notification-detail"),
    path("<str:notification_id>/retry", views.NotificationRetryView.as_view(),
         name="notification-retry"),
]
