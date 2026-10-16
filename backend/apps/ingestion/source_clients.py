"""HTTP clients for the supported climate providers.

Each client returns already-normalised canonical documents plus provenance about the
exact request made, so an ingestion record can be audited later. Missing values from the
provider stay missing: sentinel codes are dropped rather than converted to zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import requests

from common.canonical import dedupe_key
from common.env import ENV
from common.exceptions import ServiceUnavailableConfig, ValidationError
from common.logging_config import get_logger

logger = get_logger("earthscape.sources")

NASA_DEFAULT_BASE = "https://power.larc.nasa.gov/api/temporal"
NOAA_DEFAULT_BASE = "https://www.ncdc.noaa.gov/cdo-web/api/v2"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"

NASA_VARIABLES = {
    "temperature": "T2M", "temperature_max": "T2MMAX", "temperature_min": "T2MMIN",
    "precipitation": "PRECTOTCORR", "humidity": "RH2M", "wind_speed": "WS10M",
    "wind_direction": "WD10M", "pressure": "PS", "cloud_cover": "ALLSKY_SFC_CF",
    "solar_radiation": "ALLSKY_SFC_SW_DWN",
}
NOAA_DATA_TYPES = {
    "temperature": "TAVG", "temperature_max": "TMAX", "temperature_min": "TMIN",
    "precipitation": "PRCP", "snow": "SNOW", "humidity": "AVGDHUM", "wind_speed": "WSPD",
}
OPEN_METEO_DAILY = {
    "temperature_2m_max": ("temperature_max", "C"),
    "temperature_2m_min": ("temperature_min", "C"),
    "temperature_2m_mean": ("temperature", "C"),
    "precipitation_sum": ("precipitation", "mm"),
    "wind_speed_10m_max": ("wind_speed", "km/h"),
    "shortwave_radiation_sum": ("solar_radiation", "MJ/m2"),
}
OPEN_METEO_HOURLY = {
    "temperature_2m": ("temperature", "C"),
    "relative_humidity_2m": ("humidity", "%"),
    "precipitation": ("precipitation", "mm"),
    "wind_speed_10m": ("wind_speed", "km/h"),
    "wind_direction_10m": ("wind_direction", "deg"),
    "surface_pressure": ("pressure", "hPa"),
    "cloud_cover": ("cloud_cover", "%"),
}

DEFAULT_VARIABLES = ["temperature", "temperature_max", "temperature_min", "precipitation",
                     "humidity", "wind_speed", "pressure"]


class ConnectorNotConfigured(Exception):
    """Raised when a provider needs credentials or settings that are absent."""


def _session(timeout: int) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "EarthScapeClimateAgency/1.0 (+eProject)"})
    session.timeout = timeout
    return session


def _request(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int = 45, max_attempts: int = 3) -> requests.Response:
    delay = 1.5
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = _session(timeout).get(url, params=params, headers=headers, timeout=timeout)
        except requests.RequestException as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            import time as _time

            _time.sleep(delay)
            delay *= 2
            continue
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "0") or 0)
            import time as _time

            _time.sleep(min(max(retry_after, 1), 30))
            last_error = ServiceUnavailableConfig(f"{url.split('/')[2]} rate-limited the request.")
            continue
        if response.status_code >= 500 and attempt < max_attempts - 1:
            import time as _time

            _time.sleep(delay)
            delay *= 2
            continue
        return response
    raise ServiceUnavailableConfig(
        f"The provider request failed after {max_attempts} attempts "
        f"({type(last_error).__name__ if last_error else 'error response'}). "
        "Check network access and the provider status page.",
        code="provider_unreachable",
    )


def _iso(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip().replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text[:len(text)], fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def canonical_document(*, dataset_id: str, timestamp: datetime, latitude: float, longitude: float,
                       granularity: str, metrics: dict, location: dict, source: dict,
                       ingestion_job_id: str | None, provider: str,
                       quality_flags: list[str] | None = None) -> dict:
    from common.canonical import PLAUSIBLE_RANGES

    flags = list(quality_flags or [])
    for name, measurement in metrics.items():
        bounds = PLAUSIBLE_RANGES.get(name)
        if bounds and measurement.get("value") is not None:
            if not (bounds[0] <= measurement["value"] <= bounds[1]):
                flags.append(f"out_of_range:{name}")
    now = datetime.now(timezone.utc)
    station_id = location.get("station_id")
    return {
        "dataset_id": dataset_id,
        "ingestion_job_id": ingestion_job_id,
        "dedupe_key": dedupe_key(dataset_id=dataset_id, timestamp=timestamp,
                                granularity=granularity, station_id=station_id,
                                latitude=latitude, longitude=longitude),
        "timestamp": timestamp,
        "granularity": granularity,
        "source": {**source, "provider": provider, "kind": "api", "retrieved_at": now},
        "location": {**location, "latitude": float(latitude), "longitude": float(longitude)},
        "metrics": metrics,
        "quality": {
            "status": "suspect" if flags else "valid",
            "flags": flags,
            "missing_fields": [],
            "validated_at": now,
            "validator_version": "1.0.0",
        },
        "provenance": {
            "ingestion_job_id": ingestion_job_id,
            "ingested_at": now,
            "schema_version": "1.0.0",
            "unit_source": "provider metadata",
            "transformations": [],
        },
    }


def fetch_nasa_power(*, dataset_id: str, latitude: float, longitude: float, start: datetime,
                     end: datetime, variables: list[str] | None = None, granularity: str = "daily",
                     ingestion_job_id: str | None = None,
                     location_name: str = "") -> tuple[list[dict], dict]:
    """NASA POWER point data. Keyless; `-999` style sentinels become omitted metrics."""
    if granularity not in ("daily", "hourly"):
        raise ValidationError("NASA POWER supports 'daily' or 'hourly' granularity.")
    params_map = {}
    for variable in (variables or DEFAULT_VARIABLES):
        code = NASA_VARIABLES.get(variable)
        if code:
            params_map[variable] = code
    if not params_map:
        raise ValidationError("No supported NASA POWER variables were requested.")

    base = ENV.raw("NASA_POWER_BASE_URL", NASA_DEFAULT_BASE)
    timeout = int(ENV.integer("NASA_POWER_TIMEOUT_SECONDS", 60))
    if granularity == "daily":
        url = f"{base}/daily/point"
        query = {
            "latitude": round(latitude, 4), "longitude": round(longitude, 4),
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "parameters": " ".join(params_map.values()), "community": "ag", "format": "JSON",
        }
    else:
        url = f"{base}/hourly/point"
        query = {
            "latitude": round(latitude, 4), "longitude": round(longitude, 4),
            "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
            "parameters": " ".join(params_map.values()), "community": "ag", "format": "JSON",
        }

    response = _request(url, params=query, timeout=timeout)
    if response.status_code != 200:
        raise ServiceUnavailableConfig(
            f"NASA POWER returned HTTP {response.status_code} for this request.",
            code="provider_error",
        )
    payload = response.json()
    parameters_meta = (payload.get("parameters") or {})
    units = {code: meta.get("unit", "") for code, meta in parameters_meta.items()}
    rows = ((payload.get("properties") or {}).get("parameter") or {})

    dates = sorted(set(rows.get(next(iter(params_map.values()), ""), []) or []))
    documents: list[dict] = []
    missing_cells = 0
    for index, stamp in enumerate(dates):
        timestamp = _iso(stamp if granularity == "daily" else f"{stamp[:8]} {stamp[8:]:0>2}"
                         if len(str(stamp)) > 8 else stamp)
        if timestamp is None:
            missing_cells += 1
            continue
        metrics: dict[str, dict[str, Any]] = {}
        for variable, code in params_map.items():
            series = rows.get(code) or []
            if index >= len(series):
                continue
            raw = series[index]
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= -900:
                continue
            from common.canonical import convert_value

            converted, unit, transformation = convert_value(variable, value,
                                                             units.get(code) or "celsius")
            metrics[variable] = {"value": round(converted, 4),
                                 "unit": unit or units.get(code, "")}
        if not metrics:
            missing_cells += 1
            continue
        documents.append(canonical_document(
            dataset_id=dataset_id, timestamp=timestamp, latitude=latitude, longitude=longitude,
            granularity=granularity, metrics=metrics,
            location={"name": location_name or f"NASA POWER {latitude:.2f},{longitude:.2f}",
                      "region": location_name or "", "latitude": latitude, "longitude": longitude},
            source={"endpoint": url, "request_params": {k: str(v) for k, v in query.items()}},
            ingestion_job_id=ingestion_job_id, provider="nasa_power"))
    return documents, {"requested_rows": len(dates), "omitted_cells": missing_cells,
                       "endpoint": url, "unit_map": units}


def fetch_noaa_cdc(*, dataset_id: str, start: datetime, end: datetime,
                   data_types: list[str] | None = None, station_id: str = "",
                   location_id: str = "", dataset_code: str = "GCCN",
                   ingestion_job_id: str | None = None,
                   latitude: float | None = None, longitude: float | None = None) -> tuple[list[dict], dict]:
    """NOAA Climate Data Online. Requires NOAA_API_TOKEN; pages through results."""
    token = ENV.raw("NOAA_API_TOKEN")
    if not token:
        raise ConnectorNotConfigured(
            "NOAA_API_TOKEN is not configured. Request a token from the NOAA CDC portal and "
            "add it to .env to enable this connector."
        )
    base = ENV.raw("NOAA_CDC_BASE_URL", NOAA_DEFAULT_BASE)
    timeout = int(ENV.integer("NOAA_TIMEOUT_SECONDS", 45))
    page_size = int(ENV.integer("NOAA_PAGE_SIZE", 500))
    headers = {"token": token}

    selected = [NOAA_DATA_TYPES[v] for v in (data_types or []) if v in NOAA_DATA_TYPES]
    if not selected:
        selected = [NOAA_DATA_TYPES["temperature"], NOAA_DATA_TYPES["precipitation"]]

    documents: list[dict] = []
    offset = 1
    fetched = 0
    endpoint = f"{base}/data"
    while True:
        query = {
            "datasetid": dataset_code or "GCCN",
            "startdate": start.strftime("%Y-%m-%d"),
            "enddate": end.strftime("%Y-%m-%d"),
            "datatypeid": ",".join(selected),
            "limit": min(page_size, 1000),
            "offset": offset,
            "units": "standard",
            "resulttype": "tabular",
        }
        if station_id:
            query["stations"] = station_id
            query["datasetid"] = "DS3200" if query["datasetid"] == "GCCN" else query["datasetid"]
        elif location_id:
            query["locations"] = location_id
        response = _request(endpoint, params=query, headers=headers, timeout=timeout)
        if response.status_code == 400:
            raise ValidationError(f"NOAA rejected the request: {response.text[:180]}",
                                  code="provider_error")
        if response.status_code == 429:
            raise ServiceUnavailableConfig("NOAA rate limit reached; retry later.",
                                          code="provider_rate_limited")
        if response.status_code != 200:
            raise ServiceUnavailableConfig(f"NOAA returned HTTP {response.status_code}.",
                                          code="provider_error")
        body = response.json()
        results = body.get("results") or []
        count_total = int(body.get("count") or 0)
        fetched += len(results)
        for row in results:
            timestamp = _iso(row.get("date"))
            if timestamp is None:
                continue
            metrics: dict[str, dict[str, Any]] = {}
            datatype = row.get("datatype")
            value = row.get("value")
            unit = row.get("units") or "standard"
            if datatype and value is not None:
                variable = next((k for k, v in NOAA_DATA_TYPES.items() if v == datatype), None)
                if variable:
                    from common.canonical import convert_value

                    number = float(value)
                    # NOAA 'standard' means Fahrenheit and inches; convert to canonical units.
                    if unit == "standard":
                        source_unit = "F" if variable.startswith("temperature") else (
                            "inches" if variable == "precipitation" else unit)
                    else:
                        source_unit = unit
                    converted, target, _ = convert_value(variable, number, source_unit)
                    metrics[variable] = {"value": round(converted, 4), "unit": target}
            if not metrics:
                continue
            station = row.get("station") or ""
            documents.append(canonical_document(
                dataset_id=dataset_id, timestamp=timestamp,
                latitude=latitude if latitude is not None else 0.0,
                longitude=longitude if longitude is not None else 0.0,
                granularity="daily", metrics=metrics,
                location={"name": row.get("name") or station, "station_id": str(station),
                          "region": row.get("name") or "", "latitude": latitude or 0.0,
                          "longitude": longitude or 0.0,
                          "country": "US"},
                source={"endpoint": endpoint, "dataset_code": dataset_code,
                        "station_id": str(station),
                        "request_params": {k: str(v) for k, v in query.items()}},
                ingestion_job_id=ingestion_job_id, provider="noaa_cdc"))
        if not results or offset + len(results) - 1 >= count_total:
            break
        offset += len(results)
        if offset > 10_000:
            break
    return documents, {"fetched": fetched, "endpoint": endpoint, "pages": (offset // page_size) + 1}


def fetch_open_meteo(*, dataset_id: str, latitude: float, longitude: float, start: datetime,
                     end: datetime, variables: list[str] | None = None,
                     granularity: str = "daily", live: bool = False,
                     ingestion_job_id: str | None = None, location_name: str = "") -> tuple[list[dict], dict]:
    """Open-Meteo: keyless archive (history) and forecast (current) endpoints.

    `live=True` uses the forecast endpoint, which is periodically refreshed rather than a
    continuous sensor stream; callers label it accordingly.
    """
    requested = variables or DEFAULT_VARIABLES
    daily_vars = [name for name, (target, _unit) in OPEN_METEO_DAILY.items() if target in requested]
    hourly_vars = [name for name, (target, _unit) in OPEN_METEO_HOURLY.items() if target in requested]

    if granularity == "hourly":
        chosen = hourly_vars or ["temperature_2m"]
    else:
        chosen = daily_vars or ["temperature_2m_max", "temperature_2m_min", "precipitation_sum"]

    params = {
        "latitude": round(latitude, 4), "longitude": round(longitude, 4),
        "timezone": "UTC",
        "start_date": start.strftime("%Y-%m-%d"), "end_date": end.strftime("%Y-%m-%d"),
    }
    if granularity == "hourly":
        params["hourly"] = ",".join(chosen)
        params["forecast_days"] = 3 if live else None
        if not live:
            params.pop("forecast_days")
    else:
        params["daily"] = ",".join(chosen)

    if live:
        params.pop("start_date", None)
        params.pop("end_date", None)
        params["forecast_days"] = int(ENV.integer("LIVE_POLL_FORECAST_DAYS", 3))
        url = ENV.raw("OPEN_METEO_LIVE_URL", OPEN_METEO_FORECAST)
    else:
        url = ENV.raw("OPEN_METEO_ARCHIVE_URL", OPEN_METEO_ARCHIVE)

    timeout = int(ENV.integer("OPEN_METEO_TIMEOUT_SECONDS", 45))
    response = _request(url, params={k: v for k, v in params.items() if v is not None}, timeout=timeout)
    if response.status_code != 200:
        raise ServiceUnavailableConfig(
            f"Open-Meteo returned HTTP {response.status_code}: {response.text[:160]}",
            code="provider_error",
        )
    body = response.json()
    if body.get("error"):
        raise ValidationError(f"Open-Meteo rejected the request: {body.get('reason')}",
                              code="provider_error")
    units_meta = body.get("hourly_units") or body.get("daily_units") or {}
    series = body.get("hourly") or body.get("daily") or {}
    stamps = series.get("time") or []
    if not stamps:
        return [], {"requested_points": 0, "note": "provider returned no timestamps for this window"}

    documents: list[dict] = []
    omitted = 0
    for index, stamp in enumerate(stamps):
        timestamp = _iso(stamp)
        if timestamp is None:
            omitted += 1
            continue
        metrics: dict[str, dict[str, Any]] = {}
        transformations: list[str] = []
        for source_name, (variable, source_unit) in (OPEN_METEO_HOURLY if granularity == "hourly"
                                                     else OPEN_METEO_DAILY).items():
            values = series.get(source_name)
            if values is None or index >= len(values):
                continue
            raw = values[index]
            if raw is None:
                omitted += 1
                continue
            from common.canonical import convert_value

            declared_unit = units_meta.get(source_name) or source_unit
            converted, target, transformation = convert_value(variable, float(raw), declared_unit)
            metrics[variable] = {"value": round(converted, 4), "unit": target}
            if transformation:
                transformations.append(transformation)
        if not metrics:
            omitted += 1
            continue
        document = canonical_document(
            dataset_id=dataset_id, timestamp=timestamp, latitude=latitude, longitude=longitude,
            granularity=granularity, metrics=metrics,
            location={"name": location_name or f"{latitude:.2f},{longitude:.2f}",
                      "region": location_name or "", "latitude": latitude, "longitude": longitude},
            source={"endpoint": url, "request_params": {k: str(v) for k, v in params.items()
                                                       if v is not None},
                    "model_gcm": body.get("model_gcm"), "generationtime_ms": body.get("generationtime_ms")},
            ingestion_job_id=ingestion_job_id, provider="open_meteo")
        document["provenance"]["transformations"] = transformations
        documents.append(document)
    return documents, {"requested_points": len(stamps), "omitted_points": omitted, "endpoint": url,
                       "units": units_meta}


def provider_options() -> dict:
    """What the UI can offer right now, based on genuine configuration state."""
    return {
        "nasa_power": {"available": True, "requires_key": False,
                       "granularities": ["daily", "hourly"],
                       "variables": [v for v in NASA_VARIABLES],
                       "note": "Keyless NASA POWER point data."},
        "noaa_cdc": {"available": bool(ENV.raw("NOAA_API_TOKEN")),
                     "requires_key": True,
                     "missing_config": None if ENV.raw("NOAA_API_TOKEN") else "NOAA_API_TOKEN",
                     "granularities": ["daily"],
                     "variables": [v for v in NOAA_DATA_TYPES]},
        "open_meteo": {"available": True, "requires_key": False,
                       "granularities": ["daily", "hourly"],
                       "variables": sorted({target for _k, (target, _u) in OPEN_METEO_DAILY.items()} |
                                           {target for _k, (target, _u) in OPEN_METEO_HOURLY.items()}),
                       "note": "Open-Meteo archive for history and a keyless forecast endpoint "
                               "for periodically refreshed current conditions."},
    }
