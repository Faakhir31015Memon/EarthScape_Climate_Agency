"""Request validation for the alert-rule, alert and notification APIs."""
from __future__ import annotations

from rest_framework import serializers

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.alerts import rules as rule_engine  # noqa: E402
from common.analytics import METRICS  # noqa: E402


class ScopeSerializer(serializers.Serializer):
    regions = serializers.ListField(child=serializers.CharField(max_length=60), required=False,
                                    max_length=50)
    location_codes = serializers.ListField(child=serializers.CharField(max_length=20),
                                           required=False, max_length=50)
    countries = serializers.ListField(child=serializers.CharField(max_length=60), required=False,
                                      max_length=50)
    datasets = serializers.ListField(child=serializers.CharField(max_length=64), required=False,
                                     max_length=50)


class ConditionSerializer(serializers.Serializer):
    operator = serializers.ChoiceField(choices=rule_engine.OPERATORS)
    value = serializers.FloatField(required=False, allow_null=True)
    value_upper = serializers.FloatField(required=False, allow_null=True)
    sigma = serializers.FloatField(required=False, allow_null=True, min_value=0.1, max_value=20)


class RuleCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    target_variable = serializers.ChoiceField(choices=METRICS)
    severity = serializers.ChoiceField(choices=rule_engine.SEVERITIES, required=False,
                                       default="warning")
    is_enabled = serializers.BooleanField(required=False, default=True)
    scope = ScopeSerializer(required=False)
    condition = ConditionSerializer()
    applicable_sources = serializers.ListField(child=serializers.CharField(max_length=80),
                                               required=False, max_length=60,
                                               help_text="Observation source names and/or the "
                                                         "control tokens 'observations' and "
                                                         "'predictions'.")
    cooldown_minutes = serializers.IntegerField(required=False, min_value=0, max_value=10080)
    recipients = serializers.ListField(child=serializers.CharField(max_length=64), required=False,
                                       max_length=500,
                                       help_text="Account ids, 'role:<role>' selectors or 'all'.")
    rule_key = serializers.CharField(required=False, max_length=64)


class RuleUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, max_length=120)
    description = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    target_variable = serializers.ChoiceField(required=False, choices=METRICS)
    severity = serializers.ChoiceField(required=False, choices=rule_engine.SEVERITIES)
    is_enabled = serializers.BooleanField(required=False)
    scope = ScopeSerializer(required=False)
    condition = ConditionSerializer(required=False)
    applicable_sources = serializers.ListField(child=serializers.CharField(max_length=80),
                                               required=False, max_length=60)
    cooldown_minutes = serializers.IntegerField(required=False, min_value=0, max_value=10080)
    recipients = serializers.ListField(child=serializers.CharField(max_length=64), required=False,
                                       max_length=500)

    def validate_condition(self, value: dict) -> dict:
        # Partial condition updates are merged with the stored condition by the engine,
        # but an explicitly supplied condition must be complete.
        if not value.get("operator"):
            raise serializers.ValidationError("condition.operator is required.")
        return value


class AlertResolveSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=2000)
    status = serializers.ChoiceField(choices=("resolved", "dismissed"), required=False,
                                     default="resolved")


class AlertAcknowledgeSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)


class NotificationReadSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("alert", "report", "system", "ticket"),
                                   required=False)
    unread_only = serializers.BooleanField(required=False, default=False)


class NotificationPreferencesSerializer(serializers.Serializer):
    in_app = serializers.BooleanField(required=False, default=True)
    email = serializers.BooleanField(required=False, default=False)
