"""The canonical observation contract shared by batch, MapReduce and streaming.

One definition (derived from ``backend/common/validators.py``) is expressed three
ways so that every producer writes and every consumer reads the same contract:

1. ``spark_schema()``   - a ``pyspark.sql.types.StructType``;
2. ``pyarrow_schema()`` - a ``pyarrow.Schema`` used for Parquet;
3. ``JSON_SCHEMA``      - a draft-07 style dict used by streaming validation.

Field, type, unit and nullability are listed explicitly: a measurement the source
did not supply stays ``None`` (never coerced to zero), so every metric column is
nullable while identity/provenance columns are required.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from common import validators  # noqa: E402

SCHEMA_VERSION = validators.SCHEMA_VERSION

# Metric fields, their types and units are lifted from the validators module so
# the ingestion path and the big-data path cannot drift apart.
METRIC_FIELDS: List[str] = list(validators.RANGE_LIMITS)

_UNITS: Dict[str, str] = {
    "temperature_c": "celsius",
    "temperature_min_c": "celsius",
    "temperature_max_c": "celsius",
    "precipitation_mm": "millimetres",
    "relative_humidity_pct": "percent",
    "wind_speed_ms": "metres/second",
    "wind_direction_deg": "degrees",
    "surface_pressure_hpa": "hectopascal",
    "cloud_coverage_pct": "percent",
    "solar_radiation_kwh_m2": "kWh/m^2",
}

#: Ordered field contract: (name, arrow type, unit, nullable, description).
OBSERVATION_FIELDS: List[Dict[str, Any]] = [
    {"field": "dedupe_key", "type": "string", "unit": None, "nullable": False,
     "description": "dataset_id|location_code|observation_date - guarantees idempotent writes."},
    {"field": "dataset_id", "type": "string", "unit": None, "nullable": False,
     "description": "MongoDB datasets._id this observation belongs to."},
    {"field": "source_name", "type": "string", "unit": None, "nullable": True,
     "description": "Provenance label (nasa_power, noaa, upload, stream)."},
    {"field": "observation_date", "type": "string", "unit": None, "nullable": False,
     "description": "UTC calendar day, YYYY-MM-DD."},
    {"field": "year", "type": "int", "unit": None, "nullable": False,
     "description": "Partition column derived from observation_date."},
    {"field": "month", "type": "int", "unit": None, "nullable": False,
     "description": "Partition column 1-12 derived from observation_date."},
    {"field": "location_code", "type": "string", "unit": None, "nullable": True,
     "description": "Station identifier (uppercase)."},
    {"field": "location_name", "type": "string", "unit": None, "nullable": True,
     "description": "Human station name."},
    {"field": "region_code", "type": "string", "unit": None, "nullable": False,
     "description": "Region partition key from common/regions.py."},
    {"field": "country", "type": "string", "unit": None, "nullable": True,
     "description": "ISO-style country label."},
    {"field": "latitude", "type": "double", "unit": "degrees", "nullable": False,
     "description": "-90..90 (validators.parse_coordinate enforces bounds)."},
    {"field": "longitude", "type": "double", "unit": "degrees", "nullable": False,
     "description": "-180..180."},
    {"field": "quality_flags", "type": "array<string>", "unit": None, "nullable": False,
     "description": "Issue codes from validators.quality_flags_for_row; empty = clean."},
    {"field": "unit_audit", "type": "string", "unit": None, "nullable": True,
     "description": "Original source units that were converted (batch + streaming share this)."},
    {"field": "schema_version", "type": "int", "unit": None, "nullable": False,
     "description": f"validators.SCHEMA_VERSION at write time ({SCHEMA_VERSION})."},
    {"field": "ingested_at", "type": "timestamp", "unit": None, "nullable": False,
     "description": "UTC write time (batch import or stream event processing)."},
    {"field": "event_time", "type": "timestamp", "unit": None, "nullable": True,
     "description": "Streaming only: source event time used for the watermark."},
]

for _metric in METRIC_FIELDS:
    low, high = validators.RANGE_LIMITS[_metric]
    OBSERVATION_FIELDS.append({
        "field": _metric, "type": "double", "unit": _UNITS[_metric], "nullable": True,
        "description": f"Canonical unit; plausible range {low}..{high}; null = not supplied.",
    })

FIELD_INDEX: Dict[str, Dict[str, Any]] = {item["field"]: item for item in OBSERVATION_FIELDS}

_TYPE_MAP = {
    "string": "string", "int": "integer", "double": "number",
    "timestamp": "string", "array<string>": "array",
}


def json_schema() -> Dict[str, Any]:
    """JSON-schema dict used by the streaming validator and documented contracts."""
    properties: Dict[str, Any] = {}
    for item in OBSERVATION_FIELDS:
        base: Dict[str, Any] = {"type": _TYPE_MAP[item["type"]],
                                "unit": item["unit"], "description": item["description"]}
        if not item["nullable"]:
            base["type"] = _TYPE_MAP[item["type"]]
        if item["field"] in validators.RANGE_LIMITS:
            low, high = validators.RANGE_LIMITS[item["field"]]
            base["minimum"], base["maximum"] = low, high
            base["type"] = ["number", "null"]  # missing stays null, never zero
        if item["type"] == "array<string>":
            base["items"] = {"type": "string"}
        if item["field"] == "latitude":
            base["minimum"], base["maximum"] = -90, 90
        if item["field"] == "longitude":
            base["minimum"], base["maximum"] = -180, 180
        if item["field"] in ("year", "month"):
            base["type"] = "integer"
        if item["nullable"]:
            base.setdefault("default", None)
        properties[item["field"]] = base
    required = [item["field"] for item in OBSERVATION_FIELDS if not item["nullable"]]
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "EarthScape climate observation",
        "version": SCHEMA_VERSION,
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


JSON_SCHEMA = json_schema()


def spark_schema():
    """pyspark StructType for the same contract (import happens on demand)."""
    from pyspark.sql.types import (ArrayType, DoubleType, IntegerType, StringType,
                                   StructField, StructType, TimestampType)

    mapping = {
        "string": StringType(), "int": IntegerType(), "double": DoubleType(),
        "timestamp": TimestampType(), "array<string>": ArrayType(StringType()),
    }
    return StructType([
        StructField(item["field"], mapping[item["type"]],
                    nullable=item["nullable"] or _is_metric(item["field"]))
        for item in OBSERVATION_FIELDS
    ])


def pyarrow_schema():
    import pyarrow as pa

    mapping = {
        "string": pa.string(), "int": pa.int32(), "double": pa.float64(),
        "timestamp": pa.timestamp("us", tz="UTC"), "array<string>": pa.list_(pa.string()),
    }
    return pa.schema([
        pa.field(item["field"], mapping[item["type"]],
                 nullable=item["nullable"] or _is_metric(item["field"]))
        for item in OBSERVATION_FIELDS
    ])


def _is_metric(field: str) -> bool:
    return field in validators.RANGE_LIMITS
