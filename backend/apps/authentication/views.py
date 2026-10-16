"""Authentication endpoints: login, refresh, logout, session, password change/recovery."""

from __future__ import annotations

from django.conf import settings
from rest_framework.permissions import AllowAny

from apps.authentication import services
from apps.authentication.permissions import IsAuthenticatedMongo
from apps.users import repository
from common.validators import (
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequestRequest,
    ProfileUpdate,
    RefreshRequest,
    RegisterRequest,
    parse,
)
from common.views import ApiView, PublicView
from common.responses import ok


class LoginView(PublicView):
    """Exchange credentials for an access/refresh pair."""

    permission_classes = [AllowAny]
    throttle_scope = "login"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(LoginRequest, self.body(request))
        session = services.login(
            request,
            email=payload.email,
            password=payload.password,
            device_hint=payload.device_hint or "",
        )
        return ok(session, meta={"authenticated": True}, request=request)


class RegisterView(PublicView):
    permission_classes = [AllowAny]
    throttle_scope = "register"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(RegisterRequest, self.body(request))
        user = services.register(
            request,
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
            organisation=payload.organisation or "",
        )
        return ok({"user": user, "registration_enabled": settings.APP["ALLOW_SELF_REGISTRATION"]},
                  meta={"next": "An administrator must confirm your email before sign-in is enabled "
                                "if email verification is enforced."}, request=request)


class RefreshView(PublicView):
    permission_classes = [AllowAny]
    throttle_scope = "burst"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(RefreshRequest, self.body(request))
        session = services.refresh(request, refresh_token=payload.refresh_token)
        return ok(session, request=request)


class LogoutView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(LogoutRequest, self.body(request))
        result = services.logout(
            request,
            refresh_token=payload.refresh_token,
            all_sessions=payload.all_sessions,
            identity=request.auth_identity,
        )
        return ok(result, meta={"logged_out": True}, request=request)


class MeView(ApiView):
    http_method_names = ["get"]

    def get(self, request):
        return ok(services.current_session(request), request=request)


class ProfileView(ApiView):
    required_capability = "platform.view"
    http_method_names = ["get", "patch"]

    def get(self, request):
        return ok(repository.public_view(repository.require(request.auth_identity.user_id)), request=request)

    def patch(self, request):
        from apps.authentication.validators_bridge import ProfileUpdate

        payload = parse(ProfileUpdate, self.body(request))
        user = repository.update_profile(
            request.auth_identity.user_id,
            full_name=payload.full_name,
            preferences=payload.preferences,
        )
        return ok(repository.public_view(user), request=request)


class PasswordChangeView(ApiView):
    required_capability = "platform.view"
    throttle_scope = "burst"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(PasswordChangeRequest, self.body(request))
        repository.change_password(
            request.auth_identity.user_id,
            current_password=payload.current_password,
            new_password=payload.new_password,
        )
        from apps.authentication import tokens

        tokens.revoke_all_for_user(request.auth_identity.user_id, reason="password_changed")
        return ok({"password_changed": True, "sessions_revoked": True},
                  meta={"note": "All other sessions were signed out. Sign in again to continue."},
                  request=request)


class PasswordResetRequestView(PublicView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(PasswordResetRequestRequest, self.body(request))
        result = services.request_password_reset(request, email=payload.email)
        return ok(result, request=request)


class PasswordResetConfirmView(PublicView):
    permission_classes = [AllowAny]
    throttle_scope = "password_reset"
    http_method_names = ["post"]

    def post(self, request):
        payload = parse(PasswordResetConfirm, self.body(request))
        result = services.confirm_password_reset(request, token=payload.token,
                                                 new_password=payload.new_password)
        return ok(result, request=request)


class RegistrationStatusView(PublicView):
    """Lets the login screen tell users whether sign-up is available at all."""

    http_method_names = ["get"]

    def get(self, request):
        return ok({
            "self_registration": settings.APP["ALLOW_SELF_REGISTRATION"],
            "default_role": settings.APP["DEFAULT_ROLE"],
            "password_min_length": settings.APP["PASSWORD_MIN_LENGTH"],
            "recovery_channel": "email" if settings.EMAIL_ENABLED else "administrator",
            "administrator_present": repository.administrator_exists(),
        }, request=request)
