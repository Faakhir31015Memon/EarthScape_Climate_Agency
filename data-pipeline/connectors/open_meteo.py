"""Open-Meteo connector — the default key-free development data path.

Endpoints
---------
* Historical daily/hourly archive: ``https://archive-api.open-meteo.com/v1/archive``
* Live/forecast polling (used by ``streaming/polling_ingest.py``):
  ``https://api.open-meteo.com/v1/forecast``

Open-Meteo returns ``null`` for missing values; those measurements are OMITTED
from ``metrics`` (never written as 0).  Units come from the response metadata
(``daily_units`` / ``hourly_units``); the only value transformation we apply is
wind speed km/h -> m/s, which is recorded in ``provenance.transformations``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from connectors.base import (ConnectorUnavailable, SourceConnector, now_utc)
from pipeline_config import ENV
from validation.dedupe import make_dedupe_key

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: canonical metric -> (open-meteo daily variable, open-meteo hourly variable, native unit)
DAILY_VARIABLES = {
    "temperature_max": ("temperature_2m_max", None, "°C"),
    "temperature_min": ("temperature_2m_min", None, "°C"),
    "temperature": ("temperature_2m_mean", "temperature_2m", "°C"),
    "precipitation": ("precipitation_sum", "precipitation", "mm"),
    "humidity": ("relative_humidity_2m_mean", "relative_humidity_2m", "%"),
    "wind_speed": ("wind_speed_10m_max", "wind_speed_10m", "km/h"),
    "wind_direction": ("wind_direction_10m_dominant", "wind_direction_10m", "°"),
    "pressure": ("pressure_msl_mean", "pressure_msl", "hPa"),
    "solar_radiation": ("shortwave_radiation_sum", "shortwave_radiation", "MJ/m²"),
}
HOURLY_VARIABLES = {
    "temperature": ("temperature_2m", "°C"),
    "precipitation": ("precipitation", "mm"),
    "humidity": ("relative_humidity_2m", "%"),
    "wind_speed": ("wind_speed_10m", "km/h"),
    "wind_direction": ("wind_direction_10m", "°"),
    "pressure": ("surface_pressure", "hPa"),
    "solar_radiation": ("shortwave_radiation", "MJ/m²"),
}
KMH_TO_MS = "wind_speed: km/h -> m/s (multiply by 1/3.6, source unit recorded in provider metadata)"


class OpenMeteoConnector(SourceConnector):
    name = "open_meteo"
    kind = "api"
    provider = "open_meteo"

    def __init__(self, config: Optional[dict] = None) -> None:
        super().__init__(config)
        self.latitude = float(self.config.get("latitude", 24.8607))
        self.longitude = float(self.config.get("longitude", 67.0011))
        self.mode = self.config.get("mode", "archive")  # archive | forecast
        self.granularity = self.config.get("granularity", "daily")  # daily | hourly
        self.start_date = self.config.get("start_date", "2015-01-01")
        self.end_date = self.config.get("end_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        self.location_name = self.config.get("location_name", "site")
        self.region = self.config.get("region", "")
        self.country = self.config.get("country", "")
        self.requested_variables = self.config.get(
            "variables", ["temperature_max", "temperature_min", "precipitation", "humidity", "wind_speed"])
        self.dataset_id = self.config.get("dataset_id", f"open_meteo_{self.granularity}_{self.location_name.lower().replace(' ', '_')}")
        base_key = "OPEN_METEO_ARCHIVE_URL" if self.mode == "archive" else "OPEN_METEO_FORECAST_URL"
        default_url = ARCHIVE_URL if self.mode == "archive" else FORECAST_URL
        self.base_url = self.config.get("base_url", ENV.raw("OPEN_METEO_BASE_URL") or ENV.raw(base_key) or default_url)

    # ------------------------------------------------------------ plan
    def plan(self) -> list:
        yearly = []
        start_year = int(self.start_date[:4])
        end_year = int(self.end_date[:4])
        # archive API is generous, but keep single requests per year segment so a
        # partial failure only loses that segment (honest coverage).
        for year in range(start_year, end_year + 1):
            s = f"{year}-01-01" if year > start_year else self.start_date
            e = f"{year}-12-31" if year < end_year else self.end_date
            yearly.append({
                "endpoint": self.base_url,
                "params": self._params_for(s, e),
                "segment": {"year": year, "start": s, "end": e},
            })
        return yearly

    def _params_for(self, start: str, end: str) -> dict:
        vars_key = self._om_variables()
        params = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": "UTC",
            self.granularity: ",".join(sorted({v for v in vars_key.values() if v})),
        }
        if self.mode == "archive":
            params["start_date"] = start
            params["end_date"] = end
        return params

    def _om_variables(self) -> dict:
        table = DAILY_VARIABLES if self.granularity == "daily" else HOURLY_VARIABLES
        mapping: dict = {}
        for metric in self.requested_variables:
            if metric not in table:
                continue
            if self.granularity == "daily":
                daily, hourly, _unit = table[metric]
                mapping[metric] = daily
            else:
                hourly_name, _unit = table[metric]
                mapping[metric] = hourly_name
        return {k: v for k, v in mapping.items() if v}

    # ------------------------------------------------------------ fetch
    def fetch(self, result):
        import requests

        for unit in self.plan():
            try:
                payload = self.http_get_json(unit["endpoint"], params=unit["params"])
            except (ConnectorUnavailable, requests.exceptions.RequestException) as exc:
                result.status = "unavailable"
                result.add_error(f"fetch:{unit['segment']['year']}", exc)
                result.coverage.setdefault("failed_segments", []).append(unit["segment"])
                continue
            payload["_segment"] = unit["segment"]
            payload["_request_params"] = unit["params"]
            yield payload

    # ------------------------------------------------------------ normalize
    def normalize(self, raw_payload: dict) -> list:
        segment = raw_payload.get("_segment", {})
        values = raw_payload.get(self.granularity)
        if not values or "time" not in values:
            return []
        units = raw_payload.get(f"{self.granularity}_units", {})
        om_to_metric = {v: k for k, v in self._om_variables().items()}
        times = values["time"]
        records = []
        for idx, ts_raw in enumerate(times):
            ts, ts_source = self._timestamp(ts_raw)
            metrics = {}
            for om_name, metric in om_to_metric.items():
                series = values.get(om_name)
                if series is None:
                    continue
                value = series[idx] if idx < len(series) else None
                if value is None:
                    continue  # omitted: null is absence, never a fabricated 0
                unit = units.get(om_name, DAILY_VARIABLES.get(metric, (None, None, "?"))[2]
                                 if self.granularity == "daily" else HOURLY_VARIABLES.get(metric, (None, "?"))[2])
                value, unit = self._convert(metric, value, unit)
                metrics[metric] = {"value": round(float(value), 4), "unit": unit}
            if not metrics:
                records.append(None)  # fully-empty day: counted as skipped, not accepted
                continue
            lat = float(raw_payload.get("latitude", self.latitude))
            lon = float(raw_payload.get("longitude", self.longitude))
            rec = {
                "timestamp": ts,
                "granularity": self.granularity,
                "dataset_id": self.dataset_id,
                "source": {
                    "provider": self.provider,
                    "kind": self.kind,
                    "endpoint": self.base_url,
                    "retrieved_at": self._run_started_at,
                    "request_params": raw_payload.get("_request_params", {}),
                },
                "location": {
                    "latitude": lat,
                    "longitude": lon,
                    "name": self.location_name,
                    "country": self.country or None,
                    "region": self.region or None,
                    "elevation_m": raw_payload.get("elevation"),
                    "coordinate_precision": 4,
                },
                "metrics": metrics,
            }
            rec["location"] = {k: v for k, v in rec["location"].items() if v is not None}
            rec["_timestamp_source"] = ts_source
            rec["dedupe_key"] = make_dedupe_key(rec)
            records.append(rec)
        return records

    def _timestamp(self, date_str: str):
        if self.granularity == "daily":
            return f"{date_str}T00:00:00Z", "provider daily date anchored at 00:00 UTC (day granularity)"
        clean = date_str.replace(":", "").replace("-", "")[:13]
        dt = datetime.strptime(date_str, "%Y-%m-%dT%H:%M")
        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "provider hourly local=UTC hour start"

    def _convert(self, metric: str, value, unit: str):
        if metric == "wind_speed" and unit and "km" in unit.lower():
            self._note_transform(KMH_TO_MS)
            return float(value) / 3.6, "m/s"
        if metric == "wind_speed" and unit and unit.strip().lower() == "m/s":
            return float(value), "m/s"
        return float(value), unit or ""

    def run_coverage(self, result):
        result.coverage.setdefault("segments_ok", len(self.plan()) - len(result.coverage.get("failed_segments", [])))


class OpenMeteoLiveConnector(OpenMeteoConnector):
    """Forecast-endpoint variant used by periodic polling (kind stays api)."""

    def __init__(self, config=None):
        cfg = dict(config or {})
        cfg.setdefault("mode", "forecast")
        cfg.setdefault("granularity", "hourly")
        cfg.setdefault("variables", ["temperature", "precipitation", "humidity", "wind_speed", "pressure"])
        super().__init__(cfg)
        self.base_url = cfg.get("base_url", ENV.raw("OPEN_METEO_FORECAST_URL") or FORECAST_URL)

    def plan(self) -> list:
        past = int(self.config.get("past_hours", 48))
        return [{
            "endpoint": self.base_url,
            "segment": {"live": True},
            "params": {
                "latitude": self.latitude,
                "longitude": self.longitude,
                "hourly": ",".join(sorted({v for v in
                                           (HOURLY_VARIABLES[m][0] for m in self.requested_variables if m in HOURLY_VARIABLES)})),
                "forecast_days": self.config.get("forecast_days", 2),
                "past_days": past,
                "timezone": "UTC",
            },
        }]
