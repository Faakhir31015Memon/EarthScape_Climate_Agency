"""Server-side role checks; the frontend route guards are cosmetic."""

from __future__ import annotations

from rest_framework.permissions import BasePermission

from apps.users.repository import ROLES

ADMIN = "administrator"
ANALYST = "climate_analyst"
VIEWER = "viewer"

# capability -> allowed roles, kept in one table rather than inline role checks
CAPABILITIES: dict[str, tuple[str, ...]] = {
    "platform.view": (ADMIN, ANALYST, VIEWER),
    "dataset.upload": (ADMIN, ANALYST),
    "dataset.admin": (ADMIN,),
    "dataset.delete": (ADMIN,),
    "ingestion.run": (ADMIN, ANALYST),
    "ingestion.view": (ADMIN, ANALYST, VIEWER),
    "processing.run": (ADMIN, ANALYST),
    "processing.view": (ADMIN, ANALYST, VIEWER),
    "model.train": (ADMIN, ANALYST),
    "model.register": (ADMIN,),
    "prediction.run": (ADMIN, ANALYST),
    "prediction.view": (ADMIN, ANALYST, VIEWER),
    "anomaly.view": (ADMIN, ANALYST, VIEWER),
    "alert.rule.manage": (ADMIN, ANALYST),
    "alert.rule.admin": (ADMIN,),
    "alert.acknowledge": (ADMIN, ANALYST),
    "notification.view_own": (ADMIN, ANALYST, VIEWER),
    "report.generate": (ADMIN, ANALYST),
    "report.view_any": (ADMIN,),
    "export.download": (ADMIN, ANALYST, VIEWER),
    "analysis.save": (ADMIN, ANALYST),
    "support.create": (ADMIN, ANALYST, VIEWER),
    "support.manage": (ADMIN,),
    "feedback.create": (ADMIN, ANALYST, VIEWER),
    "feedback.view": (ADMIN,),
    "user.manage": (ADMIN,),
    "audit.view": (ADMIN,),
    "settings.manage": (ADMIN,),
    "health.view": (ADMIN, ANALYST, VIEWER),
    "ops.logs": (ADMIN, ANALYST),
}


def role_allows(role: str, capability: str) -> bool:
    return role in CAPABILITIES.get(capability, ())


class IsAuthenticatedMongo(BasePermission):
    message = "Authentication is required to access this resource."

    def has_permission(self, request, view) -> bool:
        identity = getattr(request, "auth_identity", None)
        if identity is None:
            return False
        if not getattr(identity, "user", {}).get("is_active", True):
            self.message = "This account has been deactivated."
            return False
        capability = getattr(view, "required_capability", "platform.view")
        if not role_allows(identity.role, capability):
            self.message = f"Role '{identity.role}' is not permitted to perform: {capability}."
            return False
        return True


class AllowAnonymous(BasePermission):
    def has_permission(self, request, view) -> bool:
        return True


class HasRole(BasePermission):
    roles: tuple[str, ...] = ()

    def has_permission(self, request, view) -> bool:
        identity = getattr(request, "auth_identity", None)
        return bool(identity and identity.role in self.roles)


class IsAdministrator(HasRole):
    roles = (ADMIN,)


class IsAnalystOrAdmin(HasRole):
    roles = (ADMIN, ANALYST)


def identity_or_none(request):
    return getattr(request, "auth_identity", None)
