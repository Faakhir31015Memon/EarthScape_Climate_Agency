# Test data used in the project

Two clearly separated kinds of data live in this repository. **They are never
mixed, and the platform tells you which one you are looking at.**

---

## 1. Real climate observations — the only data the application ingests

| Source | How it is fetched | Where it lands |
| --- | --- | --- |
| NASA POWER (daily, point-based) | `backend/apps/ingestion/connectors/` HTTPS calls; no API key required | `climate_observations` collection |
| NOAA Climate Data Online | connector + `NOAA_API_KEY`; no key ⇒ connector reports *not configured* | `climate_observations` |
| Open-Meteo live provider | connector + `LIVE_CLIMATE_PROVIDER` | `climate_observations` |
| User uploads | CSV / JSON / XLSX / Parquet through the upload + mapping flow | `climate_observations` |
| `backend/scripts/seed_real_data.py` | pulls genuine NASA POWER series through the *same* connector and importer code path the UI uses | `climate_observations` |

If `seed_real_data.py` cannot reach the provider it aborts and says so. It does
not substitute invented rows, and no other code path writes an observation the
platform did not receive from a provider, a file, or a documented manual entry.

---

## 2. Synthetic data — pipeline verification only

| File | Purpose |
| --- | --- |
| `data-pipeline/tools/generate_synthetic_observations.py` | deterministic generator (`--seed`, `--rows`, `--days`) |
| `generated/synthetic_observations_2022-01-01_400d.jsonl` | 9,696 rows, 400 days, 6% missing values — exercises the missing-data handling in MapReduce/Spark |
| `generated/synthetic_observations_2022-01-01_400d.parquet` | same rows in the columnar format the data lake writes |
| `generated/synthetic_observations_2022-01-01_400d.meta.json` | provenance record: generator, seed, row count, warning |
| `data-pipeline/mapreduce/example-input.jsonl` | small synthetic contract example the local streaming job falls back to when the collection is empty |

These files exist to prove that the distributed jobs, schema validation,
partitioning and feature builders do the right thing on a known volume —
including rows with gaps, which real feeds rarely give you on demand. The ML
layer is deliberately excluded from that: `ml/training/pipeline.py` reads the
observation repository and takes no file input, so no synthetic row can become a
model.

**They must never be presented as climate truth.** Every synthetic document
carries:

```json
"dataset_id": "synthetic-stress",
"source_name": "synthetic-generator",
"unit_audit": "synthetic-canonical"
```

so it is distinguishable from a provider record by inspection, by query, and in
any export. The `.meta.json` sidecar repeats the warning. The generator writes
only into `sample-data/generated/`; there is no supported path that loads it
into the Atlas `climate_observations` collection, and the analytics API never
reads from `sample-data`.

Regenerate or delete the synthetic corpus with:

```bash
.venv/Scripts/python.exe data-pipeline/tools/generate_synthetic_observations.py \
  --days 400 --seed 20260101
```

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
