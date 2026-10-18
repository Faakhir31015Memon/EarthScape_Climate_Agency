from django.urls import path

from apps.machine_learning import views

urlpatterns = [
    path("", views.ModelListView.as_view(), name="model-list"),
    path("train", views.TrainView.as_view(), name="model-train"),
    path("catalogue", views.ModelCatalogueView.as_view(), name="model-catalogue"),
    path("training-status", views.TrainingStatusView.as_view(), name="model-training-status"),
    path("<str:model_id>/activate", views.ActivateModelView.as_view(), name="model-activate"),
    path("<str:model_id>/retire", views.RetireModelView.as_view(), name="model-retire"),
    path("<str:model_id>/retrain", views.RetrainModelView.as_view(), name="model-retrain"),
    path("<str:model_id>/artifacts/<str:kind>", views.ModelArtifactView.as_view(),
         name="model-artifact"),
    path("<str:model_id>", views.ModelDetailView.as_view(), name="model-detail"),
]
