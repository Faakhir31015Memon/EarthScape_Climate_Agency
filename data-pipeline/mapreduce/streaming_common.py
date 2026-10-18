"""Shared parsing utilities for the Hadoop Streaming mapper/reducer scripts.

These modules run standalone on a cluster (distributed with ``-file``) *and*
locally via ``python mapper.py | sort | python reducer.py``. To keep the mapper
contract identical between the two, the missing-value semantics mirror
``backend/common/validators.py``: when that module is importable (local runs) it
is used directly; on a cluster the same constants are reproduced below, and a
missing measurement is always ``None`` - never zero.
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Make the backend importable in local runs so the shared contract is used.
_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "backend")
if os.path.isdir(_BACKEND) and _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:  # pragma: no cover - cluster containers fall back to the literals below
    from common import validators as _validators

    MISSING_TOKENS = _validators.MISSING_TOKENS
    RANGE_LIMITS = _validators.RANGE_LIMITS
except Exception:  # noqa: BLE001 - must work inside a Hadoop container
    MISSING_TOKENS = {"", "-", "--", "na", "n/a", "nan", "null", "none", "nil",
                      "-999", "-999.0", "-9999"}
    RANGE_LIMITS = {
        "temperature_c": (-90.0, 60.0),
        "temperature_min_c": (-90.0, 60.0),
        "temperature_max_c": (-90.0, 60.0),
        "precipitation_mm": (0.0, 2000.0),
        "relative_humidity_pct": (0.0, 100.0),
        "wind_speed_ms": (0.0, 120.0),
        "wind_direction_deg": (0.0, 360.0),
        "surface_pressure_hpa": (500.0, 1100.0),
        "cloud_coverage_pct": (0.0, 100.0),
        "solar_radiation_kwh_m2": (0.0, 15.0),
    }


def is_missing(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and math.isnan(raw):
        return True
    return str(raw).strip().lower() in MISSING_TOKENS


def to_float(raw: Any) -> Optional[float]:
    if is_missing(raw):
        return None
    try:
        value = float(str(raw).strip().replace(",", ".")) if isinstance(raw, str) else float(raw)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def bounded_metric(record: Dict[str, Any], field: str) -> Tuple[Optional[float], bool]:
    """Return (value_or_None, out_of_range). Out-of-range values are NOT zeroed."""
    raw = record.get(field)
    value = to_float(raw)
    if value is None:
        return None, False
    low, high = RANGE_LIMITS.get(field, (-math.inf, math.inf))
    if value < low or value > high:
        return None, True
    return value, False


def split_line(line: str, headers: Optional[List[str]]) -> Tuple[Optional[Dict[str, Any]], str, Optional[List[str]]]:
    """Parse one JSON-line or CSV line into a dict.

    Returns ``(record, kind, headers)``. CSV input must start with a header row;
    on that row ``kind`` is ``"header"`` and ``headers`` is the normalised list
    used for subsequent rows.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None, "blank", headers
    if text[0] in "{[":
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None, "malformed", headers
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else None
        return (parsed if isinstance(parsed, dict) else None), "json", headers
    cells = [normalize_header(part) for part in text.split(",")]
    if headers is None:
        return None, "header", cells
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != len(headers):
        return None, "malformed", headers
    return {headers[i]: parts[i] for i in range(len(headers))}, "csv", headers


def normalize_header(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


def derive_period(record: Dict[str, Any]) -> Tuple[Optional[str], Optional[int], Optional[int]]:
    region = record.get("region_code") or record.get("region")
    region = str(region).strip() if region is not None else None
    year = to_int(record.get("year"))
    month = to_int(record.get("month"))
    date_text = str(record.get("observation_date") or record.get("date") or "").strip()
    if date_text and (year is None or month is None):
        chunks = date_text.replace("/", "-").split("-")
        if len(chunks) >= 2:
            year = year if year is not None else to_int(chunks[0])
            month = month if month is not None else to_int(chunks[1])
    if not region or not year or not month or month < 1 or month > 12:
        return None, None, None
    return region, year, month


def to_int(raw: Any) -> Optional[int]:
    if is_missing(raw):
        return None
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def counter(name: str, value: int) -> None:
    """Hadoop-style counter line on stderr - never pollutes the key/value stream."""
    sys.stderr.write(f"reporter:counter:EarthScape,{name},{value}\n")
    sys.stderr.flush()


def emit(key: str, value: str) -> None:
    sys.stdout.write(f"{key}\t{value}\n")


def stdin_lines():
    for line in io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"):
        yield line
