"""URLs for in-app notifications, mounted at ``/api/v1/notifications/``."""
from django.urls import path

from apps.alerts import notification_views as views

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notifications-list"),
    path("unread-count", views.UnreadCountView.as_view(), name="notifications-unread-count"),
    path("read-all", views.ReadAllView.as_view(), name="notifications-read-all"),
    path("preferences", views.NotificationPreferencesView.as_view(),
         name="notification-preferences"),
    path("<str:notification_id>", views.NotificationDetailView.as_view(),
         name="notification-detail"),
    path("<str:notification_id>/read", views.NotificationReadView.as_view(),
         name="notification-read"),
    path("<str:notification_id>/acknowledge", views.NotificationAcknowledgeView.as_view(),
         name="notification-acknowledge"),
]
