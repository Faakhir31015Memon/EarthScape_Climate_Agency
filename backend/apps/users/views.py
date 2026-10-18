"""Administrator user-management endpoints.

Every mutation is audited (the service layer writes the audit record), and the
surfaces here are administrator-enforced server-side: ``IsAdministrator`` is
checked by DRF's permission machinery, never by the frontend route guard.
"""
from __future__ import annotations

import logging

from bson import ObjectId
from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from apps.users import serializers as user_serializers  # noqa: E402
from apps.users import services  # noqa: E402
from common import audit  # noqa: E402
from common.api import ApiError, IsAdministrator, MongoJWTAuthentication  # noqa: E402
from common.repositories import RepositoryError, refresh_tokens, utcnow  # noqa: E402
from common.views import (  # noqa: E402
    AuthenticatedView,
    bool_param,
    int_param,
    str_param,
)

logger = logging.getLogger(__name__)

ROLES_CHOICES = ("administrator", "climate_analyst", "viewer")
STATUS_CHOICES = ("active", "inactive", "locked", "all")
SORT_CHOICES = ("created_at", "name", "email", "last_login", "role")


class AdministratorView(AuthenticatedView):
    """Base for the whole of ``/api/v1/users/`` - administrator authentication only."""

    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAdministrator]


def require_account_id(value: str) -> str:
    """Validate an account identifier without leaking storage internals."""
    if not ObjectId.is_valid(value or ""):
        raise ApiError("not_found", "No such account.", status=404)
    return value


class UserListView(AdministratorView):
    """``GET /api/v1/users/`` (search, filters, sort, paging) and ``POST`` (create)."""

    def get(self, request):
        query = str_param(request, "search", max_length=60)
        role = str_param(request, "role", choices=ROLES_CHOICES)
        status_filter = str_param(request, "status", default="all", choices=STATUS_CHOICES)
        sort = str_param(request, "sort", default="created_at", choices=SORT_CHOICES)
        page = int_param(request, "page", 1, minimum=1, maximum=100000)
        page_size = int_param(request, "page_size", 25, minimum=1, maximum=200)

        result = services.search(query=query, role=role,
                                 status_filter=None if status_filter == "all" else status_filter,
                                 sort=sort, page=page, page_size=page_size)
        result["filters_applied"] = {"search": query, "role": role, "status": status_filter,
                                     "sort": sort}
        return Response(result)

    def post(self, request):
        serializer = user_serializers.UserCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = self.current_user(request)
        data = serializer.validated_data
        document = services.create(
            full_name=data["full_name"],
            email=data["email"],
            password=data["password"],
            role=data["role"],
            organisation=data.get("organisation"),
            actor_id=user.id,
            request=request,
            email_verified=bool(data.get("email_verified", True)),
        )
        return Response({"account": document}, status=201)


class RoleCatalogueView(AdministratorView):
    """``GET /api/v1/users/roles`` - role catalogue, capabilities and live account counts."""

    def get(self, request):
        return Response({"roles": services.role_catalogue(),
                         "note": "Role capabilities are the server-side permission map; "
                                 "frontend menus are decoration only."})


class UserDetailView(AdministratorView):
    """``GET``/``PATCH``/``DELETE /api/v1/users/<id>`` - delete is a soft deactivation."""

    def get(self, request, account_id: str):
        require_account_id(account_id)
        try:
            return Response({"account": services.detail(account_id)})
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)

    def patch(self, request, account_id: str):
        require_account_id(account_id)
        serializer = user_serializers.UserUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = request.user
        try:
            account = services.update(account_id, dict(serializer.validated_data),
                                      actor_id=user.id, request=request)
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)
        return Response({"account": account})

    def delete(self, request, account_id: str):
        """Soft delete: the account is deactivated and its sessions revoked."""
        require_account_id(account_id)
        user = request.user
        try:
            account = services.set_active(account_id, False, actor_id=user.id, request=request)
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)
        return Response({"account": account, "deleted": False,
                         "note": "Accounts are never hard-deleted; this one is deactivated."})


