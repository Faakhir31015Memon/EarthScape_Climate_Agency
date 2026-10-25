"""Historical explorer helpers.

Turns the stored daily record into the quantities a climate explorer actually
asks for: annual and decadal normals for a chosen reference period, the deviation
of every later year against that normal, and the change between two periods.

Everything here is computed from documents supplied by
:func:`apps.climate.services.fetch_rows` (or, for the region/year matrix, from a
MongoDB aggregation pipeline). No value is estimated, extrapolated or filled in:
a year with no stored measurement is reported as ``null`` and summarised by the
``reason`` field instead of being drawn as a line on a chart.

WMO-style normals need 30 years; this module therefore labels the reference
window honestly (``reference_period`` plus ``years_in_reference_period``) and
flags a normal as provisional when it rests on fewer years than
``MIN_YEARS_FOR_STABLE_NORMAL``.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from common import analytics
from common.analytics import ADDITIVE_METRICS, METRIC_LABELS, METRIC_UNITS

MIN_YEARS_FOR_SLOPE = 3
MIN_YEARS_FOR_STABLE_NORMAL = 10
MAX_DECADE_ENTRIES = 200
MAX_YEAR_ENTRIES = 2000

GROUP_FIELDS = {
    "none": None,
    "region": "region_code",
    "location": "location_code",
    "country": "country",
}


def _round(value: Any, digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return round(number, digits)


def normalise_period(value: Any, *, default: Optional[str] = None) -> Optional[str]:
    """Accept a year (``1991``) or a date (``1991-05-01``) and return the year."""
    if value in (None, ""):
        return default
    text = str(value).strip()
    if text.isdigit() and len(text) == 4:
        return text
    try:
        return str(datetime.strptime(text[:4], "%Y").year)
    except (ValueError, TypeError):
        return default


def year_of(observation: Dict[str, Any]) -> Optional[int]:
    year = observation.get("year")
    if isinstance(year, int) and year > 0:
        return year
    date_value = observation.get("observation_date")
    if date_value:
        try:
            return int(str(date_value)[:4])
        except (TypeError, ValueError):
            return None
    return None


def bucket_observations(observations: Sequence[Dict[str, Any]], metric: str,
                        *, group_by: str = "none") -> Dict[Tuple[str, int], List[float]]:
    """``{(group, year): [metric values]}`` from real daily documents only."""
    field = GROUP_FIELDS.get(group_by)
    buckets: Dict[Tuple[str, int], List[float]] = {}
    for observation in observations:
        value = observation.get(metric)
        if value is None:
            continue
        year = year_of(observation)
        if year is None:
            continue
        group = str(observation.get(field)) if field and observation.get(field) else "GLOBAL"
        buckets.setdefault((group, year), []).append(float(value))
    return buckets


def aggregate_years(buckets: Dict[Tuple[str, int], List[float]], metric: str) -> Dict[str, List[Dict[str, Any]]]:
    """Per-group annual statistic: mean for averages, total for additive metrics."""
    additive = metric in ADDITIVE_METRICS
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for (group, year), values in buckets.items():
        if not values:
            continue
        grouped.setdefault(group, []).append({
            "year": year,
            "value": _round(sum(values) if additive else statistics.fmean(values)),
            "aggregation": "sum" if additive else "mean",
            "unit": METRIC_UNITS.get(metric),
            "observations": len(values),
            "min": _round(min(values)),
            "max": _round(max(values)),
        })
    for entries in grouped.values():
        entries.sort(key=lambda item: item["year"])
    return grouped


def decades_of(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per-decade aggregates of the annual series (decade = 1900s, 1910s, ...)."""
    buckets: Dict[int, List[Dict[str, Any]]] = {}
    for entry in entries:
        year = entry.get("year")
        if not isinstance(year, int):
            continue
        buckets.setdefault((year // 10) * 10, []).append(entry)
    out: List[Dict[str, Any]] = []
    for decade, items in sorted(buckets.items()):
        values = [item["value"] for item in items if item["value"] is not None]
        if not values:
            continue
        out.append({
            "decade": f"{decade}s",
            "years": sorted(item["year"] for item in items),
            "years_covered": len(items),
            "mean": _round(statistics.fmean(values)),
            "total": _round(sum(values)),
            "min": _round(min(values)),
            "max": _round(max(values)),
            "unit": items[0].get("unit"),
            "aggregation": items[0].get("aggregation"),
        })
    return out


def normal_for(entries: Sequence[Dict[str, Any]], base_from: Optional[str],
               base_to: Optional[str]) -> Dict[str, Any]:
    """Reference-period normal plus how much data that normal actually rests on."""
    low = int(base_from) if base_from else None
    high = int(base_to) if base_to else None
    inside = [entry for entry in entries
              if entry["value"] is not None
              and (low is None or entry["year"] >= low)
              and (high is None or entry["year"] <= high)]
    values = [float(entry["value"]) for entry in inside]
    if not values:
        return {
            "reference_period": {"start": base_from, "end": base_to},
            "years_in_reference_period": 0,
            "normal": None,
            "stable": False,
            "reason": ("No stored year for this scope falls inside the reference period, so no "
                       "normal can be computed. Widen base_from/base_end or ingest older data."),
        }
    normal = statistics.fmean(values)
    return {
        "reference_period": {"start": base_from, "end": base_to},
        "years_in_reference_period": len(values),
        "normal": _round(normal),
        "reference_min": _round(min(values)),
        "reference_max": _round(max(values)),
        "reference_std": _round(statistics.stdev(values)) if len(values) > 1 else None,
        "stable": len(values) >= MIN_YEARS_FOR_STABLE_NORMAL,
        "note": (None if len(values) >= MIN_YEARS_FOR_STABLE_NORMAL else
                 f"This normal rests on {len(values)} year(s) of stored data. A WMO-style normal "
                 f"uses 30; treat it as provisional."),
    }


def annual_grouped(rows: Sequence[Dict[str, Any]], *, metric: str) -> Dict[str, List[Dict[str, Any]]]:
    """Reshape ``$group`` output (group x year) into the explorer's annual entries."""
    additive = metric in ADDITIVE_METRICS
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        value = row.get("total") if additive else row.get("mean")
        year = row.get("year")
        if value is None or not isinstance(year, int) or year <= 0:
            continue
        grouped.setdefault(str(row.get("group") or "GLOBAL"), []).append({
            "year": year,
            "value": _round(value),
            "aggregation": "sum" if additive else "mean",
            "unit": METRIC_UNITS.get(metric),
            "observations": int(row.get("observation_count") or 0),
            "min": _round(row.get("min")),
            "max": _round(row.get("max")),
        })
    for entries in grouped.values():
        entries.sort(key=lambda item: item["year"])
    return grouped


def historical_explorer(observations: Sequence[Dict[str, Any]], *, metric: str,
                        base_from: Optional[str] = None, base_to: Optional[str] = None,
                        group_by: str = "none",
                        pipeline_rows: Optional[Sequence[Dict[str, Any]]] = None,
                        annual_rows: Optional[Sequence[Dict[str, Any]]] = None,
                        observations_evaluated: Optional[int] = None) -> Dict[str, Any]:
    """Per-year/per-decade normals and change deltas for one metric.

    ``annual_rows`` carries per (group, year) aggregates already computed by a
    MongoDB ``$group``; when it is present the daily documents are not needed, so
    ``observations`` can be empty and ``observations_evaluated`` states the scope's
    row count instead. Otherwise the buckets are built from ``observations`` here.
    """
    base_from = normalise_period(base_from, default="1961")
    base_to = normalise_period(base_to, default="1990")
    if metric not in analytics.METRICS:
        return {"available": False, "metric": metric, "groups": [],
                "reason": f"Metric '{metric}' is not part of the observation schema."}

    evaluated = len(observations) if observations_evaluated is None else int(observations_evaluated)
    if annual_rows is not None:
        grouped = annual_grouped(annual_rows, metric=metric)
    else:
        grouped = aggregate_years(bucket_observations(observations, metric, group_by=group_by), metric)
    if not grouped:
        return {
            "available": False,
            "metric": metric,
            "label": METRIC_LABELS.get(metric),
            "unit": METRIC_UNITS.get(metric),
            "group_by": group_by,
            "reference_period": {"start": base_from, "end": base_to},
            "groups": [],
            "year_count": 0,
            "observations_evaluated": evaluated,
            "reason": (f"No stored observation carries a {metric} value for this filter set, so "
                       "no normals, anomalies or deltas can be computed."),
        }

    groups: List[Dict[str, Any]] = []
    for name, entries in sorted(grouped.items()):
        reference = normal_for(entries, base_from, base_to)
        normal = reference.get("normal")
        years = [
            {
                "year": entry["year"],
                "value": entry["value"],
                "anomaly_vs_reference": (_round(entry["value"] - normal)
                                         if entry["value"] is not None and normal is not None
                                         else None),
                "observations": entry["observations"],
                "min": entry["min"],
                "max": entry["max"],
            }
            for entry in entries
        ][-MAX_YEAR_ENTRIES:]
        decades = decades_of(entries)
        latest = entries[-1] if entries else None
        warmest = max((entry for entry in entries if entry["value"] is not None),
                      key=lambda item: item["value"], default=None)
        coolest = min((entry for entry in entries if entry["value"] is not None),
                      key=lambda item: item["value"], default=None)
        slope = analytics.trend_slope([(str(entry["year"]), entry["value"]) for entry in entries
                                       if entry["value"] is not None])
        change: Dict[str, Any] = {"available": False}
        if normal is not None and latest and latest["value"] is not None:
            change = {
                "available": True,
                "kind": "latest_year_vs_reference_normal",
                "from_period": reference["reference_period"],
                "from_value": normal,
                "to_year": latest["year"],
                "to_value": latest["value"],
                "delta": _round(latest["value"] - normal),
                "relative_pct": (_round((latest["value"] - normal) / normal * 100, 2)
                                 if normal else None),
            }
        first_value = next((entry["value"] for entry in entries if entry["value"] is not None), None)
        last_value = next((entry["value"] for entry in reversed(entries)
                           if entry["value"] is not None), None)
        groups.append({
            "group": name,
            "group_by": GROUP_FIELDS.get(group_by) or "global",
            "years_covered": len(entries),
            "first_year": entries[0]["year"],
            "last_year": entries[-1]["year"],
            "reference": reference,
            "years": years,
            "decades": decades[:MAX_DECADE_ENTRIES],
            "change": change,
            "record_high": ({"year": warmest["year"], "value": warmest["value"]} if warmest else None),
            "record_low": ({"year": coolest["year"], "value": coolest["value"]} if coolest else None),
            "slope": slope,
            "total_change": (_round(last_value - first_value)
                             if first_value is not None and last_value is not None else None),
            "available": bool(slope.get("available")) or len(entries) >= 1,
        })

    groups.sort(key=lambda item: (item["change"] or {}).get("delta") is None,
                reverse=False)
    groups.sort(key=lambda item: abs((item["change"] or {}).get("delta") or 0), reverse=True)
    year_matrix = pipeline_matrix(pipeline_rows, metric=metric)
    all_years = sorted({entry["year"] for entries in grouped.values() for entry in entries})
    return {
        "available": True,
        "metric": metric,
        "label": METRIC_LABELS.get(metric),
        "unit": METRIC_UNITS.get(metric),
        "aggregation": "sum" if metric in ADDITIVE_METRICS else "mean",
        "group_by": group_by,
        "reference_period": {"start": base_from, "end": base_to},
        "groups": groups[:200],
        "group_count": len(groups),
        "year_span": {"first": all_years[0] if all_years else None,
                      "last": all_years[-1] if all_years else None},
        "year_count": len(all_years),
        "observations_evaluated": evaluated,
        "pipeline_year_matrix": year_matrix,
        "method": "annual means (sums for additive metrics) computed only from stored daily "
                  "observations; anomalies are deviations against the chosen reference normal",
        "caveat": "A deviation against a reference normal describes change in the stored record. "
                  "It is not, by itself, attribution of a single event to any cause.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def pipeline_matrix(rows: Optional[Sequence[Dict[str, Any]]], *, metric: str) -> List[Dict[str, Any]]:
    """Reshape the ``$group`` output (region x year) into explorer rows."""
    if not rows:
        return []
    additive = metric in ADDITIVE_METRICS
    out: List[Dict[str, Any]] = []
    for row in rows:
        value = row.get("total") if additive else row.get("mean")
        out.append({
            "region_code": row.get("region_code"),
            "year": row.get("year"),
            "value": _round(value),
            "aggregation": "sum" if additive else "mean",
            "unit": METRIC_UNITS.get(metric),
            "observation_count": row.get("observation_count"),
            "min": row.get("min"),
            "max": row.get("max"),
            "computed_by": "MongoDB aggregation ($match/$group/$sort/$limit)",
        })
    return out[:1000]


def merge_pipeline_normals(pipeline_rows: Sequence[Dict[str, Any]], *,
                           metric: str) -> Dict[str, List[Tuple[str, float]]]:
    """``{region: [(year, value), ...]}`` for :func:`common.analytics.trend_slope`."""
    additive = metric in ADDITIVE_METRICS
    series: Dict[str, List[Tuple[str, float]]] = {}
    for row in pipeline_rows:
        value = row.get("total") if additive else row.get("mean")
        if value is None or not row.get("region_code") or not row.get("year"):
            continue
        series.setdefault(str(row["region_code"]), []).append((str(row["year"]), float(value)))
    for entries in series.values():
        entries.sort(key=lambda item: item[0])
    return series


__all__ = [
    "GROUP_FIELDS",
    "MIN_YEARS_FOR_SLOPE",
    "MIN_YEARS_FOR_STABLE_NORMAL",
    "aggregate_years",
    "annual_grouped",
    "bucket_observations",
    "decades_of",
    "historical_explorer",
    "merge_pipeline_normals",
    "normal_for",
    "normalise_period",
    "pipeline_matrix",
    "year_of",
]
