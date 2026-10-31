"""Forecast endpoints. A horizon over FORECAST_MAX_HORIZON_DAYS is rejected and an unknown
model returns 404."""
from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response

from apps.machine_learning import services
from apps.machine_learning.views import MLView
from common import audit
from common.api import ApiError
from common.views import int_param, str_param


class PredictionCollectionView(MLView):
    def get(self, request):
        return Response({
            "items": services.list_predictions(
                model_id=str_param(request, "model_id"),
                target_variable=str_param(request, "target_variable"),
                region_code=str_param(request, "region_code"),
                location_code=str_param(request, "location_code"),
                limit=int_param(request, "limit", 50, minimum=1, maximum=500),
            ),
            "note": "Each forecast carries the metrics measured against the observations that overlap it. "
                    "It is a projection of day-to-day weather, not a climate trend, and not a guarantee.",
        })

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        outcome = services.create_prediction(str(getattr(request.user, "id", "anonymous")), body)
        audit.for_request(request, action="prediction.generated", resource_type="predictions",
                          resource_id=str(outcome.get("id")),
                          metadata={"model_id": body.get("model_id"),
                                    "horizon_days": body.get("horizon_days"),
                                    "scope": body.get("scope")})
        return Response(outcome, status=status.HTTP_201_CREATED)


class PredictionDetailView(MLView):
    def get(self, request, prediction_id: str):
        return Response(services.get_prediction(prediction_id))

    def delete(self, request, prediction_id: str):
        outcome = services.delete_prediction(prediction_id, request.user)
        audit.for_request(request, action="prediction.deleted", resource_type="predictions",
                          resource_id=prediction_id)
        return Response(outcome)


class PredictionExportView(MLView):
    def get(self, request, prediction_id: str):
        csv_text = services.export_prediction_csv(prediction_id)
        from django.http import HttpResponse

        return HttpResponse(csv_text, content_type="text/csv",
                            headers={"Content-Disposition":
                                     f'attachment; filename="forecast-{prediction_id}.csv"'})


class PredictionExplainView(MLView):
    def get(self, request):
        model_id = str_param(request, "model_id")
        if not model_id:
            raise ApiError("validation_failed", "model_id query parameter is required.")
        scope = {"region_code": str_param(request, "region_code"),
                 "location_code": str_param(request, "location_code")}
        if not scope["region_code"] and not scope["location_code"]:
            raise ApiError("validation_failed", "Provide region_code or location_code.")
        return Response(services.explain(model_id, scope))


class ForecastSummaryView(MLView):
    """Dashboard helper: the latest stored forecast for a scope (read-only)."""

    def get(self, request):
        return Response(services.forecast_summary_for_scope(
            region_code=str_param(request, "region_code"),
            location_code=str_param(request, "location_code"),
            target_variable=str_param(request, "target_variable"),
        ))


__all__ = ["PredictionCollectionView", "PredictionDetailView", "PredictionExportView",
           "PredictionExplainView", "ForecastSummaryView"]
