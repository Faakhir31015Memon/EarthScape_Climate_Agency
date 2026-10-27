"""NASA POWER daily-point connector (no API key required).

Verified live endpoint:
``GET https://power.larc.nasa.gov/api/temporal/daily/point``
with ``parameters``, ``community``, ``latitude``, ``longitude``, ``start`` / ``end``
(YYYYMMDD) and ``format=JSON``.  The response nests each parameter's values at
``["properties"]["parameter"][PARAM]["YYYYMMDD"]`` and uses ``-999`` to mean missing.

Parameter -> canonical field mapping comes from ``common.regions.NASA_PARAMETERS``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from common import regions, validators
from apps.ingestion.connectors.base import (
    IngestionRequest,
    NotConfigured,
    SourceConnector,
    SourceValidationError,
    Unreachable,
    assemble_observation,
)
from config import env

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
DEFAULT_COMMUNITY = "AG"
# Parameters used by the platform; T2M..PS are all supported by the daily point API.
SUPPORTED_PARAMETERS = list(regions.NASA_PARAMETERS)

# NASA POWER's daily point API publishes surface pressure in **kPa** (~100 kPa at sea
# level), whereas the platform's canonical field is ``surface_pressure_hpa`` and the
# contract's ``NASA_PARAMETERS`` entry labels the unit "hectopascal".  Override only the
# *source* unit here so :func:`common.validators.convert_measurement` applies its
# documented kPa->hPa factor (x10).  This keeps the unmodifiable contract intact while
# storing physically correct pressure.  Real NASA values are ~100.04 kPa -> 1000.4 hPa.
SOURCE_UNIT_OVERRIDES: Dict[str, str] = {"PS": "kpa"}

# NASA POWER's daily point API serves ``PRECTOTCORR`` at two accumulations for the same
# station-day: the documented millimetres per day, and a figure exactly 24x larger.  Which
# one a response carries is decided by the *request window*, not by the data - measured
# 2026-10-22 at station SVD for 2026-09-17: ``end=2026-09-30`` returned 33.8 mm/day while
# ``end=2026-10-02`` and every fresher end returned 811.2 mm (24x) for that same day.  The
# boundary is the source's quality lag: ends at least this many days in the past are final.
# Temperature is identical in both bands, so only precipitation needs the correction.
PRECIP_SETTLED_LAG_DAYS = 22
PRECIP_HOURLY_ACCUMULATION_FACTOR = 24.0


def _precipitation_needs_rescaling(end: Optional[str]) -> bool:
    """True when the requested window ends inside NASA's unsettled precipitation band."""
    moment = _day_to_datetime(_compact(end) or "")
    if moment is None:
        return False
    return (datetime.now(timezone.utc) - moment).days < PRECIP_SETTLED_LAG_DAYS


