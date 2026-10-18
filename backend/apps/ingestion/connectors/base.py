"""Abstract source connector shared by every live-data ingestion path.

A connector is responsible for:

* reporting whether it is configured *without ever echoing a secret* (via
  :func:`config.env.mask_value`);
* fetching raw records from a live HTTP source with retries, exponential backoff and
  HTTP 429 ``Retry-After`` handling;
* normalising those records into the exact ``climate_observations`` document
  contract (see :func:`assemble_observation`);
* building provenance that names the connector, endpoint, run and job.

Connectors NEVER fabricate data.  When a source is unconfigured or unreachable they
raise :class:`NotConfigured` / :class:`Unreachable` so callers report an honest
failure rather than a fake success.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests

from common import validators
from common.repositories import utcnow
from config import env

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45
MAX_ATTEMPTS = 4
BACKOFF_BASE_SECONDS = 1.5
MAX_BACKOFF_SECONDS = 30.0
MAX_RETRY_AFTER_SECONDS = 120.0


# ---------------------------------------------------------------------- errors
class ConnectorError(Exception):
    code = "source_error"

    def __init__(self, message: str, *, detail: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.detail = _scrub(detail or "")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class NotConfigured(ConnectorError):
    code = "needs_configuration"


class Unreachable(ConnectorError):
    code = "service_unavailable"


class SourceValidationError(ConnectorError):
    code = "validation_failed"


class RateLimited(ConnectorError):
    code = "rate_limited"

    def __init__(self, message: str, *, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class HttpError(ConnectorError):
    code = "source_http_error"

    def __init__(self, status: int, message: str, *, body: Optional[str] = None):
        super().__init__(message, detail=body)
        self.status = status


# --------------------------------------------------------------------- request
@dataclass
class IngestionRequest:
    dataset_id: str
    run_id: str
    job_id: str
    source_name: str
    source_type: str
    dataset_name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    locations: List[Dict[str, Any]] = field(default_factory=list)
    start: Optional[str] = None
    end: Optional[str] = None
    processing_mode: str = "live-api"

    def metric_parameters(self) -> List[str]:
        return [str(item).upper() for item in self.parameters.get("parameters", []) if item]


def scrub_url(url: str) -> str:
    """Strip embedded credentials from a URL before it is stored or logged."""
    try:
        parts = urlsplit(str(url))
        if parts.netloc and "@" in parts.netloc:
            netloc = parts.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))
        # Also drop any obvious secret query parameters.
        if parts.query:
            kept = [pair for pair in parts.query.split("&")
                    if not any(secret in pair.lower() for secret in ("key=", "token=", "password="))]
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))
    except (ValueError, AttributeError):
        return str(url)[:200]
    return str(url)[:200]


def _scrub(text: str) -> str:
    for token in ("mongodb://", "mongodb+srv://"):
        if token in text:
            return text.split(token)[0] + token + "***"
    return text[:300]


def sanitise_parameters(parameters: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key, value in dict(parameters or {}).items():
        if key.lower() in ("api_key", "apikey", "token", "secret", "password",
                           "authorization", "connection_string", "uri"):
            cleaned[key] = "[redacted]"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            cleaned[key] = value if not isinstance(value, str) else value[:200]
        elif isinstance(value, (list, tuple)):
            cleaned[key] = [str(item)[:60] for item in list(value)[:40]]
    return cleaned


# -------------------------------------------------------------------- base class
class SourceConnector:
    name: str = "base"
    source_type: str = "live_api"
    #: connectors that require a secret token/key before they can do anything
    requires_key: bool = False
    #: environment variable(s) consulted by :meth:`is_configured`
    configuration_keys: Tuple[str, ...] = ()
    #: human setup hint shown to the UI when unconfigured
    setup_hint: str = ""

    # ------------------------------------------------------------------ config
    def is_configured(self) -> bool:
        if not self.requires_key:
            return True
        return all(bool(env.get_raw(key)) for key in self.configuration_keys)

    def configuration_status(self) -> Dict[str, Any]:
        configured = self.is_configured()
        secret = env.get_raw("NOAA_API_KEY") if "NOAA_API_KEY" in self.configuration_keys else None
        status = {
            "connector": self.name,
            "source_type": self.source_type,
            "configured": configured,
            "state": "configured" if configured else "needs_configuration",
            "requires_key": self.requires_key,
            "setup_hint": "" if configured else self.setup_hint,
        }
        if self.requires_key:
            status["secret_present"] = bool(secret)
            status["secret_summary"] = env.mask_value(secret)  # never the value itself
        return status

    # ---------------------------------------------------------------- transport
    def http_get(self, url: str, *, params: Optional[Dict[str, Any]] = None,
                 headers: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None,
                 max_attempts: int = MAX_ATTEMPTS) -> requests.Response:
        """GET with bounded retries, exponential backoff and 429 handling.

        Returns the final successful response.  Raises ``Unreachable`` for network
        failures and ``HttpError``/``RateLimited`` for non-2xx responses.
        """
        timeout = timeout or env.get_int("HTTP_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
        last_error: Optional[ConnectorError] = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.get(url, params=params, headers=headers, timeout=timeout)
            except requests.exceptions.Timeout:
                last_error = Unreachable(f"The {self.name} request timed out.", detail=scrub_url(url))
            except requests.exceptions.ConnectionError:
                last_error = Unreachable(f"Could not reach the {self.name} endpoint.",
                                         detail=scrub_url(url))
            except requests.exceptions.RequestException as exc:  # pragma: no cover
                last_error = Unreachable(f"{self.name} request failed.", detail=str(exc))
            else:
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response)
                    if attempt < max_attempts:
                        self._sleep(retry_after)
                        continue
                    raise RateLimited(f"{self.name} rate limit reached.", retry_after=retry_after)
                if 500 <= response.status_code < 600:
                    last_error = HttpError(response.status_code,
                                           f"{self.name} server error ({response.status_code}).",
                                           body=response.text)
                    if attempt < max_attempts:
                        self._sleep(self._backoff(attempt))
                        continue
                    raise last_error
                if response.status_code >= 400:
                    raise self._classify_client_error(response)
                return response
            # network error path
            if attempt < max_attempts:
                self._sleep(self._backoff(attempt))
        raise last_error or Unreachable(f"{self.name} request failed.")

    def _classify_client_error(self, response: requests.Response) -> ConnectorError:
        body = response.text[:400]
        if response.status_code in (401, 403):
            return NotConfigured(f"{self.name} rejected the credentials/configuration.",
                                 detail=body)
        if response.status_code == 404:
            return SourceValidationError(f"{self.name} reported the resource was not found.",
                                         detail=body)
        if response.status_code == 400:
            return SourceValidationError(f"{self.name} rejected the request parameters.",
                                         detail=body)
        return HttpError(response.status_code, f"{self.name} returned an error.", body=body)

    @staticmethod
    def _retry_after_seconds(response: requests.Response) -> float:
        raw = response.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), MAX_RETRY_AFTER_SECONDS)
            except (TypeError, ValueError):
                pass
        return BACKOFF_BASE_SECONDS * 2

    @staticmethod
    def _backoff(attempt: int) -> float:
        delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
        return min(delay, MAX_BACKOFF_SECONDS)

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(max(0.0, min(seconds, MAX_RETRY_AFTER_SECONDS)))

    # ------------------------------------------------------------------- reach
    def preflight(self, request: Optional[IngestionRequest] = None) -> Dict[str, Any]:
        """Cheap reachability check.  Must NOT fabricate success when unconfigured."""
        if not self.is_configured():
            return {"reachable": False, "configured": False, "state": "needs_configuration",
                    "detail": self.setup_hint or "The connector is not configured."}
        return {"reachable": True, "configured": True, "state": "configured", "detail": ""}

    # --------------------------------------------------------------- data fetch
    def fetch(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        raise NotImplementedError

    def normalize(self, record: Dict[str, Any], request: IngestionRequest) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def run(self, request: IngestionRequest) -> Iterator[Dict[str, Any]]:
        """Yield fully-formed observation documents for a request."""
        for raw in self.fetch(request):
            for observation in self.normalize(raw, request):
                yield observation

    # -------------------------------------------------------------- provenance
    def provenance(self, request: IngestionRequest, endpoint: str,
                   request_parameters: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "connector": self.name,
            "endpoint": scrub_url(endpoint) if isinstance(endpoint, str) else str(endpoint)[:200],
            "run_id": request.run_id,
            "retrieved_at": utcnow(),
            "ingestion_job_id": request.job_id,
            "processing_mode": request.processing_mode,
            "request_parameters": sanitise_parameters(request_parameters),
        }


# ---------------------------------------------------------- observation assembly
def assemble_observation(*, context: IngestionRequest, location: Dict[str, Any],
                         moment: datetime, values: Dict[str, Optional[float]],
                         source_units: Dict[str, str], endpoint: str,
                         request_parameters: Dict[str, Any], connector_name: str,
                         provider_record_id: Optional[str] = None,
                         quality_flags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build an observation document that matches the platform contract exactly."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    observation_date = moment.strftime("%Y-%m-%d")
    latitude = float(location["latitude"])
    longitude = float(location["longitude"])
    location_code = str(location.get("code") or location.get("name") or "unknown")

    observation: Dict[str, Any] = {
        "dedupe_key": f"{context.dataset_id}|{location_code}|{observation_date}",
        "dataset_id": context.dataset_id,
        "observation_date": observation_date,
        "observed_at": moment,
        "year": moment.year,
        "month": moment.month,
        "day": moment.day,
        "location_code": location_code,
        "location_name": location.get("name", location_code),
        "region_code": location.get("region_code", ""),
        "country": location.get("country", ""),
        "latitude": latitude,
        "longitude": longitude,
        "coordinates": {"type": "Point", "coordinates": [longitude, latitude]},
    }
    for field_name in validators.RANGE_LIMITS:
        observation[field_name] = values.get(field_name)
    observation["source_units"] = dict(source_units)
    observation["quality_flags"] = sorted(set(quality_flags or []))
    observation["source_name"] = context.source_name
    observation["source_type"] = context.source_type
    observation["dataset_name"] = context.dataset_name
    observation["provider_record_id"] = provider_record_id
    observation["provenance"] = {
        "connector": connector_name,
        "endpoint": scrub_url(endpoint),
        "run_id": context.run_id,
        "retrieved_at": utcnow(),
        "ingestion_job_id": context.job_id,
        "processing_mode": context.processing_mode,
        "request_parameters": sanitise_parameters(request_parameters),
    }
    observation["ingested_at"] = utcnow()
    observation["schema_version"] = validators.SCHEMA_VERSION
    return observation
