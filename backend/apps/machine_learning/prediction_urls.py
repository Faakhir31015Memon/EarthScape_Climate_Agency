from django.urls import path

from apps.machine_learning import prediction_views

urlpatterns = [
    path("", prediction_views.PredictionCollectionView.as_view(), name="prediction-collection"),
    path("explain", prediction_views.PredictionExplainView.as_view(), name="prediction-explain"),
    path("summary", prediction_views.ForecastSummaryView.as_view(), name="prediction-summary"),
    path("<str:prediction_id>/export", prediction_views.PredictionExportView.as_view(),
         name="prediction-export"),
    path("<str:prediction_id>", prediction_views.PredictionDetailView.as_view(),
         name="prediction-detail"),
]
