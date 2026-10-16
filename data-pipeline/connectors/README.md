# connectors — provider retrieval, normalisation, provenance

Every connector implements `base.SourceConnector` (`plan` / `fetch` /
`normalize` / `validate` / `provenance`) and produces records conforming to
`../schemas/observation.schema.json`. **No connector fabricates data.** If a
provider is unreachable the run finishes with status `unavailable` plus a
sanitised reason (URLs/credentials stripped); if a credential env var is
missing it raises `ConnectorNotConfigured` naming that variable exactly.

`ConnectorResult` carries `records`, `fetched` / `accepted` / `rejected` /
`skipped` counts, `errors`, `status` (`ok|partial|unavailable|error`) and
`coverage`. Missing measurements are *skipped* (omitted from `metrics`), never
written as 0. All HTTP goes through `http_get_json`: timeouts
(`CONNECTOR_TIMEOUT_SECONDS`, default 30), retries with exponential backoff +
jitter (`CONNECTOR_MAX_ATTEMPTS`, default 4), and 429 handling honouring
`Retry-After`; rate-limit response headers are captured per run.

## open_meteo (default development path, no key)

* Archive: `https://archive-api.open-meteo.com/v1/archive`
  (`mode="archive"`, daily/hourly, one request per year so a failure costs one
  segment — recorded in `coverage.failed_segments`).
* Forecast: `https://api.open-meteo.com/v1/forecast` (`open_meteo_live`, used
  by periodic polling).
* Config: `latitude`, `longitude`, `start_date`, `end_date`, `granularity`,
  `variables`, `dataset_id`, `location_name`, `region`, `country`.
* Variable map: `temperature_2m_max/min[_mean]`→`temperature_max/min/` (°C),
  `precipitation_sum`→mm, `relative_humidity_2m*`→%, `wind_speed_10m*` **km/h →
  m/s (÷3.6, transformation recorded in `provenance.transformations`)**,
  `wind_direction_10m_dominant`→°, `pressure_msl`→hPa,
  `shortwave_radiation_sum`→MJ/m². Units are read from `daily_units`/
  `hourly_units` response metadata.
* Base URL overridable via `OPEN_METEO_BASE_URL`.

## nasa_power (no key)

* `https://power.larc.nasa.gov/api/temporal/{daily|hourly}/point`, community
  `RE`, geometry point. Parameters: T2M/T2M.MAX/T2M.MIN, PRECTOTCORR (mm/day),
  RH2M (%), WS10M (m/s), WD10M (°), PS (hPa), ALLSKY_SFC_SW_DWN (kWh/m²/day →
  MJ/m² ×3.6, recorded).
* Sentinel values `-999` / `-9999` / `NA` mean *missing* → metric omitted.
* Units taken from the response `meta.parameters[...]units` with a fallback
  table. Config: `scope` (`daily|hourly`), `start_date`/`end_date` (`YYYYMMDD`),
  `variables`, coordinates, `location_name`/`region`/`country`; base URL via
  `NASA_POWER_BASE_URL`.

## noaa_cdc (requires `NOAA_API_TOKEN`)

* `https://www.ncdc.noaa.gov/cdo-web/api/v2/data`, token header, GHCND station
  results (one row per station/date/datatype).
* Validated inputs: `datasetid` ∈ {GHCND, GHCND_DAILY, GSOD, LTE}, ISO dates,
  station-id shape (`USW00023174`), datatypes (`TMAX, TMIN, TAVG, PRCP, ...`).
* Pagination via `offset` (limit ≤ 1000); `metadata.resultcount` drives loop
  termination; `X-RateLimit-*` headers and CDO metadata rate fields captured.
* Without `NOAA_API_TOKEN` → `ConnectorNotConfigured("NOAA_API_TOKEN")`.

## registry

`connectors/registry.py` — add a source by calling
`register(name, cls, title, description, requirements)`; instantiate with
`build_connector(name, config)`; introspect with `registered()` /
`provider_info(name)`. Built-ins: `open_meteo`, `open_meteo_live`,
`nasa_power`, `noaa_cdc`.

## provenance fields

Every accepted record gets `provenance = {ingested_at, schema_version,
unit_source, transformations[], timestamp_source?}` where `transformations`
lists every unit conversion / normalisation string applied (e.g.
`"wind_speed: km/h -> m/s (/ 3.6)"`), and `timestamp_source` explains how the
provider timestamp was anchored to UTC.
