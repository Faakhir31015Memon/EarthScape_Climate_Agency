"""Anomaly detection layer.

This module deliberately delegates the core deviation maths to
``common.analytics.detect_anomalies`` (threshold, region+month statistical
baseline and Isolation Forest are already implemented there against real
stored observations) and only *enriches* the results for the alerts pipeline:
severity confirmation, human-readable explanations built strictly from the
observable deviation, and ``alerts``-compatible candidate dictionaries.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common import analytics
from common.analytics import InsufficientData, detect_anomalies, fetch_observations, observation_query

VALID_METHODS = ("threshold", "statistical_baseline", "isolation_forest")

SEVERITY_LABELS = {
    "critical": "Exceptional deviation (>= 4 sigma from the stored reference)",
    "high": "Severe deviation (>= 3 sigma)",
    "medium": "Notable deviation (>= 2.5 sigma)",
    "low": "Mild deviation",
}


def run_detection(*, metric: str, method: str = "statistical_baseline",
                  threshold_sigma: float = 2.5, absolute_threshold: Optional[float] = None,
                  direction: str = "both", region_code: Optional[str] = None,
                  location_code: Optional[str] = None, start_date: Optional[str] = None,
                  end_date: Optional[str] = None, limit: int = 50000) -> Dict[str, Any]:
    """Detect anomalies from stored observations and enrich each with an explanation.

    Raises :class:`InsufficientData` when the collection has nothing to measure
    against; the result otherwise reports real evaluated-row counts.
    """
    if metric not in analytics.METRICS:
        raise InsufficientData(f"Metric '{metric}' is not supported.")
    if method not in VALID_METHODS:
        raise InsufficientData(f"Unknown method '{method}'. Use one of {', '.join(VALID_METHODS)}.")

    criteria = _criteria(metric=metric, regions=[region_code] if region_code else None,
                         location_codes=[location_code] if location_code else None,
                         start_date=start_date, end_date=end_date, limit=limit)
    observations = fetch_observations(criteria)
    if not observations:
        raise InsufficientData(
            "No training/detection data available: climate_observations holds no rows for this "
            "metric and scope. Detection refuses to run on invented values.", available=0)

    result = detect_anomalies(observations, metric, method=method,
                              threshold_sigma=threshold_sigma,
                              absolute_threshold=absolute_threshold, direction=direction,
                              region_code=region_code, location_code=location_code)
    for item in result.get("anomalies") or []:
        item["severity_label"] = SEVERITY_LABELS.get(item.get("severity"), "Deviation")
        item["explanation_source"] = "observable deviation only - no causal claim is made"
    result["source_documents_evaluated"] = len(observations)
    result["candidates"] = alert_candidates(result)
    return result


def _criteria(*, metric: str, regions: Optional[List[str]], location_codes: Optional[List[str]],
              start_date: Optional[str], end_date: Optional[str], limit: int) -> Dict[str, Any]:
    return observation_query(metric=metric, regions=regions, location_codes=location_codes,
                             start_date=start_date, end_date=end_date, limit=limit)


def alert_candidates(result: Dict[str, Any], *, min_severity: str = "medium") -> List[Dict[str, Any]]:
    """Turn detected anomalies into ``alerts`` collection-ready candidate documents.

    The alerts app owns persistence; this returns dicts with a stable
    ``rule_key`` so it can deduplicate.
    """
    order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
    floor = order.get(min_severity, 1)
    candidates: List[Dict[str, Any]] = []
    for item in result.get("anomalies") or []:
        if order.get(item.get("severity"), 0) < floor:
            continue
        date = item.get("observation_date") or ""
        location = (item.get("location_code") or item.get("region_code") or "scope").lower()
        candidates.append({
            "rule_key": f"ml-anomaly:{result.get('method')}:{item.get('metric')}:{location}:{date}",
            "triggered_at": datetime.now(timezone.utc),
            "severity": item.get("severity"),
            "status": "open",
            "source": "ml_anomaly_detector",
            "title": f"{item.get('metric_label')} anomaly at {item.get('location_name') or location} "
                     f"({item.get('value')} {item.get('unit')})",
            "message": item.get("explanation"),
            "metric": item.get("metric"),
            "unit": item.get("unit"),
            "region_code": item.get("region_code"),
            "location_code": item.get("location_code"),
            "observation_date": item.get("observation_date"),
            "deviation_score": item.get("deviation_score"),
            "direction": item.get("direction"),
            "method": result.get("method"),
            "baseline": item.get("baseline"),
            "dedupe_key": f"ml-anomaly:{item.get('metric')}:{location}:{date}:"
                          f"{item.get('deviation_score')}",
            "caveat": "A statistical deviation from the stored reference period; it is not, by "
                      "itself, proof that an event is scientifically exceptional, and it carries "
                      "no causal interpretation.",
        })
    return candidates


__all__ = ["run_detection", "alert_candidates", "VALID_METHODS", "SEVERITY_LABELS"]
