# DRF resolves DEFAULT_AUTHENTICATION_CLASSES while importing rest_framework.views, and
# common/api.py imports from that same module, so whichever loads first sees a half-built
# module. common/ and settings.py are frozen; the cycle is untangled here instead.
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATUS: Dict[str, Any] = {"applied": False, "detail": "not run yet"}

# DRF settings whose import strings live inside common, i.e. the ones closing the cycle
CYCLIC_KEYS = ("DEFAULT_AUTHENTICATION_CLASSES",)


def status() -> Dict[str, Any]:
    return dict(_STATUS)


def provide_security_alias() -> None:
    """Publish common.repositories.to_object_id as common.security.to_object_id.

    users/ and authentication/ services call it through security, but common/ is
    frozen so the alias is added at import time. ObjectId parse failures stay
    RepositoryError and become 404 not_found at the API layer.
    """
    from common import security

    if not hasattr(security, "to_object_id"):
        from common.repositories import to_object_id

        security.to_object_id = to_object_id


def ensure_common_api_importable() -> Dict[str, Any]:
    global _STATUS
    if _STATUS.get("applied"):
        return dict(_STATUS)
    with _LOCK:
        if _STATUS.get("applied"):
            return dict(_STATUS)
        from django.conf import settings

        provide_security_alias()

        configured = dict(getattr(settings, "REST_FRAMEWORK", {}) or {})
        cyclic = {key: configured[key] for key in CYCLIC_KEYS if key in configured}
        if not cyclic or "common.api" in sys.modules and "rest_framework.views" in sys.modules:
            # nothing to untangle, or the normal path already did it
            from rest_framework.settings import api_settings  # noqa: F401  (forces a real load)

            _STATUS = {"applied": True, "detail": "no action required", "restored": []}
            return dict(_STATUS)

        from rest_framework.settings import api_settings, perform_import

        def _hide() -> None:
            settings.REST_FRAMEWORK = {k: v for k, v in configured.items() if k not in cyclic}
            api_settings.__dict__.pop("_user_settings", None)

        def _restore() -> None:
            settings.REST_FRAMEWORK = configured
            api_settings.__dict__.pop("_user_settings", None)

        resolved: Dict[str, List[Any]] = {}
        try:
            _hide()
            import rest_framework.views  # noqa: F401 - loads schemas/splinters cleanly now

            _restore()
            import common.api  # noqa: F401 - safe: DRF views are fully loaded

            for key, value in cyclic.items():
                resolved[key] = perform_import(value, key)
                api_settings.__dict__[key] = resolved[key]
                api_settings._cached_attrs.add(key)

            authenticators = resolved.get("DEFAULT_AUTHENTICATION_CLASSES")
            if authenticators and list(rest_framework.views.APIView.authentication_classes) != authenticators:
                # APIView captured the neutralised default at class-definition time
                rest_framework.views.APIView.authentication_classes = authenticators

            _STATUS = {
                "applied": True,
                "detail": "import cycle untangled before DRF settings resolution",
                "restored": sorted(resolved),
                "authentication_classes": [
                    f"{item.__module__}.{item.__name__}" for item in (authenticators or [])
                ],
            }
        except Exception as exc:  # noqa: BLE001 - surface a clear, non-secret failure
            _restore()
            _STATUS = {"applied": False, "detail": f"import cycle shim failed: {type(exc).__name__}"}
            logger.error("DRF import-cycle shim failed: %s", type(exc).__name__)
            raise
        return dict(_STATUS)


__all__ = ["ensure_common_api_importable", "status", "CYCLIC_KEYS"]
