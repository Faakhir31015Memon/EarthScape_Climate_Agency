"""Alert rules, fired alerts and the evaluation trigger endpoints.

Mounted at ``/api/v1/alerts/``. Mutations are administrator/analyst only and every
one is audited; a viewer can read rules without seeing who is notified.
"""
from __future__ import annotations

import logging

from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import monitor, notifications, rules as engine, serializers as alert_serializers  # noqa: E402
from common import analytics, audit  # noqa: E402
from common.api import (  # noqa: E402
    ADMINISTRATOR,
    CLIMATE_ANALYST,
    ApiError,
    AdministratorOrAnalyst,
    IsAuthenticatedAccount,
    MongoJWTAuthentication,
)
from common.repositories import RepositoryError  # noqa: E402
from common.views import (  # noqa: E402
    AuthenticatedView,
    bool_param,
    date_param,
    int_param,
    str_param,
)

logger = logging.getLogger(__name__)

STAFF_ROLES = (ADMINISTRATOR, CLIMATE_ANALYST)


class AlertView(AuthenticatedView):
    """Authenticated base: the JWT authenticator is declared, not inherited empty."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]


class StaffView(AlertView):
    permission_classes = [AdministratorOrAnalyst]


def require_object_id(value: str, label: str) -> str:
    from bson import ObjectId

    if not ObjectId.is_valid(value or ""):
        raise ApiError("not_found", f"No such {label}.", status=404)
    return value


def _is_staff(request) -> bool:
    user = getattr(request, "user", None)
    return bool(user and getattr(user, "is_authenticated", False) and user.role in STAFF_ROLES)


def require_staff(request):
    """Per-method role enforcement on views that are readable by every account."""
    if not _is_staff(request):
        raise ApiError("permission_denied",
                       "Climate analyst or administrator role required.", status=403)
    return request.user


class RuleListView(AlertView):
    """``GET /api/v1/alerts/rules`` (role-aware) / ``POST`` (analyst or administrator)."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)
        # ``enabled`` and ``enabled_only`` are accepted synonyms (the web client uses the
        # shorter name); both mean "only rules that the engine will actually run".
        enabled_only = bool_param(request, "enabled_only", False) or bool_param(
            request, "enabled", False)
        result = engine.list_rules(enabled_only=enabled_only, page=page, page_size=page_size,
                                  include_recipients=_is_staff(request))
        if not _is_staff(request):
            for item in result["items"]:
                item.pop("evaluation_state", None)
                item.pop("created_by", None)
        result["role_view"] = "staff" if _is_staff(request) else "restricted"
        return Response(result)

    def post(self, request):
        user = require_staff(request)
        serializer = alert_serializers.RuleCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rule = engine.create_rule(dict(serializer.validated_data), actor_id=user.id)
        audit.for_request(request, action="alert_rule.created", resource_type="alert_rule",
                         resource_id=str(rule.get("id")), actor_user_id=user.id,
                         metadata={"rule_key": rule.get("rule_key"),
                                   "target_variable": rule.get("target_variable"),
                                   "operator": (rule.get("condition") or {}).get("operator"),
                                   "severity": rule.get("severity")})
        return Response({"rule": rule}, status=201)


class RuleDetailView(AlertView):
    def get(self, request, rule_id: str):
        require_object_id(rule_id, "alert rule")
        rule = engine.get_rule(rule_id)
        if not _is_staff(request):
            rule.pop("recipients", None)
            rule.pop("evaluation_state", None)
            rule.pop("created_by", None)
        return Response({"rule": rule})

    def patch(self, request, rule_id: str):
        require_staff(request)
        require_object_id(rule_id, "alert rule")
        serializer = alert_serializers.RuleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        payload = {key: value for key, value in serializer.validated_data.items()
                   if value is not None or key == "is_enabled"}
        rule = engine.update_rule(rule_id, payload)
        audit.for_request(request, action="alert_rule.updated", resource_type="alert_rule",
                         resource_id=rule_id,
                         metadata={"fields": sorted(payload)})
        return Response({"rule": rule})

    def delete(self, request, rule_id: str):
        require_staff(request)
        require_object_id(rule_id, "alert rule")
        deleted = engine.delete_rule(rule_id)
        audit.for_request(request, action="alert_rule.deleted", resource_type="alert_rule",
                         resource_id=rule_id, metadata={"deleted": deleted})
        return Response({"status": "deleted", "deleted": deleted,
                         "note": "Alerts already fired by this rule are retained."})


