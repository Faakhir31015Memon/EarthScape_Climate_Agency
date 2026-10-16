"""Health, readiness and service-status endpoints.

`health` is a shallow liveness probe. `ready` verifies the database and data lake and
returns 503 when a required dependency is missing, so an orchestrator can act on it.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import AllowAny

from apps.monitoring import probe
from common.responses import ok
from common.views import ApiView, PublicView


class HealthView(PublicView):
    """Liveness only - never touches the database, always fast."""

    http_method_names = ["get"]

    def get(self, request):
        return ok({"status": "alive", "service": "earthscape-api", "version": "1.0.0"}, request=request)


class ReadinessView(PublicView):
    http_method_names = ["get"]

    def get(self, request):
        data = probe.snapshot()
        database = data["database"]
        ready = bool(database.get("reachable")) and all(data["data_lake"]["layers_present"].values())
        payload = {
            "status": "ready" if ready else "not_ready",
            "checks": {
                "database": {"ok": bool(database.get("reachable")),
                             "detail": "connected" if database.get("reachable")
                                       else database.get("error", "unreachable")},
                "data_lake": {"ok": all(data["data_lake"]["layers_present"].values()),
                              "missing_layers": [k for k, v in data["data_lake"]["layers_present"].items()
                                                 if not v],
                              "engine": data["data_lake"]["engine"]},
            },
            "observations": data["collection_counts"].get("climate_observations", 0),
        }
        return ok(payload, http_status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
                  request=request)


class ServiceStatusView(ApiView):
    """Full dependency picture for the administrator system-health page."""

    required_capability = "ops.logs"
    http_method_names = ["get"]

    def get(self, request):
        return ok(probe.snapshot(), request=request)