class UserActivateView(AdministratorView):
    def post(self, request, account_id: str):
        require_account_id(account_id)
        try:
            return Response({"account": services.set_active(account_id, True,
                                                            actor_id=request.user.id,
                                                            request=request)})
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)


class UserDeactivateView(AdministratorView):
    def post(self, request, account_id: str):
        require_account_id(account_id)
        try:
            return Response({"account": services.set_active(account_id, False,
                                                            actor_id=request.user.id,
                                                            request=request)})
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)


class UserRoleView(AdministratorView):
    """``POST /api/v1/users/<id>/role`` - role change (last-administrator guarded)."""

    def post(self, request, account_id: str):
        require_account_id(account_id)
        serializer = user_serializers.RoleAssignmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            account = services.update(account_id, {"role": serializer.validated_data["role"]},
                                      actor_id=request.user.id, request=request)
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)
        return Response({"account": account,
                         "note": "Existing sessions for this account were revoked."})


class UserPasswordResetView(AdministratorView):
    """``POST /api/v1/users/<id>/reset-password`` - the new password is never returned."""

    def post(self, request, account_id: str):
        require_account_id(account_id)
        serializer = user_serializers.AdministratorPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.admin_set_password(account_id,
                                                 serializer.validated_data["new_password"],
                                                 actor_id=request.user.id, request=request)
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)
        return Response(result)


class UserUnlockView(AdministratorView):
    def post(self, request, account_id: str):
        require_account_id(account_id)
        try:
            result = services.unlock(account_id, actor_id=request.user.id, request=request)
        except RepositoryError:
            raise ApiError("not_found", "No such account.", status=404)
        return Response(result)


class UserSessionsView(AdministratorView):
    """``GET``/``DELETE /api/v1/users/<id>/sessions``.

    Only session *metadata* is exposed - token material and hashes never leave the
    database.
    """

    MAX_LISTED = 50

    def get(self, request, account_id: str):
        require_account_id(account_id)
        now = utcnow()
        projection = {"_id": 1, "user_id": 1, "issued_at": 1, "expires_at": 1, "revoked_at": 1,
                      "revocation_reason": 1, "kind": 1, "source_ip": 1, "user_agent": 1}
        documents = refresh_tokens.find({"user_id": account_id, "kind": {"$ne": "password_reset"}},
                                        projection=projection,
                                        sort=[("issued_at", -1)], limit=self.MAX_LISTED)
        sessions = []
        active = 0
        for document in documents:
            expires_at = document.get("expires_at")
            is_active = (document.get("revoked_at") is None
                         and bool(expires_at) and expires_at > now)
            active += 1 if is_active else 0
            sessions.append({
                "id": str(document["_id"]),
                "issued_at": document.get("issued_at"),
                "expires_at": document.get("expires_at"),
                "revoked_at": document.get("revoked_at"),
                "revocation_reason": document.get("revocation_reason"),
                "active": is_active,
                "source_ip": document.get("source_ip"),
                "user_agent": document.get("user_agent"),
            })
        return Response({
            "user_id": account_id,
            "active_session_count": active,
            "listed_session_count": len(sessions),
            "sessions": sessions,
            "truncated": len(documents) >= self.MAX_LISTED,
            "note": "Refresh-token material is never returned by this endpoint.",
        })

    def delete(self, request, account_id: str):
        require_account_id(account_id)
        reason = str_param(request, "reason", default="administrator_revoked", max_length=60)
        revoked = services.revoke_all_for_user(account_id, reason=reason)
        audit.for_request(request, action="user.sessions_revoked", resource_type="user",
                          resource_id=account_id, actor_user_id=request.user.id,
                          metadata={"revoked": int(revoked or 0), "reason": reason})
        return Response({"status": "revoked", "sessions_revoked": int(revoked or 0),
                         "all_sessions": bool_param(request, "all", default=True)})
