"""Overview dashboard payload assembled entirely from stored MongoDB documents.

No placeholder climate numbers are produced: every empty collection reports an explicit
unavailable state with the reason, so the UI never renders a fabricated figure.
"""

from __future__ import annotations

from typing import Any

from apps.climate import analytics, repository


def _trend(variable: str) -> dict:
    series = repository.variable_series(variable, bucket="month")
    if not series:
        return {"available": False, "variable": variable,
                "reason": f"no {variable} measurements stored yet", "series": []}
    return {
        "available": True,
        "variable": variable,
        "bucket": "month",
        "sample_size": sum(int(row["sample_size"]) for row in series),
        "period_covered": {"start": series[0]["period"], "end": series[-1]["period"]},
        "series": [
            {"period": row["period"], "sample_size": row["sample_size"],
             "mean": round(float(row["mean"]), 3) if row["mean"] is not None else None,
             "minimum": round(float(row["minimum"]), 3) if row["minimum"] is not None else None,
             "maximum": round(float(row["maximum"]), 3) if row["maximum"] is not None else None}
            for row in series
        ],
    }


def _compliance(quality: dict) -> dict:
    datasets = quality.get("datasets") or []
    aggregate_counts: dict[str, int] = {}
    for entry in datasets:
        for variable, stat in (entry.get("completeness") or {}).items():
            aggregate_counts[variable] = aggregate_counts.get(variable, 0) + int(stat.get("present", 0))
    total_observations = sum(int(entry.get("observations", 0)) for entry in datasets)
    completeness = {
        variable: {"present": present,
                   "coverage": round(present / total_observations, 4) if total_observations else None}
        for variable, present in sorted(aggregate_counts.items())
    }
    return {"total_observations": total_observations, "by_variable": completeness}


def build_overview() -> dict[str, Any]:
    observation_total = repository.observation_count()
    quality = repository.quality_report()
    ingest = repository.ingestion_overview()
    processing = repository.processing_overview()
    alerts = repository.alerts_overview(limit=8)
    forecasts = repository.forecast_overview(limit=8)

    observed_range = repository.observed_range()

    data_quality = repository.count_by_quality()
    return {
        "counts": {
            "datasets": repository.dataset_count(),
            "observations": observation_total,
            "analytics_summaries": repository.analytics_summary_overview()["total"],
            "predictions": forecasts["total"],
            "alert_events": alerts_event_total(alerts),
        },
        "ingestion": {
            "success": ingest["success"],
            "failed": ingest["failed"],
            "by_status": ingest["by_status"],
            "records_accepted": ingest["records_accepted"],
            "records_rejected": ingest["records_rejected"],
            "records_duplicate": ingest["records_duplicate"],
            "last_successful_sync": _last_sync(ingest["last_successful_sync"]),
        },
        "trends": {
            "temperature": _trend("temperature"),
            "precipitation": _trend("precipitation"),
        },
        "geographic": analytics.regions_summary(),
        "observed_range": observed_range,
        "anomalies": _anomaly_block(alerts),
        "forecasts": _forecast_block(forecasts),
        "data_quality": {
            "by_status": data_quality,
            "completeness": _compliance(quality),
            "transformations_applied": quality.get("transformations_applied", []),
            "per_dataset": quality.get("datasets", []),
        },
        "pipeline": {
            "available": bool(processing["by_status"]),
            "by_status": processing["by_status"],
            "by_engine": processing["by_engine"],
            "recent_jobs": processing["recent"],
            "reason": None if processing["by_status"] else "no processing jobs have run yet",
        },
        "recent_alerts": _recent_alerts(alerts),
    }


def alerts_event_total(alerts: dict) -> int:
    return sum(int(value) for value in (alerts.get("by_status") or {}).values())


def _last_sync(job: dict | None) -> dict:
    if not job:
        return {"available": False, "reason": "no completed ingestion job recorded yet"}
    return {
        "available": True,
        "job_ref": job.get("job_ref"),
        "dataset_id": job.get("dataset_id"),
        "source": job.get("source"),
        "completed_at": job.get("completed_at"),
        "records_accepted": job.get("records_accepted"),
    }


def _anomaly_block(alerts: dict) -> dict:
    total = alerts_event_total(alerts)
    if total == 0:
        return {"available": False, "reason": "no alert events have been raised yet",
                "by_status": alerts.get("by_status", {}), "recent": []}
    return {"available": True, "total": total, "by_status": alerts.get("by_status", {}),
            "by_region": alerts.get("by_region", {}), "recent": alerts.get("recent", [])[:8]}


def _forecast_block(forecasts: dict) -> dict:
    if not forecasts["total"]:
        return {"available": False, "reason": "no predictions have been generated yet",
                "latest": []}
    return {"available": True, "total": forecasts["total"], "latest": forecasts["latest"]}


def _recent_alerts(alerts: dict) -> dict:
    recent = alerts.get("recent", [])
    if not recent:
        return {"available": False, "reason": "no alerts raised yet", "items": []}
    return {"available": True, "items": recent}
