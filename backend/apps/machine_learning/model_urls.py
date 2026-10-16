"""Model registry endpoints, mounted at /api/v1/models/."""

from django.urls import path

from apps.machine_learning import views

urlpatterns = [
    path("", views.ModelRegistryListView.as_view()),
    path("capabilities", views.ModelCapabilitiesView.as_view()),
    path("available-data", views.ModelAvailabilityView.as_view()),
    path("train", views.ModelTrainView.as_view()),
    path("sync", views.ModelSyncView.as_view()),
    path("jobs", views.ModelJobListView.as_view()),
    path("jobs/<str:job_id>", views.ModelJobDetailView.as_view()),
    path("<str:model_ref>/evaluate", views.ModelEvaluateView.as_view()),
    path("<str:model_ref>/promote", views.ModelPromoteView.as_view()),
    path("<str:model_ref>", views.ModelDetailView.as_view()),
]
