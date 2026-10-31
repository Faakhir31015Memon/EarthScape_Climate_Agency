from django.urls import path

from apps.datasets import file_analytics_views

# literal paths first or <analysis_id> swallows them
urlpatterns = [
    path("", file_analytics_views.FileAnalyticsListView.as_view(), name="file-analytics-list"),
    path("upload", file_analytics_views.FileAnalyticsUploadView.as_view(),
         name="file-analytics-upload"),
    path("<str:analysis_id>", file_analytics_views.FileAnalyticsDetailView.as_view(),
         name="file-analytics-detail"),
]
