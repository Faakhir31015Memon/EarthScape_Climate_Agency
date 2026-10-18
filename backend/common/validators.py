"""Canonical data-quality primitives shared by every ingestion path.

Design rules (from the EarthScape specification):

* a measurement the source did not supply stays ``None`` - it is never coerced to 0;
* every rejected or suspicious value produces a recorded issue instead of being
  silently dropped;
* units are converted to one canonical unit set and the original unit is retained
  in provenance so an import is reproducible.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from dateutil import parser as date_parser

SCHEMA_VERSION = 3

MISSING_TOKENS = {"", "-", "--", "na", "n/a", "nan", "null", "none", "nil", "-999", "-999.0", "-9999"}

# Canonical numeric limits. A value outside these bounds is stored but flagged so a
# human can review it - it is never silently discarded.
RANGE_LIMITS: Dict[str, Tuple[float, float]] = {
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

CANONICAL_FIELDS = list(RANGE_LIMITS) + ["observation_date", "latitude", "longitude"]

# field -> accepted source units -> multiplier to reach the canonical unit
UNIT_CONVERSIONS: Dict[str, Dict[str, float]] = {
    "temperature_c": {"c": 1.0, "celsius": 1.0, "degc": 1.0, "f": "F", "fahrenheit": "F",
                      "k": "K", "kelvin": "K"},
    "temperature_min_c": {"c": 1.0, "celsius": 1.0, "degc": 1.0, "f": "F", "fahrenheit": "F", "k": "K"},
    "temperature_max_c": {"c": 1.0, "celsius": 1.0, "degc": 1.0, "f": "F", "fahrenheit": "F", "k": "K"},
    "precipitation_mm": {"mm": 1.0, "millimeter": 1.0, "millimeters": 1.0, "millimetre": 1.0,
                         "cm": 10.0, "inch": 25.4, "inches": 25.4, "in": 25.4, "kg/m2": 1.0,
                         "mm/day": 1.0, "kg/m2/day": 1.0},
    "relative_humidity_pct": {"%": 1.0, "percent": 1.0, "pct": 1.0, "fraction": 100.0},
    "wind_speed_ms": {"m/s": 1.0, "ms": 1.0, "mps": 1.0, "km/h": 1 / 3.6, "kmh": 1 / 3.6,
                      "kph": 1 / 3.6, "mph": 0.44704, "kt": 0.514444, "knots": 0.514444},
    "wind_direction_deg": {"deg": 1.0, "degrees": 1.0, "direction": 1.0},
    "surface_pressure_hpa": {"hpa": 1.0, "mb": 1.0, "millibar": 1.0, "millibars": 1.0,
                             "kpa": 10.0, "pa": 0.01},
    "cloud_coverage_pct": {"%": 1.0, "percent": 1.0, "fraction": 100.0},
    "solar_radiation_kwh_m2": {"kwh/m2": 1.0, "kwh/m2/day": 1.0, "mj/m2": 1 / 3.6,
                               "w/m2": 1 / 1000.0, "mj/m2/day": 1 / 3.6},
}


class Issue:
    __slots__ = ("code", "severity", "field", "message", "row")

    def __init__(self, code: str, severity: str, field: Optional[str], message: str,
                 row: Optional[int] = None):
        self.code = code
        self.severity = severity
        self.field = field
        self.message = message
        self.row = row

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "severity": self.severity, "field": self.field,
                "message": self.message, "row": self.row}


def is_missing(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, float) and math.isnan(raw):
        return True
    text = str(raw).strip().lower()
    return text in MISSING_TOKENS


def to_float(raw: Any) -> Optional[float]:
    if is_missing(raw):
        return None
    text = str(raw).strip().replace(",", ".") if isinstance(raw, str) else raw
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def parse_timestamp(raw: Any) -> Tuple[Optional[datetime], List[Issue]]:
    """Return a UTC datetime plus any issues found while parsing."""
    issues: List[Issue] = []
    if is_missing(raw):
        return None, [Issue("timestamp_missing", "error", "observation_date",
                            "No observation timestamp was supplied.")]
    if isinstance(raw, datetime):
        moment = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc), issues
    if isinstance(raw, date):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc), issues
    if isinstance(raw, (int, float)):
        # Excel serial date or POSIX epoch - both are handled explicitly.
        try:
            if 20000 < raw < 60000:
                from datetime import timedelta

                return datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=float(raw)), issues
            if raw > 10 ** 9:
                return datetime.fromtimestamp(float(raw), tz=timezone.utc), issues
        except (OverflowError, OSError, ValueError):
            pass
    text = str(raw).strip()
    compact = re.fullmatch(r"(\d{4})[-/]?(\d{2})[-/]?(\d{2})", text)
    if compact:
        try:
            parsed = datetime(int(compact.group(1)), int(compact.group(2)), int(compact.group(3)),
                              tzinfo=timezone.utc)
            return parsed, issues
        except ValueError:
            pass
    try:
        parsed = date_parser.parse(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc), issues
    except (ValueError, TypeError, OverflowError):
        return None, [Issue("timestamp_unparsable", "error", "observation_date",
                            f"Could not interpret the timestamp '{text[:40]}'.")]


def parse_coordinate(raw: Any, *, kind: str) -> Tuple[Optional[float], List[Issue]]:
    value = to_float(raw)
    if value is None:
        return None, [Issue(f"{kind}_missing", "error", kind, f"A {kind} value is required.")]
    limit = 90.0 if kind == "latitude" else 180.0
    if abs(value) > limit:
        return None, [Issue(f"{kind}_out_of_range", "error", kind,
                            f"{kind} {value} is outside -{limit}..{limit}.")]
    return round(value, 6), issues_for_precision(value, kind)


def issues_for_precision(value: float, kind: str) -> List[Issue]:
    if value == 0.0:
        return [Issue(f"{kind}_zero", "warning", kind,
                      f"{kind} is exactly 0.0 - confirm this is a real location and not a default.")]
    return []


def convert_measurement(field: str, value: float, source_unit: Optional[str]) -> Tuple[float, Optional[str]]:
    """Convert to the canonical unit. Returns (value, flag) where flag may be set."""
    unit = (source_unit or "").strip().lower()
    table = UNIT_CONVERSIONS.get(field, {})
    if not unit or unit not in table:
        return value, ("unit_unknown" if unit else None)
    factor = table[unit]
    if isinstance(factor, str):
        if factor == "F":
            return (value - 32.0) * 5.0 / 9.0, None
        if factor == "K":
            return value - 273.15, None
        return value, "unit_unhandled"
    return value * factor, None


def validate_measurement(field: str, raw: Any, source_unit: Optional[str] = None,
                         row: Optional[int] = None) -> Tuple[Optional[float], List[Issue]]:
    issues: List[Issue] = []
    if is_missing(raw):
        return None, [Issue(f"{field}_absent", "info", field,
                            f"{field} was not supplied by this source.", row)]
    value = to_float(raw)
    if value is None:
        return None, [Issue(f"{field}_non_numeric", "error", field,
                            f"{field} value '{str(raw)[:24]}' is not numeric.", row)]
    converted, unit_flag = convert_measurement(field, value, source_unit)
    if unit_flag == "unit_unknown":
        issues.append(Issue(f"{field}_unit_unknown", "warning", field,
                            f"Unrecognised unit '{source_unit}' for {field}; stored as supplied.", row))
    low, high = RANGE_LIMITS[field]
    if converted < low or converted > high:
        issues.append(Issue(f"{field}_out_of_range", "error", field,
                            f"{field}={round(converted, 3)} is outside the physically plausible "
                            f"range {low}..{high}.", row))
        return None, issues
    if abs(converted) > 0 and abs(converted) < 1e-6:
        issues.append(Issue(f"{field}_suspiciously_small", "warning", field,
                            f"{field} is near zero after conversion.", row))
    return round(converted, 4), issues


def normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_")


COLUMN_SYNONYMS: Dict[str, List[str]] = {
    "observation_date": ["date", "datetime", "timestamp", "time", "day", "obs_date", "date_utc",
                         "observation_date", "validdate_gmt", "vtime", "year_month_day", "date_gmt"],
    "latitude": ["lat", "latitude", "site_latitude", "latitude_dd", "y", "decimal_latitude",
                 "latitude_deg", "lat_dd"],
    "longitude": ["lon", "long", "lng", "longitude", "site_longitude", "longitude_dd", "x",
                  "long_dd", "lon_dd"],
    "location_name": ["site", "site_name", "station", "station_name", "location", "name",
                      "city", "place", "location_name", "stn_name"],
    "location_code": ["station_id", "station_code", "id", "site_id", "location_code", "usaf",
                      "wmo", "stn_id", "plotid"],
    "region_code": ["region", "state", "province", "admin1", "district", "region_code", "zone"],
    "country": ["country", "nation", "cntry", "country_code"],
    "temperature_c": ["t2m", "temp", "temperature", "air_temperature", "tavg", "mean_temp",
                      "temp_c", "temperature_c", "t_2_m", "tmax_mean"],
    "temperature_min_c": ["tmin", "t2m_min", "temperature_min", "min_temp", "mintemp",
                          "temperature_min_c"],
    "temperature_max_c": ["tmax", "t2m_max", "temperature_max", "max_temp", "maxtemp",
                          "temperature_max_c"],
    "precipitation_mm": ["prcp", "precip", "rainfall", "precipitation", "rain", "allsky_precip",
                         "prectotcorr", "precipitation_mm", "daily_precip"],
    "relative_humidity_pct": ["rh", "humidity", "relative_humidity", "rh2m", "avgrh",
                              "relative_humidity_pct"],
    "wind_speed_ms": ["wspd", "wind", "wind_speed", "ws", "ave_wspd", "wind_speed_ms", "gust",
                      "vndrt"],
    "wind_direction_deg": ["wind_direction", "wdir", "wd", "vndr", "wind_direction_deg"],
    "surface_pressure_hpa": ["pressure", "slp", "stp", "surface_pressure", "ps",
                             "pressure_msl", "surface_pressure_hpa"],
    "cloud_coverage_pct": ["cloud", "cloud_cover", "cloud_coverage", "clouds", "n_eff",
                           "cloud_coverage_pct"],
    "solar_radiation_kwh_m2": ["solar", "solar_radiation", "ghi", "rs", "swdwn",
                               "allsky_sfc_sw_dwn", "solar_radiation_kwh_m2"],
}

UNIT_COLUMN_PATTERN = re.compile(r"^(?P<base>[a-z0-9_]+?)_(?P<unit>degc|c|f|k|mm|cm|in|inch|"
                                 r"kmh|kph|ms|mph|kt|hpa|mb|kpa|pct|percent)$")


def guess_canonical_field(header: str) -> Optional[str]:
    normalized = normalize_header(header)
    match = UNIT_COLUMN_PATTERN.match(normalized)
    candidate = match.group("base") if match else normalized
    for field, synonyms in COLUMN_SYNONYMS.items():
        if candidate == field or candidate in synonyms:
            return field
    for field, synonyms in COLUMN_SYNONYMS.items():
        if any(synonym in candidate for synonym in synonyms if len(synonym) > 4):
            return field
    return None


def guess_unit(header: str) -> Optional[str]:
    normalized = normalize_header(header)
    match = UNIT_COLUMN_PATTERN.match(normalized)
    if match:
        return match.group("unit")
    if "fahrenheit" in normalized or normalized.endswith("_f"):
        return "f"
    if "celsius" in normalized or normalized.endswith("_c"):
        return "c"
    return None


def auto_map_columns(headers: List[str]) -> Dict[str, str]:
    """Return a {canonical_field: source_header} suggestion for a file."""
    mapping: Dict[str, str] = {}
    for header in headers:
        field = guess_canonical_field(header)
        if field and field not in mapping:
            mapping[field] = header
    return mapping


def quality_flags_for_row(issues: List[Issue]) -> List[str]:
    return sorted({issue.code for issue in issues if issue.severity in ("error", "warning")})
