"""Observation quality engine: schema, timestamps, coordinates, units,
plausibility, deduplication and a JSON-serialisable :class:`ValidationReport`.

Data-quality score formula (documented per brief)::

    dq_score = 0.40 * field_completeness
             + 0.35 * valid_share
             + 0.25 * (1 - duplicate_share)        clamped to [0.0, 1.0]

* ``field_completeness`` — mean, over the ten canonical metric fields, of the
  share of accepted records that carry that measurement (absent values are
  counted as missing, never as zeros).
* ``valid_share`` — accepted-and-not-suspect records / total input records.
* ``duplicate_share`` — duplicate rejects / total input records.

Schema validation uses ``jsonschema`` when installed (it is in the reference
environment); otherwise a bundled draft-07 subset validator covering required
fields, types, enums, numeric bounds, ``$ref``, ``items`` and
``additionalProperties`` runs — the behaviour the pipeline actually relies on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from schemas import VALIDATOR_VERSION, load_observation_schema
from validation.dedupe import DeduplicationIndex, make_dedupe_key
from validation.units import convert_metric

CANONICAL_METRICS = [
    "temperature", "temperature_max", "temperature_min", "precipitation", "humidity",
    "wind_speed", "wind_direction", "pressure", "cloud_cover", "solar_radiation",
]

#: physically plausible ranges (value is flagged suspect, record kept)
PHYSICAL_RANGES = {
    "temperature": (-90.0, 60.0),
    "temperature_max": (-90.0, 60.0),
    "temperature_min": (-90.0, 60.0),
    "precipitation": (0.0, 2000.0),
    "humidity": (0.0, 100.0),
    "cloud_cover": (0.0, 100.0),
    "wind_speed": (0.0, 113.0),
    "wind_direction": (0.0, 360.0),
    "pressure": (850.0, 1100.0),
    "solar_radiation": (0.0, 60.0),
}

GRANULARITIES = {"instantaneous", "hourly", "daily", "monthly"}
MAX_EXAMPLES = 20


# --------------------------------------------------------------------------
# schema validation
# --------------------------------------------------------------------------
def _build_schema_validator():
    try:
        import jsonschema

        schema = load_observation_schema()

        def _check(record: dict) -> list:
            errors = []
            validator = jsonschema.Draft7Validator(schema)
            for err in validator.iter_errors(record):
                errors.append({"rule": "schema", "detail": f"{'/'.join(map(str, err.path))}: {err.message}"[:300]})
            return errors

        return _check, "jsonschema"
    except Exception:
        return _subset_check, "subset"


def _value_matches_type(value, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return True


def _resolve_ref(ref: str, root: dict) -> dict:
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def _subset_check(record: dict, _root: Optional[dict] = None) -> list:
    root = _root if _root is not None else load_observation_schema()
    errors: list = []

    def walk(value, spec, path):
        if "$ref" in spec:
            spec = _resolve_ref(spec["$ref"], root)
        t = spec.get("type")
        if t and not _value_matches_type(value, t):
            errors.append({"rule": "schema", "detail": f"{path}: expected type {t}"})
            return
        if "enum" in spec and value not in spec["enum"]:
            errors.append({"rule": "schema", "detail": f"{path}: {value!r} not in enum"})
        if isinstance(value, dict):
            for req in spec.get("required", []):
                if req not in value:
                    errors.append({"rule": "schema", "detail": f"{path}: missing required '{req}'"})
            props = spec.get("properties", {})
            if spec.get("additionalProperties") is False:
                for k in value:
                    if k not in props:
                        errors.append({"rule": "schema", "detail": f"{path}: unexpected property '{k}'"})
            for k, sub in props.items():
                if k in value:
                    walk(value[k], sub, f"{path}.{k}")
        if isinstance(value, list) and "items" in spec:
            for i, item in enumerate(value):
                walk(item, spec["items"], f"{path}[{i}]")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append({"rule": "schema", "detail": f"{path}: {value} < minimum {spec['minimum']}"})
            if "maximum" in spec and value > spec["maximum"]:
                errors.append({"rule": "schema", "detail": f"{path}: {value} > maximum {spec['maximum']}"})

    walk(record, root, "$")
    return errors


_SCHEMA_CHECK, SCHEMA_ENGINE = _build_schema_validator()


# --------------------------------------------------------------------------
# timestamps / coordinates
# --------------------------------------------------------------------------
def normalize_timestamp(value) -> tuple:
    """Parse to UTC ISO-8601. Returns (iso_utc, flags). Naive input assumed UTC and flagged."""
    flags: list = []
    if not isinstance(value, str) or not value.strip():
        return None, ["timestamp_missing"]
    text = value.strip()
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    iso = iso.replace(" ", "T", 1) if " " in iso and "T" not in iso else iso
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None, ["timestamp_unparsable"]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
        flags.append("timestamp_tz_assumed_utc")
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ"), flags


def check_coordinates(location: dict) -> tuple:
    flags = []
    lat, lon = location.get("latitude"), location.get("longitude")
    if lat is None or lon is None:
        return ["coordinate_missing"]
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return ["coordinate_out_of_range"]
    if float(lat) == 0.0 and float(lon) == 0.0:
        flags.append("null_island_suspect")
    precision = location.get("coordinate_precision")
    if precision is not None and int(precision) < 2:
        flags.append("coordinate_precision_low")
    return flags


# --------------------------------------------------------------------------
# outcomes
# --------------------------------------------------------------------------
@dataclass
class ValidationOutcome:
    record: Optional[dict]
    issues: list = field(default_factory=list)   # hard failures -> rejection
    flags: list = field(default_factory=list)    # suspect markers, record kept


def validate_record(raw: dict, transformations_sink: Optional[list] = None) -> ValidationOutcome:
    """Validate/normalise one record. Never mutates silently: every conversion
    is appended to ``transformations_sink`` (a copy is returned for
    provenance) and every suspect rule becomes a flag."""
    issues: list = []
    flags: list = []
    conversions: list = []
    record = json.loads(json.dumps(raw, default=str))  # deep copy, JSON-safe

    # 1. schema
    if SCHEMA_ENGINE == "subset":
        schema_errors = _subset_check(record)
    else:
        schema_errors = _SCHEMA_CHECK(record)
    if schema_errors:
        return ValidationOutcome(None, issues=schema_errors)

    # 2. timestamp UTC normalisation
    ts, ts_flags = normalize_timestamp(record.get("timestamp"))
    if ts is None:
        return ValidationOutcome(None, issues=[{"rule": "timestamp", "detail": f"unparsable {record.get('timestamp')!r}"}])
    flags.extend(ts_flags)
    record["timestamp"] = ts

    # 3. coordinates
    coord_flags = check_coordinates(record.get("location", {}))
    if "coordinate_missing" in coord_flags or "coordinate_out_of_range" in coord_flags:
        return ValidationOutcome(None, issues=[{"rule": "coordinates", "detail": ", ".join(coord_flags)}])
    flags.extend(coord_flags)

    # 4. metrics: units + plausibility; missing measurements stay omitted.
    metrics = record.get("metrics", {})
    if not metrics:
        return ValidationOutcome(None, issues=[{"rule": "metrics", "detail": "empty metrics object — no measurement to keep"}])
    cleaned: dict = {}
    for name, meas in metrics.items():
        if not isinstance(meas, dict) or "value" not in meas or meas.get("value") is None:
            continue  # omitted, never 0
        value, unit, note = convert_metric(name, float(meas["value"]), meas.get("unit", ""))
        if note:
            conversions.append(note)
            if "unrecognised unit" in note:
                flags.append("unit_unnormalised")
        rng = PHYSICAL_RANGES.get(name)
        if rng and not (rng[0] <= value <= rng[1]):
            flags.append(f"out_of_range:{name}")
        cleaned[name] = {"value": value, "unit": unit}
    record["metrics"] = cleaned

    # 5. dedupe key consistency (rule: sha1 of dataset|ts|gran|station-or-cell)
    expected = make_dedupe_key(record)
    given = record.get("dedupe_key")
    if given != expected:
        conversions.append("dedupe_key recomputed to canonical rule")
        flags.append("dedupe_key_recomputed")
        record["dedupe_key"] = expected

    status = "suspect" if flags else "valid"
    missing = [m for m in CANONICAL_METRICS if m not in cleaned]
    record["quality"] = {
        "status": status,
        "flags": sorted(set(flags)),
        "missing_fields": missing,
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "validator_version": VALIDATOR_VERSION,
    }
    if transformations_sink is not None:
        for c in conversions:
            if c not in transformations_sink:
                transformations_sink.append(c)
    if conversions:
        prov = record.setdefault("provenance", {})
        prov["transformations"] = sorted(set(list(prov.get("transformations", [])) + conversions))
    return ValidationOutcome(record, issues=issues, flags=flags)


# --------------------------------------------------------------------------
# batch validation + report
# --------------------------------------------------------------------------
@dataclass
class ValidationReport:
    total_input: int = 0
    accepted: int = 0
    rejected: int = 0
    suspect: int = 0
    duplicates: int = 0
    per_rule: dict = field(default_factory=dict)
    examples: list = field(default_factory=list)
    missing_profile: dict = field(default_factory=dict)
    dq_score: float = 0.0
    schema_engine: str = SCHEMA_ENGINE

    def bump(self, rule: str) -> None:
        self.per_rule[rule] = self.per_rule.get(rule, 0) + 1

    def add_example(self, kind: str, detail) -> None:
        if len(self.examples) < MAX_EXAMPLES:
            self.examples.append({"kind": kind, "detail": detail})

    def compute_scores(self, accepted_records: list) -> None:
        self.dq_score = dq_score_from(self, accepted_records)
        if accepted_records:
            n = len(accepted_records)
            self.missing_profile = {
                m: round(sum(1 for r in accepted_records if m in r.get("metrics", {})) / n, 4)
                for m in CANONICAL_METRICS
            }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_dict(self) -> dict:
        return {
            "totals": {"input": self.total_input, "accepted": self.accepted, "rejected": self.rejected,
                       "suspect": self.suspect, "duplicates": self.duplicates},
            "per_rule": dict(sorted(self.per_rule.items())),
            "examples": self.examples,
            "missing_profile": self.missing_profile,
            "data_quality_score": round(self.dq_score, 4),
            "schema_engine": self.schema_engine,
            "score_formula": "0.40*field_completeness + 0.35*valid_share + 0.25*(1-duplicate_share)",
        }


def dq_score_from(report: ValidationReport, accepted_records: list) -> float:
    total = max(report.total_input, 1)
    if accepted_records:
        n = len(accepted_records)
        completeness = sum(
            sum(1 for r in accepted_records if m in r.get("metrics", {})) / n for m in CANONICAL_METRICS
        ) / len(CANONICAL_METRICS)
    else:
        completeness = 0.0
    valid_share = (report.accepted - report.suspect) / total
    dup_share = report.duplicates / total
    score = 0.40 * completeness + 0.35 * valid_share + 0.25 * (1.0 - dup_share)
    return max(0.0, min(1.0, score))


class BatchValidator:
    """Validate many records with cross-record duplicate detection."""

    def __init__(self) -> None:
        self.index = DeduplicationIndex()
        self.report = ValidationReport()
        self.accepted: list = []

    def add(self, raw: dict) -> Optional[dict]:
        self.report.total_input += 1
        outcome = validate_record(raw)
        if outcome.record is None:
            self.report.rejected += 1
            for issue in outcome.issues:
                self.report.bump(issue["rule"])
                self.report.add_example("rejected", issue["detail"])
            return None
        if not self.index.accept(outcome.record):
            self.report.duplicates += 1
            self.report.bump("duplicate")
            self.report.add_example("duplicate", outcome.record.get("dedupe_key"))
            return None
        if outcome.record["quality"]["status"] == "suspect":
            self.report.suspect += 1
            for flag in outcome.record["quality"]["flags"]:
                self.report.bump(f"suspect:{flag}")
                self.report.add_example("suspect", {"dedupe_key": outcome.record["dedupe_key"], "flag": flag})
        self.report.accepted += 1
        self.accepted.append(outcome.record)
        return outcome.record

    def finish(self) -> ValidationReport:
        self.report.compute_scores(self.accepted)
        return self.report


def validate_batch(records) -> tuple:
    validator = BatchValidator()
    for rec in records:
        validator.add(rec)
    return validator.accepted, validator.finish()
