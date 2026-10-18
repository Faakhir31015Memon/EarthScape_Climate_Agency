"""Datasets routing (mounted at ``/api/v1/datasets/``).

Literal collection paths (``upload``, ``column-suggestion``, ``formats``) are
declared before the ``<dataset_id>`` catch-all so they are never mistaken for an id.
"""
from django.urls import path

from apps.datasets import views

urlpatterns = [
    path("", views.DatasetListView.as_view(), name="datasets-list"),
    path("upload", views.DatasetUploadView.as_view(), name="datasets-upload"),
    path("column-suggestion", views.ColumnSuggestionView.as_view(), name="datasets-column-suggestion"),
    path("formats", views.DatasetFormatsView.as_view(), name="datasets-formats"),
    path("<str:dataset_id>", views.DatasetDetailView.as_view(), name="dataset-detail"),
    path("<str:dataset_id>/validate", views.DatasetValidateView.as_view(), name="dataset-validate"),
    path("<str:dataset_id>/preview", views.DatasetPreviewView.as_view(), name="dataset-preview"),
    path("<str:dataset_id>/ingest", views.DatasetIngestView.as_view(), name="dataset-ingest"),
    path("<str:dataset_id>/mapping", views.DatasetMappingView.as_view(), name="dataset-mapping"),
    path("<str:dataset_id>/download", views.DatasetDownloadView.as_view(), name="dataset-download"),
]
