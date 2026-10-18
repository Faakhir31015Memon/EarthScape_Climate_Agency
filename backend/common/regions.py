"""Reference geography: monitored locations and the regions they belong to.

Locations are observation points used by the NASA POWER / NOAA connectors. Each
station measurement is reported as a point observation - the platform never
presents a single station value as a regional average without saying so.
"""
from __future__ import annotations

from typing import Any, Dict, List

# code, name, country, region_code, latitude, longitude, elevation_m, wmo/station id
LOCATIONS: List[Dict[str, Any]] = [
    {"code": "KHI", "name": "Karachi", "country": "Pakistan", "region_code": "Sindh", "latitude": 24.8607, "longitude": 67.0011, "elevation_m": 8, "station_id": "WMO:41850"},
    {"code": "LHE", "name": "Lahore", "country": "Pakistan", "region_code": "Punjab", "latitude": 31.5497, "longitude": 74.3436, "elevation_m": 215, "station_id": "WMO:41810"},
    {"code": "ISB", "name": "Islamabad", "country": "Pakistan", "region_code": "Punjab", "latitude": 33.6844, "longitude": 73.0479, "elevation_m": 540, "station_id": "WMO:41812"},
    {"code": "PEW", "name": "Peshawar", "country": "Pakistan", "region_code": "Khyber Pakhtunkhwa", "latitude": 34.0151, "longitude": 71.5249, "elevation_m": 331, "station_id": "WMO:41726"},
    {"code": "UET", "name": "Quetta", "country": "Pakistan", "region_code": "Balochistan", "latitude": 30.1798, "longitude": 66.9750, "elevation_m": 1680, "station_id": "WMO:41724"},
    {"code": "MUX", "name": "Multan", "country": "Pakistan", "region_code": "Punjab", "latitude": 30.1575, "longitude": 71.4244, "elevation_m": 123, "station_id": "WMO:41771"},
    {"code": "LYP", "name": "Faisalabad", "country": "Pakistan", "region_code": "Punjab", "latitude": 31.4187, "longitude": 73.0950, "elevation_m": 184, "station_id": "WMO:41758"},
    {"code": "SKD", "name": "Skardu", "country": "Pakistan", "region_code": "Gilgit-Baltistan", "latitude": 35.3082, "longitude": 75.6297, "elevation_m": 2230, "station_id": "WMO:41716"},
    {"code": "GWD", "name": "Gwadar", "country": "Pakistan", "region_code": "Balochistan", "latitude": 25.1216, "longitude": 62.3254, "elevation_m": 6, "station_id": "WMO:41860"},
    {"code": "CJI", "name": "Chitral", "country": "Pakistan", "region_code": "Khyber Pakhtunkhwa", "latitude": 35.8518, "longitude": 71.7863, "elevation_m": 1509, "station_id": "WMO:41708"},
    {"code": "WNS", "name": "Nawabshah", "country": "Pakistan", "region_code": "Sindh", "latitude": 26.2448, "longitude": 68.3916, "elevation_m": 74, "station_id": "WMO:41814"},
    {"code": "ATD", "name": "Gilgit", "country": "Pakistan", "region_code": "Gilgit-Baltistan", "latitude": 35.9081, "longitude": 74.3895, "elevation_m": 1505, "station_id": "WMO:41712"},
    {"code": "MWD", "name": "Muzaffarabad", "country": "Pakistan", "region_code": "Azad Jammu & Kashmir", "latitude": 34.3700, "longitude": 73.4700, "elevation_m": 762, "station_id": "WMO:41804"},
    {"code": "SVD", "name": "Sialkot", "country": "Pakistan", "region_code": "Punjab", "latitude": 32.5290, "longitude": 74.5190, "elevation_m": 254, "station_id": "WMO:41808"},
    {"code": "DEL", "name": "New Delhi", "country": "India", "region_code": "South Asia", "latitude": 28.6139, "longitude": 77.2090, "elevation_m": 216, "station_id": "WMO:41816"},
    {"code": "DXB", "name": "Dubai", "country": "United Arab Emirates", "region_code": "Middle East", "latitude": 25.2048, "longitude": 55.2708, "elevation_m": 5, "station_id": "WMO:41194"},
    {"code": "CAI", "name": "Cairo", "country": "Egypt", "region_code": "North Africa", "latitude": 30.0444, "longitude": 31.2357, "elevation_m": 23, "station_id": "WMO:62610"},
    {"code": "NBO", "name": "Nairobi", "country": "Kenya", "region_code": "East Africa", "latitude": -1.2921, "longitude": 36.8219, "elevation_m": 1795, "station_id": "WMO:67620"},
    {"code": "LON", "name": "London", "country": "United Kingdom", "region_code": "Western Europe", "latitude": 51.5074, "longitude": -0.1278, "elevation_m": 11, "station_id": "WMO:03772"},
    {"code": "REY", "name": "Reykjavik", "country": "Iceland", "region_code": "Northern Europe", "latitude": 64.1466, "longitude": -21.9426, "elevation_m": 26, "station_id": "WMO:04080"},
    {"code": "NYC", "name": "New York", "country": "United States", "region_code": "North America", "latitude": 40.7128, "longitude": -74.0060, "elevation_m": 10, "station_id": "WMO:72503"},
    {"code": "TYO", "name": "Tokyo", "country": "Japan", "region_code": "East Asia", "latitude": 35.6762, "longitude": 139.6503, "elevation_m": 40, "station_id": "WMO:47646"},
    {"code": "SYD", "name": "Sydney", "country": "Australia", "region_code": "Oceania", "latitude": -33.8688, "longitude": 151.2093, "elevation_m": 3, "station_id": "WMO:94767"},
    {"code": "SAO", "name": "Sao Paulo", "country": "Brazil", "region_code": "South America", "latitude": -23.5505, "longitude": -46.6333, "elevation_m": 760, "station_id": "WMO:83826"},
]

