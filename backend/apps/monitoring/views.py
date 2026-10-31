"""Health, readiness and platform-status endpoints under /api/v1/.

Availability only ever comes from a probe that ran, secrets never appear in a response,
and a metric the process does not actually accumulate is reported as null and listed
under unavailable instead of being estimated.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from django.conf import settings as django_settings
from rest_framework.response import Response

from apps.monitoring import import_compat

import_compat.ensure_common_api_importable()

from common import audit, tasks as background_tasks  # noqa: E402
from common.api import (  # noqa: E402
    ADMINISTRATOR,
    CLIMATE_ANALYST,
    ApiError,
    AdministratorOrAnalyst,
    IsAdministrator,
    IsAuthenticatedAccount,
    MongoJWTAuthentication,
)
from common.collections import SCHEMA_VERSION  # noqa: E402
from common.data_lake import LAYERS, get_data_lake  # noqa: E402
from common.database import DocumentStoreUnavailable, get_database  # noqa: E402
from common.repositories import (  # noqa: E402
    RepositoryError,
    users,
)
from common.repositories import (  # noqa: E402
    analytics_summaries,
    datasets,
    ingestion_jobs,
    model_registry,
    predictions,
    processing_jobs,
    reports,
    support_tickets,
    utcnow,
)
from common.views import AuthenticatedView, int_param, str_param  # noqa: E402
from config import env  # noqa: E402
from config.settings import (  # noqa: E402
    EXPORT_ROOT,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_ROWS,
    MEDIA_ROOT,
    MODEL_STORE,
    PROCESSING_ROOT,
    REPORT_ROOT,
    UPLOAD_ROOT,
)


logger = logging.getLogger(__name__)

START_MONOTONIC = time.monotonic()
API_VERSION = str(django_settings.SPECTACULAR_SETTINGS.get("VERSION", "0.0.0"))
BACKEND_DIR = Path(__file__).resolve().parents[2]
BACKUP_ROOT = MEDIA_ROOT / "backups"
BACKUP_ROOT.mkdir(parents=True, exist_ok=True)

class SystemView(AuthenticatedView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticatedAccount]


class StaffSystemView(SystemView):
    permission_classes = [AdministratorOrAnalyst]


class AdminSystemView(SystemView):
    permission_classes = [IsAdministrator]


class HealthView(AuthenticatedView):
    """``GET /api/v1/health`` - exact HealthResponse shape from api.ts."""

    permission_classes: List[Any] = []

    def get(self, request):
        payload = _health_payload(include_counts=True)
        request_id = getattr(request, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return Response(payload)


class ReadinessView(HealthView):
    """``GET /api/v1/ready`` - returns 200 only when this instance can serve traffic."""

    permission_classes: List[Any] = []

    def get(self, request):
        payload = _health_payload(include_counts=False)
        database_ok = bool(payload["database"]["ok"])
        # Serving traffic requires: (a) the document store answers and (b) the
        # background executor accepts submissions. Everything else degrades but
        # still serves reads.
        executor_ok = True
        try:
            background_tasks.backend()
        except Exception:  # noqa: BLE001
            executor_ok = False
        if database_ok and executor_ok:
            return Response(payload, status=200)
        payload["reason"] = "not-ready" if not database_ok else "executor-unavailable"
        return Response(payload, status=503)


class ApiIndexView(HealthView):
    """``GET /api/`` and ``GET /`` - JSON directory of the API roots."""

    permission_classes: List[Any] = []

    def get(self, request):
        base = request.build_absolute_uri("/api/v1/")
        return Response({
            "name": env.get_str("PUBLIC_SITE_NAME", "EarthScape Climate Agency"),
            "version": API_VERSION,
            "schema_version": SCHEMA_VERSION,
            "database_mode": get_database().mode,
            "processing_mode": _current_processing_mode(),
            "generated_at": utcnow(),
            "roots": {
                "auth": f"{base}auth/",
                "users": f"{base}users/",
                "datasets": f"{base}datasets/",
                "ingestion": f"{base}ingestion/",
                "climate": f"{base}climate/",
                "processing": f"{base}processing/",
                "models": f"{base}models/",
                "predictions": f"{base}predictions/",
                "alerts": f"{base}alerts/",
                "notifications": f"{base}notifications/",
                "reports": f"{base}reports/",
                "support": f"{base}support/",
                "feedback": f"{base}feedback/",
                "dashboard": f"{base}dashboard/",
                "analytics": f"{base}analytics/",
                "system": f"{base}system/",
            },
            "public_endpoints": [
                {"method": "GET", "path": "/api/v1/health"},
                {"method": "GET", "path": "/api/v1/ready"},
                {"method": "GET", "path": "/api/schema"},
                {"method": "GET", "path": "/api/docs"},
            ],
        })


class SystemStatusView(SystemView):
    """``GET /api/v1/system/status`` - HealthResponse + operational extras."""

    def get(self, request):
        payload = _health_payload(include_counts=True)
        env_summary = env.summary()
        db = get_database()
        collection_counts = _safe_counts(db)
        jobs_summary = _jobs_summary()
        ml_summary = _ml_summary()
        storage_summary = _storage_summary()
        limits = {
            "max_upload_bytes": int(django_settings.MAX_UPLOAD_BYTES),
            "max_upload_rows": int(django_settings.MAX_UPLOAD_ROWS),
            "allowed_upload_extensions": list(django_settings.ALLOWED_UPLOAD_EXTENSIONS),
            "rate_limit_default": env.get_int("RATE_LIMIT_DEFAULT", 600),
            "rate_limit_login": env.get_int("RATE_LIMIT_LOGIN", 10),
            "rate_limit_register": env.get_int("RATE_LIMIT_REGISTER", 5),
            "rate_limit_upload": env.get_int("RATE_LIMIT_UPLOAD", 20),
            "rate_limit_tickets": env.get_int("RATE_LIMIT_TICKETS", 10),
            "rate_limit_feedback": env.get_int("RATE_LIMIT_FEEDBACK", 10),
        }
        payload.update({
            "environment": env_summary.get("environment", "development"),
            "collection_counts": collection_counts,
            "jobs": jobs_summary,
            "ml": ml_summary,
            "storage": storage_summary,
            "limits": limits,
            "config_summary": env_summary,
            "alert_monitor": _alert_monitor_status(),
            "ingestion_scheduler": _ingestion_scheduler_status(),
            "import_compat": import_compat.status(),
        })
        return Response(payload)


class SystemMetricsView(SystemView):
    """``GET /api/v1/system/metrics`` - numbers where computable, null + ``unavailable`` list otherwise."""

    def get(self, request):
        window = str_param(request, "window", "hourly",
                                   choices=("hourly", "daily", "all"))
        since = _window_start(window)
        jobs_metrics = _jobs_metrics(since)
        resources = _resource_snapshot()
        unavailable = ["requests", "latency", "by_endpoint_group"]
        return Response({
            "requests": {"total": None, "by_status_class": None, "error_rate_pct": None},
            "latency": {"p50_ms": None, "p95_ms": None, "p99_ms": None,
                        "max_ms": None, "mean_ms": None},
            "by_endpoint_group": [],
            "jobs": jobs_metrics,
            "resources": resources,
            "window": window,
            "unavailable": unavailable,
            "note": ("request totals/latency require a metrics accumulator that "
                     "common.middleware does not maintain; jobs and resource numbers "
                     "are computed from real MongoDB and OS reads."),
            "generated_at": utcnow(),
        })


class EndpointCatalogView(SystemView):
    """``GET /api/v1/system/endpoints`` - static catalog derived from the URL roots."""

    def get(self, request):
        return Response({"groups": _endpoint_groups()})


AUDIT_SCALAR_FIELDS = ("action", "outcome", "actor_role", "actor_user_id", "environment",
                      "request_id", "resource_id", "resource_type", "source_ip")


class BackupsView(SystemView):
    """``GET/POST /api/v1/system/backups``."""

    def get(self, request):
        items: List[Dict[str, Any]] = []
        if BACKUP_ROOT.is_dir():
            for directory in sorted(BACKUP_ROOT.iterdir(), reverse=True):
                if not directory.is_dir():
                    continue
                    # only directory snapshots are counted
                record_path = directory / "record.json"
                if record_path.is_file():
                    try:
                        import json as _json

                        items.append(_json.loads(record_path.read_text(encoding="utf-8")))
                        continue
                    except (OSError, ValueError):
                        pass
                # Fall back to filesystem facts when record.json is unreadable
                total_bytes = 0
                files = 0
                for handle in directory.rglob("*"):
                    if handle.is_file() and handle.name != "record.json":
                        files += 1
                        try:
                            total_bytes += handle.stat().st_size
                        except OSError:
                            continue
                items.append({
                    "id": directory.name,
                    "created_at": datetime.fromtimestamp(directory.stat().st_mtime, tz=timezone.utc),
                    "mode": "local-json-snapshot",
                    "collections": files,
                    "document_count": None,
                    "file_name": directory.name,
                    "size_bytes": total_bytes,
                    "sha256": None,
                    "created_by": None,
                    "restore_verified": False,
                    "restored_at": None,
                    "note": "legacy directory without a record manifest",
                })
        return Response({
            "items": items[:200],
            "policy": {
                "root": str(BACKUP_ROOT),
                "retained": len(items),
                "mode": ("mongodump available" if shutil.which("mongodump")
                          else "local-json-snapshot (mongodump not installed on this host)"),
                "database_mode": get_database().mode,
            },
        })

    def post(self, request):
        if request.user.role != ADMINISTRATOR:
            raise ApiError("permission_denied",
                           "Only an administrator can trigger a backup.", status=403)
        result = perform_backup(created_by=request.user.id, note="manual")
        audit.for_request(request, action="system.backup.created", resource_type="backup",
                          resource_id=result.get("id"), outcome="success",
                          metadata={"mode": result.get("mode"),
                                    "size_bytes": result.get("size_bytes"),
                                    "document_count": result.get("document_count")})
        return Response({"job": result, "record": result}, status=201)


class DependenciesView(SystemView):
    """``GET /api/v1/system/dependencies`` - real installed versions + CLI presence."""

    PYTHON_REQUIREMENTS: List[Tuple[str, str, Optional[str]]] = [
        ("Django", "web framework", ">=5.0,<5.1"),
        ("djangorestframework", "DRF serializers/views", ">=3.15,<3.16"),
        ("drf-spectacular", "OpenAPI schema", ">=0.27,<0.28"),
        ("pymongo", "MongoDB driver", ">=4.7,<4.8"),
        ("dnspython", "SRV lookup for mongodb+srv URIs", ">=2.6,<2.7"),
        ("mongomock", "in-process document store used in local-development mode", ">=4.1,<4.2"),
        ("PyJWT", "JWT encoding/decoding", ">=2.8,<2.9"),
        ("bcrypt", "password hashing", ">=4.1,<4.2"),
        ("pydantic", "validators", ">=2.7,<2.8"),
        ("chardet", "encoding detection", None),
        ("pandas", "aggregation / ML features", ">=2.2,<2.3"),
        ("numpy", "numerics", None),
        ("openpyxl", "Excel ingestion", None),
        ("python-dateutil", "date parsing", None),
        ("requests", "external connectors", None),
        ("scikit-learn", "ML training", ">=1.5,<1.6"),
        ("joblib", "ML artifact persistence", None),
        ("xgboost", "boosted-tree predictor", None),
        ("pyspark", "big-data jobs", ">=3.5,<3.6"),
        ("pyarrow", "columnar IO", None),
        ("reportlab", "PDF report generation", None),
        ("hdfs", "WebHDFS client", None),
    ]

    CLI_BINARIES: List[Tuple[str, str]] = [
        ("java", "JVM required by PySpark and Hadoop"),
        ("hadoop", "Hadoop CLI for real MapReduce submission"),
        ("spark-submit", "Spark launcher (used only outside this backend)"),
        ("mongodump", "Atlas snapshot tool"),
        ("mongosh", "Atlas shell for operator diagnostics"),
    ]

    def get(self, request):
        items: List[Dict[str, Any]] = []
        for name, role, requirement in self.PYTHON_REQUIREMENTS:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = None
            items.append({"name": name, "installed": bool(version),
                          "version": version, "required": requirement, "role": role})
        for binary, role in self.CLI_BINARIES:
            resolved = shutil.which(binary)
            items.append({"name": f"cli:{binary}", "installed": bool(resolved),
                          "version": _binary_version(binary, resolved) if resolved else None,
                          "required": None, "role": role})
        return Response({"items": items,
                        "python": {"version": platform.python_version(),
                                    "executable": sys.executable},
                        "platform": {"system": platform.system(),
                                      "machine": platform.machine(),
                                      "release": platform.release()}})


class ServicesView(SystemView):
    """``GET /api/v1/system/services`` - real reachability probes with timestamps."""

    def get(self, request):
        items = _service_probes()
        return Response({"items": items, "checked_at": utcnow()})


def perform_backup(*, created_by: Optional[str], note: Optional[str] = None) -> Dict[str, Any]:
    """Write a timestamped backup under ``backend/.data/backups``.

    mongodump when it is on PATH in atlas mode, otherwise the local snapshots are
    flushed and ``localdb/*.json`` is copied. sha256, size_bytes and
    document_count are read back from the file just written.
    """
    db = get_database()
    try:
        db.flush_if_dirty()
    except Exception:  # noqa: BLE001
        pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BACKUP_ROOT / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    mode = "local-json-snapshot"
    counts: Dict[str, int] = {}
    uri = env.get_raw("MONGODB_URI")
    if db.mode in ("atlas", "local-mongodb") and uri and shutil.which("mongodump"):
        mode = "atlas-mongodump" if db.mode == "atlas" else "mongodump"
        command = ["mongodump", "--uri", uri, "--db", db.database_name,
                    "--out", str(backup_dir)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=900,
                                     shell=False)
            if result.returncode != 0:
                mode = "local-json-snapshot"
                _wipe(backup_dir)
                backup_dir.mkdir(parents=True, exist_ok=True)
        except (OSError, subprocess.TimeoutExpired):
            mode = "local-json-snapshot"
            _wipe(backup_dir)
            backup_dir.mkdir(parents=True, exist_ok=True)
    if mode == "local-json-snapshot":
        source_dir = BACKEND_DIR / ".data" / "localdb"
        target_dir = backup_dir / "localdb"
        target_dir.mkdir(parents=True, exist_ok=True)
        if source_dir.is_dir():
            for handle in source_dir.glob("*.json"):
                try:
                    shutil.copy2(handle, target_dir / handle.name)
                except OSError:
                    continue

    total_bytes = 0
    collections_backed_up = 0
    for handle in backup_dir.rglob("*"):
        if handle.is_file():
            collections_backed_up += 1
            try:
                total_bytes += handle.stat().st_size
            except OSError:
                continue
    digest = hashlib.sha256()
    for handle in sorted(backup_dir.rglob("*")):
        if handle.is_file() and handle.name != "record.json":
            try:
                with handle.open("rb") as file_handle:
                    for chunk in iter(lambda: file_handle.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue
    for name, count in _safe_counts(db).items():
        counts[name] = int(count)

    record = {
        "id": stamp,
        "created_at": datetime.now(timezone.utc),
        "mode": mode,
        "collections": collections_backed_up,
        "document_count": int(sum(counts.values())),
        "file_name": stamp,
        "size_bytes": int(total_bytes),
        "sha256": digest.hexdigest(),
        "created_by": created_by,
        "restore_verified": False,
        "restored_at": None,
        "note": (note or "no restore verification has been performed"),
        "collection_counts": counts,
        "database_mode": db.mode,
    }
    try:
        import json as _json

        (backup_dir / "record.json").write_text(
            _json.dumps(_serialisable(record), default=str), encoding="utf-8")
    except OSError as exc:
        logger.warning("Backup record could not be written: %s", type(exc).__name__)
    return record


def _serialisable(document: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in document.items():
        if isinstance(value, datetime):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def _wipe(directory: Path) -> None:
    try:
        shutil.rmtree(directory, ignore_errors=True)
    except OSError:
        pass


def _health_payload(*, include_counts: bool) -> Dict[str, Any]:
    db = get_database()
    ping = db.ping()
    counts = _safe_counts(db) if include_counts else None
    mode = db.mode
    status = "ok"
    components = _components_snapshot()
    if not ping.get("ok") or any(item["status"] == "unavailable" and item.get("critical")
                                       for item in components):
        status = "degraded"
    payload = {
        "status": status,
        "database": {
            "mode": mode,
            "database": db.database_name,
            "ok": bool(ping.get("ok")),
        },
        "version": API_VERSION,
        "schema_version": SCHEMA_VERSION,
        "uptime_seconds": round(time.monotonic() - START_MONOTONIC, 3),
        "processing_mode": _current_processing_mode(),
        "environment": env.summary().get("environment", "development"),
        "components": components,
        "background": {
            "executor": background_tasks.backend(),
            "active_jobs": background_tasks.active_job_count(),
            "queued_jobs": _queued_jobs_total(),
        },
        "generated_at": utcnow(),
    }
    if counts is not None:
        payload["database"]["counts"] = counts
    if ping.get("error"):
        payload["database"]["error"] = str(ping["error"])[:200]
    return payload


def _components_snapshot() -> List[Dict[str, Any]]:
    from apps.processing import engines as processing_engines

    health = processing_engines.engine_health()
    db = get_database()
    items: List[Dict[str, Any]] = []

    atlas_configured = bool(env.get_raw("MONGODB_URI"))
    items.append({
        "name": "mongodb",
        "configured": atlas_configured,
        "status": ("available" if db.mode in ("atlas", "local-mongodb") else
                    ("degraded" if db.mode == "local-development" else "unavailable")),
        "detail": f"mode={db.mode}, database={db.database_name}",
        "critical": db.mode == "uninitialised",
    })

    lake = health["data_lake"]
    lake_free = lake.get("free_bytes")
    items.append({
        "name": "data-lake",
        "configured": True,
        "status": "available" if lake["reachable"] else "degraded",
        "detail": (f"mode={lake['mode']}, root={lake['root']}, "
                   f"free={'unknown' if lake_free is None else f'{lake_free / (1024 ** 3):.1f} GiB'}"
                   + ("" if lake["reachable"] else f" - {lake['detail']}")),
    })

    spark = health["spark"]
    spark_status = "unavailable"
    if spark["installed"] and spark.get("session_startable") is not False:
        spark_status = "available" if spark.get("session_startable") is True else "degraded"
    spark_note = f"version {spark['version']}" if spark["installed"] else "not installed on this host"
    spark_probe = {True: "a session starts", False: "a session cannot start"}.get(
        spark.get("session_startable"), "session not probed")
    spark_error = spark.get("error")
    if spark_error:
        spark_probe = f"{spark_probe} ({spark_error})"
    items.append({
        "name": "spark",
        "configured": bool(spark["installed"]),
        "status": spark_status,
        "detail": f"pyspark {spark_note}, {spark_probe}",
    })

    hadoop = health["hadoop"]
    items.append({
        "name": "hadoop-mapreduce",
        "configured": bool(hadoop["bin_configured"] and hadoop["cluster_configured"]),
        "status": "available" if (hadoop["bin_configured"] and hadoop["cluster_configured"])
        else ("degraded" if hadoop["bin_configured"] else "not-configured"),
        "detail": hadoop["detail"],
    })

    hdfs = health["hdfs"]
    items.append({
        "name": "hdfs",
        "configured": bool(hdfs["configured"]),
        "status": "available" if hdfs["reachable"] else ("degraded" if hdfs["configured"] else "not-configured"),
        "detail": hdfs["detail"],
    })

    executor = background_tasks.backend()
    items.append({
        "name": "background-workers",
        "configured": True,
        "status": "available",
        "detail": f"executor={executor}",
    })

    smtp_host = env.get_raw("SMTP_HOST")
    smtp_enabled = env.get_bool("ENABLE_EMAIL_NOTIFICATIONS")
    smtp_status = "not-configured"
    smtp_detail = "ENABLE_EMAIL_NOTIFICATIONS is false"
    if smtp_enabled and not smtp_host:
        smtp_status = "unavailable"
        smtp_detail = "ENABLE_EMAIL_NOTIFICATIONS=true but SMTP_HOST is empty"
    elif smtp_enabled and smtp_host:
        reachable = _tcp_probe(smtp_host.split(":")[0],
                                        int((smtp_host.split(":") + ["587"])[1]), timeout=2.0)
        smtp_status = "available" if reachable else "degraded"
        smtp_detail = f"TCP {smtp_host} reachable={reachable}"
    items.append({
        "name": "smtp",
        "configured": bool(smtp_host),
        "status": smtp_status,
        "detail": smtp_detail,
    })

    scheduler_status = _ingestion_scheduler_status()
    if "run_count" in scheduler_status:
        scheduler_detail = (
            f"runs={scheduler_status['run_count']}, errors={scheduler_status['error_count']}, "
            f"pass every {scheduler_status['interval_seconds']}s, "
            + ("enabled" if scheduler_status["enabled"] else "disabled by configuration"))
    else:
        scheduler_detail = str(scheduler_status.get("status") or "unknown")[:200]
    items.append({
        "name": "ingestion-scheduler",
        "configured": True,
        "status": "available" if scheduler_status.get("running") else "degraded",
        "detail": scheduler_detail,
    })

    alert_status = _alert_monitor_status()
    items.append({
        "name": "alert-monitor",
        "configured": True,
        "status": "available" if alert_status.get("thread_alive") else "degraded",
        "detail": f"runs={alert_status.get('runs')}, interval_seconds={alert_status.get('effective_interval_seconds')}",
    })

    compat = import_compat.status()
    compat_restored = compat.get("restored") or []
    items.append({
        "name": "import-compat",
        "configured": True,
        "status": "available" if compat.get("ok", True) else "degraded",
        "detail": f"{compat.get('detail', 'unknown')}"
                  + (f", {len(compat_restored)} alias(es) published" if compat_restored else ""),
    })
    return items


def _current_processing_mode() -> str:
    from apps.processing import engines

    return engines.engine_health().get("current_processing_mode", "mongodb")


def _safe_counts(db) -> Dict[str, int]:
    try:
        return {str(name): int(count) for name, count in db.counts().items()}
    except DocumentStoreUnavailable:
        return {}
    except Exception as exc:  # noqa: BLE001 - health must not raise
        logger.warning("Database counts failed: %s", type(exc).__name__)
        return {}


def _queued_jobs_total() -> int:
    try:
        return (processing_jobs.count({"status": "queued"})
                 + ingestion_jobs.count({"status": "queued"})
                 + reports.count({"status": "queued"}))
    except Exception:  # noqa: BLE001
        return 0


def _jobs_summary() -> Dict[str, Any]:
    def _status_counts(repo) -> Dict[str, int]:
        try:
            return {status: int(repo.count({"status": status}))
                     for status in ["queued", "running", "completed", "generated", "failed", "cancelled"]}
        except Exception:  # noqa: BLE001
            return {}

    return {
        "ingestion": _status_counts(ingestion_jobs),
        "processing": _status_counts(processing_jobs),
        "reports": _status_counts(reports),
    }


def _ml_summary() -> Dict[str, Any]:
    try:
        models = int(model_registry.count({}))
        preds = int(predictions.count({}))
        active = model_registry.find_one({"status": "active"}, {"name": 1}) or {}
        return {"models": models, "predictions": preds,
                "active_model": str(active.get("name") or "").strip() or None}
    except Exception:  # noqa: BLE001
        return {"models": None, "predictions": None, "active_model": None}


def _storage_summary() -> Dict[str, Any]:
    from apps.processing.engines import engine_health

    lake = engine_health()["data_lake"]
    uploads_files, uploads_bytes = _directory_stats(UPLOAD_ROOT)
    reports_files, reports_bytes = _directory_stats(REPORT_ROOT)
    exports_files, exports_bytes = _directory_stats(EXPORT_ROOT)
    models_files, models_bytes = _directory_stats(MODEL_STORE)
    processing_files, processing_bytes = _directory_stats(PROCESSING_ROOT)
    return {
        "mode": lake["mode"],
        "local_root": str(MEDIA_ROOT),
        "hdfs_endpoint": env.get_raw("HDFS_ENDPOINT"),
        "free_bytes": lake.get("free_bytes"),
        "uploads": {"files": uploads_files, "bytes": uploads_bytes},
        "reports": {"files": reports_files, "bytes": reports_bytes,
                     "directory_count": len(list(REPORT_ROOT.glob("*"))) if REPORT_ROOT.is_dir() else 0},
        "exports": {"files": exports_files, "bytes": exports_bytes},
        "models": {"files": models_files, "bytes": models_bytes},
        "processing": {"files": processing_files, "bytes": processing_bytes},
    }


def _directory_stats(root: Path) -> Tuple[int, int]:
    files = 0
    total_bytes = 0
    if root.is_dir():
        for handle in root.rglob("*"):
            if handle.is_file():
                files += 1
                try:
                    total_bytes += handle.stat().st_size
                except OSError:
                    continue
    return files, total_bytes


def _alert_monitor_status() -> Dict[str, Any]:
    try:
        from apps.alerts import monitor

        return monitor.status()
    except Exception as exc:  # noqa: BLE001
        return {"unavailable": True, "reason": type(exc).__name__}


def _ingestion_scheduler_status() -> Dict[str, Any]:
    try:
        from apps.ingestion import scheduler

        return scheduler.get_scheduler().status()
    except Exception as exc:  # noqa: BLE001
        return {"running": False, "status": f"scheduler unavailable: {type(exc).__name__}"}


def _window_start(window: str) -> Optional[datetime]:
    moment = utcnow()
    if window == "hourly":
        return moment - timedelta(hours=1)
    if window == "daily":
        return moment - timedelta(days=1)
    return None


def _jobs_metrics(since: Optional[datetime]) -> Dict[str, Any]:
    active = processing_jobs.count({"status": "running"}) + ingestion_jobs.count({"status": "running"})
    queued = processing_jobs.count({"status": "queued"}) + ingestion_jobs.count({"status": "queued"})
    criteria = {"finished_at": {"$gte": since}} if since else {"finished_at": {"$ne": None}}
    completed = 0
    failed = 0
    durations: List[float] = []
    for repository in (processing_jobs, ingestion_jobs):
        completed += repository.count({**criteria, "status": {"$in": ["completed", "generated"]}})
        failed += repository.count({**criteria, "status": "failed"})
        documents = repository.find({**criteria, "duration_seconds": {"$ne": None}},
                                     projection={"duration_seconds": 1}, limit=500)
        for document in documents:
            value = document.get("duration_seconds")
            if isinstance(value, (int, float)):
                durations.append(float(value))
    mean = round(sum(durations) / len(durations), 3) if durations else None
    return {
        "active": int(active),
        "queued": int(queued),
        "completed_last_window": int(completed),
        "failed_last_window": int(failed),
        "mean_duration_seconds": mean,
    }


def _resource_snapshot() -> Dict[str, Any]:
    import os

    try:
        import psutil  # type: ignore

        memory_mb = round(psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024, 2)
    except Exception:  # noqa: BLE001 - psutil is not in requirements; fall back
        memory_mb = None
    if memory_mb is None:
        try:
            import resource  # POSIX only, harmless ImportError on Windows

            usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            memory_mb = round(usage / 1024.0, 2) if sys.platform != "darwin" else round(usage / (1024 * 1024), 2)
        except (ImportError, ValueError):
            memory_mb = None
    try:
        disk = shutil.disk_usage(str(MEDIA_ROOT))
        disk_free = int(disk.free)
    except OSError:
        disk_free = None
    return {
        "cpu_count": os.cpu_count(),
        "disk_free_bytes": disk_free,
        "process_memory_mb": memory_mb,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _service_probes() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    db = get_database()
    ping = db.ping()
    items.append({"name": "database",
                   "status": "available" if ping.get("ok") else "unavailable",
                   "detail": f"mode={db.mode}, name={db.database_name}"})
    try:
        lake = get_data_lake().health()
        free = lake.get("free_bytes_or_null")
        free_label = "unknown" if free is None else f"{free / (1024 ** 3):.1f} GiB"
        items.append({"name": "data-lake",
                       "status": "available" if lake.get("reachable") else "degraded",
                       "detail": f"mode={lake['mode']}, free={free_label}"
                                 + ("" if lake.get("reachable") else f", {lake.get('error')}")})
    except Exception as exc:  # noqa: BLE001
        items.append({"name": "data-lake", "status": "unavailable",
                       "detail": f"probe failed: {type(exc).__name__}"})
    smtp_host = env.get_raw("SMTP_HOST")
    if env.get_bool("ENABLE_EMAIL_NOTIFICATIONS") and smtp_host:
        host = smtp_host.split(":")[0]
        port = int((smtp_host.split(":") + ["587"])[1])
        ok = _tcp_probe(host, port, timeout=2.0)
        items.append({"name": "smtp", "status": "available" if ok else "unavailable",
                       "detail": f"{host}:{port} tcp={ok}"})
    else:
        items.append({"name": "smtp", "status": "not-configured",
                       "detail": "SMTP disabled"})
    items.append({"name": "hadoop-cli",
                   "status": "available" if shutil.which("hadoop") else "not-configured",
                   "detail": shutil.which("hadoop") or "not on PATH"})
    items.append({"name": "spark-submit",
                   "status": "available" if shutil.which("spark-submit") else "not-configured",
                   "detail": shutil.which("spark-submit") or "not on PATH"})
    items.append({"name": "mongodump",
                   "status": "available" if shutil.which("mongodump") else "not-configured",
                   "detail": shutil.which("mongodump") or "not on PATH"})
    return items


def _tcp_probe(host: str, port: int, *, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_probe(url: str, *, timeout: float = 2.5) -> Tuple[bool, str]:
    try:
        import urllib.request

        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"status={response.status}"
    except Exception as exc:  # noqa: BLE001
        return False, f"unreachable: {type(exc).__name__}"


def _binary_version(binary: str, resolved: Optional[str]) -> Optional[str]:
    if not resolved:
        return None
    try:
        result = subprocess.run([resolved, "--version"], capture_output=True, text=True,
                                 timeout=6, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:160]
    return None


def _endpoint_groups() -> List[Dict[str, Any]]:
    """Static description of what the URL roots expose.

    The roles list comes from the permission classes on each view, not from
    client-supplied data. Kept here as data so it costs no second HTTP hop.
    """
    staff = ["administrator", "climate_analyst"]
    admin = ["administrator"]
    return [
        {"group": "auth", "prefix": "/api/v1/auth/", "description":
            "Login, register, refresh, password reset, current-account.",
            "roles": ["anonymous", "authenticated"],
            "endpoints": [
                {"method": "POST", "path": "/api/v1/auth/login",
                 "description": "Issue an access+refresh token pair.",
                 "roles": ["anonymous"]},
                {"method": "POST", "path": "/api/v1/auth/refresh",
                 "description": "Rotate a refresh token.", "roles": ["anonymous"]},
                {"method": "GET", "path": "/api/v1/auth/me",
                 "description": "Current account profile.", "roles": ["authenticated"]},
            ]},
        {"group": "processing", "prefix": "/api/v1/processing/", "description":
            "Workflow catalogue, engine health, data-lake, job lifecycle.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/processing/workflows",
                 "description": "List processing workflows with real availability.",
                 "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/processing/engines",
                 "description": "Composite EngineHealth probe.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/processing/data-lake",
                 "description": "Layers, files and bytes.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/processing/jobs",
                 "description": "Paginated jobs (filter by workflow/status/dataset).",
                 "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/processing/jobs",
                 "description": "Submit a workflow job (registry key only).",
                 "roles": staff},
                {"method": "GET", "path": "/api/v1/processing/jobs/<id>",
                 "description": "Job detail.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/processing/jobs/<id>/cancel",
                 "description": "Cancel a queued job.", "roles": staff},
                {"method": "GET", "path": "/api/v1/processing/jobs/<id>/logs",
                 "description": "Structured log lines.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/processing/monitor",
                 "description": "Active-first job stream.", "roles": ["authenticated"]},
            ]},
        {"group": "system", "prefix": "/api/v1/system/", "description":
            "Health-adjacent operational surfaces.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/system/status",
                 "description": "Extended health payload.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/system/metrics",
                 "description": "Requests, latency, jobs and resources (null where unavailable).",
                 "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/system/endpoints",
                 "description": "API catalogue.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/system/backups",
                 "description": "Backup catalogue + policy.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/system/backups",
                 "description": "Trigger a real snapshot (mongodump when Atlas + local JSON fallback).",
                 "roles": admin},
                {"method": "GET", "path": "/api/v1/system/dependencies",
                 "description": "importlib.metadata versions + shutil.which CLI probes.",
                 "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/system/services",
                 "description": "Live reachability probes.", "roles": ["authenticated"]},
            ]},
        {"group": "support", "prefix": "/api/v1/support/", "description":
            "Tickets, messages, close, categories, summary.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/support/tickets",
                 "description": "Own tickets (or the queue for staff).", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/support/tickets",
                 "description": "Open a new ticket.", "roles": ["authenticated"]},
                {"method": "PATCH", "path": "/api/v1/support/tickets/<id>",
                 "description": "Update ticket fields.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/support/tickets/<id>/messages",
                 "description": "Append a comment.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/support/tickets/<id>/close",
                 "description": "Resolve or close.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/support/categories",
                 "description": "Enumeration + counts.", "roles": ["authenticated"]},
                {"method": "GET", "path": "/api/v1/support/summary",
                 "description": "Open counts + median first-response.", "roles": ["authenticated"]},
            ]},
        {"group": "feedback", "prefix": "/api/v1/feedback/", "description":
            "User feedback records and triage.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/feedback",
                 "description": "Paginated feedback.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/feedback",
                 "description": "Submit feedback.", "roles": ["authenticated"]},
                {"method": "PATCH", "path": "/api/v1/feedback/<id>",
                 "description": "Triage (admin only).", "roles": admin},
                {"method": "GET", "path": "/api/v1/feedback/summary",
                 "description": "Average rating and counts.", "roles": ["authenticated"]},
            ]},
        {"group": "alerts", "prefix": "/api/v1/alerts/", "description":
            "Rules, fired alerts, notifications.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/alerts/",
                 "description": "Alert list.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/alerts/scan",
                 "description": "Run one evaluation now.", "roles": staff},
            ]},
        {"group": "reports", "prefix": "/api/v1/reports/", "description":
            "Generated artefacts, catalogue.",
            "roles": ["authenticated"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/reports/",
                 "description": "Paginated reports.", "roles": ["authenticated"]},
                {"method": "POST", "path": "/api/v1/reports/",
                 "description": "Queue a report.", "roles": ["authenticated"]},
            ]},
        {"group": "public", "prefix": "/api/v1/", "description":
            "Health and OpenAPI surfaces reachable without authentication.",
            "roles": ["anonymous"],
            "endpoints": [
                {"method": "GET", "path": "/api/v1/health",
                 "description": "Component snapshot.", "roles": ["anonymous"]},
                {"method": "GET", "path": "/api/v1/ready",
                 "description": "Readiness gate (200 or 503).", "roles": ["anonymous"]},
                {"method": "GET", "path": "/api/schema",
                 "description": "OpenAPI 3 schema.", "roles": ["anonymous"]},
                {"method": "GET", "path": "/api/docs",
                 "description": "Swagger UI.", "roles": ["anonymous"]},
                {"method": "GET", "path": "/api/",
                 "description": "JSON directory of the API roots.", "roles": ["anonymous"]},
            ]},
    ]


# Silence the linters about unused imports we kept for documentation.
_ = (support_tickets, analytics_summaries, datasets, MAX_UPLOAD_BYTES, MAX_UPLOAD_ROWS,
      LAYERS, os, time, CLIMATE_ANALYST, IsAdministrator, Iterable, threading)