class RuleEnableView(StaffView):
    def post(self, request, rule_id: str):
        require_object_id(rule_id, "alert rule")
        rule = engine.set_rule_enabled(rule_id, True)
        audit.for_request(request, action="alert_rule.enabled", resource_type="alert_rule",
                         resource_id=rule_id)
        return Response({"rule": rule})


class RuleDisableView(StaffView):
    def post(self, request, rule_id: str):
        require_object_id(rule_id, "alert rule")
        rule = engine.set_rule_enabled(rule_id, False)
        audit.for_request(request, action="alert_rule.disabled", resource_type="alert_rule",
                         resource_id=rule_id)
        return Response({"rule": rule})


class RuleActiveView(AlertView):
    """``POST /api/v1/alerts/rules/<id>/active`` - one switch for both directions.

    Same engine call as ``enable``/``disable``; kept because the web client toggles a
    single boolean. The role check is performed here because the view accepts either
    direction, so it cannot rely on the URL to express intent.
    """

    def post(self, request, rule_id: str):
        require_staff(request)
        require_object_id(rule_id, "alert rule")
        payload = request.data if isinstance(request.data, dict) else {}
        if "enabled" not in payload and "is_enabled" not in payload:
            raise ApiError("validation_failed", "enabled (true or false) is required.")
        raw = payload.get("enabled", payload.get("is_enabled"))
        enabled = str(raw).strip().lower() in {"1", "true", "yes", "on"} if not isinstance(raw, bool) else raw
        rule = engine.set_rule_enabled(rule_id, bool(enabled))
        audit.for_request(request,
                          action="alert_rule.enabled" if enabled else "alert_rule.disabled",
                          resource_type="alert_rule", resource_id=rule_id,
                          metadata={"enabled": bool(enabled),
                                    "rule_key": rule.get("rule_key")})
        return Response({"rule": rule})


class RuleTemplateView(AlertView):
    """``GET /api/v1/alerts/templates`` - starting payloads for the rule builder."""

    def get(self, request):
        return Response({
            "templates": engine.templates(),
            "operators": list(engine.OPERATORS),
            "severities": list(engine.SEVERITIES),
            "metrics": list(analytics.METRICS),
            "note": "Templates are suggestions; each is validated by the same engine on create.",
        })


class AlertScanView(StaffView):
    """``POST /api/v1/alerts/scan`` - alias of ``rules/evaluate`` for the web client."""

    def post(self, request):
        outcome = monitor.run_once(source="manual")
        audit.for_request(request, action="alert_rules.evaluated", resource_type="alert_rule",
                          metadata={"rules_evaluated": outcome.get("rules_evaluated"),
                                    "alerts_created": outcome.get("alerts_created"),
                                    "trigger": "scan"})
        return Response({
            "job": None,
            "matched": outcome.get("alerts_created", 0),
            "detail": f"{outcome.get('rules_evaluated', 0)} enabled rule(s) evaluated; "
                      f"{outcome.get('alerts_created', 0)} alert(s) created.",
            "evaluation": outcome,
            "scheduler": monitor.status(),
        })


class RuleEvaluateView(StaffView):
    """``POST /api/v1/alerts/rules/evaluate`` - run the engine now, report real counts."""

    def post(self, request):
        outcome = monitor.run_once(source="manual")
        audit.for_request(request, action="alert_rules.evaluated", resource_type="alert_rule",
                         resource_id=None,
                         metadata={"rules_evaluated": outcome.get("rules_evaluated"),
                                   "alerts_created": outcome.get("alerts_created"),
                                   "notifications_queued": outcome.get("notifications_queued"),
                                   "suppressed_by_dedupe": outcome.get("suppressed_by_dedupe")})
        return Response({"evaluation": outcome, "scheduler": monitor.status()})


