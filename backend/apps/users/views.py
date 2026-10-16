"""Administrator-facing user management endpoints."""

from __future__ import annotations

from apps.authentication import tokens
from apps.users import repository
from common.audit import audit
from common.exceptions import Conflict, NotFound, ValidationError
from common.pagination import normalize_pagination
from common.responses import paginated
from common.security import is_valid_email, normalize_email
from common.validators import UserCreate, UserPatch, parse
from common.views import ApiView


class UserListView(ApiView):
    required_capability = "user.manage"

    def get(self, request):
        params = self.params(request)
        page, size, skip = normalize_pagination(request.query_params)
        items, total = repository.list_users(
            search=params.get("search", ""),
            role=params.get("role", ""),
            active=params.get("active", ""),
            page=page,
            page_size=size,
        )
        return paginated(items, total=total, page=page, page_size=size,
                         extra={"role_counts": repository.count_by_role()}, request=request)

    def post(self, request):
        payload = parse(UserCreate, self.body(request))
        user = repository.create_user(
            full_name=payload.full_name,
            email=payload.email,
            password=payload.password,
            role=payload.role,
            email_verified=payload.email_verified,
            must_change_password=payload.must_change_password,
            created_by=request.auth_identity.user_id,
        )
        audit(request, "user.provision", resource_type="users", resource_id=str(user["_id"]),
              metadata={"role": user["role"]})
        return self.created(request, repository.public_view(user))


class UserDetailView(ApiView):
    required_capability = "user.manage"

    def get(self, request, user_id: str):
        from common.db import find

        user = repository.get_by_id(user_id)
        if not user:
            raise NotFound("User account not found.")
        detail = repository.public_view(user)
        detail["sessions"] = tokens.active_sessions(user_id)
        detail["recent_activity"] = [
            {
                "action": row.get("action"),
                "outcome": row.get("outcome"),
                "resource": row.get("resource_type"),
                "at": row["occurred_at"].isoformat() if row.get("occurred_at") else None,
            }
            for row in find("audit_logs", {"identity": user_id}, sort=[("occurred_at", -1)], limit=15)
        ]
        return self.ok(request, detail)

    def patch(self, request, user_id: str):
        payload = parse(UserPatch, self.body(request))
        target = repository.get_by_id(user_id)
        if not target:
            raise NotFound("User account not found.")
        if str(target["_id"]) == request.auth_identity.user_id and payload.is_active is False:
            raise Conflict("You cannot deactivate your own account.")
        changed: dict[str, str] = {}
        if payload.role:
            changed["role"] = repository.set_role(user_id, payload.role)
        if payload.is_active is not None:
            repository.set_active(user_id, payload.is_active)
            changed["is_active"] = str(payload.is_active).lower()
        if payload.full_name:
            repository.update_profile(user_id, full_name=payload.full_name)
            changed["full_name"] = "updated"
        if payload.reset_password:
            repository.set_password(user_id, payload.reset_password)
            tokens.revoke_all_for_user(user_id, reason="administrator_password_reset")
            changed["password"] = "reset_and_sessions_revoked"
        audit(request, "user.update", resource_type="users", resource_id=user_id,
              metadata=changed)
        return self.ok(request, repository.public_view(repository.require(user_id)))

    def delete(self, request, user_id: str):
        """Deactivate rather than erase, preserving audit and dataset provenance."""
        target = repository.get_by_id(user_id)
        if not target:
            raise NotFound("User account not found.")
        if str(target["_id"]) == request.auth_identity.user_id:
            raise Conflict("You cannot delete your own account.")
        repository.set_active(user_id, False)
        audit(request, "user.deactivate", resource_type="users", resource_id=user_id)
        return self.ok(request, {"deactivated": True, "id": user_id,
                                 "note": "Account deactivated; records and audit history retained."})


class RoleListView(ApiView):
    required_capability = "user.manage"

    def get(self, request):
        from apps.authentication.permissions import CAPABILITIES

        matrix: dict[str, list[str]] = {}
        for capability, roles in sorted(CAPABILITIES.items()):
            matrix[capability] = list(roles)
        return self.ok(request, {
            "roles": list(repository.ROLES),
            "counts": repository.count_by_role(),
            "capability_matrix": matrix,
        })
