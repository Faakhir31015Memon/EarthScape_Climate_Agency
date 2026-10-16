"""HTTP layer for alert rules, alert events and the notification inbox."""

from __future__ import annotations

from apps.alerts import notifier, services
from apps.authentication.permissions import role_allows
from common.audit import audit
from common.db import find_one, now_utc, oid, update_one
from common.exceptions import Forbidden, NotFound
from common.pagination import normalize_pagination
from common.responses import paginated
from common.serialization import document_to_api
from common.validators import AlertRuleRequest, parse
from common.views import ApiView


def _bool_param(value: str | None) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class AlertRuleListView(ApiView):
    required_capability = "alert.rule.manage"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = services.list_rules(
            enabled=params.get("enabled", ""),
            condition=params.get("condition", ""),
            variable=params.get("variable", ""),
            search=params.get("search", ""),
            page=page, page_size=size,
        )
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"policy": "unique dedupe per rule+scope+variable+UTC day, then cooldown"},
                         request=request)

    def post(self, request):
        payload = parse(AlertRuleRequest, self.body(request))
        rule = services.create_rule(payload, request.auth_identity.user_id)
        audit(request, "alert_rule.create", resource_type="alert_rules",
              resource_id=rule["id"], metadata={"condition": rule.get("condition"),
                                                "variable": rule.get("target_variable")})
        return self.created(request, rule)


class AlertRuleDetailView(ApiView):
    required_capability = "alert.rule.manage"

    def get(self, request, rule_id: str):
        return self.ok(request, services.get_rule(rule_id))

    def patch(self, request, rule_id: str):
        payload = parse(AlertRuleRequest, self.body(request))
        rule = services.update_rule(rule_id, payload, request.auth_identity)
        audit(request, "alert_rule.update", resource_type="alert_rules", resource_id=rule_id)
        return self.ok(request, rule)

    def delete(self, request, rule_id: str):
        result = services.delete_rule(rule_id, request.auth_identity)
        audit(request, "alert_rule.delete", resource_type="alert_rules", resource_id=rule_id,
              metadata=result)
        return self.ok(request, result)


class AlertRuleEvaluateView(ApiView):
    required_capability = "alert.rule.manage"
    throttle_scope = "processing"
    http_method_names = ["post"]

    def post(self, request, rule_id: str):
        result = services.evaluate_rule_by_id(rule_id)
        audit(request, "alert_rule.evaluate", resource_type="alert_rules", resource_id=rule_id,
              metadata={"status": result.get("status"),
                        "events_created": len(result.get("events_created", []))})
        return self.ok(request, result)


class AlertEvaluationSweepView(ApiView):
    """Administrator-driven full pass over every enabled rule."""

    required_capability = "alert.rule.admin"
    throttle_scope = "processing"
    http_method_names = ["post"]

    def post(self, request):
        params = self.params(request)
        result = services.evaluate_sweep(include_disabled=_bool_param(params.get("include_disabled")))
        audit(request, "alert.evaluate_all", resource_type="alert_rules",
              metadata={"rules_evaluated": result["rules_evaluated"],
                        "events_created": result["events_created"],
                        "status_counts": result["status_counts"]})
        return self.ok(request, result)


class AlertEventListView(ApiView):
    """Read-only event board; every authenticated role may see raised alerts."""

    required_capability = "platform.view"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = services.list_events(params, page=page, page_size=size)
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"counts": services.event_counts()}, request=request)


class AlertEventDetailView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request, event_id: str):
        return self.ok(request, services.get_event(event_id))


class _EventTransitionView(ApiView):
    required_capability = "alert.acknowledge"
    http_method_names = ["post"]
    action_name = "alert.event.update"

    def _note(self, request) -> str | None:
        body = self.body(request)
        note = body.get("note") or body.get("reason")
        return str(note)[:400] if note else None


class AlertEventAcknowledgeView(_EventTransitionView):
    action_name = "alert.event.acknowledge"

    def post(self, request, event_id: str):
        event = services.acknowledge(event_id, request.auth_identity, note=self._note(request))
        audit(request, self.action_name, resource_type="alert_events", resource_id=event_id,
              metadata={"note": self._note(request)})
        return self.ok(request, event)


