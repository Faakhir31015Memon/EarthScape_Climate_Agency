from django.urls import path

from apps.climate import dashboard_views

urlpatterns = [
    path("summary", dashboard_views.DashboardSummaryView.as_view(), name="dashboard-summary"),
    path("activity", dashboard_views.DashboardActivityView.as_view(), name="dashboard-activity"),
    path("widgets", dashboard_views.DashboardWidgetsView.as_view(), name="dashboard-widgets"),
]
