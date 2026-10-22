"""Environment configuration loading and validation.

Reads a plain ``.env`` file (no external dependency) from the repository root or
the backend directory, exposes typed accessors and validates required settings at
startup without ever echoing secret values.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def load_env_file() -> Dict[str, str]:
    """Load ``.env`` from the repository root, then the backend directory."""
    merged: Dict[str, str] = {}
    for candidate in (REPO_ROOT / ".env", BACKEND_DIR / ".env"):
        merged.update(_parse_env_file(candidate))
    return merged


_FILE_VALUES: Optional[Dict[str, str]] = None


def _file_values() -> Dict[str, str]:
    global _FILE_VALUES
    if _FILE_VALUES is None:
        _FILE_VALUES = load_env_file()
    return _FILE_VALUES


def get_raw(key: str, default: Optional[str] = None) -> Optional[str]:
    """Process environment first, then the ``.env`` file."""
    value = os.environ.get(key)
    if value is None or value == "":
        value = _file_values().get(key)
    if value is None or value == "":
        return default
    return value


def get_str(key: str, default: str = "") -> str:
    return get_raw(key, default) or default  # type: ignore[return-value]


def get_int(key: str, default: int) -> int:
    raw = get_raw(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(f"{key} must be an integer (got a non-numeric value).")


def get_bool(key: str, default: bool = False) -> bool:
    raw = get_raw(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_list(key: str, default: Optional[List[str]] = None) -> List[str]:
    raw = get_raw(key)
    if raw is None:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


def mask_value(value: Optional[str]) -> str:
    """Return a non-reversible short description of a secret for logs/UI status."""
    if not value:
        return "not configured"
    if len(value) <= 8:
        return f"configured ({len(value)} chars)"
    return f"configured ({value[:3]}…{len(value)} chars)"


def usable_mongodb_uri() -> Optional[str]:
    """Resolved Atlas URI, or None while placeholders remain unfilled."""
    try:
        from common.env import resolve_mongodb_uri
    except ImportError:  # pragma: no cover - backend must be on sys.path
        return get_raw("MONGODB_URI")
    return resolve_mongodb_uri() or None


def validate_startup(*, require_atlas: bool = False) -> List[str]:
    """Validate configuration. Returns human-safe warnings, raises on hard errors."""
    warnings: List[str] = []

    if not get_raw("DJANGO_SECRET_KEY"):
        raise ConfigurationError(
            "DJANGO_SECRET_KEY is required. Copy .env.example to .env and set it."
        )

    uri = usable_mongodb_uri()
    if require_atlas and not uri:
        raise ConfigurationError(
            "MONGODB_URI is required when FORCE_ATLAS_CONNECTION=true. Set "
            "MONGODB_ATLAS_USER / MONGODB_ATLAS_PASSWORD if the URI still holds placeholders."
        )
    if not uri:
        warnings.append(
            "MONGODB_URI is not usable (missing, or a <username>/<password> placeholder was "
            "never filled) - the backend runs in local document-store mode "
            "(see backend/common/database.py). Set MONGODB_URI plus MONGODB_ATLAS_USER to use "
            "MongoDB Atlas."
        )

    if get_bool("DJANGO_DEBUG") and get_bool("FORCE_ATLAS_CONNECTION"):
        warnings.append("DJANGO_DEBUG=true is not appropriate for a production deployment.")

    if not get_raw("JWT_SIGNING_KEY"):
        raise ConfigurationError("JWT_SIGNING_KEY is required for authentication.")

    if not get_raw("NOAA_API_KEY"):
        warnings.append("NOAA_API_KEY missing - the NOAA connector stays in 'needs_configuration' state.")

    if get_bool("ENABLE_EMAIL_NOTIFICATIONS") and not get_raw("SMTP_HOST"):
        warnings.append("ENABLE_EMAIL_NOTIFICATIONS=true but SMTP_HOST is missing; email delivery is disabled.")

    return warnings


@lru_cache(maxsize=1)
def summary() -> Dict[str, Any]:
    """Non-secret configuration summary exposed through the health endpoint."""
    return {
        "environment": get_str("DEPLOYMENT_ENVIRONMENT", "development"),
        "debug": get_bool("DJANGO_DEBUG"),
        "allowed_hosts": get_list("DJANGO_ALLOWED_HOSTS", ["localhost", "127.0.0.1"]),
        "mongo_database": get_str("MONGODB_DATABASE", "earthscape_climate"),
        "mongo_uri": mask_value(usable_mongodb_uri()),
        "atlas_required": get_bool("FORCE_ATLAS_CONNECTION"),
        "hdfs_endpoint": get_raw("HDFS_ENDPOINT") or "not configured",
        "spark_master": get_raw("SPARK_MASTER") or "not configured",
        "kafka_brokers": get_raw("KAFKA_BROKERS") or "not configured",
        "redis_url": mask_value(get_raw("REDIS_URL")) if get_raw("REDIS_URL") else "not configured",
        "nasa_power": "configured" if get_raw("NASA_POWER_BASE_URL") else "default endpoint",
        "noaa_api_key": mask_value(get_raw("NOAA_API_KEY")),
        "email_notifications": get_bool("ENABLE_EMAIL_NOTIFICATIONS"),
        "registration_enabled": get_bool("ENABLE_SELF_REGISTRATION", True),
        "log_level": get_str("LOG_LEVEL", "INFO"),
    }
