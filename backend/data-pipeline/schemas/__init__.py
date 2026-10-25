"""Accessor for the canonical observation contract (``observation.schema.json``).

Every connector, validator, MapReduce job, Spark job and streaming engine must
produce records that satisfy this single schema; batch and stream outputs are
checked against it by ``streaming.schema_compat``.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA_DIR = Path(__file__).resolve().parent
OBSERVATION_SCHEMA_PATH = SCHEMA_DIR / "observation.schema.json"
VALIDATOR_VERSION = "observation-1.0.0"

_cache: dict = {}


def load_observation_schema() -> dict:
    key = str(OBSERVATION_SCHEMA_PATH)
    if key not in _cache:
        _cache[key] = json.loads(OBSERVATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _cache[key]


def canonical_metric_names() -> list:
    props = load_observation_schema()["properties"]["metrics"]["properties"]
    return sorted(props.keys())


def required_top_level_fields() -> list:
    return list(load_observation_schema()["required"])
