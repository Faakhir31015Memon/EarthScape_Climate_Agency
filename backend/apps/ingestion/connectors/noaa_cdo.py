"""NOAA Climate Data Online (CDO) v2 connector.

Base: ``https://www.ncdc.noaa.gov/cdo-web/api/v2``.  Authentication is a token read
from the environment variable ``NOAA_API_KEY`` and sent in the ``token`` header.

Implemented API surface:

* ``/datasets``   - dataset discovery
* ``/datacatalogs`` - catalogue search
* ``/stations``   - station lookup (bounded by datasetid, date and locationid)
* ``/data``       - observation search with offset/limit pagination

The connector raises ``needs_configuration`` whenever the token is absent.  Date
ranges and ``locationid`` arguments are validated before a request is issued.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from common import validators
from apps.ingestion.connectors.base import (
    IngestionRequest,
    NotConfigured,
    RateLimited,
    SourceConnector,
    SourceValidationError,
    assemble_observation,
)
from config import env

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.ncdc.noaa.gov/cdo-web/api/v2"
PAGE_SIZE = 100
# NOAA documents a 5 requests / 30s soft limit; keep a little headroom.
RATE_LIMIT_PER_MINUTE = 30

# A documented, conservative mapping from CDO daily GHCND-style datatypes to canonical
# fields.  Datatypes outside this table are recorded without a canonical field.
DATATYPE_MAP: Dict[str, Dict[str, Any]] = {
    "TMAX": {"field": "temperature_max_c", "scale": 1.0, "unit": "celsius"},
    "TMIN": {"field": "temperature_min_c", "scale": 1.0, "unit": "celsius"},
    "TAVG": {"field": "temperature_c", "scale": 1.0, "unit": "celsius"},
    "PRCP": {"field": "precipitation_mm", "scale": 0.254, "unit": "1/10 mm"},   # 0.01 inch -> mm
    "AWND": {"field": "wind_speed_ms", "scale": 0.44704, "unit": "mph (0.1)"},
    "WDF2": {"field": "wind_direction_deg", "scale": 1.0, "unit": "degrees"},
    "PSLP": {"field": "surface_pressure_hpa", "scale": 0.01, "unit": "hPa (0.01)"},
}

_LOCATION_ID = re.compile(r"^[A-Z]{2}(?:[A-Z]-[A-Za-z0-9.:\-]+|[A-Z0-9:.:\-]+)?$")


class NcdcCdoConnector(SourceConnector):
    name = "noaa_cdo"
    source_type = "noaa"
    requires_key = True
    configuration_keys = ("NOAA_API_KEY",)
    setup_hint = ("Set NOAA_API_KEY (register a token at "
                  "https://www.ncdc.noaa.gov/cdo-web/token) to enable NOAA CDO ingestion.")

    @property
    def base_url(self) -> str:
        return env.get_str("NOAA_BASE_URL", DEFAULT_BASE_URL).rstrip("/")

    @property
    def timeout(self) -> float:
        return float(env.get_int("NOAA_TIMEOUT_SECONDS", 45))

    # ------------------------------------------------------------------ config
    def _token(self) -> str:
        token = env.get_raw("NOAA_API_KEY")
        if not token:
            raise NotConfigured("NOAA_API_KEY is not configured; the NOAA connector cannot run.")
        return token

    def _headers(self) -> Dict[str, str]:
        return {"token": self._token()}

    # --------------------------------------------------------------- rate limit
    _last_request_at: float = 0.0

    def _pace(self) -> None:
        minimum_gap = 60.0 / max(1, env.get_int("NOAA_RATE_LIMIT_PER_MINUTE", RATE_LIMIT_PER_MINUTE))
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < minimum_gap:
            time.sleep(minimum_gap - elapsed)
        self._last_request_at = time.monotonic()

    # ------------------------------------------------------------------ request
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._pace()
        response = self.http_get(f"{self.base_url}{path}", params=params, headers=self._headers(),
                                 timeout=self.timeout)
        try:
            return response.json()
        except ValueError:
            raise RateLimited("NOAA returned a non-JSON response.") from None

    # --------------------------------------------------------- discovery helpers
    def list_datasets(self, *, limit: int = PAGE_SIZE, offset: int = 0,
                      start_date: Optional[str] = None, data_type_id: Optional[str] = None) -> Dict[str, Any]:
        self._token()  # raises NotConfigured when unconfigured
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if start_date:
            params["startdate"] = _validate_date(start_date)
        if data_type_id:
            params["dataTypes"] = data_type_id
        return self._get("/datasets", params)

    def search_datacatalogs(self, *, search: str = "", extent: Optional[str] = None,
                           countonly: bool = False) -> Any:
        self._token()
        params: Dict[str, Any] = {}
        if search:
            params["searchTerm"] = search[:120]
        if extent:
            params["extent"] = extent
        if countonly:
            params["countonly"] = "true"
        return self._get("/datacatalogs", params)

    def find_stations(self, *, dataset_id: str, date: str,
                      location_id: Optional[str] = None, limit: int = PAGE_SIZE,
                      offset: int = 0) -> Dict[str, Any]:
        self._token()
        params: Dict[str, Any] = {"datasetid": _validate_dataset_id(dataset_id),
                                  "date": _validate_date(date),
                                  "limit": limit, "offset": offset, "units": "metric"}
        if location_id:
            params["locations"] = _validate_location_id(location_id)
        return self._get("/stations", params)

    # -------------------------------------------------------------- data search
    def search_data(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        """Yield raw ``datapayload`` records, following pagination to the end."""
        params = self._data_params(request)
        offset = int(params.get("offset", 0))
        limit = int(params["limit"])
        while True:
            params["offset"] = offset
            payload = self._get("/data", params)
            results = payload.get("results", []) or []
            metadata = payload.get("metadata", {}) or {}
            total = int(metadata.get("resultCount", metadata.get("totalCount", len(results))) or 0)
            for item in results:
                yield item
            offset += limit
            if offset >= total or not results:
                break

    def _data_params(self, request: IngestionRequest) -> Dict[str, Any]:
        token = self._token()  # NotConfigured if absent
        dataset_id = request.parameters.get("datasetid") or request.parameters.get("dataset_id")
        if not dataset_id:
            raise SourceValidationError("A NOAA datasetid is required to search /data.")
        params: Dict[str, Any] = {
            "datasetid": _validate_dataset_id(str(dataset_id)),
            "limit": min(int(request.parameters.get("limit", PAGE_SIZE)), 1000),
            "offset": int(request.parameters.get("offset", 0)),
            "units": "metric",
            "includeLocations": "true",
        }
        if request.start:
            params["startdate"] = _validate_date(request.start)
        if request.end:
            params["enddate"] = _validate_date(request.end)
        if params.get("startdate") and params.get("enddate") and params["startdate"] > params["enddate"]:
            raise SourceValidationError("The NOAA start date is after the end date.")
        for key, target in (("locations", "locations"), ("datacategories", "datacategories"),
                           ("countries", "countries"), ("states", "states")):
            value = request.parameters.get(target)
            if value:
                joined = value if isinstance(value, str) else ",".join(str(item) for item in value)
                if key == "locations":
                    for part in joined.split(","):
                        _validate_location_id(part)
                params[key] = joined[:300]
        return params

    # ------------------------------------------------------------------- fetch
    def fetch(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        yield from self.search_data(request)

    # ---------------------------------------------------------------- normalize
    def normalize(self, record: Dict[str, Any], request: IngestionRequest) -> List[Dict[str, Any]]:
        moment = _record_datetime(record)
        if moment is None:
            return []
        location = {
            "code": str(record.get("station") or record.get("station_name") or "NOAA")[:20],
            "name": record.get("station_name") or record.get("name") or str(record.get("station")),
            "region_code": (record.get("state") or record.get("country") or ""),
            "country": record.get("country_name") or "",
            "latitude": validators.to_float(record.get("latitude")),
            "longitude": validators.to_float(record.get("longitude")),
        }
        if location["latitude"] is None or location["longitude"] is None:
            # Without coordinates we cannot honour the observation contract - report, not fake.
            logger.warning("NOAA record %s lacks coordinates; skipped.", record.get("station"))
            return []

        values: Dict[str, Optional[float]] = {}
        source_units: Dict[str, str] = {}
        quality: List[str] = []
        for entry in record.get("datapayload", []) or []:
            datatype = str(entry.get("datatype", "")).upper()
            meta = DATATYPE_MAP.get(datatype)
            if not meta:
                quality.append(f"unmapped_datatype_{datatype.lower()}")
                continue
            number = validators.to_float(entry.get("value"))
            if number is None or entry.get("attributes"):
                values[meta["field"]] = None
                continue
            converted = round(number * meta["scale"], 4)
            validated, issues = validators.validate_measurement(meta["field"], converted, "c"
                                                               if meta["field"].endswith("_c") else None)
            values[meta["field"]] = validated
            if validated is None:
                quality.extend(issue.code for issue in issues)
            else:
                source_units[meta["field"]] = meta["unit"]

        return [assemble_observation(
            context=request, location=location, moment=moment, values=values,
            source_units=source_units, endpoint=f"{self.base_url}/data",
            request_parameters=self._data_params(request), connector_name=self.name,
            provider_record_id=str(record.get("station")), quality_flags=quality)]

    # ----------------------------------------------------------------- preflight
    def preflight(self, request: Optional[IngestionRequest] = None) -> Dict[str, Any]:
        base = {"connector": self.name, "requires_key": True}
        if not self.is_configured():
            return {**base, "configured": False, "reachable": False,
                    "state": "needs_configuration", "detail": self.setup_hint}
        try:
            result = self.list_datasets(limit=1)
            count = len(result.get("results", []) or [])
            return {**base, "configured": True, "reachable": count > 0,
                    "state": "reachable" if count else "degraded", "detail": ""}
        except (NotConfigured, RateLimited, SourceValidationError) as exc:
            return {**base, "configured": True, "reachable": False,
                    "state": "unreachable", "detail": exc.message}


# --------------------------------------------------------------------- helpers
def _validate_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise SourceValidationError(f"Date '{str(value)[:40]}' must be YYYY-MM-DD.") from None
    return value


def _validate_dataset_id(value: str) -> str:
    if not re.fullmatch(r"[A-Z0-9\-]{3,30}", value.upper()):
        raise SourceValidationError(f"Invalid NOAA dataset id '{value[:40]}'.")
    return value.upper()


def _validate_location_id(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise SourceValidationError("A location id is required.")
    if not _LOCATION_ID.match(text):
        raise SourceValidationError(
            f"Invalid NOAA location id '{text[:40]}'. Expected forms like "
            "USW00014826, FIPS:US24, GB:UKPP.")
    return text


def _record_datetime(record: Dict[str, Any]) -> Optional[datetime]:
    for key in ("date", "observationDate"):
        raw = record.get(key)
        if raw:
            parsed, _ = validators.parse_timestamp(raw)
            if parsed:
                return parsed
    return None
