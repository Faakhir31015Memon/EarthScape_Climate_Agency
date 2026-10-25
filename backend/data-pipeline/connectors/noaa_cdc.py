"""NOAA Climate Data Online (CDO) connector — GHCND station data via ``/api/v2``.

Requires a free account token in the environment variable ``NOAA_API_TOKEN``
(sent as the ``token`` header).  Without it the connector raises
:class:`ConnectorNotConfigured` naming that exact variable — it never fabricates
station data.

* Base: ``https://www.ncdc.noaa.gov/cdo-web/api/v2``
* Search: ``GET /data?datasetid=GHCND&stations=...&datatypes=...&startdate=&enddate=&limit=&offset=``
* Pagination uses ``offset`` (max ``limit`` 1000); rate-limit headers
  (``X-RateLimit-Available/-Used/-Reset``) are captured per run.
* Data types GHCND:TMAX/TMIN/PRCP/AWDI... map to canonical metrics; values are
  provider units (temperature °C in CDO v2, PRCP in hundredths of mm -> divided
  by 100? no: CDO PRCP is millimetres as float; we keep the provider unit and
  normalise in ``validation.units``, recording each conversion string).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from connectors.base import (ConnectorNotConfigured, ConnectorUnavailable,
                             SourceConnector, now_utc)
from pipeline_config import ENV
from validation.dedupe import make_dedupe_key

API_ROOT = "https://www.ncdc.noaa.gov/cdo-web/api/v2"
VALID_DATASETS = {"GHCND", "GHCND_DAILY", "GSOD", "LTE"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: CDO datatype code -> (canonical metric, provider unit)
DATATYPE_MAP = {
    "TMAX": ("temperature_max", "°C"),
    "TMIN": ("temperature_min", "°C"),
    "TAVG": ("temperature", "°C"),
    "PRCP": ("precipitation", "mm"),
    "AWDD": ("wind_speed", "m/s"),
    "AWDV": ("wind_speed", "m/s"),
    "WDMV": ("wind_direction", "°"),
    "SLP ": ("pressure", "hPa"),
    "FDSN": ("cloud_cover", "%"),
}


class NoaaCdcConnector(SourceConnector):
    name = "noaa_cdc"
    kind = "api"
    provider = "noaa_cdc"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config)
        self.token = ENV.secret("NOAA_API_TOKEN") if hasattr(ENV, "secret") else ENV.raw("NOAA_API_TOKEN")
        self.datasetid = self.config.get("datasetid", "GHCND")
        self.stations = self.config.get("stations", [])
        self.datatypes = self.config.get("datatypes", ["TMAX", "TMIN", "PRCP"])
        self.startdate = self.config.get("startdate", "2020-01-01")
        self.enddate = self.config.get("enddate", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.location_ids = self.config.get("location_ids", [])
        self.limit = min(int(self.config.get("limit", 1000)), 1000)
        self.dataset_id = self.config.get("dataset_id", f"noaa_ghcnd_{'-'.join(self.stations[:1]) or 'region'}")
        self.api_root = self.config.get("api_root", ENV.raw("NOAA_CDC_BASE_URL") or API_ROOT)
        self.rate_limit_headers: list = []
        self._check_config()
        self._check_params()

    # ----------------------------------------------------------- config
    def _check_config(self) -> None:
        if not self.token:
            raise ConnectorNotConfigured(
                "NOAA_API_TOKEN",
                "Register a (free) Climate Data Online token at "
                "https://www.ncdc.noaa.gov/cdo-web/token and set NOAA_API_TOKEN.")

    def _check_params(self) -> None:
        if self.datasetid not in VALID_DATASETS:
            raise ValueError(f"datasetid {self.datasetid!r} not in supported set {sorted(VALID_DATASETS)}")
        for label, value in (("startdate", self.startdate), ("enddate", self.enddate)):
            if not DATE_RE.match(value or ""):
                raise ValueError(f"{label} {value!r} must be ISO YYYY-MM-DD")
        if self.startdate > self.enddate:
            raise ValueError("startdate must not be after enddate")
        if not self.stations and not self.location_ids:
            raise ValueError("provide stations (e.g. USW00023174) or location_ids (e.g. country:PK) for a CDO search")
        for st in self.stations:
            if not re.match(r"^[A-Z]{2}[0-9A-Z]{8,10}$", st):
                raise ValueError(f"station id {st!r} does not look like a GHCND station id")

    # ------------------------------------------------------------- plan
    def plan(self) -> list:
        base = {
            "datasetid": self.datasetid,
            "startdate": self.startdate,
            "enddate": self.enddate,
            "limit": self.limit,
            "offset": 1,
        }
        if self.stations:
            base["stations"] = ",".join(self.stations)
        if self.location_ids:
            base["locations"] = ",".join(self.location_ids)
        if self.datatypes:
            base["datatypes"] = ",".join(self.datatypes)
        return [{"endpoint": f"{self.api_root}/data", "params": base, "segment": {"paginated": True}}]

    # ------------------------------------------------------------- fetch
    def fetch(self, result):
        import requests

        for unit in self.plan():
            offset = unit["params"]["offset"]
            total_reported = None
            while True:
                params = dict(unit["params"], offset=offset)
                try:
                    payload = self._get_with_token(unit["endpoint"], params)
                except (ConnectorUnavailable, requests.exceptions.RequestException) as exc:
                    result.status = "unavailable"
                    result.add_error(f"fetch offset={offset}", exc)
                    break
                meta = payload.get("metadata", {})
                total_reported = meta.get("resultcount", total_reported)
                self._capture_cdo_headers(meta)
                for rec in payload.get("results", []):
                    rec["_request_params"] = params
                    yield rec
                fetched_upto = offset - 1 + len(payload.get("results", []))
                if not payload.get("results") or fetched_upto >= int(total_reported or 0):
                    break
                offset += self.limit
            result.coverage["cdo_resultcount"] = total_reported

    def _get_with_token(self, url, params):
        headers = {"token": self.token}
        attempt = 0
        import requests

        while True:
            attempt += 1
            try:
                resp = requests.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.exceptions.RequestException as exc:
                if attempt >= self.max_attempts:
                    raise ConnectorUnavailable(f"CDO unreachable after {attempt} attempts: {type(exc).__name__}")
                self._sleep(attempt)
                continue
            if resp.status_code == 429 or resp.status_code == 403 and "rate limit" in (resp.text or "").lower():
                if attempt >= self.max_attempts:
                    raise ConnectorUnavailable("CDO rate limit hit (HTTP 429) and retries exhausted")
                retry_after = resp.headers.get("Retry-After")
                import time

                time.sleep(float(retry_after) if retry_after and retry_after.isdigit() else self._backoff(attempt))
                continue
            if resp.status_code in (401, 403):
                raise ConnectorUnavailable(
                    "CDO rejected the token (HTTP %d); verify NOAA_API_TOKEN" % resp.status_code)
            if resp.status_code >= 400:
                if resp.status_code >= 500 and attempt < self.max_attempts:
                    self._sleep(attempt)
                    continue
                raise ConnectorUnavailable(f"CDO returned HTTP {resp.status_code}")
            self._capture_rate_limit_headers(resp.headers)
            return resp.json()

    def _capture_cdo_headers(self, meta) -> None:
        rl = {k: v for k, v in meta.items() if "rate" in k.lower()}
        if rl:
            self.rate_limit_headers.append(rl)

    # --------------------------------------------------------- normalize
    def normalize(self, raw_payload: dict) -> list:
        # CDO /data results are one row per (station, date, datatype).
        code = str(raw_payload.get("datatype", ""))
        metric_unit = DATATYPE_MAP.get(code[5:] if code.startswith("GHCND:") else code)
        value = raw_payload.get("value")
        if metric_unit is None or value is None:
            return []
        metric, unit = metric_unit
        date = raw_payload.get("date", "")[:10]
        station = raw_payload.get("station", "")
        loc = raw_payload.get("location", {}) or {}
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            return []
        rec = {
            "timestamp": f"{date}T00:00:00Z",
            "granularity": "daily",
            "dataset_id": self.dataset_id,
            "source": {
                "provider": self.provider,
                "kind": self.kind,
                "endpoint": f"{self.api_root}/data",
                "station_id": station,
                "dataset_code": self.datasetid,
                "retrieved_at": self._run_started_at,
                "request_params": {k: v for k, v in raw_payload.get("_request_params", {}).items() if k != "token"},
            },
            "location": {
                "latitude": float(lat),
                "longitude": float(lon),
                "station_id": station,
                "name": loc.get("name", station),
                "region": (loc.get("government") or {}).get("name") if isinstance(loc.get("government"), dict) else None,
            },
            "metrics": {metric: {"value": float(value), "unit": unit}},
        }
        rec["location"] = {k: v for k, v in rec["location"].items() if v is not None}
        rec["_timestamp_source"] = "CDO date field (local station date per dataset definition); stored as UTC midnight, provider tz not converted"
        rec["dedupe_key"] = make_dedupe_key(rec)
        return [rec]
