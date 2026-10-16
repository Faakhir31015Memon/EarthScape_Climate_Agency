"""Column auto-mapping: suggest canonical fields from header names, always user-editable."""

from __future__ import annotations

import re

from common.canonical import CANONICAL_FIELDS

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("timestamp", re.compile(r"^(date|datetime|time|timestamp|observation_date|dt|valid_date|"
                              r"date_utc|local_date)$|_(date|time)$|^date_", re.I)),
    ("temperature", re.compile(r"^(t|temp|temperature|tavg|air_temp|temperature_2m|"
                               r"mean_temp|avg_temp)$|t2m", re.I)),
    ("temperature_max", re.compile(r"(tmax|temp_max|max_temp|temperature_2m_max|daily_max)", re.I)),
    ("temperature_min", re.compile(r"(tmin|temp_min|min_temp|temperature_2m_min|daily_min)", re.I)),
    ("precipitation", re.compile(r"^(prcp|precip|precipitation|rain|rainfall|precipitation_sum|"
                                 r"total_precip|prcp_mm)$", re.I)),
    ("humidity", re.compile(r"(rh|humidity|relative_humidity)", re.I)),
    ("wind_speed", re.compile(r"(wspd|wind_speed|wind_10m|ws_10m|max_wind|gust)", re.I)),
    ("wind_direction", re.compile(r"(wd|wind_dir|wind_direction)", re.I)),
    ("pressure", re.compile(r"(press|pressure|slp|mslp|station_pressure)", re.I)),
    ("cloud_cover", re.compile(r"(cloud|cld|cover)", re.I)),
    ("solar_radiation", re.compile(r"(swd|shortwave|solar|ghi|radiation)", re.I)),
    ("latitude", re.compile(r"^(lat|latitude|lat_dd|y)$", re.I)),
    ("longitude", re.compile(r"^(lon|lng|long|longitude|lon_dd|x)$", re.I)),
    ("station_id", re.compile(r"(station|stn|id|site_code|gsod|usaf|wban)", re.I)),
    ("location_name", re.compile(r"(name|city|location|place|station_name|site)", re.I)),
    ("region", re.compile(r"^(region|state|province|district|admin|division|zone)$", re.I)),
    ("country", re.compile(r"^(country|nation|iso)$", re.I)),
    ("elevation_m", re.compile(r"(elev|altitude|elevation|height_m)", re.I)),
]

UNIT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\(°?f\)|_degf|_f$|\[f\]", re.I), "F"),
    (re.compile(r"\(°?c\)|_degc|_c$|\[c\]", re.I), "C"),
    (re.compile(r"\(mm\)|_mm$|\[mm\]", re.I), "mm"),
    (re.compile(r"\(in\)|_in$|\[in\]|inches", re.I), "inches"),
    (re.compile(r"(km/h|kmh|_kmh)", re.I), "km/h"),
    (re.compile(r"(m/s|mps)", re.I), "m/s"),
    (re.compile(r"(hpa|mb\b)", re.I), "hPa"),
    (re.compile(r"(%|percent)", re.I), "%"),
]

# Ordered so the most specific canonical fields win when several patterns match.
PRIORITY = {"timestamp": 0, "latitude": 1, "longitude": 1, "temperature_max": 2,
            "temperature_min": 2, "temperature": 3}


def suggest(column_names: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    taken: set[str] = set()
    ranked = []
    for column in column_names:
        name = str(column).strip()
        for index, (canonical, pattern) in enumerate(PATTERNS):
            if pattern.search(name):
                weight = PRIORITY.get(canonical, index + 5)
                ranked.append((weight, canonical, name))
                break
    for _weight, canonical, name in sorted(ranked):
        if canonical in taken or canonical not in CANONICAL_FIELDS:
            mapping[name] = "ignore"
            continue
        mapping[name] = canonical
        taken.add(canonical)
    for column in column_names:
        mapping.setdefault(str(column).strip(), "ignore")
    return mapping


def suggest_units(column_names: list[str], mapping: dict[str, str]) -> dict[str, str]:
    """Units declared inside a header cell (e.g. `tmax (F)`) are honoured per variable."""
    hints: dict[str, str] = {}
    for column in column_names:
        name = str(column).strip()
        canonical = mapping.get(name)
        if not canonical or canonical == "ignore":
            continue
        for pattern, unit in UNIT_PATTERNS:
            if pattern.search(name):
                hints[canonical] = unit
                break
    return hints


REQUIRED_FOR_IMPORT = ("timestamp", "latitude", "longitude")


def missing_required(mapping: dict[str, str]) -> list[str]:
    inverted = {v for v in mapping.values() if v and v != "ignore"}
    return [field for field in REQUIRED_FOR_IMPORT if field not in inverted]


def available_targets() -> list[str]:
    return CANONICAL_FIELDS + ["ignore"]
