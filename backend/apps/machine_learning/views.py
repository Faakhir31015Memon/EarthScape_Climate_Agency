"""Model-management endpoints mounted at /api/v1/models/.

Roles: every authenticated role may read models; climate analysts and
administrators may train and retrain; only administrators may activate, retire,
patch or delete. Viewers are strictly read-only.
"""
from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from apps.machine_learning import services
from common import audit, storage_paths
from common.api import (ADMINISTRATOR, AdministratorOrAnalyst, ApiError, IsAdministrator,
                        MongoJWTAuthentication, record_denied)
from common.views import AuthenticatedView, int_param, str_param


class MLView(AuthenticatedView):
    """Authenticated base for this app: platform convention is that every view
    declares ``authentication_classes`` explicitly (``common.views.BaseView``
    keeps the list empty)."""

    authentication_classes = [MongoJWTAuthentication]


class ModelListView(MLView):
    """GET /api/v1/models/ - paginated registry list; POST also accepted here."""

    def get(self, request):
        result = services.list_models(
            status=str_param(request, "status", None,
                             choices=("training", "candidate", "active", "retired", "failed")),
            target_variable=str_param(request, "target_variable"),
            algorithm=str_param(request, "algorithm", None,
                                choices=("ridge", "random_forest", "xgboost")),
            page=int_param(request, "page", 1, minimum=1, maximum=100000),
            page_size=int_param(request, "page_size", 25, minimum=1, maximum=200),
        )
        return Response(result)

    def post(self, request):
        return _train(request)


class TrainView(MLView):
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request):
        return _train(request)


def _train(request):
    if getattr(request.user, "role", None) == "viewer":
        raise ApiError("permission_denied",
                       "Viewers are read-only on models; training requires climate analyst "
                       "or administrator role.", status=403)
    body = request.data if isinstance(request.data, dict) else {}
    outcome = services.train_model(str(getattr(request.user, "id", "anonymous")), body)
    audit.for_request(request, action="model.training_requested", resource_type="model_registry",
                      resource_id=",".join(outcome.get("model_ids", [])),
                      metadata={"target_variable": body.get("target_variable"),
                                "algorithms": body.get("algorithms")})
    return Response(outcome, status=status.HTTP_202_ACCEPTED)


class ModelCatalogueView(MLView):
    def get(self, request):
        return Response(services.catalogue())


class TrainingStatusView(MLView):
    def get(self, request):
        return Response(services.training_status())


class ModelDetailView(MLView):
    def get(self, request, model_id: str):
        return Response(services.get_model(model_id))

    def patch(self, request, model_id: str):
        if getattr(request.user, "role", None) != ADMINISTRATOR:
            raise ApiError("permission_denied", "Only administrators may modify models.", status=403)
        body = request.data if isinstance(request.data, dict) else {}
        outcome = services.patch_model(model_id, body)
        audit.for_request(request, action="model.updated", resource_type="model_registry",
                          resource_id=model_id, metadata={"keys": sorted(body.keys())})
        return Response(outcome)

    def delete(self, request, model_id: str):
        if getattr(request.user, "role", None) != ADMINISTRATOR:
            raise ApiError("permission_denied", "Only administrators may delete models.", status=403)
        outcome = services.delete_model(model_id)
        audit.for_request(request, action="model.deleted", resource_type="model_registry",
                          resource_id=model_id)
        return Response(outcome)


class ActivateModelView(MLView):
    permission_classes = [IsAdministrator]

    def post(self, request, model_id: str):
        outcome = services.activate_model(model_id)
        audit.for_request(request, action="model.activated", resource_type="model_registry",
                          resource_id=model_id)
        return Response(outcome)


class RetireModelView(MLView):
    permission_classes = [IsAdministrator]

    def post(self, request, model_id: str):
        outcome = services.retire_model(model_id)
        audit.for_request(request, action="model.retired", resource_type="model_registry",
                          resource_id=model_id)
        return Response(outcome)


class RetrainModelView(MLView):
    permission_classes = [AdministratorOrAnalyst]

    def post(self, request, model_id: str):
        outcome = services.retrain_model(model_id, str(getattr(request.user, "id", "anonymous")))
        audit.for_request(request, action="model.retrain_requested", resource_type="model_registry",
                          resource_id=model_id)
        return Response(outcome, status=status.HTTP_202_ACCEPTED)


class ModelArtifactView(MLView):
    """GET /api/v1/models/<id>/artifacts/<kind>.

    kind = report (markdown), metrics (JSON metrics), features (feature
    definition JSON) for any authenticated role; kind = model (the raw joblib
    artifact) requires administrator.
    """

    def get(self, request, model_id: str, kind: str):
        document = services.get_model(model_id)
        is_admin = getattr(request.user, "role", None) == ADMINISTRATOR
        if kind == "model":
            if not is_admin:
                record_denied(request, self, "raw model artifact requested by non-admin")
                raise ApiError("permission_denied",
                               "The raw model artifact is administrator-only.", status=403)
            uri = document.get("artifact_uri")
            return _file_response(uri, "application/octet-stream",
                                  f"{document.get('name')}-v{document.get('version')}.joblib")
        if kind in ("features", "feature_definition"):
            payload = document.get("feature_definition")
            if not payload:
                raise ApiError("not_found", "This model has no stored feature definition.", status=404)
            return Response(payload)
        if kind == "metrics":
            return Response({
                "model_id": document.get("id"),
                "name": document.get("name"),
                "version": document.get("version"),
                "evaluation_metrics": document.get("evaluation_metrics"),
                "feature_importance": document.get("feature_importance"),
                "uncertainty_method": document.get("uncertainty_method"),
                "train_size": document.get("train_size"),
                "test_size": document.get("test_size"),
                "dataset_ids": document.get("dataset_ids"),
            })
        if kind == "report":
            metrics = document.get("evaluation_metrics") or {}
            if not metrics:
                raise ApiError("not_found", "No metrics report exists for this model yet.", status=404)
            from ml.evaluation.report import reports_dir

            path = reports_dir() / f"{document.get('name')}-v{document.get('version')}.md"
            if not path.is_file():
                raise ApiError("not_found", "The report file is missing from the artifact store.", status=404)
            return _file_response(str(path), "text/markdown", path.name)
        raise ApiError("validation_failed",
                       "kind must be one of: report, metrics, features, model.")


def _file_response(uri: str, content_type: str, filename: str):
    from django.http import FileResponse

    path = storage_paths.stored_path(uri)
    if path is None or not path.is_file():
        raise ApiError("not_found", "The artifact file is missing from the model store.", status=404)
    response = FileResponse(open(path, "rb"), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


__all__ = ["ModelListView", "TrainView", "ModelCatalogueView", "TrainingStatusView",
           "ModelDetailView", "ActivateModelView", "RetireModelView", "RetrainModelView",
           "ModelArtifactView"]