class AlertEventResolveView(_EventTransitionView):
    action_name = "alert.event.resolve"

    def post(self, request, event_id: str):
        event = services.resolve(event_id, request.auth_identity, note=self._note(request))
        audit(request, self.action_name, resource_type="alert_events", resource_id=event_id,
              metadata={"note": self._note(request)})
        return self.ok(request, event)


class AlertEventReopenView(_EventTransitionView):
    action_name = "alert.event.reopen"

    def post(self, request, event_id: str):
        event = services.reopen(event_id, request.auth_identity, note=self._note(request))
        audit(request, self.action_name, resource_type="alert_events", resource_id=event_id,
              metadata={"note": self._note(request)})
        return self.ok(request, event)


class AlertSummaryView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, services.event_counts())


class NotificationTestView(ApiView):
    required_capability = "notification.view_own"
    http_method_names = ["post"]

    def post(self, request):
        channel = str(self.body(request).get("channel", "in_app"))
        result = services.test_notification(request.auth_identity, channel=channel)
        audit(request, "notification.test", resource_type="notifications",
              metadata={"channel": channel, "email_enabled": result["email_enabled"]})
        return self.created(request, result)


class NotificationListView(ApiView):
    required_capability = "notification.view_own"

    def get(self, request):
        params = self.params(request)
        page, size, _skip = normalize_pagination(request.query_params)
        items, total = notifier.list_for_user(
            request.auth_identity.user_id,
            unread_only=_bool_param(params.get("unread_only")),
            kind=params.get("kind", ""),
            page=page, page_size=size,
        )
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"unread": notifier.unread_count(request.auth_identity.user_id),
                                "email_enabled": bool(_email_enabled())},
                         request=request)


class NotificationDetailView(ApiView):
    required_capability = "notification.view_own"

    def get(self, request, notification_id: str):
        document = find_one("notifications", {"_id": oid(notification_id)})
        if not document:
            raise NotFound("Notification not found.")
        if str(document.get("recipient")) != str(request.auth_identity.user_id) and not role_allows(
                request.auth_identity.role, "alert.rule.admin"):
            raise Forbidden("You can only read your own notifications.")
        return self.ok(request, document_to_api(document))

    def patch(self, request, notification_id: str):
        body = self.body(request)
        mark = body.get("read", True)
        if str(mark).lower() in ("false", "0", "no"):
            existing = find_one("notifications", {"_id": oid(notification_id)})
            if not existing:
                raise NotFound("Notification not found.")
            if str(existing.get("recipient")) != str(request.auth_identity.user_id):
                raise Forbidden("You can only manage your own notifications.")
            update_one("notifications", {"_id": existing["_id"]},
                       {"$set": {"read_at": None, "acknowledged_at": None, "updated_at": now_utc()}})
            return self.ok(request, document_to_api(find_one("notifications", {"_id": existing["_id"]})))
        return self.ok(request, notifier.mark_read(notification_id, request.auth_identity.user_id))


class NotificationReadAllView(ApiView):
    required_capability = "notification.view_own"
    http_method_names = ["post"]

    def post(self, request):
        marked = notifier.mark_all_read(request.auth_identity.user_id)
        return self.ok(request, {"marked_read": marked})


class NotificationUnreadView(ApiView):
    required_capability = "notification.view_own"
    http_method_names = ["get"]

    def get(self, request):
        return self.ok(request, {"unread": notifier.unread_count(request.auth_identity.user_id)})


class NotificationRetryView(ApiView):
    required_capability = "notification.view_own"
    http_method_names = ["post"]

    def post(self, request, notification_id: str):
        is_admin = role_allows(request.auth_identity.role, "alert.rule.admin")
        result = notifier.retry_delivery(notification_id, request.auth_identity.user_id,
                                        is_admin=is_admin)
        audit(request, "notification.retry", resource_type="notifications",
              resource_id=notification_id, metadata=result)
        return self.ok(request, result)


def _email_enabled() -> bool:
    from django.conf import settings

    return bool(settings.EMAIL_ENABLED)
