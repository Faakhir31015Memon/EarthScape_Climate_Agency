# sample-data — REAL retrieved climate data (NOT synthetic)

- Retrieval date (UTC): **2026-10-16T16:13:37Z**
- Coverage requested: 2015-01-01 .. 2025-12-31 (daily granularity, timezone UTC)
- Provider/API: **Open-Meteo Historical Weather API (ERA5/ERA5-Land based reanalysis)**
- Endpoint: `https://archive-api.open-meteo.com/v1/archive` (no key required)
- Client: `data-pipeline/connectors/open_meteo.py` (one request per city per year segment)
- Variables and units (provider metadata preserved, canonicalised by `validation/units.py`):
  - `temperature_2m_max/min/mean` -> °C (`temperature_max_C` …)
  - `precipitation_sum` -> mm
  - `relative_humidity_2m_mean` -> %
  - `wind_speed_10m_max` -> **converted km/h to m/s** (recorded in `provenance.transformations`)
  - `pressure_msl_mean` -> hPa
  - `shortwave_radiation_sum` -> MJ/m²
- Missing measurements are EMPTY cells in the CSV and are OMITTED from `metrics` in JSONL — never 0.
- Each row also carries the canonical `dedupe_key` and validator quality status/flags.

| file | city | rows fetched | rows accepted | rejected | status |
|------|------|-------------|---------------|----------|--------|
| Karachi_daily.csv | Karachi | 4018 | 4018 | 0 | ok |
| Lahore_daily.csv | Lahore | 4018 | 4018 | 0 | ok |
| Islamabad_daily.csv | Islamabad | 4018 | 4018 | 0 | ok |
| Peshawar_daily.csv | Peshawar | 4018 | 4018 | 0 | ok |
| Quetta_daily.csv | Quetta | 4018 | 4018 | 0 | ok |
| Multan_daily.csv | Multan | 4018 | 4018 | 0 | ok |

Also in this folder: `SYNTHETIC_high_volume.csv` — see its `.meta.json` sidecar; that file
is **synthetic** (deterministic test data for volume testing) and must never be presented
as climate truth.
