"""Support ticket routes. Mount under ``support/`` (see ``config/urls.py``)."""

from __future__ import annotations

from django.urls import path

from apps.support import views

urlpatterns = [
    path("", views.TicketListView.as_view(), name="support-tickets"),
    path("queue", views.TicketQueueView.as_view(), name="support-queue"),
    path("tickets", views.TicketListView.as_view(), name="support-tickets-list"),
    path("tickets/<str:ticket_id>", views.TicketDetailView.as_view(), name="support-ticket-detail"),
    path("tickets/<str:ticket_id>/messages", views.TicketMessageView.as_view(),
         name="support-ticket-messages"),
]
