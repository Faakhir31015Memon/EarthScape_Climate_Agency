"""Canonical observation builder shared by every ingestion path.

`data-pipeline/schemas/observation.schema.json` is the contract; this module is the
runtime implementation used by the API layer (file uploads and provider polling), while
the distributed jobs use the same field names in Parquet.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from common.serialization import parse_datetime

SCHEMA_VERSION = "1.0.0"

# canonical field -> (default unit, accepted source units with conversion factors to default)
UNIT_CONVERSIONS: dict[str, dict[str, tuple[float, str]]] = {
    "temperature": {
        "c": (1.0, "C"), "°c": (1.0, "C"), "celsius": (1.0, "C"),
        "k": (1.0, "K"), "kelvin": (1.0, "K"),
        "f": (1.0, "F"), "°f": (1.0, "F"), "fahrenheit": (1.0, "F"),
    },
    "precipitation": {
        "mm": (1.0, "mm"), "millimeter": (1.0, "mm"), "millimeters": (1.0, "mm"),
        "in": (25.4, "mm"), "inch": (25.4, "mm"), "inches": (25.4, "mm"), "cm": (10.0, "mm"),
    },
    "humidity": {"%": (1.0, "%"), "percent": (1.0, "%"), "fraction": (100.0, "%"), "1": (100.0, "%")},
    "wind_speed": {
        "m/s": (1.0, "m/s"), "mps": (1.0, "m/s"), "ms-1": (1.0, "m/s"),
        "km/h": (1 / 3.6, "m/s"), "kmh": (1 / 3.6, "m/s"),
        "mph": (0.44704, "m/s"), "knot": (0.514444, "m/s"), "knots": (0.514444, "m/s"),
    },
    "wind_direction": {"deg": (1.0, "deg"), "degrees": (1.0, "deg"), "rad": (57.29578, "deg")},
    "pressure": {"hpa": (1.0, "hPa"), "mb": (1.0, "hPa"), "pa": (0.01, "hPa"),
                 "kpa": (10.0, "hPa"), "inhg": (33.8639, "hPa")},
    "cloud_cover": {"%": (1.0, "%"), "percent": (1.0, "%"), "okta": (12.5, "%")},
    "solar_radiation": {"mj/m2": (1.0, "MJ/m²"), "mj/m²": (1.0, "MJ/m²"), "kwh/m2": (3.6, "MJ/m²"),
                         "w/m2": (1.0, "W/m²")},
}

TEMPERATURE_OFFSETS = {"K": -273.15, "F": None}

# Physically plausible envelope; outside it a value is flagged, never silently dropped.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (-90.0, 60.0),
    "temperature_max": (-90.0, 60.0),
    "temperature_min": (-90.0, 60.0),
    "precipitation": (0.0, 2000.0),
    "humidity": (0.0, 100.0),
    "wind_speed": (0.0, 150.0),
    "wind_direction": (0.0, 360.0),
    "pressure": (500.0, 1100.0),
    "cloud_cover": (0.0, 100.0),
    "solar_radiation": (0.0, 200.0),
}

MISSING_SENTINELS = {"-999", "-9999", "-9999.0", "-9999.9", "nan", "na", "n/a", "null", "none", "",
                     "-9998", "-99.9", "-99999"}

CANONICAL_FIELDS = list(PLAUSIBLE_RANGES) + ["timestamp", "latitude", "longitude", "region",
                                             "station_id", "location_name", "country", "elevation_m"]


def normalise_unit(variable: str, unit: Optional[str]) -> tuple[float, str, bool]:
    """Return (factor, target_unit, needs_offset) for a declared source unit."""
    default_target = {"temperature": "C", "temperature_max": "C", "temperature_min": "C",
                      "precipitation": "mm", "humidity": "%", "wind_speed": "m/s",
                      "wind_direction": "deg", "pressure": "hPa", "cloud_cover": "%",
                      "solar_radiation": "MJ/m²"}.get(variable, "unit")
    key = (unit or "").strip().lower()
    if not key:
        return 1.0, default_target, False
    table = UNIT_CONVERSIONS.get(variable, {})
    if key in table:
        factor, target = table[key]
        return factor, target, target in ("K", "F")
    return 1.0, default_target, False


def convert_value(variable: str, value: float, source_unit: Optional[str]) -> tuple[float, str, str]:
    factor, target, needs_offset = normalise_unit(variable, source_unit)
    original = f"{value}{source_unit or ''}"
    converted = value * factor
    suffix = (source_unit or "").strip().lower()
    if target == "K" or suffix in ("k", "kelvin"):
        converted = value - 273.15
    elif target == "F" or suffix in ("f", "°f", "fahrenheit"):
        converted = (value - 32.0) * 5.0 / 9.0
    transformation = ""
    if abs(factor - 1.0) > 1e-9 or target != (source_unit or target):
        transformation = f"{variable}: {original} -> {round(converted, 3)} {target}"
    return converted, target, transformation


def is_missing(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and (math.isnan(raw) or math.isinf(raw)):
        return True
    text = str(raw).strip().lower()
    return text in MISSING_SENTINELS


def to_float(raw: Any) -> Optional[float]:
    if is_missing(raw):
        return None
    try:
        text = str(raw).strip().replace(",", "") if not isinstance(raw, (int, float)) else raw
        value = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def cell_key(latitude: float, longitude: float, precision: int = 2) -> str:
    """Coarse grid identity so duplicate measurements from the same cell dedupe."""
    lat = round(latitude, precision)
    lon = round(longitude, precision)
    return f"cell:{lat:+06.2f}:{lon:+07.2f}"


def dedupe_key(*, dataset_id: str, timestamp: datetime, granularity: str,
               station_id: Optional[str], latitude: float, longitude: float) -> str:
    locator = (station_id or "").strip().lower() or cell_key(latitude, longitude)
    stamp = timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    raw = f"{dataset_id}|{stamp}|{granularity}|{locator}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def build_observation(*, dataset_id: str, row: dict, column_map: dict[str, str],
                      units_hint: Optional[dict[str, str]] = None, granularity: str = "daily",
                      provider: str = "university_upload", kind: str = "file",
                      ingestion_job_id: Optional[str] = None, source_file: Optional[str] = None,
                      row_number: Optional[int] = None,
                      default_location: Optional[dict] = None,
                      source_timezone: Optional[timezone] = None) -> tuple[Optional[dict], list[str]]:
    """Map one raw row to a canonical document.

    Returns (document, issues). Issues are human-readable and become quality flags.
    A document is None only when the row cannot be placed in time or space at all.
    """
    issues: list[str] = []
    units_hint = units_hint or {}
    values: dict[str, Any] = {}
    for source_col, canonical in column_map.items():
        if canonical in ("ignore", "", None):
            continue
        raw = row.get(source_col, row.get(str(source_col)))
        values[canonical] = raw

    timestamp = parse_datetime(values.get("timestamp"))
    if timestamp is None:
        return None, ["no usable timestamp in this row"]
    if timestamp.tzinfo is None and source_timezone is not None:
        timestamp = timestamp.replace(tzinfo=source_timezone)
        issues.append("timestamp assumed to be in the declared timezone")
    timestamp = timestamp.astimezone(timezone.utc)

    latitude = to_float(values.get("latitude"))
    longitude = to_float(values.get("longitude"))
    if latitude is None or longitude is None:
        if default_location:
            latitude = to_float(default_location.get("latitude"))
            longitude = to_float(default_location.get("longitude"))
            if latitude is not None and longitude is not None:
                issues.append("coordinates taken from the dataset location default")
    if latitude is None or longitude is None:
        return None, ["row has no coordinates and no dataset default location"]
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return None, [f"coordinates out of range ({latitude}, {longitude})"]

    metrics: dict[str, dict[str, Any]] = {}
    transformations: list[str] = []
    flags: list[str] = []
    missing: list[str] = []
    for variable in PLAUSIBLE_RANGES:
        if variable not in values:
            continue
        raw = values[variable]
        if is_missing(raw):
            missing.append(variable)
            continue
        number = to_float(raw)
        if number is None:
            flags.append(f"non_numeric:{variable}")
            issues.append(f"{variable} is not numeric; recorded as rejected value")
            continue
        converted, unit, transformation = convert_value(variable, number, units_hint.get(variable))
        low, high = PLAUSIBLE_RANGES[variable]
        if not (low <= converted <= high):
            flags.append(f"out_of_range:{variable}")
            issues.append(f"{variable}={round(converted, 3)} {unit} is outside the plausible range {low}..{high}")
        metrics[variable] = {"value": round(converted, 4), "unit": unit}
        if transformation:
            transformations.append(transformation)

    if not metrics:
        return None, ["row carries no usable measurement"]

    station_id = None if is_missing(values.get("station_id")) else str(values.get("station_id")).strip()[:64]
    document = {
        "dataset_id": dataset_id,
        "dedupe_key": dedupe_key(dataset_id=dataset_id, timestamp=timestamp,
                                 granularity=granularity, station_id=station_id,
                                 latitude=latitude, longitude=longitude),
        "timestamp": timestamp,
        "granularity": granularity,
        "source": {
            "provider": provider,
            "kind": kind,
            "source_file": source_file,
            "row_number": row_number,
            "retrieved_at": datetime.now(timezone.utc),
        },
        "location": {
            "name": None if is_missing(values.get("location_name")) else str(values.get("location_name")).strip()[:120],
            "country": None if is_missing(values.get("country")) else str(values.get("country")).strip()[:80],
            "region": None if is_missing(values.get("region")) else str(values.get("region")).strip()[:80],
            "station_id": station_id,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "elevation_m": to_float(values.get("elevation_m")),
            "coordinate_precision": 2,
        },
        "metrics": metrics,
        "quality": {
            "status": "suspect" if flags else "valid",
            "flags": flags,
            "missing_fields": missing,
            "validated_at": datetime.now(timezone.utc),
            "validator_version": SCHEMA_VERSION,
        },
        "provenance": {
            "ingestion_job_id": ingestion_job_id,
            "ingested_at": datetime.now(timezone.utc),
            "schema_version": SCHEMA_VERSION,
            "unit_source": "declared" if units_hint else "assumed canonical units",
            "transformations": transformations,
        },
    }
    if ingestion_job_id:
        document["ingestion_job_id"] = ingestion_job_id
    return document, issues


def validate_document(document: dict) -> list[str]:
    """Structural check that mirrors observation.schema.json."""
    errors: list[str] = []
    for required in ("dedupe_key", "dataset_id", "timestamp", "granularity", "location", "metrics"):
        if not document.get(required):
            errors.append(f"missing required field: {required}")
    location = document.get("location") or {}
    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        errors.append("invalid coordinates")
    for name, measurement in (document.get("metrics") or {}).items():
        if not isinstance(measurement, dict) or "value" not in measurement or "unit" not in measurement:
            errors.append(f"metric {name} must carry value and unit")
        if name in PLAUSIBLE_RANGES:
            low, high = PLAUSIBLE_RANGES[name]
            if measurement.get("value") is not None and not (low <= measurement["value"] <= high):
                errors.append(f"metric {name} outside plausible range")
    return errors


def summarise_metrics(document: dict) -> dict[str, float]:
    return {name: measurement["value"] for name, measurement in (document.get("metrics") or {}).items()
            if isinstance(measurement, dict) and measurement.get("value") is not None}
