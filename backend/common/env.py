"""Environment loading and startup validation.

Every external dependency (Atlas, HDFS, Spark, Kafka, SMTP, provider API keys) is
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


def development_signing_key() -> str:
    """Persist a local-only signing key so dev tokens survive a server restart.

    Production must set DJANGO_SECRET_KEY; a generated key is never committed.
    """
    key_path = REPO_ROOT / "artifacts" / ".dev-signing-key"
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
    configured = ENV.raw("DATA_LAKE_ROOT", str(REPO_ROOT / "artifacts" / "datalake"))
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_artifact_root() -> Path:
    configured = ENV.raw("MODEL_ARTIFACT_ROOT", str(REPO_ROOT / "artifacts" / "models"))
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_root() -> Path:
    configured = ENV.raw("REPORT_STORAGE_ROOT", str(REPO_ROOT / "artifacts" / "reports"))
    path = Path(configured)
    if not path.is_absolute():
        path = REPO_ROOT / path
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

    mongo_uri = ENV.raw("MONGODB_URI")
    statuses.append(
        ServiceStatus(
            name="mongodb",
            configured=bool(mongo_uri),
            required=True,
            detail=(f"Target { _split_uri(mongo_uri)[0] } / db {ENV.raw('MONGODB_DATABASE', 'earthscape_climate')}"
                    if mongo_uri else "MONGODB_URI is not set"),
            hint="Point MONGODB_URI at MongoDB Atlas or a local mongod, then run scripts/migrate_mongo.py.",
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
            hint="Set HDFS_WEBHDFS_URL plus HADOOP_USER and start infrastructure/hadoop to use real HDFS.",
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
            hint="pip install pyspark (needs a JDK) or start infrastructure/spark for cluster mode.",
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

    kafka_brokers = ENV.raw("KAFKA_BROKERS")
    statuses.append(
        ServiceStatus(
            name="kafka",
            configured=bool(kafka_brokers),
            required=False,
            detail=(f"brokers {kafka_brokers}" if kafka_brokers else "not configured; polling ingestion used"),
            hint="Start infrastructure/kafka, set KAFKA_BROKERS, then enable EVENT_STREAM_ENABLED=true.",
            mode="kafka" if kafka_brokers else "polling",
        )
    )

    redis_url = ENV.raw("REDIS_URL")
    celery_enabled = ENV.flag("CELERY_ENABLED", False)
    statuses.append(
        ServiceStatus(
            name="background-tasks",
            configured=bool(redis_url) and celery_enabled,
            required=False,
            detail=(f"redis {redis_url}" if (redis_url and celery_enabled)
                    else "in-process thread worker pool"),
            hint="Set CELERY_ENABLED=true with a reachable REDIS_URL to run tasks on Celery workers.",
            mode="celery" if (redis_url and celery_enabled) else "threaded",
        )
    )

    smtp_host = ENV.raw("EMAIL_HOST")
    statuses.append(
        ServiceStatus(
            name="email",
            configured=bool(smtp_host),
            required=False,
            detail=f"{smtp_host}:{ENV.raw('EMAIL_PORT', '587')}" if smtp_host else "no SMTP provider configured",
            hint="Email notifications stay disabled (in-app only) until SMTP settings are valid.",
            mode="smtp" if smtp_host else "in-app-only",
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
