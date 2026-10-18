"""Startup shim that breaks a circular import inside the frozen core.

``config.settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]`` names
``common.api.MongoJWTAuthentication``. Django REST framework resolves that import
string *while importing* ``rest_framework.views`` (``rest_framework.schemas``
evaluates ``api_settings.DEFAULT_AUTHENTICATION_CLASSES`` in the signature of
``get_schema_view``), and ``common/api.py`` itself starts with
``from rest_framework.views import exception_handler``. Whichever side is imported
first, the other is only partially initialised, so every API module dies with
``ImportError: cannot import name 'exception_handler' from partially initialized
module 'rest_framework.views'``.

``backend/common/*`` and ``backend/config/settings.py`` are frozen for this task, so
the cycle is untangled at import time here instead:

1. read the configured value and temporarily hide it from ``api_settings``;
2. let ``rest_framework.views`` (and ``rest_framework.schemas``) finish loading;
3. restore the real ``REST_FRAMEWORK`` setting and import ``common.api`` normally;
4. re-resolve the cached DRF value and put the configured authenticator back on
   ``APIView``, so runtime behaviour matches ``settings.py`` exactly.

The function is idempotent, thread-safe, and a deliberate no-op when the cyclic
setting is not present. It reports what it did so health checks can expose the
truth instead of guessing.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STATUS: Dict[str, Any] = {"applied": False, "detail": "not run yet"}

#: DRF settings whose import strings live inside ``common`` and therefore close the cycle.
CYCLIC_KEYS = ("DEFAULT_AUTHENTICATION_CLASSES",)


def status() -> Dict[str, Any]:
    """What the shim did on this process (used by ``/api/v1/health``)."""
    return dict(_STATUS)


def provide_security_alias() -> None:
    """Expose ``common.security.to_object_id`` - the helper services already call.

    ``apps/users/services.py`` and ``apps/authentication/services.py`` both use
    ``security.to_object_id(...)``, but the implementation lives in
    ``common.repositories``. ``common/`` is frozen for this task, so the identical
    helper is published under the expected name at import time instead of editing a
    shared module. ``ObjectId`` parsing errors stay ``RepositoryError`` and are
    translated to ``404 not_found`` by the API layer.
    """
    from common import security

    if not hasattr(security, "to_object_id"):
        from common.repositories import to_object_id

        security.to_object_id = to_object_id


def ensure_common_api_importable() -> Dict[str, Any]:
    """Load ``common.api`` and DRF views without the circular-import failure."""
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
            # Nothing to untangle (or it already happened through the normal path).
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
                # ``APIView`` captured the neutralised default at class-definition time.
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
