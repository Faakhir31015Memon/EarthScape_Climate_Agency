"""Configurable "live climate API" provider connector (Open-Meteo by default).

Open-Meteo is key-free and polled on a schedule - it is **not** a continuous sensor
stream.  EarthScape reads a bounded window of daily values per request on the
ingestion interval, which is exactly how the platform characterises "live climate
APIs": short-lived HTTP reads of the most recent observations.

The concrete provider is selected by the ``LIVE_CLIMATE_PROVIDER`` environment
variable and resolved through :data:`PROVIDERS`, so a new provider (e.g. another
key-free daily weather API) can be registered here without touching ``views`` or
``services`` application logic.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from common import validators
from apps.ingestion.connectors.base import (
    IngestionRequest,
    SourceConnector,
    SourceValidationError,
    Unreachable,
    assemble_observation,
)
from config import env

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo daily variable -> canonical field (unit is documented per field).
OPEN_METEO_VARIABLES: Dict[str, Tuple[str, str]] = {
    "temperature_2m_max": ("temperature_max_c", "celsius"),
    "temperature_2m_min": ("temperature_min_c", "celsius"),
    "temperature_2m_mean": ("temperature_c", "celsius"),
    "precipitation_sum": ("precipitation_mm", "millimetres"),
    "relative_humidity_2m_mean": ("relative_humidity_pct", "percent"),
    "wind_speed_10m_max": ("wind_speed_ms", "kilometres per hour"),
    "wind_direction_10m_dominant": ("wind_direction_deg", "degrees"),
    "surface_pressure_mean": ("surface_pressure_hpa", "hPa"),
    "cloud_cover_mean": ("cloud_coverage_pct", "percent"),
    "shortwave_radiation_sum": ("solar_radiation_kwh_m2", "MJ/m2"),
}

# The unit for wind must be converted (Open-Meteo default km/h -> m/s); handled by
# validators.convert_measurement using the declared source unit string.
_UNIT_TO_CANONICAL = {
    "kilometres per hour": "km/h",
    "MJ/m2": "mj/m2",
    "millimetres": "mm",
    "celsius": "c",
    "percent": "pct",
    "degrees": "deg",
    "hPa": "hpa",
}


def _open_meteo_spec(request: IngestionRequest, location: Dict[str, Any]) -> Dict[str, str]:
    variables = _selected_variables(request)
    params: Dict[str, str] = {
        "latitude": f"{validators.to_float(location.get('latitude')):.6f}",
        "longitude": f"{validators.to_float(location.get('longitude')):.6f}",
        "daily": ",".join(variables.keys()),
        "timezone": "UTC",
    }
    if request.start:
        params["start_date"] = request.start
    if request.end:
        params["end_date"] = request.end
    return params


def _selected_variables(request: IngestionRequest) -> Dict[str, Tuple[str, str]]:
    requested = {str(name).lower() for name in request.parameters.get("providers_variables", [])}
    if not requested:
        return OPEN_METEO_VARIABLES
    return {name: meta for name, meta in OPEN_METEO_VARIABLES.items() if name.lower() in requested}


# A provider registry: name -> (base_url_resolver, params_builder, response_parser).
PROVIDERS: Dict[str, Dict[str, Callable[..., Any]]] = {
    "open_meteo": {
        "base_url": lambda: env.get_str("LIVE_CLIMATE_BASE_URL", DEFAULT_BASE_URL),
        "params": _open_meteo_spec,
        "parse": lambda payload: ((payload.get("daily", {}) or {}).get("time", []) or []),
    },
}


def register_provider(name: str, *, base_url: Callable[[], str], params: Callable[..., Dict[str, str]],
                      parse: Callable[[Dict[str, Any]], List[str]]) -> None:
    """Register a new key-free daily weather provider without touching app logic."""
    PROVIDERS[name] = {"base_url": base_url, "params": params, "parse": parse}


class LiveClimateConnector(SourceConnector):
    name = "open_meteo"
    source_type = "live_api"
    requires_key = False  # key-free by default; some providers may need one.

    def __init__(self):
        self.provider_key = env.get_str("LIVE_CLIMATE_PROVIDER", "open_meteo").lower()

    @property
    def provider(self) -> Dict[str, Callable[..., Any]]:
        entry = PROVIDERS.get(self.provider_key)
        if entry is None:
            raise SourceValidationError(
                f"Unknown LIVE_CLIMATE_PROVIDER '{self.provider_key}'. "
                f"Registered providers: {', '.join(sorted(PROVIDERS))}.")
        return entry

    def configuration_status(self) -> Dict[str, Any]:
        configured = self.provider_key in PROVIDERS
        needs_key = bool(env.get_raw("LIVE_CLIMATE_API_KEY")) and not configured
        return {
            "connector": self.name,
            "source_type": self.source_type,
            "provider": self.provider_key,
            "configured": configured,
            "state": "configured" if configured else "needs_configuration",
            "requires_key": False,
            "key_summary": env.mask_value(env.get_raw("LIVE_CLIMATE_API_KEY")),
            "polling_model": "scheduled HTTP polling of the latest daily values "
                             "(not a continuous sensor stream)",
            "setup_hint": "" if configured else
                f"Registered providers: {', '.join(sorted(PROVIDERS))}. Set LIVE_CLIMATE_PROVIDER "
                "to one of these.",
        }

    def fetch(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        provider = self.provider
        base_url = provider["base_url"]()
        if not request.locations:
            raise SourceValidationError("No locations were supplied for the live climate request.")
        for location in request.locations:
            params = provider["params"](request, location)
            response = self.http_get(base_url, params=params, timeout=30)
            try:
                payload = response.json()
            except ValueError as exc:
                raise Unreachable("The live climate provider returned non-JSON.", detail=str(exc)) from exc
            if payload.get("error"):
                raise SourceValidationError("The live climate provider rejected the request.",
                                            detail=str(payload.get("reason", "")))
            yield {"location": location, "payload": payload, "params": params, "endpoint": base_url}

    def normalize(self, record: Dict[str, Any], request: IngestionRequest) -> List[Dict[str, Any]]:
        location = record["location"]
        daily = (record["payload"].get("daily", {}) or {})
        days = daily.get("time", []) or []
        observations: List[Dict[str, Any]] = []
        for index, day in enumerate(days):
            moment = _day_moment(day, index)
            if moment is None:
                continue
            values: Dict[str, Optional[float]] = {}
            source_units: Dict[str, str] = {}
            quality: List[str] = []
            for variable, (field_name, unit_label) in _selected_variables(request).items():
                series = daily.get(variable)
                if not isinstance(series, list) or index >= len(series):
                    continue
                raw = series[index]
                if raw is None:
                    values.setdefault(field_name, None)
                    continue
                source_unit = _UNIT_TO_CANONICAL.get(unit_label, unit_label)
                validated, issues = validators.validate_measurement(field_name, raw, source_unit, index + 1)
                if validated is None:
                    values[field_name] = None
                    quality.extend(issue.code for issue in issues)
                else:
                    values[field_name] = validated
                    source_units[field_name] = source_unit
            observations.append(assemble_observation(
                context=request, location=location, moment=moment, values=values,
                source_units=source_units, endpoint=record["endpoint"],
                request_parameters=record["params"], connector_name=self.name,
                provider_record_id=f"{location.get('code')}|{day}", quality_flags=quality))
        return observations

    def preflight(self, request: Optional[IngestionRequest] = None) -> Dict[str, Any]:
        base = {"connector": self.name, "requires_key": False, "configured": True}
        if self.provider_key not in PROVIDERS:
            return {**base, "configured": False, "reachable": False,
                    "state": "needs_configuration", "detail": self.configuration_status()["setup_hint"]}
        probe = IngestionRequest(
            dataset_id="preflight", run_id="preflight", job_id="preflight",
            source_name="Live Climate", source_type=self.source_type, dataset_name="preflight",
            parameters={"providers_variables": ["temperature_2m_max"]},
            locations=[{"code": "PREFLIGHT", "latitude": 0.0, "longitude": 0.0,
                         "region_code": "", "country": ""}],
            start=_days_ago(4), end=_days_ago(2))
        try:
            response = self.http_get(self.provider["base_url"](),
                                     params=self.provider["params"](probe, probe.locations[0]),
                                     timeout=20, max_attempts=2)
            data = response.json()
            ok = response.status_code == 200 and bool(data.get("daily", {}).get("time"))
            return {**base, "reachable": ok, "state": "reachable" if ok else "degraded",
                    "detail": "" if ok else "The provider responded but returned no daily series."}
        except (Unreachable, SourceValidationError, ValueError) as exc:
            return {**base, "reachable": False, "state": "unreachable", "detail": exc.message}


def _day_moment(day: str, index: int) -> Optional[datetime]:
    parsed, _ = validators.parse_timestamp(day)
    return parsed


def _days_ago(count: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=count)).strftime("%Y-%m-%d")