class RuleTestView(StaffView):
    """``POST /api/v1/alerts/rules/<id>/test`` - dry run; nothing is persisted."""

    def post(self, request, rule_id: str):
        require_object_id(rule_id, "alert rule")
        result = engine.dry_run_rule(rule_id)
        audit.for_request(request, action="alert_rule.tested", resource_type="alert_rule",
                         resource_id=rule_id,
                         metadata={"candidates": result.get("candidate_count",
                                                            len(result.get("candidates", []))),
                                   "in_cooldown": result.get("in_cooldown")})
        return Response({
            "dry_run": True,
            "rule": engine.get_rule(rule_id),
            "candidates": result.get("candidates", []),
            "candidate_count": len(result.get("candidates", [])),
            "would_persist": result.get("would_persist"),
            "in_cooldown": result.get("in_cooldown"),
            "recipients_resolved": result.get("recipients_resolved"),
            "observations_scanned": result.get("observations_scanned"),
            "predictions_scanned": result.get("predictions_scanned"),
            "state": result.get("state"),
            "note": "Nothing was written: this is a dry run of the stored rule.",
        })


class AlertListView(AlertView):
    """``GET /api/v1/alerts/`` - fired alerts (rules live behind ``/rules``)."""

    def get(self, request):
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)
        rule_key = str_param(request, "rule_key", max_length=64)
        rule_id = str_param(request, "rule_id", max_length=24)
        if rule_id and not rule_key:
            # A fired alert stores the rule_key it was created from; resolve the id so
            # clients can filter by either handle.
            try:
                rule_key = (engine.get_rule(rule_id) or {}).get("rule_key")
            except (ApiError, RepositoryError):
                rule_key = None
            if not rule_key:
                raise ApiError("not_found", "No such alert rule.", status=404)
        result = engine.list_alerts(
            status_filter=str_param(request, "status", choices=engine.ALERT_STATUSES),
            severity=str_param(request, "severity", choices=engine.SEVERITIES),
            region=str_param(request, "region", max_length=60)
            or str_param(request, "region_code", max_length=60),
            rule_key=rule_key,
            start_date=date_param(request, "start_date"),
            end_date=date_param(request, "end_date"),
            page=page, page_size=page_size,
        )
        result["unread_notifications"] = notifications.unread_counts(request.user.id)["total"] \
            if getattr(request, "user", None) else None
        return Response(result)


class AlertDetailView(AlertView):
    def get(self, request, alert_id: str):
        require_object_id(alert_id, "alert")
        try:
            return Response({"alert": engine.get_alert(alert_id)})
        except RepositoryError:
            raise ApiError("not_found", "No such alert.", status=404)


class AlertAcknowledgeView(StaffView):
    def post(self, request, alert_id: str):
        require_object_id(alert_id, "alert")
        user = self.current_user(request)
        serializer = alert_serializers.AlertAcknowledgeSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        alert = engine.acknowledge_alert(alert_id, user_id=user.id, role=user.role)
        comment = serializer.validated_data.get("comment")
        if comment:
            from common.repositories import alerts

            alerts.update_by_id(alert_id, {"metadata": {**(alert.get("metadata") or {}),
                                                        "acknowledge_comment": str(comment)[:500]}})
            alert = engine.get_alert(alert_id)["alert"]
        audit.for_request(request, action="alert.acknowledged", resource_type="alert",
                         resource_id=alert_id, actor_user_id=user.id,
                         metadata={"rule_key": alert.get("rule_key"),
                                   "severity": alert.get("severity")})
        return Response({"alert": alert})


class AlertResolveView(StaffView):
    def post(self, request, alert_id: str):
        require_object_id(alert_id, "alert")
        user = self.current_user(request)
        serializer = alert_serializers.AlertResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        status_value = serializer.validated_data.get("status", "resolved")
        alert = engine.resolve_alert(alert_id, user_id=user.id, role=user.role,
                                    note=serializer.validated_data["note"], status=status_value)
        audit.for_request(request, action=f"alert.{status_value}", resource_type="alert",
                         resource_id=alert_id, actor_user_id=user.id,
                         metadata={"rule_key": alert.get("rule_key"),
                                   "severity": alert.get("severity")})
        return Response({"alert": alert})


class AlertSummaryView(AlertView):
    """``GET /api/v1/alerts/summary`` - severity/status/region counts and newest alert."""

    def get(self, request):
        payload = engine.summary()
        payload["notification_delivery"] = notifications.counts()
        payload["email_delivery"] = _email_state()
        return Response(payload)


def _email_state() -> dict:
    from common import emailer

    status = emailer.configuration_status()
    status["note"] = ("Email notifications are recorded as suppressed until SMTP_HOST and "
                      "ENABLE_EMAIL_NOTIFICATIONS are configured.")
    return status