class NasaPowerConnector(SourceConnector):
    name = "nasa_power"
    source_type = "nasa_power"
    requires_key = False
    setup_hint = "NASA POWER needs no API key, just outbound network access."

    @property
    def base_url(self) -> str:
        return env.get_str("NASA_POWER_BASE_URL", DEFAULT_BASE_URL)

    @property
    def timeout(self) -> float:
        return float(env.get_int("NASA_POWER_TIMEOUT_SECONDS", 45))

    # ------------------------------------------------------------------ request
    def build_params(self, request: IngestionRequest, location: Dict[str, Any]) -> Dict[str, str]:
        parameters = [param for param in request.metric_parameters() if param in SUPPORTED_PARAMETERS]
        if not parameters:
            parameters = ["T2M", "T2M_MIN", "T2M_MAX", "PRECTOTCORR", "RH2M", "WS10M", "WD10M", "PS"]
        start = _compact(request.start)
        end = _compact(request.end)
        if not start or not end:
            raise SourceValidationError("NASA POWER requires both a start and end date (YYYY-MM-DD).")
        if start > end:
            raise SourceValidationError("The start date is after the end date.")
        latitude = _coordinate(location.get("latitude"), "latitude", 90.0)
        longitude = _coordinate(location.get("longitude"), "longitude", 180.0)
        return {
            "parameters": ",".join(parameters),
            "community": request.parameters.get("community", DEFAULT_COMMUNITY),
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "start": start,
            "end": end,
            "format": "JSON",
        }

    def fetch(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        if not request.locations:
            raise SourceValidationError("No locations were supplied for the NASA POWER request.")
        for location in request.locations:
            params = self.build_params(request, location)
            response = self.http_get(self.base_url, params=params, timeout=self.timeout)
            try:
                payload = response.json()
            except ValueError as exc:
                raise Unreachable("NASA POWER returned a non-JSON response.", detail=str(exc)) from exc
            yield {"location": location, "payload": payload, "params": params}

    # ---------------------------------------------------------------- normalize
    def normalize(self, record: Dict[str, Any], request: IngestionRequest) -> List[Dict[str, Any]]:
        location = record["location"]
        payload = record["payload"]
        parameter_block = (payload.get("properties", {}) or {}).get("parameter", {}) or {}
        if not isinstance(parameter_block, dict) or not parameter_block:
            # A partial/empty response is logged here and normalises to no rows.
            logger.warning("NASA POWER returned no parameter block for %s", location.get("code"))
            return []

        # Collect every YYYYMMDD key seen across parameters so days line up.
        days: set[str] = set()
        for values_by_day in parameter_block.values():
            if isinstance(values_by_day, dict):
                days.update(key for key in values_by_day if _looks_like_day(key))

        rescale_precip = _precipitation_needs_rescaling(request.end)
        observations: List[Dict[str, Any]] = []
        for day in sorted(days):
            moment = _day_to_datetime(day)
            if moment is None:
                continue
            values: Dict[str, Optional[float]] = {}
            source_units: Dict[str, str] = {}
            quality: List[str] = []
            for param, block in parameter_block.items():
                meta = regions.NASA_PARAMETERS.get(param.upper())
                if not meta or not isinstance(block, dict):
                    continue
                field_name = meta["field"]
                source_unit = SOURCE_UNIT_OVERRIDES.get(param.upper(), meta["unit"])
                raw = block.get(day, block.get(_display_day(day)))
                number = _to_float(raw)
                if number is None or _is_missing(number):
                    values.setdefault(field_name, None)
                    continue
                if rescale_precip and field_name == "precipitation_mm":
                    number /= PRECIP_HOURLY_ACCUMULATION_FACTOR
                    quality.append("precipitation_rescaled_from_hourly_accumulation")
                converted, _unit_flag = validators.convert_measurement(field_name, number, source_unit)
                validated, issues = validators.validate_measurement(field_name, converted,
                                                                    None, len(observations) + 1)
                if validated is None:
                    values[field_name] = None
                    quality.extend(iss.code for iss in issues if iss.severity == "error")
                else:
                    values[field_name] = validated
                    source_units[field_name] = source_unit

            observations.append(assemble_observation(
                context=request, location=location, moment=moment, values=values,
                source_units=source_units, endpoint=self.base_url,
                request_parameters=record["params"], connector_name=self.name,
                provider_record_id=f"{location.get('code')}|{day}", quality_flags=quality))
        return observations

    # ------------------------------------------------------------------ preflight
    def preflight(self, request: Optional[IngestionRequest] = None) -> Dict[str, Any]:
        base = {"connector": self.name, "configured": True, "requires_key": False}
        probe = IngestionRequest(
            dataset_id="preflight", run_id="preflight", job_id="preflight",
            source_name="NASA POWER", source_type=self.source_type, dataset_name="preflight",
            parameters={"parameters": ["T2M"]},
            locations=[{"code": "PREFLIGHT", "latitude": 0.0, "longitude": 0.0,
                         "region_code": "", "country": ""}],
            start=_days_ago(6), end=_days_ago(4))
        try:
            response = self.http_get(self.base_url, params=self.build_params(probe, probe.locations[0]),
                                     timeout=min(self.timeout, 20), max_attempts=2)
            data = response.json()
            block = (data.get("properties", {}) or {}).get("parameter", {}) or {}
            ok = response.status_code == 200 and bool(block)
            return {**base, "reachable": ok, "state": "reachable" if ok else "degraded",
                    "detail": "" if ok else "The endpoint responded but returned no data."}
        except (Unreachable, SourceValidationError, ValueError) as exc:
            return {**base, "reachable": False, "state": "unreachable", "detail": exc.message}


# --------------------------------------------------------------------- helpers
def _compact(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    digits = "".join(char for char in str(value) if char.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _looks_like_day(key: str) -> bool:
    return len(key) == 8 and key.isdigit()


def _display_day(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _day_to_datetime(day: str) -> Optional[datetime]:
    try:
        return datetime(int(day[:4]), int(day[4:6]), int(day[6:]), tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _to_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


def _is_missing(value: float) -> bool:
    return any(abs(value - sentinel) < 1e-6 for sentinel in regions.MISSING_SENTINELS)


def _coordinate(raw: Any, kind: str, limit: float) -> float:
    value = validators.to_float(raw)
    if value is None or abs(value) > limit:
        raise SourceValidationError(f"A valid {kind} (-{limit}..{limit}) is required.")
    return value


def _days_ago(count: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=count)).strftime("%Y-%m-%d")
