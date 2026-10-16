"""Scheduled provider pulls - the in-process stand-in for the Airflow ingestion DAG.

The schedule is data, not code: it lives in the `ingestion_schedule` platform setting so an
administrator can change it from the admin UI without a deploy.

    {"enabled": true,
     "sources": [{"provider": "open_meteo", "dataset_name": "Lahore daily climate",
                  "coordinates": {"latitude": 31.5204, "longitude": 74.3587},
                  "location_name": "Lahore", "variables": ["temperature", "precipitation"],
                  "granularity": "daily", "interval_minutes": 1440, "lookback_days": 7}]}

Due-ness is derived from the real `ingestion_jobs` history (the newest job for that provider
and dataset), so restarting the scheduler never double-pulls and nothing is recorded as run
that did not run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from apps.ingestion import jobs as ingestion_jobs
from apps.ingestion import service as ingestion
from apps.ingestion import source_clients
from common.db import collection, find_one, now_utc
from common.exceptions import ValidationError
from common.logging_config import get_logger
from common.tasks import submit
from common.validators import IngestionJobRequest, parse

logger = get_logger("earthscape.ingestion.schedule")

SETTING_KEY = "ingestion_schedule"
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 7 * 24 * 60
DEFAULT_INTERVAL_MINUTES = 24 * 60
MAX_LOOKBACK_DAYS = 366
MAX_SOURCES = 20


def schedule_configuration() -> dict[str, Any]:
    document = find_one("platform_settings", {"key": SETTING_KEY}, {"value": 1})
    value = (document or {}).get("value") or {}
    sources = value.get("sources")
    if not isinstance(sources, list):
        sources = []
    return {"configured": document is not None, "enabled": bool(value.get("enabled")),
            "sources": sources[:MAX_SOURCES], "truncated": len(sources) > MAX_SOURCES}


def interval_minutes(entry: dict[str, Any]) -> int:
    raw = entry.get("interval_minutes")
    value = DEFAULT_INTERVAL_MINUTES if raw in (None, "") else int(raw)
    if not MIN_INTERVAL_MINUTES <= value <= MAX_INTERVAL_MINUTES:
        raise ValidationError(f"interval_minutes must be between {MIN_INTERVAL_MINUTES} "
                              f"and {MAX_INTERVAL_MINUTES}.")
    return value


def pull_request(entry: dict[str, Any], moment: datetime) -> IngestionJobRequest:
    """Turn a schedule entry into the same validated request the API accepts."""
    lookback = entry.get("lookback_days")
    lookback = 7 if lookback in (None, "") else int(lookback)
    if not 1 <= lookback <= MAX_LOOKBACK_DAYS:
        raise ValidationError(f"lookback_days must be between 1 and {MAX_LOOKBACK_DAYS}.")
    end = (moment - timedelta(days=1)).date()
    start = end - timedelta(days=lookback - 1)
    return parse(IngestionJobRequest, {
        "provider": entry.get("provider"),
        "dataset_name": entry.get("dataset_name"),
        "coordinates": entry.get("coordinates") or {},
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "variables": list(entry.get("variables") or []),
        "granularity": entry.get("granularity") or "daily",
        "station_id": entry.get("station_id"),
        "dataset_code": entry.get("dataset_code"),
        "location_code": entry.get("location_code"),
        "location_name": entry.get("location_name"),
    })


def last_run_at(provider: str, dataset_id: str) -> Optional[datetime]:
    job = collection("ingestion_jobs").find_one(
        {"kind": "provider_pull", "source": provider, "dataset_id": dataset_id},
        sort=[("created_at", -1)], projection={"created_at": 1})
    moment = (job or {}).get("created_at")
    if isinstance(moment, datetime):
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return None


def scheduler_actor() -> str:
    """Attribute scheduled jobs to a real active administrator, never a fabricated user."""
    admin = find_one("users", {"role": "administrator", "is_active": True}, {"_id": 1})
    return str(admin["_id"]) if admin else "scheduler"


def run_source(entry: dict[str, Any], *, actor: str, moment: datetime,
               wait: bool = True, force: bool = False) -> dict[str, Any]:
    name = str(entry.get("dataset_name") or entry.get("provider") or "schedule entry")
    try:
        payload = pull_request(entry, moment)
        every = interval_minutes(entry)
    except (ValidationError, TypeError, ValueError) as exc:
        return {"dataset_name": name, "started": False, "reason": str(exc)[:300]}

    option = source_clients.provider_options().get(payload.provider) or {}
    if not option.get("available"):
        missing = option.get("missing_config") or "provider configuration"
        return {"dataset_name": name, "started": False,
                "reason": f"{payload.provider} connector is unavailable (set {missing})."}

    dataset = ingestion_jobs.dataset_for_pull(payload, actor)
    dataset_id = str(dataset["_id"])
    previous = last_run_at(payload.provider, dataset_id)
    if previous and not force:
        elapsed_minutes = (moment - previous).total_seconds() / 60
        if elapsed_minutes < every:
            return {"dataset_name": name, "started": False,
                    "reason": f"next run in {round(every - elapsed_minutes)} min",
                    "last_run_at": previous.isoformat(), "interval_minutes": every}

    job = ingestion_jobs.create_pull_job(payload, dataset_id, actor)
    kwargs = ingestion_jobs.pull_kwargs(payload=payload, dataset_id=dataset_id,
                                       job_id=str(job["_id"]))
    if wait:
        try:
            result = ingestion_jobs.run_provider_ingest(**kwargs)
        except Exception as exc:  # a scheduler run must record the real failure, not hide it
            message = f"{type(exc).__name__}: {str(exc)[:200]}"
            ingestion.mark_job_failed(job["_id"], message)
            logger.warning("scheduled pull failed for %s: %s", name, type(exc).__name__)
            return {"dataset_name": name, "started": True, "ingestion_job_id": str(job["_id"]),
                    "dataset_id": dataset_id, "window": payload.date_range,
                    "outcome": {"status": "failed", "error": message}}
        return {"dataset_name": name, "started": True, "ingestion_job_id": str(job["_id"]),
                "dataset_id": dataset_id, "window": payload.date_range, "outcome": result}
    submit("provider_pull", ingestion_jobs.run_provider_ingest, kwargs=kwargs,
           created_by=actor, payload={"dataset_id": dataset_id, "provider": payload.provider},
           label=job["label"])
    return {"dataset_name": name, "started": True, "ingestion_job_id": str(job["_id"]),
            "dataset_id": dataset_id, "window": payload.date_range,
            "outcome": "submitted to the background engine"}


def run_due_pulls(*, wait: bool = True, force: bool = False,
                  moment: Optional[datetime] = None) -> dict[str, Any]:
    moment = moment or now_utc()
    config = schedule_configuration()
    if not config["configured"]:
        return {"status": "not_configured",
                "detail": f"platform_settings key '{SETTING_KEY}' is missing; run "
                          f"scripts/migrate_mongo.py, then enable it from the admin settings page.",
                "sources": []}
    if not config["enabled"]:
        return {"status": "disabled", "detail": f"'{SETTING_KEY}'.enabled is false.", "sources": []}
    if not config["sources"]:
        return {"status": "idle", "detail": "the schedule has no sources configured.", "sources": []}

    actor = scheduler_actor()
    results = [run_source(entry, actor=actor, moment=moment, wait=wait, force=force)
               for entry in config["sources"] if isinstance(entry, dict)]
    started = [item for item in results if item.get("started")]
    return {"status": "ran", "enabled": True, "ran_at": moment.isoformat(),
            "sources_checked": len(results), "pulls_started": len(started),
            "truncated": config["truncated"], "results": results}
