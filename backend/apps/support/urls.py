"""``/api/v1/support/`` routing.

Every handler lives in :mod:`apps.support.views`; this module only maps URL
patterns to the view classes. ``APPEND_SLASH`` is false, so collection routes
use the empty path (relative to the mounted prefix) and identifier catch-alls
are registered after the literal ``summary`` / ``categories`` segments.
"""
from __future__ import annotations

from django.urls import path

from apps.support import views

urlpatterns = [
    path("", views.TicketListView.as_view(), name="support-tickets"),
    path("tickets", views.TicketListView.as_view(), name="support-tickets-alt"),
    path("summary", views.SupportSummaryView.as_view(), name="support-summary"),
    path("categories", views.TicketCategoryListView.as_view(), name="support-categories"),
    path("tickets/<str:ticket_id>", views.TicketDetailView.as_view(), name="support-ticket-detail"),
    path("tickets/<str:ticket_id>/messages", views.TicketCommentView.as_view(),
         name="support-ticket-messages"),
    path("tickets/<str:ticket_id>/comments", views.TicketCommentView.as_view(),
         name="support-ticket-comments"),
    path("tickets/<str:ticket_id>/close", views.TicketCloseView.as_view(),
         name="support-ticket-close"),
]
