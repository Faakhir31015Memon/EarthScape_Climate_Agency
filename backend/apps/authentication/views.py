"""Authentication endpoints. All state is enforced server-side."""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response

from apps.authentication import serializers as auth_serializers
from apps.authentication import services
from common import audit
from common.api import ApiError, AuthenticationFailed
from common.repositories import users as users_repository, utcnow
from common.views import AuthenticatedView, BaseView, PublicView
from config import env

logger = logging.getLogger(__name__)


class RegisterView(PublicView):
    """Self-registration. Disabled by default and never grants elevated roles."""

    def post(self, request):
        if not services.self_registration_enabled():
            raise ApiError(
                "registration_disabled",
                "Self-registration is disabled on this deployment. An administrator must "
                "provision accounts (see scripts/provision_admin.py and User management).",
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = auth_serializers.RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        requested_role = data.get("role")
        if requested_role and requested_role != services.registration_role(requested_role):
            raise ApiError("role_not_permitted", "Self-registration cannot request that role.", status=403)

        document = services.create_user(
            full_name=data["full_name"],
            email=data["email"],
            password=data["password"],
            role=services.registration_role(requested_role),
            created_by="self-registration",
            organisation=data.get("organisation"),
        )
        audit.for_request(request, action="user.self_registered", resource_type="user",
                          resource_id=str(document["_id"]), actor_user_id=str(document["_id"]),
                          metadata={"role": document["role"]})
        tokens = services.issue_token_pair(document)
        return Response({"account": services.public_profile(document), **tokens},
                        status=status.HTTP_201_CREATED)


class LoginView(PublicView):
    def post(self, request):
        serializer = auth_serializers.LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = services.authenticate(
            serializer.validated_data["email"],
            serializer.validated_data["password"],
            request=request,
        )
        return Response(tokens)


class RefreshView(PublicView):
    def post(self, request):
        serializer = auth_serializers.RefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.rotate_refresh_token(
            serializer.validated_data["refresh_token"], request=request))


class LogoutView(AuthenticatedView):
    def post(self, request):
        serializer = auth_serializers.LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.current_user(request)
        revoked = services.logout(
            serializer.validated_data.get("refresh_token") or None,
            request=request,
            user_id=user.id if serializer.validated_data.get("all_sessions") else None,
        )
        return Response({"status": "logged_out", "sessions_ended": bool(revoked)})


class CurrentUserView(AuthenticatedView):
    def get(self, request):
        user = self.current_user(request)
        document = services.get_user(user.id)
        if document is None:
            raise AuthenticationFailed("account_unknown", "Account no longer exists.")
        return Response({
            "account": services.public_profile(document),
            "capabilities": capabilities_for(document.get("role")),
        })

    def patch(self, request):
        user = self.current_user(request)
        serializer = auth_serializers.ProfileUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updates: dict = {"updated_at": utcnow()}
        if "full_name" in serializer.validated_data:
            updates["full_name"] = serializer.validated_data["full_name"]
        if "preferences" in serializer.validated_data:
            preferences = serializer.validated_data["preferences"]
            if not isinstance(preferences, dict):
                raise ApiError("validation_failed", "preferences must be an object.")
            existing = (services.get_user(user.id) or {}).get("preferences", {})
            allowed = {"theme", "units", "notification_channels", "dashboard_region", "density"}
            merged = dict(existing)
            for key, value in preferences.items():
                if key in allowed:
                    merged[key] = value
            updates["preferences"] = merged
        document = users_repository.update_by_id(user.id, updates, return_document=True)
        audit.for_request(request, action="user.profile_updated", resource_type="user",
                          resource_id=user.id, actor_user_id=user.id)
        return Response({"account": services.public_profile(document)})


class PasswordChangeView(AuthenticatedView):
    def post(self, request):
        serializer = auth_serializers.PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.current_user(request)
        services.change_password(
            user.id,
            serializer.validated_data["current_password"],
            serializer.validated_data["new_password"],
            request=request,
        )
        return Response({"status": "password_changed",
                         "note": "All existing sessions were revoked. Sign in again."})


class PasswordResetRequestView(PublicView):
    def post(self, request):
        serializer = auth_serializers.PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(services.begin_password_reset(serializer.validated_data["email"],
                                                      request=request))


class PasswordResetConfirmView(PublicView):
    def post(self, request):
        serializer = auth_serializers.PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.complete_password_reset(serializer.validated_data["token"],
                                         serializer.validated_data["new_password"],
                                         request=request)
        return Response({"status": "password_reset", "note": "Sign in with your new password."})


class AuthConfigurationView(PublicView):
    """Non-secret deployment facts the sign-in screens need."""

    def get(self, request):
        return Response({
            "self_registration_enabled": services.self_registration_enabled(),
            "registration_roles": ["viewer"] + (["climate_analyst"]
                                                if env.get_bool("ALLOW_ANALYST_SELF_REGISTRATION") else []),
            "password_reset_enabled": env.get_bool("ENABLE_PASSWORD_RESET", True),
            "email_delivery": env.get_raw("SMTP_HOST") is not None
            and env.get_bool("ENABLE_EMAIL_NOTIFICATIONS"),
            "password_policy": {
                "min_length": env.get_int("PASSWORD_MIN_LENGTH", 10),
                "requires_uppercase": True,
                "requires_lowercase": True,
                "requires_number": True,
                "requires_symbol": env.get_bool("PASSWORD_REQUIRE_SYMBOL", True),
            },
            "access_token_minutes": env.get_int("JWT_ACCESS_MINUTES", 20),
            "refresh_token_days": env.get_int("JWT_REFRESH_DAYS", 7),
        })


#: Capability vocabulary shared by the API and the React guards
#: (``frontend/src/features/auth/AuthContext.tsx``). Server-side enforcement uses
#: ``common.api.require_roles``; this map is what the UI disables or hides.
ROLE_CAPABILITIES = {
    "administrator": [
        "platform:manage",
        "users:read",
        "users:manage",
        "datasets:read",
        "datasets:manage",
        "ingestion:read",
        "ingestion:run",
        "processing:read",
        "processing:run",
        "models:train",
        "models:evaluate",
        "predictions:run",
        "alerts:read",
        "alerts:manage",
        "reports:read",
        "reports:manage",
        "support:read",
        "support:manage",
        "feedback:read",
        "feedback:manage",
        "audit:read",
        "system:read",
        "system:manage",
        "analytics:export",
        "climate:read",
        "analytics:read",
    ],
    "climate_analyst": [
        "datasets:read",
        "datasets:manage",
        "ingestion:read",
        "ingestion:run",
        "processing:read",
        "processing:run",
        "models:train",
        "models:evaluate",
        "predictions:run",
        "alerts:read",
        "alerts:manage",
        "reports:read",
        "reports:manage",
        "support:read",
        "feedback:create",
        "analytics:export",
        "analytics:read",
        "system:read",
        "climate:read",
    ],
    "viewer": [
        "climate:read",
        "datasets:read",
        "analytics:read",
        "alerts:read",
        "reports:read",
        "feedback:create",
        "support:read",
    ],
}


def capabilities_for(role: str) -> list:
    return ROLE_CAPABILITIES.get(role, [])
