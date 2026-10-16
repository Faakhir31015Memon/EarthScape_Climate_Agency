"""Dataset endpoints: catalogue, metadata, upload, validate-preview, commit."""

from apps.datasets import views
from django.urls import path

urlpatterns = [
    path("", views.DatasetListView.as_view()),
    path("regions", views.DatasetRegionsView.as_view()),
    path("upload", views.DatasetUploadView.as_view()),
    path("upload/validate", views.DatasetValidateView.as_view()),
    path("upload/commit", views.DatasetCommitView.as_view()),
    path("<str:dataset_id>", views.DatasetDetailView.as_view()),
]
