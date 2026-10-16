"""DRF authentication backed by MongoDB user records."""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication

from apps.authentication import tokens
from apps.users.repository import authenticate_identity
from common.exceptions import Unauthorized
from common.logging_config import get_logger

logger = get_logger("earthscape.auth")


class MongoJWTAuthentication(BaseAuthentication):
    """Validates the access token, then re-reads the account so revoked roles apply now."""

    keyword = "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        if not header:
            return None
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            return None
        payload = tokens.decode_access(credential.strip())
        identity = authenticate_identity(payload.get("sub", ""))
        if identity is None:
            raise Unauthorized("Account is inactive or no longer exists.")
        if identity.role != payload.get("role"):
            # Role changed since the token was issued: trust the database, not the token.
            logger.info("role drift corrected from database",
                        extra={"correlation_id": getattr(request, "correlation_id", None)})
        identity.token_jti = payload.get("jti")
        request.auth_identity = identity
        return identity, credential.strip()

    def authenticate_header(self, request) -> str:
        return self.keyword

    def authenticate_request(self, request):  # pragma: no cover - compatibility shim
        return self.authenticate(request)


class OptionalMongoJWTAuthentication(MongoJWTAuthentication):
    def authenticate(self, request):
        try:
            return super().authenticate(request)
        except Unauthorized:
            return None
