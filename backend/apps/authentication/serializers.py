from __future__ import annotations

from rest_framework import serializers

from common.api import ALL_ROLES
from common import security


class RegisterSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=160)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8, max_length=72)
    organisation = serializers.CharField(required=False, allow_blank=True, max_length=160)
    role = serializers.ChoiceField(choices=ALL_ROLES, required=False)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, max_length=72, style={"input_type": "password"})


class RefreshSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(max_length=200)


class LogoutSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(max_length=200, required=False, allow_blank=True)
    all_sessions = serializers.BooleanField(required=False, default=False)


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, max_length=72)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=72)

    def validate_new_password(self, value):
        problems = security.password_problems(value)
        if problems:
            raise serializers.ValidationError(" ".join(problems))
        if value == self.initial_data.get("current_password"):
            raise serializers.ValidationError("Choose a password you have not used before.")
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200)
    new_password = serializers.CharField(write_only=True, min_length=8, max_length=72)

    def validate_new_password(self, value):
        problems = security.password_problems(value)
        if problems:
            raise serializers.ValidationError(" ".join(problems))
        return value


class ProfileUpdateSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=160, required=False)
    preferences = serializers.JSONField(required=False)
