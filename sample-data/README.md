# Test data used in the project

**Everything in this directory is real retrieved climate data.** The platform
reports which provider, endpoint and retrieval time each row came from.

---

## 1. Real climate observations — the only data the application ingests

| Source | How it is fetched | Where it lands |
| --- | --- | --- |
| NASA POWER (daily, point-based) | `backend/apps/ingestion/connectors/` HTTPS calls; no API key required | `climate_observations` collection |
| NOAA Climate Data Online | connector + `NOAA_API_KEY`; no key ⇒ connector reports *not configured* | `climate_observations` |
| Open-Meteo live provider | connector + `LIVE_CLIMATE_PROVIDER` | `climate_observations` |
| User uploads | CSV / JSON / XLSX / XLS / XLSB / XLSM / XLTX / XLTM / ODS / Parquet through the upload + mapping flow | `climate_observations` |
| `backend/scripts/seed_real_data.py` | pulls genuine NASA POWER series through the *same* connector and importer code path the UI uses | `climate_observations` |

If `seed_real_data.py` cannot reach the provider it aborts and says so. It does
not substitute invented rows, and no other code path writes an observation the
platform did not receive from a provider, a file, or a documented manual entry.

| File | Coverage |
| --- | --- |
| `Karachi_daily.csv`, `Islamabad_daily.csv`, `Lahore_daily.csv`, `Multan_daily.csv`, `Peshawar_daily.csv`, `Quetta_daily.csv` | daily station series retrieved from the Open-Meteo archive by `scripts/fetch_real_sample_data.py`; each file carries its provider, endpoint and retrieval timestamp |

---

## 2. No synthetic data ships in this repository

Earlier revisions of the project carried a labelled synthetic corpus
(`SYNTHETIC_high_volume.csv`, `generated/synthetic_observations_*`,
`data-pipeline/mapreduce/example-input.jsonl`) plus the generators that produced
them. All of it has been removed, together with:

* the fallback that let a MapReduce run silently switch to `example-input.jsonl`
  when MongoDB was empty — such a run now aborts with an explicit error;
* the `allow_example_input` parameter on the MapReduce workflows and the
  `--allow-example-input` flag on `run_streaming_job.py`;
* the synthetic default input path on the four Spark workflows, which now fail
  honestly when no real export path is supplied.

`ml/datasets/loader.py::_is_synthetic()` stays as a firewall: any file whose name
advertises synthesis is excluded from model training even if one is dropped into
the lake later.

---

## 3. Regions and reference data

`backend/common/regions.py` seeds the `regions` collection with country,
region, latitude/longitude and station codes used by the map and grouping
screens. Region metadata is reference data, not measurement data.

---

## 4. Account bootstrap data

`backend/scripts/bootstrap_platform.py` creates the first
administrator / climate analyst / viewer accounts with **randomly generated**
passwords and writes them once to `backend/.data/bootstrap-credentials.txt`
(git-ignored). No default password, no seeded user data, no example credentials
in any committed file.