REGION_META: Dict[str, Dict[str, Any]] = {
    "Sindh": {"country": "Pakistan", "climate_zone": "Hot desert / coastal arid", "centroid": [25.4, 68.4]},
    "Punjab": {"country": "Pakistan", "climate_zone": "Semi-arid to humid subtropical", "centroid": [31.5, 73.5]},
    "Khyber Pakhtunkhwa": {"country": "Pakistan", "climate_zone": "Humid subtropical to alpine", "centroid": [34.6, 71.9]},
    "Balochistan": {"country": "Pakistan", "climate_zone": "Cold desert / arid highland", "centroid": [28.5, 65.6]},
    "Gilgit-Baltistan": {"country": "Pakistan", "climate_zone": "High-altitude continental", "centroid": [35.8, 75.2]},
    "Azad Jammu & Kashmir": {"country": "Pakistan", "climate_zone": "Humid subtropical montane", "centroid": [34.0, 73.6]},
    "South Asia": {"country": "India", "climate_zone": "Humid subtropical", "centroid": [28.6, 77.2]},
    "Middle East": {"country": "United Arab Emirates", "climate_zone": "Hot desert", "centroid": [25.2, 55.3]},
    "North Africa": {"country": "Egypt", "climate_zone": "Hot desert", "centroid": [30.0, 31.2]},
    "East Africa": {"country": "Kenya", "climate_zone": "Tropical highland", "centroid": [-1.3, 36.8]},
    "Western Europe": {"country": "United Kingdom", "climate_zone": "Oceanic temperate", "centroid": [51.5, -0.1]},
    "Northern Europe": {"country": "Iceland", "climate_zone": "Subpolar oceanic", "centroid": [64.1, -21.9]},
    "North America": {"country": "United States", "climate_zone": "Humid continental", "centroid": [40.7, -74.0]},
    "East Asia": {"country": "Japan", "climate_zone": "Humid subtropical", "centroid": [35.7, 139.7]},
    "Oceania": {"country": "Australia", "climate_zone": "Humid subtropical", "centroid": [-33.9, 151.2]},
    "South America": {"country": "Brazil", "climate_zone": "Subtropical highland", "centroid": [-23.6, -46.6]},
}

# NASA POWER daily parameters used by the platform, with their published units.
NASA_PARAMETERS: Dict[str, Dict[str, str]] = {
    "T2M": {"field": "temperature_c", "unit": "celsius", "label": "2-meter temperature"},
    "T2M_MIN": {"field": "temperature_min_c", "unit": "celsius", "label": "2-meter minimum temperature"},
    "T2M_MAX": {"field": "temperature_max_c", "unit": "celsius", "label": "2-meter maximum temperature"},
    "PRECTOTCORR": {"field": "precipitation_mm", "unit": "millimetres per day", "label": "Corrected total precipitation"},
    "RH2M": {"field": "relative_humidity_pct", "unit": "percent", "label": "2-meter relative humidity"},
    "WS10M": {"field": "wind_speed_ms", "unit": "metres per second", "label": "10-meter wind speed"},
    "WD10M": {"field": "wind_direction_deg", "unit": "degrees", "label": "10-meter wind direction"},
    "PS": {"field": "surface_pressure_hpa", "unit": "hectopascal", "label": "Surface pressure"},
    "ALLSKY_CLOUD_FRACTION": {"field": "cloud_coverage_pct", "unit": "percent", "label": "All-sky cloud fraction"},
    "ALLSKY_SFC_SW_DWN": {"field": "solar_radiation_kwh_m2", "unit": "kWh per square metre", "label": "Solar radiation"},
}

MISSING_SENTINELS = (-999.0, -999, -99.9, -9999.0)


def region_documents() -> List[Dict[str, Any]]:
    documents: List[Dict[str, Any]] = []
    for code, meta in REGION_META.items():
        locations = [
            {
                "code": location["code"],
                "name": location["name"],
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "station_id": location["station_id"],
            }
            for location in LOCATIONS
            if location["region_code"] == code
        ]
        documents.append({
            "code": code,
            "name": code,
            "country": meta["country"],
            "climate_zone": meta["climate_zone"],
            "centroid": meta["centroid"],
            "locations": locations,
            "location_count": len(locations),
        })
    return documents


def location_by_code(code: str) -> Dict[str, Any] | None:
    for location in LOCATIONS:
        if location["code"].upper() == (code or "").upper():
            return location
    return None


def all_regions() -> List[str]:
    return sorted(REGION_META)
