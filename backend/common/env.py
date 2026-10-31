"""Environment loading and startup validation.

Every external dependency (Atlas, HDFS, Spark, SMTP, provider API keys) is
optional at start-up: the platform boots and reports what is unavailable instead of
crashing or pretending the service exists.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ARTIFACT_ROOT = BACKEND_DIR / "artifacts"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


class EnvFile:
    """Layered configuration: process env wins over file values."""

    def __init__(self, files: list[Path]) -> None:
        self._file_values: dict[str, str] = {}
        self.loaded_files: list[str] = []
        for path in files:
            loaded = _parse_env_file(path)
            if loaded:
                self._file_values.update({k: v for k, v in loaded.items() if k not in self._file_values})
                self.loaded_files.append(str(path))

    def raw(self, key: str, default: str = "") -> str:
        value = os.environ.get(key)
        if value is None or value == "":
            value = self._file_values.get(key, default)
        return (value or default).strip()

    def secret(self, key: str, default: str = "") -> str:
        return self.raw(key, default)

    def number(self, key: str, default: float) -> float:
        raw = self.raw(key)
        if raw == "":
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def integer(self, key: str, default: int) -> int:
        return int(self.number(key, default))

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self.raw(key)
        if raw == "":
            return default
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        return default

    def items(self, key: str, default: str = "") -> list[str]:
        raw = self.raw(key, default)
        return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]

    def exists(self, key: str) -> bool:
        return bool(self.raw(key))


ENV = EnvFile([REPO_ROOT / ".env", BACKEND_DIR / ".env"])

# Atlas connection strings copied from the console arrive with the credential slots
# left blank ("mongodb://<db_username>:<password>@host/..."). Keeping the placeholders
# in .env and the real values in their own keys means the secret never has to be
# edited inline and a half-filled URI fails loudly instead of silently.
_USER_PLACEHOLDERS = ("<db_username>", "<username>", "<user>", "<atlas_user>")
_PASSWORD_PLACEHOLDERS = ("<db_password>", "<password>", "<pass>")


def resolve_mongodb_uri() -> str:
    """Return ``MONGODB_URI`` with placeholder credentials substituted, or ""."""
    uri = ENV.raw("MONGODB_URI")
    if not uri:
        return ""
    user = ENV.raw("MONGODB_ATLAS_USER")
    password = ENV.raw("MONGODB_ATLAS_PASSWORD")
    for placeholder in _USER_PLACEHOLDERS:
        if placeholder in uri:
            if not user:
                return ""
            uri = uri.replace(placeholder, user)
            break
    for placeholder in _PASSWORD_PLACEHOLDERS:
        if placeholder in uri:
            if not password:
                return ""
            uri = uri.replace(placeholder, password)
            break
    return uri


def development_signing_key() -> str:
    """Persist a local-only signing key so dev tokens survive a server restart.

    Production must set DJANGO_SECRET_KEY; a generated key is never committed.
    """
    key_path = ARTIFACT_ROOT / ".dev-signing-key"
    configured = ENV.raw("DJANGO_SECRET_KEY")
    if configured:
        return configured
    if key_path.is_file():
        return key_path.read_text(encoding="utf-8").strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    generated = "dev-" + secrets.token_urlsafe(48)
    key_path.write_text(generated, encoding="utf-8")
    return generated


def _split_uri(uri: str) -> tuple[str, str]:
    """Return (host:port, credential) for display without leaking a password."""
    match = re.match(r"^(?P<scheme>[a-zA-Z]+)://(?:[^@/]*@)?(?P<host>[^/?]+)", uri)
    if not match:
        return "not-configured", ""
    scheme = match.group("scheme")
    host = match.group("host")
    return f"{scheme}://{host}", ("credentials-redacted" if "@" in uri else "no-credentials")


@dataclass
class ServiceStatus:
    name: str
    configured: bool
    required: bool
    detail: str
    hint: str
    mode: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "configured": self.configured,
            "required": self.required,
            "detail": self.detail,
            "hint": self.hint,
            "mode": self.mode,
        }


def storage_root() -> Path:
    configured = ENV.raw("DATA_LAKE_ROOT", str(ARTIFACT_ROOT / "datalake"))
    path = Path(configured)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_artifact_root() -> Path:
    configured = ENV.raw("MODEL_ARTIFACT_ROOT", str(ARTIFACT_ROOT / "models"))
    path = Path(configured)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_root() -> Path:
    configured = ENV.raw("REPORT_STORAGE_ROOT", str(ARTIFACT_ROOT / "reports"))
    path = Path(configured)
    if not path.is_absolute():
        path = BACKEND_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def allowed_origins() -> list[str]:
    return ENV.items(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    )


def startup_status() -> list[ServiceStatus]:
    """Human-readable picture of which integrations are actually usable right now."""
    statuses: list[ServiceStatus] = []

    mongo_uri = resolve_mongodb_uri()
    statuses.append(
        ServiceStatus(
            name="mongodb",
            configured=bool(mongo_uri),
            required=True,
            detail=(f"Target { _split_uri(mongo_uri)[0] } / db {ENV.raw('MONGODB_DATABASE', 'earthscape_climate')}"
                    if mongo_uri else
                    ("MONGODB_URI still holds a <username>/<password> placeholder - set "
                     "MONGODB_ATLAS_USER / MONGODB_ATLAS_PASSWORD"
                     if ENV.raw("MONGODB_URI") else "MONGODB_URI is not set")),
            hint="Point MONGODB_URI at MongoDB Atlas or a local mongod, then run scripts/bootstrap_platform.py.",
        )
    )

    hdfs_url = ENV.raw("HDFS_WEBHDFS_URL")
    statuses.append(
        ServiceStatus(
            name="hdfs",
            configured=bool(hdfs_url),
            required=False,
            detail=(f"WebHDFS endpoint {hdfs_url}" if hdfs_url
                    else f"local data lake at {storage_root()}"),
            hint="Set HDFS_WEBHDFS_URL plus HADOOP_USER and start backend/infrastructure/hadoop to use real HDFS.",
            mode="webhdfs" if hdfs_url else "local",
        )
    )

    spark_master = ENV.raw("SPARK_MASTER", "local[*]")
    try:
        import pyspark  # noqa: F401

        spark_available = True
    except Exception:
        spark_available = False
    statuses.append(
        ServiceStatus(
            name="spark",
            configured=spark_available,
            required=False,
            detail=(f"PySpark importable, master {spark_master}" if spark_available
                    else "PySpark not installed; pandas engine used for the same job logic"),
            hint="pip install pyspark (needs a JDK) or start backend/infrastructure/spark for cluster mode.",
            mode="pyspark" if spark_available else "pandas-fallback",
        )
    )

    mapreduce_mode = ENV.raw("MAPREDUCE_MODE", "streaming-local")
    hadoop_bin = ENV.raw("HADOOP_BIN")
    statuses.append(
        ServiceStatus(
            name="mapreduce",
            configured=bool(hadoop_bin) or mapreduce_mode != "streaming-local",
            required=False,
            detail=(f"hadoop launcher {hadoop_bin}" if hadoop_bin
                    else "Hadoop Streaming mapper/reducer executed through the bundled local runner"),
            hint="Install Hadoop, set HADOOP_BIN, then run the documented `hadoop jar hadoop-streaming` command.",
            mode=mapreduce_mode,
        )
    )

    statuses.append(
        ServiceStatus(
            name="background-tasks",
            configured=True,
            required=False,
            detail="in-process thread worker pool",
            hint="BACKGROUND_WORKERS sets the pool size.",
            mode="threaded",
        )
    )

    smtp_host = ENV.raw("SMTP_HOST")
    email_enabled = ENV.flag("ENABLE_EMAIL_NOTIFICATIONS", False)
    statuses.append(
        ServiceStatus(
            name="email",
            configured=bool(smtp_host) and email_enabled,
            required=False,
            detail=(f"{smtp_host}:{ENV.raw('SMTP_PORT', '587')}" if smtp_host and email_enabled
                    else ("SMTP host is set but notifications are off" if smtp_host
                          else "no SMTP provider configured")),
            hint="Set SMTP_HOST plus ENABLE_EMAIL_NOTIFICATIONS=true so alert email and password-reset links are delivered.",
            mode="smtp" if smtp_host and email_enabled else "in-app-only",
        )
    )

    for provider, key in (
        ("nasa-power", "NASA_POWER_BASE_URL"),
        ("noaa-cdc", "NOAA_API_TOKEN"),
        ("open-meteo", "OPEN_METEO_BASE_URL"),
    ):
        configured = bool(ENV.raw(key)) or provider in {"nasa-power", "open-meteo"}
        statuses.append(
            ServiceStatus(
                name=f"source:{provider}",
                configured=configured,
                required=False,
                detail=("anonymous access, no key required" if provider in {"nasa-power", "open-meteo"}
                        else f"{key} {'present' if configured else 'missing'}"),
                hint="Register a token at the NOAA CDC portal and set NOAA_API_TOKEN to enable that connector.",
            )
        )

    statuses.append(
        ServiceStatus(
            name="registration",
            configured=ENV.flag("ALLOW_SELF_REGISTRATION", False),
            required=False,
            detail="self-service sign-up enabled" if ENV.flag("ALLOW_SELF_REGISTRATION", False)
                   else "sign-up closed; accounts are provisioned by an administrator",
            hint="Set ALLOW_SELF_REGISTRATION=true, or run scripts/provision_admin.py for the first account.",
        )
    )
    return statuses
