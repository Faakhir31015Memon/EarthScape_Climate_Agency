"""Administrator surfaces: audit log, platform settings, operations metrics, log tail.

Every number in ``metrics`` is read out of data that actually exists:

* ``audit_activity``        - ``audit_logs`` documents (written by ``AuditTrailMiddleware``
                              and the explicit ``common.audit.audit`` calls).
* ``background_jobs``       - ``processing_jobs`` (the ``common.tasks`` job record store).
* ``ingestion_activity``     - ``ingestion_jobs`` documents.
* ``collection_sizes``       - ``estimatedDocumentCount`` per collection (Mongo's own stats,
                              not a query over rows).
* ``request_latency``       - the structured ``http_request`` records in
                              ``artifacts/logs/backend.log`` (bounded tail read).
* ``report_storage``        - the real filesystem under ``report_root()``/``storage_root()``.

When a source has no rows the metric is reported as ``null`` with an explicit ``reason``;
nothing here is interpolated or defaulted to a plausible-looking value.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from django.conf import settings

from common.audit import audit
from common.db import (COLLECTIONS, aggregate, collection, count, db, database_name, find,
                       find_one, now_utc, oid)
from common.env import ARTIFACT_ROOT, BACKEND_DIR, ENV, report_root, storage_root
from common.exceptions import Conflict, Forbidden, NotFound, ValidationError
from common.logging_config import get_logger, redact
from common.pagination import normalize_pagination
from common.responses import paginated
from common.security import guard_query, sanitize_text
from common.serialization import document_to_api, parse_datetime
from common.tasks import engine as task_engine
from common.validators import SettingsPatchRequest, parse
from common.views import ApiView

logger = get_logger("earthscape.monitoring.admin")

SECRET_MARKERS = ("key", "token", "password", "secret", "uri")
INTERNAL_KEY_PREFIX = "counter."
MAX_SETTING_CHARS = ENV.integer("PLATFORM_SETTING_MAX_CHARS", 2000)
LOG_FILE = Path(str(getattr(settings, "LOG_DIR", ARTIFACT_ROOT / "logs"))) / "backend.log"
LOG_TAIL_LINES = ENV.integer("OPS_LOG_TAIL_LINES", 400)
LOG_TAIL_MAX_LINES = 5000
LOG_TAIL_MAX_BYTES = 4_000_000
METRIC_SCAN_LIMIT = 5000
OUTCOMES = ("success", "failed", "denied", "not_found", "blocked", "error")

_SETTING_COLUMNS = {"key": 1, "value": 1, "category": 1, "description": 1, "kind": 1,
                    "scope": 1, "updated_at": 1, "created_at": 1, "updated_by": 1}


# --------------------------------------------------------------------- masking helpers


def is_secret_key(name: Any) -> bool:
    lowered = str(name or "").lower()
    return any(marker in lowered for marker in SECRET_MARKERS)


def value_shape(value: Any) -> dict[str, Any]:
    """Describe a setting without revealing it: type, size and a short preview of the shape."""
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(str(k) for k in value)[:20], "members": len(value)}
    if isinstance(value, (list, tuple)):
        return {"type": "array", "items": len(value),
                "element_types": sorted({type(item).__name__ for item in value})[:6]}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string", "characters": len(value)}
    if value is None:
        return {"type": "null"}
    return {"type": type(value).__name__}


def present_setting(document: dict) -> dict[str, Any]:
    key = str(document.get("key") or "")
    raw = document.get("value")
    masked = is_secret_key(key)
    payload: dict[str, Any] = {
        "key": key,
        "value": None if masked else raw,
        "masked": masked,
        "value_shape": value_shape(raw),
        "category": document.get("category"),
        "description": document.get("description"),
        "kind": document.get("kind"),
        "scope": document.get("scope"),
        "updated_at": document.get("updated_at").isoformat()
        if isinstance(document.get("updated_at"), datetime) else document.get("updated_at"),
        "updated_by": document.get("updated_by"),
    }
    if masked:
        payload["reason"] = ("Secret-valued setting: names containing "
                             f"{', '.join(SECRET_MARKERS)} are never returned over the API.")
    return payload


# ------------------------------------------------------------------------ audit logs


def audit_query(params: dict[str, str]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if params.get("identity"):
        query["identity"] = sanitize_text(params["identity"], max_length=64)
    if params.get("action"):
        query["action"] = {"$regex": f"^{sanitize_text(params['action'], max_length=60)}",
                           "$options": "i"}
    if params.get("resource_type"):
        query["resource_type"] = sanitize_text(params["resource_type"], max_length=48)
    if params.get("resource_id"):
        query["resource_id"] = sanitize_text(params["resource_id"], max_length=64)
    if params.get("outcome"):
        outcome = sanitize_text(params["outcome"], max_length=24)
        if outcome not in OUTCOMES:
            raise ValidationError(f"Unknown outcome '{outcome}'. Known: {', '.join(OUTCOMES)}.")
        query["outcome"] = outcome
    if params.get("correlation_id"):
        query["correlation_id"] = sanitize_text(params["correlation_id"], max_length=64)
    window: dict[str, Any] = {}
    started = parse_datetime(params.get("from"))
    finished = parse_datetime(params.get("to"))
    if started:
        window["$gte"] = started
    if finished:
        window["$lte"] = finished
    if window:
        query["occurred_at"] = window
    return query


class AuditLogListView(ApiView):
    """Read-only audit trail. No filter means the whole (indexed) timeline, newest first."""

    required_capability = "audit.view"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        page, size, skip = normalize_pagination(request.query_params)
        query = audit_query(params)
        table = collection("audit_logs")
        total = table.count_documents(query)
        rows = find("audit_logs", query, sort=[("occurred_at", -1)], skip=skip, limit=size)
        outcome_counts = {row["_id"]: row["count"] for row in aggregate("audit_logs", [
            {"$match": {k: v for k, v in query.items() if k != "outcome"}},
            {"$group": {"_id": "$outcome", "count": {"$sum": 1}}},
        ])} if total else {}
        return paginated([document_to_api(row) for row in rows], total=total, page=page,
                         page_size=size,
                         extra={"filters": {k: v for k, v in params.items()
                                            if k not in ("page", "page_size")},
                                "outcome_counts": outcome_counts,
                                "source": "audit_logs collection"},
                         request=request)


class AuditLogDetailView(ApiView):
    required_capability = "audit.view"
    http_method_names = ["get"]

    def get(self, request, log_id: str):
        document = find_one("audit_logs", {"_id": oid(log_id)}) if oid(log_id) else None
        if not document:
            raise NotFound("Audit log entry not found.")
        return self.ok(request, document_to_api(document))


# ------------------------------------------------------------------ platform settings


class PlatformSettingListView(ApiView):
    """GET lists settings with secret values masked; PATCH-style writes use the detail view."""

    required_capability = "settings.manage"

    def get(self, request):
        params = self.params(request)
        page, size, skip = normalize_pagination(request.query_params)
        query: dict[str, Any] = {"key": {"$not": f"^{INTERNAL_KEY_PREFIX}"}}
        if params.get("category"):
            query["category"] = sanitize_text(params["category"], max_length=40)
        if params.get("search"):
            query["key"] = {"$regex": sanitize_text(params["search"], max_length=60),
                            "$options": "i"}
        total = count("platform_settings", query)
        rows = find("platform_settings", query, sort=[("key", 1)], skip=skip, limit=size,
                    projection=_SETTING_COLUMNS)
        return paginated([present_setting(row) for row in rows], total=total, page=page,
                         page_size=size,
                         extra={"masked_markers": list(SECRET_MARKERS),
                                "hidden_prefix": INTERNAL_KEY_PREFIX,
                                "hidden_reason": "internal atomic counters (for example the "
                                                 "support-ticket sequence) are not settings"},
                         request=request)


class PlatformSettingDetailView(ApiView):
    """Read or write one setting by key. Writes are audited and never echo the value back."""

    required_capability = "settings.manage"

    def get(self, request, key: str):
        document = self._require(key)
        return self.ok(request, present_setting(document))

    def patch(self, request, key: str):
        payload = parse(SettingsPatchRequest, self.body(request))
        if payload.key != key:
            raise ValidationError("The body 'key' must match the key in the URL.")
        if key.startswith(INTERNAL_KEY_PREFIX):
            raise Forbidden(f"'{key}' is an internal atomic counter and cannot be edited here.")
        if is_secret_key(key) and not isinstance(payload.value, str):
            raise ValidationError("Secret-valued settings must be written as a string.")
        if len(json.dumps(payload.value, default=str)) > MAX_SETTING_CHARS:
            raise ValidationError(f"Setting value exceeds the {MAX_SETTING_CHARS} character limit.")
        try:
            guarded = guard_query(payload.value)
        except ValueError as exc:
            raise ValidationError(str(exc)) from None
        existing = find_one("platform_settings", {"key": key}, {"_id": 1})
        if existing and existing.get("kind") == "sequence":
            raise Conflict(f"'{key}' is maintained by the platform (kind=sequence) and is read-only.")
        moment = now_utc()
        collection("platform_settings").update_one(
            {"key": key},
            {"$set": {"key": key, "value": guarded, "updated_at": moment,
                      "updated_by": str(request.auth_identity.user_id)},
             "$setOnInsert": {"created_at": moment}},
            upsert=True,
        )
        stored = self._require(key)
        audit(request, "platform_setting.update", resource_type="platform_settings",
              resource_id=key,
              metadata={"key": key, "created": existing is None,
                        "masked": is_secret_key(key), "value_shape": value_shape(guarded)})
        return self.ok(request, {"setting": present_setting(stored),
                                 "previous_existed": existing is not None,
                                 "audited": True})

    @staticmethod
    def _require(key: str) -> dict:
        clean = sanitize_text(key, max_length=80)
        document = find_one("platform_settings", {"key": clean}, _SETTING_COLUMNS)
        if not document:
            raise NotFound(f"Platform setting '{clean}' does not exist. "
                           "PATCH creates settings, GET does not.")
        return document


# -------------------------------------------------------------------- ops metrics


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    step = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * step, 2)


def _window(params: dict[str, str], default_days: int) -> tuple[datetime, datetime]:
    until = parse_datetime(params.get("to")) or now_utc()
    since = parse_datetime(params.get("from")) or (until - timedelta(days=default_days))
    if since > until:
        raise ValidationError("'from' must be before 'to'.")
    return since, until


def _duration_ms(started: Any, finished: Any) -> Optional[float]:
    if isinstance(started, datetime) and isinstance(finished, datetime):
        return max(0.0, (finished - started).total_seconds() * 1000)
    return None


def _audit_activity(since: datetime, until: datetime) -> dict[str, Any]:
    match = {"occurred_at": {"$gte": since, "$lte": until}}
    grouped = aggregate("audit_logs", [
        {"$match": match},
        {"$group": {"_id": None, "records": {"$sum": 1},
                    "actors": {"$addToSet": "$identity"},
                    "first_at": {"$min": "$occurred_at"}, "last_at": {"$max": "$occurred_at"},
                    "successes": {"$sum": {"$cond": [{"$eq": ["$outcome", "success"]}, 1, 0]}},
                    "denials": {"$sum": {"$cond": [{"$eq": ["$outcome", "denied"]}, 1, 0]}},
                    "failures": {"$sum": {"$cond": [{"$eq": ["$outcome", "failed"]}, 1, 0]}},
                    "errors": {"$sum": {"$cond": [{"$eq": ["$outcome", "error"]}, 1, 0]}},
                    "blocked": {"$sum": {"$cond": [{"$eq": ["$outcome", "blocked"]}, 1, 0]}}}},
    ])
    by_action = aggregate("audit_logs", [
        {"$match": match},
        {"$group": {"_id": {"action": "$action", "outcome": "$outcome"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}, {"$limit": 40},
    ])
    row = grouped[0] if grouped else None
    total = int(row["records"]) if row else 0
    return {
        "source": "audit_logs (occurred_at window; ix_audit_time index)",
        "window": {"from": since.isoformat(), "to": until.isoformat()},
        "records": total,
        "distinct_actors": len(row["actors"]) if row else 0,
        "first_at": row["first_at"].isoformat() if row and isinstance(row.get("first_at"), datetime) else None,
        "last_at": row["last_at"].isoformat() if row and isinstance(row.get("last_at"), datetime) else None,
        "outcomes": {name: int(row[name]) for name in
                     ("successes", "denials", "failures", "errors", "blocked")} if row else {},
        "failure_rate": round(((row["failures"] + row["errors"]) / total), 4) if total else None,
        "top_actions": [{"action": item["_id"].get("action"), "outcome": item["_id"].get("outcome"),
                         "count": item["count"]} for item in by_action],
        "reason": None if total else "no audit_logs documents inside the requested window",
    }


def _job_metrics(since: datetime, until: datetime) -> dict[str, Any]:
    match = {"created_at": {"$gte": since, "$lte": until}}
    statuses = aggregate("processing_jobs", [{"$match": match},
                                             {"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    by_kind = aggregate("processing_jobs", [
        {"$match": match},
        {"$group": {"_id": {"kind": "$kind", "status": "$status"}, "count": {"$sum": 1},
                    "durations": {"$push": {"$cond": [
                        {"$and": [{"$ne": ["$started_at", None]}, {"$ne": ["$finished_at", None]}]},
                        {"$subtract": ["$finished_at", "$started_at"]}, None]}}}},
        {"$sort": {"count": -1}}, {"$limit": 30},
    ])
    queue = aggregate("processing_jobs", [{"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    durations: list[float] = []
    for row in find("processing_jobs", {"created_at": {"$gte": since, "$lte": until},
                                        "started_at": {"$ne": None},
                                        "finished_at": {"$ne": None}},
                    sort=[("created_at", -1)], limit=METRIC_SCAN_LIMIT,
                    projection={"started_at": 1, "finished_at": 1, "kind": 1}):
        elapsed = _duration_ms(row.get("started_at"), row.get("finished_at"))
        if elapsed is not None:
            durations.append(elapsed)
    completed = int(next((r["count"] for r in statuses if r["_id"] == "completed"), 0))
    failed = int(next((r["count"] for r in statuses if r["_id"] == "failed"), 0))
    finished_total = completed + failed
    return {
        "source": "processing_jobs (common.tasks job records)",
        "jobs": sum(int(row["count"]) for row in statuses) if statuses else 0,
        "by_status": {str(row["_id"] or "unknown"): int(row["count"]) for row in statuses},
        "queue_depth_whole_collection": {str(row["_id"] or "unknown"): int(row["count"])
                                         for row in queue},
        "failure_rate": round(failed / finished_total, 4) if finished_total else None,
        "duration_ms": {
            "samples": len(durations),
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
            "max": round(max(durations), 2) if durations else None,
        },
        "by_kind": [{
            "kind": row["_id"].get("kind"), "status": row["_id"].get("status"),
            "count": int(row["count"]),
            "p50_ms": _percentile([float(v) for v in row.get("durations", [])
                                   if isinstance(v, (int, float))], 0.5),
        } for row in by_kind],
        "note": "failure_rate and durations use jobs that actually finished inside the window; "
                "None means no finished job exists there",
    }


def _ingestion_metrics(since: datetime, until: datetime) -> dict[str, Any]:
    match = {"created_at": {"$gte": since, "$lte": until}}
    statuses = aggregate("ingestion_jobs", [{"$match": match},
                                            {"$group": {"_id": "$status", "count": {"$sum": 1}}}])
    rows = find("ingestion_jobs", match, sort=[("created_at", -1)], limit=METRIC_SCAN_LIMIT,
                projection={"status": 1, "records_accepted": 1, "records_rejected": 1,
                            "started_at": 1, "finished_at": 1, "created_at": 1, "provider": 1,
                            "source": 1})
    accepted = sum(int(row.get("records_accepted") or 0) for row in rows)
    rejected = sum(int(row.get("records_rejected") or 0) for row in rows)
    durations = [value for value in (_duration_ms(row.get("started_at"), row.get("finished_at"))
                                     for row in rows) if value is not None]
    providers: dict[str, int] = {}
    for row in rows:
        raw = row.get("provider") or row.get("source")
        name = str(raw.get("provider") if isinstance(raw, dict) else raw or "unknown")
        providers[name] = providers.get(name, 0) + 1
    return {
        "source": "ingestion_jobs documents inside the window",
        "jobs": len(rows),
        "by_status": {str(row["_id"] or "unknown"): int(row["count"]) for row in statuses},
        "records_accepted": accepted,
        "records_rejected": rejected,
        "rejection_rate": round(rejected / (accepted + rejected), 4) if (accepted + rejected) else None,
        "duration_ms": {"samples": len(durations), "p50": _percentile(durations, 0.5),
                        "p95": _percentile(durations, 0.95)},
        "by_provider": [{"provider": name, "jobs": jobs}
                        for name, jobs in sorted(providers.items(), key=lambda kv: -kv[1])],
        "reason": None if rows else "no ingestion_jobs documents inside the requested window",
    }


def _collection_sizes() -> dict[str, Any]:
    probe = db()
    existing = set(probe.list_collection_names())
    sizes = {name: (probe[name].estimated_document_count() if name in existing else 0)
             for name in COLLECTIONS}
    try:
        stats = probe.command("dbstats")
    except Exception as exc:  # Mongo deployments can deny dbStats to some roles
        logger.warning("dbstats unavailable: %s", type(exc).__name__)
        stats = {}
    return {
        "source": "estimatedDocumentCount per collection (Mongo collection metadata)",
        "database": database_name(),
        "collections": sizes,
        "total_documents": sum(sizes.values()),
        "db_size_bytes": int(stats["size"]) if stats.get("size") is not None else None,
        "data_bytes": int(stats["dataSize"]) if stats.get("dataSize") is not None else None,
        "storage_bytes": int(stats["storageSize"]) if stats.get("storageSize") is not None else None,
        "indexes_bytes": int(stats["indexSize"]) if stats.get("indexSize") is not None else None,
        "stats_error": type(stats).__name__ if stats.get("ok") == 0 else None,
    }


def _log_latency(lines: list[str]) -> dict[str, Any]:
    durations: list[float] = []
    statuses: dict[str, int] = {}
    slowest: list[dict[str, Any]] = []
    parsed = 0
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("event") != "http_request":
            continue
        parsed += 1
        status = record.get("status")
        if isinstance(status, int):
            statuses[f"{status // 100}xx"] = statuses.get(f"{status // 100}xx", 0) + 1
            statuses[str(status)] = statuses.get(str(status), 0) + 1
        value = record.get("duration_ms")
        if isinstance(value, (int, float)):
            durations.append(float(value))
            slowest.append({"duration_ms": round(float(value), 1), "method": record.get("method"),
                            "path": record.get("path"), "status": status,
                            "ts": record.get("ts")})
    slowest.sort(key=lambda item: item["duration_ms"], reverse=True)
    errors = sum(count for code, count in statuses.items() if code.endswith("xx") and code[0] in "45")
    return {
        "source": f"structured http_request records in {LOG_FILE.name} (tail window)",
        "records_parsed": parsed,
        "duration_ms": {"samples": len(durations), "p50": _percentile(durations, 0.5),
                        "p95": _percentile(durations, 0.95), "p99": _percentile(durations, 0.99),
                        "max": round(max(durations), 2) if durations else None},
        "status_classes": {code: total for code, total in statuses.items()
                           if code.endswith("xx")},
        "status_codes": {code: total for code, total in statuses.items()
                         if not code.endswith("xx")},
        "client_or_server_error_share": round(errors / parsed, 4) if parsed else None,
        "slowest": slowest[:10],
        "reason": None if parsed else "the log tail contains no http_request records",
    }


def _report_storage() -> dict[str, Any]:
    reports = report_root()
    lake = storage_root()
    files = 0
    bytes_total = 0
    for dirpath, _dirs, names in os.walk(reports):
        for name in names:
            try:
                bytes_total += os.path.getsize(os.path.join(dirpath, name))
                files += 1
            except OSError:
                continue
    usage = shutil.disk_usage(str(reports))
    lake_usage = shutil.disk_usage(str(lake))
    return {
        "source": "report_root() and storage_root() filesystem stats",
        "reports_directory": str(reports.relative_to(BACKEND_DIR)) if str(reports).startswith(str(BACKEND_DIR))
                             else str(reports),
        "report_files": files,
        "report_bytes": bytes_total,
        "report_documents": count("reports", {}),
        "free_disk_bytes": int(usage.free),
        "free_disk_gb": round(usage.free / 1e9, 2),
        "total_disk_bytes": int(usage.total),
        "datalake_free_disk_bytes": int(lake_usage.free),
        "same_volume": str(reports) == str(lake) or usage.total == lake_usage.total,
    }


class OperationsMetricsView(ApiView):
    """One call, every operational number the admin dashboard needs - each labelled."""

    required_capability = "ops.logs"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        since, until = _window(params, default_days=7)
        include_logs = str(params.get("logs", "true")).lower() not in ("false", "0", "no")
        tail_lines = _read_log_tail(min(max(1, int(params.get("log_lines", 2000) or 2000)), 5000)) \
            if include_logs else []
        payload = {
            "generated_at": now_utc().isoformat(),
            "window": {"from": since.isoformat(), "to": until.isoformat(),
                       "default_days": 7},
            "audit_activity": _audit_activity(since, until),
            "background_jobs": _job_metrics(since, until),
            "ingestion_activity": _ingestion_metrics(since, until),
            "collection_sizes": _collection_sizes(),
            "report_storage": _report_storage(),
            "request_latency": _log_latency(tail_lines) if include_logs else {
                "source": "artifacts/logs/backend.log",
                "records_parsed": 0,
                "reason": "excluded because ?logs=false was requested",
            },
            "platform": {
                "source": "Django settings and the task engine actually in use",
                "debug": settings.DEBUG,
                "email_enabled": settings.EMAIL_ENABLED,
                "engine": task_engine(),
                "throttle_rates": settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {}),
            },
        }
        return self.ok(request, payload)


def _read_log_tail(lines: int) -> list[str]:
    """Read at most `lines` from the end of the backend log without loading the file."""
    if not LOG_FILE.is_file():
        return []
    requested = max(1, min(int(lines), LOG_TAIL_MAX_LINES))
    with LOG_FILE.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        block = 64 * 1024
        buffer = b""
        position = size
        collected = 0
        while position > 0 and collected <= requested:
            step = min(block, position)
            position -= step
            handle.seek(position)
            buffer = handle.read(step) + buffer
            collected = buffer.count(b"\n")
            if len(buffer) > LOG_TAIL_MAX_BYTES:
                break
    text = buffer.decode("utf-8", errors="replace")
    parsed = [line for line in text.splitlines() if line.strip()]
    return parsed[-requested:]


class LogTailView(ApiView):
    """Last N structured log lines, redacted before they leave the process."""

    required_capability = "ops.logs"
    http_method_names = ["get"]

    def get(self, request):
        params = self.params(request)
        try:
            lines = int(params.get("lines", LOG_TAIL_LINES))
        except ValueError:
            raise ValidationError("'lines' must be a whole number.") from None
        lines = max(1, min(lines, LOG_TAIL_MAX_LINES))
        level = sanitize_text(params.get("level", ""), max_length=10).upper() or None
        search = sanitize_text(params.get("search", ""), max_length=120).lower() or None
        raw = _read_log_tail(lines)
        kept: list[dict[str, Any]] = []
        for entry in raw:
            try:
                record = json.loads(entry)
            except ValueError:
                record = {"message": entry}
            if not isinstance(record, dict):
                continue
            if level and str(record.get("level", "")).upper() != level:
                continue
            safe = redact(record)
            text = json.dumps(safe, default=str, ensure_ascii=False)
            if "mongodb://" in text.lower() or "mongodb+srv://" in text.lower():
                # Defensive: a connection string must never reach a client.
                continue
            if search and search not in text.lower():
                continue
            kept.append(safe)
        return self.ok(request, {
            "file": LOG_FILE.name,
            "exists": LOG_FILE.is_file(),
            "bytes": LOG_FILE.stat().st_size if LOG_FILE.is_file() else 0,
            "lines_read": len(raw),
            "lines_returned": len(kept),
            "requested": lines,
            "filters": {"level": level, "search": search},
            "redaction": "common.logging_config.redact applied to every returned record",
            "entries": kept,
            "reason": None if LOG_FILE.is_file() else "the backend log file does not exist yet",
        })
