"""Profile an uploaded table without routing it through the climate pipeline.

Ingestion maps columns onto a fixed observation schema, so it cannot answer "what is
inside this file I just picked?". This module reads one or more files with the streaming
readers used by ingestion, classifies every column on its own evidence, and returns
bounded, JSON-safe aggregates the frontend can chart in any shape.

Only the first ``FILE_ANALYSIS_MAX_ROWS`` records are profiled across the whole selection,
so peak memory stays flat however large the upload is; ``truncated`` says the picture is
partial. Raw rows are never stored.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from apps.datasets import file_import, file_forecast
from common import validators
from config import env

MAX_ANALYZED_ROWS = env.get_int("FILE_ANALYSIS_MAX_ROWS", 20000)
MAX_FILES = 10
TYPE_SAMPLE_SIZE = 240
HISTOGRAM_BINS = env.get_int("FILE_ANALYSIS_HISTOGRAM_BINS", 12)
TOP_CATEGORIES = 24
MAX_DIMENSIONS = 8
MAX_SERIES_MEASURES = 6
MAX_CATEGORY_MEASURES = 3
CORRELATION_MAX_MEASURES = 10
SCATTER_POINTS = env.get_int("FILE_ANALYSIS_SCATTER_POINTS", 250)
SCATTER_MAX_PAIRS = 3
SERIES_MAX_POINTS = env.get_int("FILE_ANALYSIS_SERIES_POINTS", 400)
PREVIEW_ROWS = 20
PREVIEW_MAX_COLUMNS = 40
MAX_TEXT_LENGTH = 120
GEO_MAX_POINTS = 400
HEATMAP_MAX_YEARS = 15
FORECAST_MEASURES = 3
SOURCE_COLUMN = "source_file"
MAX_PAYLOAD_BYTES = env.get_int("FILE_ANALYSIS_MAX_PAYLOAD_BYTES", 800_000)
_MAX_DISTINCT_TRACKED = TOP_CATEGORIES * 20
_DATE_HINTS = ("-", "/", ":")
_TRUE_TOKENS = {"true", "t", "yes", "y", "1"}
_FALSE_TOKENS = {"false", "f", "no", "n", "0"}


class FileAnalyticsError(Exception):
    """Raised when a file cannot be opened or holds no readable table."""


def analyse_file(path: str, fmt: Optional[str] = None, *,
                 max_rows: int = MAX_ANALYZED_ROWS,
                 target_year: Optional[int] = None) -> Dict[str, Any]:
    """Return the analytics payload for one file already stored on disk."""
    return analyse_files([path], [fmt], max_rows=max_rows, target_year=target_year)


def analyse_files(paths: Sequence[str], fmts: Optional[Sequence[Optional[str]]] = None, *,
                  max_rows: int = MAX_ANALYZED_ROWS,
                  target_year: Optional[int] = None) -> Dict[str, Any]:
    """Profile several files as one table: columns are matched by name and each row keeps
    the file it came from, so a folder of station exports behaves like one wide dataset."""
    if not paths:
        raise FileAnalyticsError("Choose at least one file to analyse.")
    if len(paths) > MAX_FILES:
        raise FileAnalyticsError(f"Up to {MAX_FILES} files can be analysed together.")

    readers: List[Dict[str, Any]] = []
    headers: List[str] = []
    for index, path in enumerate(paths):
        declared = fmts[index] if fmts and index < len(fmts) else None
        try:
            detected = declared or file_import.sniff_format(path)
            own = [header for header in file_import.read_headers(path, detected) if header]
        except file_import.FileImportError as exc:
            raise FileAnalyticsError(str(exc)) from exc
        if not own:
            raise FileAnalyticsError(f"No column headers could be read from {Path(path).name}.")
        headers.extend(header for header in own if header not in headers)
        readers.append({"path": path, "format": detected, "headers": own,
                        "name": Path(path).name})

    store = _collect_all(readers, headers + [SOURCE_COLUMN], max_rows)
    if not store.rows:
        raise FileAnalyticsError("These files have headers but no data rows.")

    payload = _build(headers + [SOURCE_COLUMN], store, readers, target_year)
    return _respect_budget(payload)


def _payload_bytes(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


def _respect_budget(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Trade point density for completeness so one analysis still fits one document."""
    for series in payload["series"]:
        series["points"] = series["points"][:SERIES_MAX_POINTS]
    for step in (150, 100, 50):
        if _payload_bytes(payload) <= MAX_PAYLOAD_BYTES:
            return payload
        for series in payload["series"]:
            stride = max(1, -(-len(series["points"]) // step))
            series["points"] = series["points"][::stride]
    for scatter in payload["scatter"]:
        scatter["points"] = scatter["points"][:100]
    if payload.get("geo"):
        payload["geo"]["points"] = payload["geo"]["points"][:120]
    if _payload_bytes(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    for group in payload["category_totals"]:
        group["items"] = group["items"][:10]
    if _payload_bytes(payload) <= MAX_PAYLOAD_BYTES:
        return payload
    payload["preview"] = {"headers": payload["preview"]["headers"], "rows": []}
    payload["truncated"] = True
    if _payload_bytes(payload) > MAX_PAYLOAD_BYTES:
        raise FileAnalyticsError(
            "This file has too many columns to summarise. Please analyse a smaller selection."
        )
    return payload


class _Columns:
    """Column-major storage of the analysed rows."""

    __slots__ = ("headers", "values", "missing", "sample", "rows", "duplicates", "preview")

    def __init__(self, headers: List[str]):
        self.headers = headers
        self.values: Dict[str, List[Any]] = {header: [] for header in headers}
        self.missing: Dict[str, int] = {header: 0 for header in headers}
        self.sample: Dict[str, List[Any]] = {header: [] for header in headers}
        self.rows = 0
        self.duplicates = 0
        self.preview: List[Dict[str, Any]] = []


def _collect_all(readers: List[Dict[str, Any]], headers: List[str], max_rows: int) -> _Columns:
    store = _Columns(headers)
    seen: set = set()
    remaining = max_rows
    for entry in readers:
        allowance = max(0, remaining)
        stored = _read_into(store, entry, allowance, seen)
        entry["rows_analyzed"] = stored
        remaining -= stored
    return store


def _read_into(store: _Columns, reader: Dict[str, Any], allowance: int, seen: set) -> int:
    own_headers: List[str] = reader["headers"]
    stored = 0
    for _index, record in file_import.open_file(reader["path"], reader["format"],
                                                max_rows=allowance + 1):
        store.rows += 1
        if stored >= allowance:
            continue
        stored += 1
        for header in store.headers:
            if header == SOURCE_COLUMN:
                raw: Any = reader["name"]
            elif header in own_headers:
                raw = record.get(header)
            else:
                raw = None
            if validators.is_missing(raw):
                store.missing[header] += 1
                store.values[header].append(None)
                continue
            store.values[header].append(raw)
            sample = store.sample[header]
            if len(sample) < TYPE_SAMPLE_SIZE:
                sample.append(raw)
        if len(store.preview) < PREVIEW_ROWS:
            preview = {header: _preview_value(record.get(header)) for header in own_headers[:PREVIEW_MAX_COLUMNS]}
            preview[SOURCE_COLUMN] = reader["name"]
            store.preview.append(preview)
        signature = _row_signature(record, own_headers)
        if signature in seen:
            store.duplicates += 1
        else:
            seen.add(signature)
    return stored


def _looks_like_date(raw: Any) -> bool:
    if isinstance(raw, (datetime, date)):
        return True
    text = str(raw).strip()
    return len(text) >= 6 and any(hint in text for hint in _DATE_HINTS)


def _as_datetime(raw: Any) -> Optional[datetime]:
    if not _looks_like_date(raw):
        return None
    moment, _issues = validators.parse_timestamp(raw)
    if moment is None or not 1800 <= moment.year <= 2200:
        return None
    return moment


def _as_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in _TRUE_TOKENS:
        return True
    if text in _FALSE_TOKENS:
        return False
    return None


def _classify(sample: List[Any]) -> str:
    if not sample:
        return "empty"
    size = len(sample)
    numeric = sum(1 for value in sample if validators.to_float(value) is not None)
    dated = sum(1 for value in sample if _as_datetime(value) is not None)
    boolean = sum(1 for value in sample if _as_bool(value) is not None)
    if dated >= size * 0.7:
        return "datetime"
    if boolean >= size * 0.9 and numeric < size * 0.5:
        return "boolean"
    if numeric >= size * 0.85:
        return "number"
    return "text"


def _aligned_numeric(values: List[Any]) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for value in values:
        out.append(None if value is None else validators.to_float(value))
    return out


def _dense_numeric(values: List[Any]) -> List[float]:
    return [number for number in _aligned_numeric(values) if number is not None]


def _aligned_dates(values: List[Any]) -> List[Optional[datetime]]:
    return [None if value is None else _as_datetime(value) for value in values]


def _aligned_labels(values: List[Any]) -> List[Optional[str]]:
    out: List[Optional[str]] = []
    for value in values:
        if value is None:
            out.append(None)
        else:
            text = str(value).strip()[:MAX_TEXT_LENGTH]
            out.append(text or None)
    return out


def _number_profile(values: List[Any]) -> Dict[str, Any]:
    numbers = _dense_numeric(values)
    profile: Dict[str, Any] = {"values": len(numbers)}
    if not numbers:
        return profile
    ordered = sorted(numbers)
    low, high = ordered[0], ordered[-1]
    if len(ordered) >= 3:
        q1, median, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    else:
        median = statistics.median(ordered)
        q1 = q3 = median
    iqr = q3 - q1
    inner_low, inner_high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    profile.update({
        "min": _num(low),
        "max": _num(high),
        "sum": _num(sum(ordered)),
        "mean": _num(statistics.fmean(ordered)),
        "median": _num(median),
        "std": _num(statistics.pstdev(ordered)) if len(ordered) > 1 else 0.0,
        "q1": _num(q1),
        "q3": _num(q3),
        "iqr": _num(iqr),
        "outliers": sum(1 for value in ordered if value < inner_low or value > inner_high),
        "constant": low == high,
        "histogram": _histogram(ordered, low, high),
    })
    return profile


def _histogram(ordered: List[float], low: float, high: float) -> List[Dict[str, Any]]:
    total = len(ordered)
    if low == high:
        return [{"label": _label(low), "start": _num(low), "end": _num(high), "count": total}]
    width = (high - low) / HISTOGRAM_BINS
    counts = [0] * HISTOGRAM_BINS
    for value in ordered:
        counts[min(int((value - low) / width), HISTOGRAM_BINS - 1)] += 1
    return [
        {
            "label": _label(low + index * width),
            "start": _num(low + index * width),
            "end": _num(low + (index + 1) * width),
            "count": counts[index],
        }
        for index in range(HISTOGRAM_BINS)
    ]


def _datetime_profile(values: List[Any]) -> Dict[str, Any]:
    moments = sorted(moment for moment in _aligned_dates(values) if moment is not None)
    profile: Dict[str, Any] = {"values": len(moments)}
    if not moments:
        return profile
    first, last = moments[0], moments[-1]
    per_day = Counter(moment.date().isoformat() for moment in moments)
    profile.update({
        "first": first.isoformat(timespec="seconds"),
        "last": last.isoformat(timespec="seconds"),
        "span_days": max(0, (last.date() - first.date()).days),
        "distinct_days": len(per_day),
        "constant": first == last,
    })
    return profile


def _boolean_profile(values: List[Any]) -> Dict[str, Any]:
    counts = Counter("true" if _as_bool(value) else "false"
                     for value in values if value is not None)
    total = sum(counts.values())
    items = [{"label": key, "count": counts[key], "share": _ratio(counts[key], total)}
             for key in ("true", "false") if counts[key]]
    return {"values": total, "items": items, "constant": len(items) == 1}


def _text_profile(values: List[Any]) -> Dict[str, Any]:
    counts = Counter(label for label in _aligned_labels(values) if label)
    total = sum(counts.values())
    longest = max((len(label) for label in counts), default=0)
    return {
        "values": total,
        "top": [{"label": label, "count": count, "share": _ratio(count, total)}
                for label, count in counts.most_common(TOP_CATEGORIES)],
        "longest_length": longest,
        "high_cardinality": len(counts) > TOP_CATEGORIES,
        "constant": len(counts) == 1,
    }


def _daily_bucket(moment: datetime) -> str:
    return moment.date().isoformat()


def _weekly_bucket(moment: datetime) -> str:
    return (moment.date() - timedelta(days=moment.weekday())).isoformat()


def _monthly_bucket(moment: datetime) -> str:
    return f"{moment.year:04d}-{moment.month:02d}-01"


_BUCKET_NAMES: Dict[Callable[[datetime], str], str] = {
    _daily_bucket: "day",
    _weekly_bucket: "week",
    _monthly_bucket: "month",
}


def _build(headers: List[str], store: _Columns, readers: List[Dict[str, Any]],
           target_year: Optional[int]) -> Dict[str, Any]:
    analysed = sum(int(entry.get("rows_analyzed", 0)) for entry in readers)
    types = {header: _classify(store.sample[header]) for header in headers}

    profiles: List[Dict[str, Any]] = []
    for header in headers:
        kind = types[header]
        values = store.values[header]
        distinct, distinct_capped = _distinct_count(values, kind)
        profile: Dict[str, Any] = {
            "column": header,
            "type": kind,
            "missing": store.missing[header],
            "missing_rate": _ratio(store.missing[header], analysed),
            "distinct": distinct,
        }
        if distinct_capped:
            profile["distinct_capped"] = True
        if kind == "number":
            profile.update(_number_profile(values))
        elif kind == "datetime":
            profile.update(_datetime_profile(values))
        elif kind == "boolean":
            profile.update(_boolean_profile(values))
        elif kind == "empty":
            profile.update({"values": 0, "constant": True})
        else:
            profile.update(_text_profile(values))
        profiles.append(profile)

    latitude = _coord_column(store, types, ("latitude", "lat_", "_lat", "lat"), -90, 90)
    longitude = _coord_column(store, types, ("longitude", "long_", "long", "lng"), -180, 180)
    coordinates = {column for column in (latitude, longitude) if column}

    measures = [profile["column"] for profile in profiles
                if types[profile["column"]] == "number"
                and not profile.get("constant")
                and profile["column"] not in coordinates]
    # Richest first, so the category chart opens on the column that splits the data
    # best and the cap keeps the informative ones rather than the first in file order.
    categorical = [profile for profile in profiles
                   if types[profile["column"]] in {"text", "boolean"}
                   and not profile.get("high_cardinality")]
    categorical.sort(key=lambda profile: -profile["distinct"])
    dimensions = [profile["column"] for profile in categorical]
    dated = [profile for profile in profiles if types[profile["column"]] == "datetime"]
    time_column = max(dated, key=lambda profile: profile.get("distinct_days", 0))["column"] \
        if dated else None

    aligned: Dict[str, List[Optional[float]]] = {
        header: _aligned_numeric(store.values[header])
        for header in measures[:CORRELATION_MAX_MEASURES]
    }

    return {
        "rows_analyzed": analysed,
        "truncated": store.rows > analysed,
        "column_count": len([header for header in headers if header != SOURCE_COLUMN]),
        "files": [{"name": entry["name"], "format": entry["format"],
                   "rows_analyzed": entry.get("rows_analyzed", 0)} for entry in readers],
        "columns": profiles,
        "measures": measures[:MAX_SERIES_MEASURES],
        "dimensions": dimensions[:MAX_DIMENSIONS],
        "time_column": time_column,
        "quality": {
            "completeness": _ratio(sum(profile.get("values", 0) for profile in profiles),
                                   len(headers) * analysed),
            "duplicate_rows": store.duplicates,
            "constant_columns": [profile["column"] for profile in profiles if profile.get("constant")],
            "high_cardinality_columns": [profile["column"] for profile in profiles
                                         if profile.get("high_cardinality")],
            "most_missing": sorted(
                ({"column": profile["column"], "missing_rate": profile["missing_rate"]}
                 for profile in profiles),
                key=lambda item: item["missing_rate"], reverse=True)[:5],
        },
        "series": _series(store, time_column, measures[:MAX_SERIES_MEASURES], aligned),
        "category_totals": _category_totals(store, types, dimensions[:MAX_DIMENSIONS],
                                            measures[:MAX_CATEGORY_MEASURES], aligned),
        "correlation": _correlation(measures[:CORRELATION_MAX_MEASURES], aligned),
        "scatter": _scatter(measures[:CORRELATION_MAX_MEASURES], aligned),
        "geo": _geo(store, dimensions, latitude, longitude, measures[:MAX_CATEGORY_MEASURES], aligned),
        "heatmap": _heatmap(store, time_column, measures),
        "forecast": _forecast(store, time_column, measures, target_year),
        "preview": {"headers": headers[:PREVIEW_MAX_COLUMNS], "rows": store.preview},
    }


def _coord_column(store: _Columns, types: Dict[str, str], needles: Tuple[str, ...],
                  low: float, high: float) -> Optional[str]:
    for header in store.headers:
        if header == SOURCE_COLUMN or types.get(header) != "number":
            continue
        name = header.lower()
        if not any(needle in name for needle in needles):
            continue
        numbers = _dense_numeric(store.values[header])
        if len(numbers) < 2:
            continue
        if min(numbers) >= low and max(numbers) <= high:
            return header
    return None


def _geo(store: _Columns, dimensions: List[str], latitude: Optional[str], longitude: Optional[str],
         measures: List[str], aligned: Dict[str, List[Optional[float]]]) -> Optional[Dict[str, Any]]:
    if not latitude or not longitude:
        return None
    lats = _aligned_numeric(store.values[latitude])
    lngs = _aligned_numeric(store.values[longitude])
    label_column = next((column for column in dimensions if column != SOURCE_COLUMN), None)
    labels = _aligned_labels(store.values[label_column]) if label_column else []
    grouped: Dict[Tuple[float, float], Dict[str, Any]] = {}
    for position, (lat, lng) in enumerate(zip(lats, lngs)):
        if lat is None or lng is None:
            continue
        key = (round(lat, 2), round(lng, 2))
        bucket = grouped.setdefault(key, {"lat": key[0], "lng": key[1], "count": 0,
                                          "values": [], "labels": Counter()})
        bucket["count"] += 1
        numbers = aligned.get(measures[0], []) if measures else []
        if position < len(numbers) and numbers[position] is not None:
            bucket["values"].append(numbers[position])
        if position < len(labels) and labels[position]:
            bucket["labels"][labels[position]] += 1
    if len(grouped) < 2:
        return None
    points = []
    for bucket in grouped.values():
        points.append({
            "lat": bucket["lat"],
            "lng": bucket["lng"],
            "count": bucket["count"],
            "label": bucket["labels"].most_common(1)[0][0] if bucket["labels"] else None,
            "value": _num(statistics.fmean(bucket["values"])) if bucket["values"] else None,
        })
    points.sort(key=lambda point: -point["count"])
    return {
        "latitude_column": latitude,
        "longitude_column": longitude,
        "label_column": label_column,
        "measure": measures[0] if measures else None,
        "points": points[:GEO_MAX_POINTS],
    }


def _heatmap(store: _Columns, time_column: Optional[str],
             measures: List[str]) -> Optional[Dict[str, Any]]:
    """Year against month, so a recurring season shows up as a band instead of a wobble."""
    if time_column is None or not measures:
        return None
    moments = _aligned_dates(store.values[time_column])
    numbers = _aligned_numeric(store.values[measures[0]])
    cells: Dict[Tuple[int, int], List[float]] = {}
    for position, moment in enumerate(moments):
        number = numbers[position] if position < len(numbers) else None
        if moment is None or number is None:
            continue
        cells.setdefault((moment.year, moment.month), []).append(number)
    if not cells:
        return None
    years = sorted({year for year, _month in cells})
    trimmed = years[-HEATMAP_MAX_YEARS:]
    allowed = set(trimmed)
    values = [statistics.fmean(group) for (year, _month), group in cells.items() if year in allowed]
    centre = statistics.fmean(values) if values else 0.0
    spread = statistics.pstdev(values) if len(values) > 1 else 0.0
    return {
        "column": measures[0],
        "years": trimmed,
        "cells": [
            {"year": year, "month": month, "value": _num(statistics.fmean(group)),
             "count": len(group),
             "deviation": _num(statistics.fmean(group) - centre)}
            for (year, month), group in sorted(cells.items()) if year in allowed
        ],
        "mean": _num(centre),
        "std": _num(spread),
    }


def _forecast(store: _Columns, time_column: Optional[str],
              measures: List[str], target_year: Optional[int]) -> Dict[str, Any]:
    if time_column is None or not measures:
        return {"available": False,
                "reason": "A projection needs one date column and at least one numeric column with varying values."}
    moments = _aligned_dates(store.values[time_column])
    last_year = max((moment.year for moment in moments if moment is not None), default=None)
    if last_year is None:
        return {"available": False, "reason": "No readable dates were found in this file."}
    year = target_year or last_year + 1
    if year < 1900 or year > last_year + 50:
        return {"available": False,
                "reason": f"Pick a target year between {last_year + 1} and {last_year + 50}."}

    columns: List[Dict[str, Any]] = []
    blocked: List[str] = []
    for measure in measures[:FORECAST_MEASURES]:
        numbers = _aligned_numeric(store.values[measure])
        samples = [(moment, numbers[position]) for position, moment in enumerate(moments)
                   if moment is not None and position < len(numbers) and numbers[position] is not None]
        try:
            columns.append(file_forecast.build_forecast(samples, year, measure))
        except file_forecast.ForecastUnavailable as exc:
            blocked.append(str(exc))
    if not columns:
        return {"available": False, "target_year": year, "reason": " ".join(blocked)}
    return {"available": True, "target_year": year, "columns": columns,
            "skipped": blocked, "basis_last_year": last_year}


def _series(store: _Columns, time_column: Optional[str],
            measures: List[str], aligned: Dict[str, List[Optional[float]]]) -> List[Dict[str, Any]]:
    if time_column is None:
        return []
    moments = _aligned_dates(store.values[time_column])
    timeline = [(position, moment) for position, moment in enumerate(moments) if moment is not None]
    if not timeline:
        return []
    bucket = _adaptive_bucket(timeline)
    out: List[Dict[str, Any]] = []
    for measure in measures:
        numbers = aligned.get(measure, [])
        buckets: Dict[str, List[float]] = {}
        for position, moment in timeline:
            number = numbers[position] if position < len(numbers) else None
            if number is None:
                continue
            buckets.setdefault(bucket(moment), []).append(number)
        if not buckets:
            continue
        out.append({
            "column": measure,
            "bucket": _BUCKET_NAMES[bucket],
            "aggregation": "mean",
            "points": [
                {
                    "period": period,
                    "value": _num(statistics.fmean(group)),
                    "sum": _num(sum(group)),
                    "count": len(group),
                    "min": _num(min(group)),
                    "max": _num(max(group)),
                }
                for period, group in sorted(buckets.items())
            ][:SERIES_MAX_POINTS],
        })
    return out


def _adaptive_bucket(timeline: List[Tuple[int, datetime]]) -> Callable[[datetime], str]:
    """Keep the whole span visible instead of silently dropping the tail of the series."""
    for candidate in (_daily_bucket, _weekly_bucket, _monthly_bucket):
        if len({candidate(moment) for _position, moment in timeline}) <= SERIES_MAX_POINTS:
            return candidate
    return _monthly_bucket


def _category_totals(store: _Columns, types: Dict[str, str], dimensions: List[str],
                     measures: List[str], aligned: Dict[str, List[Optional[float]]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for dimension in dimensions:
        raw = store.values[dimension]
        labels = (["true" if _as_bool(value) else "false" if value is not None else None
                   for value in raw] if types[dimension] == "boolean"
                  else _aligned_labels(raw))
        counts: Counter = Counter()
        grouped: Dict[str, Dict[str, List[float]]] = {}
        for position, label in enumerate(labels):
            if not label:
                continue
            counts[label] += 1
            for measure in measures:
                values = aligned.get(measure, [])
                if position < len(values) and values[position] is not None:
                    grouped.setdefault(label, {}).setdefault(measure, []).append(values[position])
        total = sum(counts.values())
        items: List[Dict[str, Any]] = []
        for label, count in counts.most_common(TOP_CATEGORIES):
            item: Dict[str, Any] = {"label": label, "count": count, "share": _ratio(count, total)}
            for measure, per_label in grouped.get(label, {}).items():
                item[f"mean_{measure}"] = _num(statistics.fmean(per_label))
            items.append(item)
        out.append({"column": dimension, "measures": list(measures), "items": items})
    return out


def _correlation(measures: List[str], aligned: Dict[str, List[Optional[float]]]) -> Dict[str, Any]:
    cells: List[Dict[str, Any]] = []
    for index, row in enumerate(measures):
        for column in measures[index:]:
            cells.append({
                "row": row,
                "column": column,
                "value": 1.0 if row == column else _pearson(aligned.get(row, []), aligned.get(column, [])),
            })
    return {"columns": measures, "cells": cells}


def _scatter(measures: List[str], aligned: Dict[str, List[Optional[float]]]) -> List[Dict[str, Any]]:
    scored: List[Tuple[float, str, str]] = []
    for index, row in enumerate(measures):
        for column in measures[index + 1:]:
            strength = _pearson(aligned.get(row, []), aligned.get(column, []))
            if strength is not None:
                scored.append((abs(strength), row, column))
    scored.sort(reverse=True)

    out: List[Dict[str, Any]] = []
    for _strength, row, column in scored[:SCATTER_MAX_PAIRS]:
        pairs = _complete_pairs(aligned.get(row, []), aligned.get(column, []))
        stride = max(1, len(pairs) // SCATTER_POINTS) if pairs else 1
        out.append({
            "x": row,
            "y": column,
            "points": [{"x": _num(x), "y": _num(y)} for x, y in pairs[::stride]],
        })
    return out


def _complete_pairs(xs: List[Optional[float]], ys: List[Optional[float]]) -> List[Tuple[float, float]]:
    usable = min(len(xs), len(ys))
    return [(xs[index], ys[index]) for index in range(usable)
            if xs[index] is not None and ys[index] is not None]


def _pearson(xs: List[Optional[float]], ys: List[Optional[float]]) -> Optional[float]:
    pairs = _complete_pairs(xs, ys)
    if len(pairs) < 3:
        return None
    mean_x = statistics.fmean(x for x, _y in pairs)
    mean_y = statistics.fmean(y for _x, y in pairs)
    covariance = spread_x = spread_y = 0.0
    for x, y in pairs:
        dx = x - mean_x
        dy = y - mean_y
        covariance += dx * dy
        spread_x += dx * dx
        spread_y += dy * dy
    if spread_x <= 0 or spread_y <= 0:
        return None
    return round(covariance / math.sqrt(spread_x * spread_y), 4)


def _distinct_count(values: List[Any], kind: str) -> Tuple[int, bool]:
    """Returns the distinct count plus whether tracking stopped early, so the UI can say "480+"
    instead of presenting the cap as the real cardinality."""
    keys: set = set()
    for value in values:
        if value is None:
            continue
        if kind == "number":
            number = validators.to_float(value)
            if number is None:
                continue
            keys.add(number)
        elif kind == "datetime":
            moment = _as_datetime(value)
            if moment is not None:
                keys.add(moment.isoformat(timespec="seconds"))
        elif kind == "boolean":
            keys.add(bool(_as_bool(value)))
        else:
            keys.add(str(value).strip()[:MAX_TEXT_LENGTH])
        if len(keys) > _MAX_DISTINCT_TRACKED:
            return len(keys), True
    return len(keys), False


def _row_signature(record: Dict[str, Any], headers: List[str]) -> str:
    joined = "\x1f".join(str(record.get(header, "")) for header in headers)
    return hashlib.sha256(joined.encode("utf-8", "replace")).hexdigest()


def _preview_value(raw: Any) -> Any:
    if validators.is_missing(raw):
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return _num(float(raw))
    return str(raw).strip()[:MAX_TEXT_LENGTH]


def _ratio(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole, 4)


def _num(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    magnitude = abs(number)
    if magnitude >= 1000:
        return round(number, 2)
    if magnitude >= 1:
        return round(number, 4)
    return round(number, 8)


def _label(value: float) -> str:
    number = _num(value)
    if number is None:
        return "n/a"
    return f"{number:g}"
