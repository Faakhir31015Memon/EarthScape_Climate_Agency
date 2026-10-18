"""Request validation for the administrator user-management API."""
from __future__ import annotations

from rest_framework import serializers

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common import security  # noqa: E402
from common.api import ALL_ROLES  # noqa: E402

PREFERENCES_ALLOW_LIST = {
    "theme", "units", "notification_channels", "dashboard_region", "density",
    "locale", "default_region",
}


class UserCreateSerializer(serializers.Serializer):
    """Administrator-provisioned account."""

    full_name = serializers.CharField(max_length=160)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, max_length=72)
    role = serializers.ChoiceField(choices=ALL_ROLES)
    organisation = serializers.CharField(required=False, allow_blank=True, max_length=160)
    email_verified = serializers.BooleanField(required=False, default=True)

    def validate_full_name(self, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Enter the account holder's full name.")
        return value

    def validate_password(self, value: str) -> str:
        problems = security.password_problems(value)
        if problems:
            raise serializers.ValidationError(" ".join(problems))
        return value


class UserUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, max_length=160)
    email = serializers.EmailField(required=False)
    organisation = serializers.CharField(required=False, allow_blank=True, allow_null=True,
                                         max_length=160)
    role = serializers.ChoiceField(required=False, choices=ALL_ROLES)
    preferences = serializers.DictField(required=False, child=serializers.JSONField())

    def validate_full_name(self, value: str) -> str:
        value = value.strip()
        if len(value) < 2:
            raise serializers.ValidationError("Enter the account holder's full name.")
        return value

    def validate_preferences(self, value: dict) -> dict:
        unknown = sorted(set(value) - PREFERENCES_ALLOW_LIST)
        if unknown:
            raise serializers.ValidationError(
                f"Unsupported preference keys: {', '.join(unknown)}.")
        return value


class RoleAssignmentSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=ALL_ROLES)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=200)


class AdministratorPasswordSerializer(serializers.Serializer):
    """Administrator-set password. The value is never echoed back anywhere."""

    new_password = serializers.CharField(write_only=True, min_length=8, max_length=72)

    def validate_new_password(self, value: str) -> str:
        problems = security.password_problems(value)
        if problems:
            raise serializers.ValidationError(" ".join(problems))
        return value
