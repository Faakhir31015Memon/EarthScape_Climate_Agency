"""Unit normalisation for canonical metrics.

Every conversion is recorded as a human-readable transformation string that
ends up in ``provenance.transformations`` — conversions are never silent.
Canonical units: temperature °C, precipitation mm, humidity %, wind_speed m/s,
wind_direction degrees, pressure hPa, solar_radiation MJ/m², cloud_cover %.
"""

from __future__ import annotations

ABS_ZERO = 273.15


def _t_k_to_c(v):
    return v - ABS_ZERO


def _t_f_to_c(v):
    return (v - 32.0) * 5.0 / 9.0


#: metric -> list of (canonical_unit, [(source_aliases, factor_fn, note)])
RULES = {
    "temperature": {
        "canonical": "C",
        "accept": {"c", "°c", "celsius", "degc", "cels"},
        "converters": [
            ({"k", "kelvin"}, _t_k_to_c, "K -> C (value - 273.15)"),
            ({"f", "°f", "fahrenheit", "degf"}, _t_f_to_c, "F -> C ((value - 32) * 5/9)"),
        ],
    },
    "precipitation": {
        "canonical": "mm",
        "accept": {"mm", "millimeter", "millimeters", "millimetre"},
        "converters": [
            ({"in", "inch", "inches", "in."}, lambda v: v * 25.4, "inches -> mm (x 25.4)"),
            ({"cm", "centimeter", "centimetre"}, lambda v: v * 10.0, "cm -> mm (x 10)"),
            ({"m", "meter", "metre"}, lambda v: v * 1000.0, "m -> mm (x 1000)"),
        ],
    },
    "humidity": {
        "canonical": "%",
        "accept": {"%", "pct", "percent", "percent_relative"},
        "converters": [
            ({"fraction", "0-1", "kg/kg", "-"}, lambda v: v * 100.0, "fraction (0-1) -> % (x 100)"),
        ],
    },
    "cloud_cover": {
        "canonical": "%",
        "accept": {"%", "pct", "percent"},
        "converters": [({"fraction", "0-1"}, lambda v: v * 100.0, "fraction (0-1) -> % (x 100)")],
    },
    "wind_speed": {
        "canonical": "m/s",
        "accept": {"m/s", "mps", "meter/second", "meters/second"},
        "converters": [
            ({"km/h", "kmh", "kph", "km/hour"}, lambda v: v / 3.6, "km/h -> m/s (/ 3.6)"),
            ({"mile/h", "mph", "mi/h", "miles/hour"}, lambda v: v * 0.44704, "mile/h -> m/s (x 0.44704)"),
            ({"kt", "knot", "knots"}, lambda v: v * 0.514444, "knots -> m/s (x 0.514444)"),
        ],
    },
    "wind_direction": {
        "canonical": "degrees",
        "accept": {"°", "deg", "degrees", "dega"},
        "converters": [],
    },
    "pressure": {
        "canonical": "hPa",
        "accept": {"hpa", "hectopascal", "hectopascals", "millibar", "mb"},
        "converters": [
            ({"pa", "pascal", "pascals"}, lambda v: v / 100.0, "Pa -> hPa (/ 100)"),
            ({"kpa"}, lambda v: v * 10.0, "kPa -> hPa (x 10)"),
            ({"psi"}, lambda v: v * 68.9476, "psi -> hPa (x 68.9476)"),
        ],
    },
    "solar_radiation": {
        "canonical": "MJ/m²",
        "accept": {"mj/m²", "mj/m2", "mj per m2"},
        "converters": [
            ({"kwh/m²", "kwh/m2", "kwh/m²/day"}, lambda v: v * 3.6, "kWh/m² -> MJ/m² (x 3.6)"),
            ({"w/m²", "w/m2"}, lambda v: v * 0.0864, "mean W/m² -> MJ/m²/day (x 0.0864)"),
            ({"j/m²", "j/m2"}, lambda v: v / 1e6, "J/m² -> MJ/m² (/ 1e6)"),
        ],
    },
}
#: metrics sharing the temperature rules
RULES["temperature_max"] = RULES["temperature"]
RULES["temperature_min"] = RULES["temperature"]

METRIC_FIELD_PREFIX = {
    "temperature": "temperature", "temperature_max": "temperature", "temperature_min": "temperature",
    "precipitation": "precipitation", "humidity": "humidity", "cloud_cover": "humidity",
    "wind_speed": "wind_speed", "wind_direction": "wind_direction", "pressure": "pressure",
    "solar_radiation": "solar_radiation",
}


def _norm_token(unit: str) -> str:
    return (unit or "").strip().lower().replace(" ", "")


def convert_metric(metric: str, value: float, unit: str):
    """Return ``(value, unit, note_or_None)``; value is normalised to canonical units."""
    rule = RULES.get(metric)
    token = _norm_token(unit)
    if rule is None or not token:
        return float(value), unit or "", None
    if token in {a for a in rule["accept"]} or token == rule["canonical"].lower().replace(" ", ""):
        return float(value), rule["canonical"], None
    for aliases, fn, note in rule["converters"]:
        if token in aliases:
            return float(fn(float(value))), rule["canonical"], f"{metric}: {note}"
    # unknown unit for a known metric: keep it but say so (never guess silently)
    return float(value), unit, f"{metric}: unrecognised unit {unit!r}, kept as reported"
