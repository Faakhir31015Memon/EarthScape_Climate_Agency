"""NASA POWER connector (hourly/daily point aggregation API).

Endpoint: ``https://power.larc.nasa.gov/api/temporal/{daily|hourly}/point``
No key required (community ``RE`` = renewable energy, returns the richest set).
NASA POWER encodes missing values as ``-999`` / ``-9999`` / ``NA``; those are
converted to OMITTED metrics (never 0).  Units are read from the response
metadata block when present, with a documented fallback table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from connectors.base import ConnectorUnavailable, SourceConnector, now_utc
from pipeline_config import ENV
from validation.dedupe import make_dedupe_key

BASE_URL = "https://power.larc.nasa.gov/api/temporal/{scope}/point"
MISSING_SENTINELS = {-999.0, -9999.0, -998.0, -9998.0}

#: canonical metric -> POWER parameter name (daily, hourly)
PARAMETERS = {
    "temperature": ("T2M", "T2M", "C", None),
    "temperature_max": ("T2M.MAX", "T2M", "C", "hourly scope reports T2M only; max taken from daily T2M.MAX"),
    "temperature_min": ("T2M.MIN", "T2M", "C", "hourly scope reports T2M only; min taken from daily T2M.MIN"),
    "precipitation": ("PRECTOTCORR", "PRECTOTCORR", "mm", None),
    "humidity": ("RH2M", "RH2M", "%", None),
    "wind_speed": ("WS10M", "WS10M", "m/s", None),
    "wind_direction": ("WD10M", "WD10M", "°", None),
    "pressure": ("PS", "PS", "hPa", None),
    "solar_radiation": ("ALLSKY_SFC_SW_DWN", "ALLSKY_SFC_SW_DWN", "kWh/m²/day",
                        "ALLSKY_SFC_SW_DWN: kWh/m²/day -> MJ/m² (x 3.6)"),
}


class NasaPowerConnector(SourceConnector):
    name = "nasa_power"
    kind = "api"
    provider = "nasa_power"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config)
        self.latitude = float(self.config.get("latitude", 24.8607))
        self.longitude = float(self.config.get("longitude", 67.0011))
        self.scope = self.config.get("scope", "daily")  # daily | hourly
        self.community = self.config.get("community", "RE")
        self.start_date = self.config.get("start_date", "20200101")
        self.end_date = self.config.get("end_date", datetime.now(timezone.utc).strftime("%Y%m%d"))
        self.requested_variables = self.config.get(
            "variables", ["temperature", "temperature_max", "temperature_min", "precipitation", "humidity", "wind_speed"])
        self.location_name = self.config.get("location_name", "site")
        self.region = self.config.get("region", "")
        self.country = self.config.get("country", "")
        self.dataset_id = self.config.get("dataset_id", f"nasa_power_{self.scope}_{self.location_name.lower().replace(' ', '_')}")
        self.base_url = self.config.get(
            "base_url",
            ENV.raw("NASA_POWER_BASE_URL", BASE_URL).replace("{scope}", self.scope))
        self._granularity = "hourly" if self.scope == "hourly" else "daily"

    def plan(self) -> list:
        params = {
            "parameters": ",".join(sorted({self._power_name(m) for m in self.requested_variables if m in PARAMETERS})),
            "start": self.start_date,
            "end": self.end_date,
            "community": self.community,
            "geometry": "point",
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
        return [{"endpoint": self.base_url, "params": params, "segment": {"range": [self.start_date, self.end_date]}}]

    def _power_name(self, metric: str) -> str:
        daily, hourly, _u, _n = PARAMETERS[metric]
        return daily if self.scope == "daily" else hourly

    def fetch(self, result):
        import requests

        for unit in self.plan():
            try:
                payload = self.http_get_json(unit["endpoint"], params=unit["params"])
            except (ConnectorUnavailable, requests.exceptions.RequestException) as exc:
                result.status = "unavailable"
                result.add_error("fetch", exc)
                continue
            if "properties" not in payload or "parameter" not in payload.get("properties", {}):
                result.status = "unavailable"
                result.add_error("fetch", ConnectorUnavailable(
                    "NASA POWER response missing properties.parameter (provider shape changed or error page)"))
                continue
            payload["_request_params"] = unit["params"]
            yield payload

    def normalize(self, raw_payload: dict) -> list:
        params_block = raw_payload["properties"]["parameter"]
        units_meta = raw_payload.get("meta", {}).get("parameters", {})
        dates = raw_payload["properties"].get("date", [])
        records = []
        for idx, day in enumerate(dates):
            metrics = {}
            for metric in self.requested_variables:
                if metric not in PARAMETERS:
                    continue
                pname = self._power_name(metric)
                series = params_block.get(pname)
                if series is None:
                    continue
                value = series[idx] if idx < len(series) else None
                if value in (None, "NA") or (isinstance(value, (int, float)) and float(value) in MISSING_SENTINELS):
                    continue  # omitted: sentinel is absence, not zero
                unit = units_meta.get(pname, {}).get("units") or PARAMETERS[metric][2]
                note = PARAMETERS[metric][3]
                if note and "MJ/m²" in note and "kWh" in unit:
                    value = float(value) * 3.6
                    unit = "MJ/m²"
                    self._note_transform(note)
                metrics[metric] = {"value": round(float(value), 4), "unit": unit}
            if not metrics:
                continue
            ts = (f"{day}T00:00:00Z" if self.scope == "daily" else day.replace(" ", "T") + ":00Z")
            rec = {
                "timestamp": ts,
                "granularity": self._granularity,
                "dataset_id": self.dataset_id,
                "source": {
                    "provider": self.provider,
                    "kind": self.kind,
                    "endpoint": self.base_url,
                    "retrieved_at": self._run_started_at,
                    "request_params": raw_payload.get("_request_params", {}),
                },
                "location": {"latitude": self.latitude, "longitude": self.longitude,
                             "name": self.location_name, "coordinate_precision": 4},
                "metrics": metrics,
            }
            if self.region:
                rec["location"]["region"] = self.region
            if self.country:
                rec["location"]["country"] = self.country
            rec["_timestamp_source"] = "provider date (YYYY-MM-DD or YYYY-MM-DD HH:MM), anchored UTC"
            rec["dedupe_key"] = make_dedupe_key(rec)
            records.append(rec)
        return records
