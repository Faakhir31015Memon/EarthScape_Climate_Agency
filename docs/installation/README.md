# Installation and deployment guide

EarthScape Climate Agency — Big Data Climate Intelligence, Analytics &
Prediction Platform.

Two installation paths are documented:

* **Path A — local development** (verified in this delivery, no Docker needed)
* **Path B — container deployment** (configuration authored here; images were
  not built in the delivery environment because Docker was unavailable — see
  `infrastructure/README.md`)

---

## 1. Hardware and software requirements

| | Minimum | Recommended |
| --- | --- | --- |
| CPU | Intel i5, 4 cores | i7 or better, 8 cores (Spark `local[*]` uses one core per executor thread) |
| RAM | 16 GB | 32 GB when running the Hadoop/Spark profile |
| Storage | 10 GB free on SSD | 500 GB SSD for the data lake plus 14 backup generations |
| OS | 64-bit Windows 10 or higher | Windows 11 / Ubuntu 22.04 (Docker path requires Linux containers) |
| Network | HTTPS egress to `power.larc.nasa.gov`, `api.open-meteo.com`, `*.mongodb.net` | |

Software:

| Tool | Version used | Required? |
| --- | --- | --- |
| Python | 3.10–3.11 (pyspark/xgboost wheels need `<3.12`) | yes |
| Node.js / npm | 20 LTS | yes (frontend) |
| Java (JDK) | 17 | yes for PySpark and MapReduce |
| MongoDB Atlas | 6.0+ M0 or larger cluster | yes — primary datastore |
| MongoDB Compass / `mongosh` | current | optional, for inspection |
| Hadoop | 3.3.x | optional — enables the MapReduce/HDFS paths |
| Apache Spark | 3.5.1 | optional — installed as `pyspark`; standalone/YARN for cluster runs |
| Apache Kafka | 3.x | optional — real-time streaming path |
| Airflow | 2.7+ | optional — external scheduler, REST-only DAG |
| Jupyter / JupyterLab (Anaconda) | 3 | optional — `ml/notebooks/exploratory_climate_analysis.ipynb` |
| Docker Engine + Compose v2 | 24+ | only for Path B |

Tools named in the project brief but not used by this build, and why:
**RStudio** — the analytics stack here is Python, not R; **Tableau** — the
platform ships its own Recharts/Leaflet dashboards and CSV/PDF exports;
**Impala** — it queries Hadoop via a relational engine, which conflicts with the
document-database architecture required for this project (MongoDB Atlas is the
application store, HDFS/Parquet is the processing lake).

---

## 2. Path A — local development

### 2.1 Virtual environment

```bash
cd earthscape-climate-agency
python -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
```

### 2.2 Configure

```bash
cp .env.example .env
```

Edit `.env`. Three values are mandatory — the API refuses to start without
them:

| Key | How to obtain |
| --- | --- |
| `DJANGO_SECRET_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `JWT_SIGNING_KEY` | `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `MONGODB_URI` | Atlas → Database Access → create a user → Connect → copy the SRV string into `MONGODB_URI`. Add your IP under Network Access. |

Decide the connection policy now:

* `FORCE_ATLAS_CONNECTION=true` — production behaviour. If Atlas is unreachable
  the API fails loudly instead of running against the in-process store.
* `FORCE_ATLAS_CONNECTION=false` — development convenience. The platform uses
  the labelled `local-development` document store with JSON snapshots under
  `backend/.data/snapshots/`, and **every screen says so**. Never ship this.

Optional but recommended for a complete install: `SPARK_ENABLED=true`,
`HDFS_ENDPOINT` only if a NameNode really answers, `REDIS_URL` for Celery,
`NOAA_API_KEY` for the NOAA connector, SMTP keys for email notifications.

`.env` is git-ignored. Never commit credentials, and never paste an Atlas
connection string into an issue, a screenshot, or a log.

### 2.3 Java for Spark

```bash
java -version        # must report 17
```

PySpark 3.5.1 does not support Java 21+. If both are installed, point
`JAVA_HOME` at 17 before starting the backend.

### 2.4 Schema, indexes and first accounts

```bash
.venv/Scripts/python.exe backend/scripts/bootstrap_platform.py
```

Idempotent. It creates the 18 collections, every index in
`backend/common/collections.py`, the region reference records, the default
platform settings, and one account per role with **randomly generated**
passwords written once to `backend/.data/bootstrap-credentials.txt`. Changing a
password later does not rewrite that file; deleting the file is safe.

**Signing in when you received the archive rather than running bootstrap.** The
delivered `backend/.data/localdb/` already contains the three role accounts, but
the credentials file is deliberately *not* in the archive — it is git-ignored and
excluded from packaging. Mint a working password locally:

```bash
.venv/Scripts/python.exe backend/scripts/recover_account_password.py --role climate_analyst
.venv/Scripts/python.exe backend/scripts/recover_account_password.py --email viewer@earthscape.local
```

The tool prints exactly one new password, revokes that account's outstanding
sessions, and stores the value nowhere — not in the credentials file, not in the
database, not in the audit log.

On the development document store you must **stop `runserver` first**, run the
tool, then start the API again. The store lives inside the API process and rewrites
its JSON files as requests arrive, so a recovery performed while the server is
running has its write overwritten on the next flush — the symptom is a `401` on the
very next sign-in. Against MongoDB Atlas the change is visible immediately and no
restart is needed. The same stop-first rule applies to any out-of-process edit of
`backend/.data/localdb/*.json`, including a restore.

### 2.5 Load real data

```bash
.venv/Scripts/python.exe backend/scripts/seed_real_data.py
```

Fetches genuine NASA POWER daily observations through the same connector and
importer the UI uses. No network ⇒ it aborts rather than inventing rows.

### 2.6 Run

```bash
./scripts/dev.sh backend     # http://127.0.0.1:8000
./scripts/dev.sh frontend    # http://localhost:5173
```

Open `http://localhost:5173`, sign in, and read the EnvironmentBanner: it names
the document-store mode and lists every big-data engine that is not configured,
so you know exactly what your install is and is not doing.

### 2.7 Verify the processing layer

```bash
./scripts/dev.sh check                                   # byte-compile + typecheck
.venv/Scripts/python.exe data-pipeline/mapreduce/run_streaming_job.py \
  --force-local --job temperature_aggregation --json      # mapper | sort | reducer over the MongoDB export
.venv/Scripts/python.exe data-pipeline/tools/run_spark_jobs.py \
  --input artifacts/datalake/raw/sample_api/open_meteo_daily_karachi.jsonl \
  --dataset-id open_meteo_daily_karachi --metric temperature_c \
  --jobs missing_value_profiling,regional_climate_aggregation,anomaly_feature_generation
```

Both commands read real data only. There is no synthetic corpus in this
repository: if the observation collection is empty, `run_streaming_job.py`
aborts with an explicit error instead of substituting example rows.

`run_spark_jobs.py` takes `--input` (a file or lake prefix), `--jobs` (comma list),
`--dataset-id`, `--metric` and `--with-mapreduce-check`. It prints the Spark
session it actually created (`master`, `version`, `default_parallelism`) and one
`JOB … RESULT` line per job with real row counts and files written, so a
successful run is evidence rather than a claim. Expect two honest warnings on a
machine without infrastructure: `HDFS probe failed …` and `MongoDB Atlas
unavailable …; using local-development mode`.

### 2.8 Running from the delivered archive

`EarthScape-Climate-Agency.zip` is a superset of the git repository on purpose: it
ships the verified data snapshot that `.gitignore` keeps out of version control.

| In the archive | Why |
| --- | --- |
| `backend/.data/ml-artifacts/` | the trained joblib models plus their per-run report sidecars |
| `backend/.data/reports/`, `uploads/`, `processing/streaming/` | generated report files, the uploaded CSV and its manifest, streaming checkpoints |
| `.data-lake/` | raw/validated/processed/analytics/ml-features/exports partitions the Spark and MapReduce jobs read and write |

These local directories are regenerated by `bootstrap_platform.py`, the ingestion
seeder and the processing jobs; the delivered archive no longer carries a
snapshotted demo store, and any file that used to advertise synthesis is absent.

| Excluded | Why |
| --- | --- |
| `.venv` (1,012 MB), `frontend/node_modules` (157 MB), `frontend/dist`, every `__pycache__` | recreate with `python -m venv`, `pip install -r requirements.txt`, `npm install`, `npm run build` |
| `.env` | it holds this machine's `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` and any Atlas URI; `.env.example` ships instead and §2.2 explains the three keys you must set |
| `backend/.data/bootstrap-credentials.txt` | the generated sign-in passwords are deliberately not distributed; §2.4 shows how to set one you know |
| `backend/.data/*.log` | local run logs |
| `backend/.data/backups/`, `exports/`, `processing/spark-tmp` | pre-migration snapshots and scratch space, not delivered state |

517 files, 8.4 MB as delivered, 33.9 MB unpacked. Every stored file reference inside
it is a key relative to `MEDIA_ROOT` or a `lake://` reference, so no path in the
archive resolves to the machine that built it — see the storage-key convention in
`docs/architecture/data-dictionary.md`.

To reach a sign-in from a fresh extraction:

```bash
python -m venv .venv && .venv/Scripts/pip.exe install -r requirements.txt
cp .env.example .env      # set DJANGO_SECRET_KEY and JWT_SIGNING_KEY to new random values
.venv/Scripts/python.exe backend/scripts/recover_account_password.py --role climate_analyst
```

Stop `runserver` before that script: the development store is per-process and a
running server would overwrite the change. The script prints a new random password
once and stores only its bcrypt hash; there is no default credential anywhere in
this repository.

---

## 3. Path B — container deployment

```bash
cp .env.example .env    # set the mandatory keys, FORCE_ATLAS_CONNECTION=true
docker compose up --build
# with Hadoop + Spark as well:
docker compose --profile cluster up --build
```

The site is served by nginx on `${WEB_PORT:-8080}`; `/api/` is proxied to
Gunicorn; the SPA falls back to `index.html` on client-side routes. Run
`bootstrap_platform.py` once inside the backend container:

```bash
docker compose exec backend python scripts/bootstrap_platform.py
```

Full details, service map, volumes and the cluster profile:
[`infrastructure/README.md`](../../infrastructure/README.md).

---

## 4. Operational checklist after install

| Check | Expected |
| --- | --- |
| `GET /api/v1/health` | `database.mode` is `atlas`; `status` `ok` |
| `GET /api/v1/ready` | HTTP 200 — used by the container healthcheck |
| `/api/docs` | OpenAPI schema renders |
| Sign in as viewer | admin nav absent; write actions disabled |
| Upload `sample-data` CSV → inspect → map → commit | job reaches `completed`, rows land in `climate_observations` |
| `dedupe_key` uniqueness | re-running the same import adds 0 duplicates |
| Dashboard charts | only metrics backed by stored points render; thin series show `available: false` with reasons |
| Train model → activate | registry shows `candidate` then `active`, with real evaluation metrics |
| Alert rule fires → notification → (email) | `delivery_status` records the truth; `suppressed` when SMTP is off |
| Report generation → PDF/CSV download | file exists and matches the requested period |
| Audit log | every privileged action appears with actor, outcome, request id |

## 5. Backup and restore

`infrastructure/backup/backup.sh` (mongodump + SHA256 + 14-generation pruning)
and `restore.sh` (checksum-verified, `--yes-destroy-data` guarded). See
`infrastructure/README.md` §3.

## 6. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| API starts, banner says `local-development` | `MONGODB_URI` missing/unreachable and forcing is off | set a real URI, `FORCE_ATLAS_CONNECTION=true` |
| `MongoServerSelectionError` | Atlas IP allow-list | Network Access → add your egress IP (or `0.0.0.0/0` for a temporary test, then remove) |
| Spark raises `Java version not supported` | JDK 21+ | set `JAVA_HOME` to JDK 17 |
| Processing page reports engine unavailable | no HDFS/Spark endpoint | start `--profile cluster`, or accept `local[*]`/local data lake — the UI is telling the truth |
| Celery tasks never leave the queue | Redis not reachable | start Redis; without it jobs use the in-process pool and the UI says so |
| Login "account locked" | 8 failed attempts | 15-minute lockout, or an administrator unlocks on the Users page |
| Ports clash | 8000/5173/8080/9870/8085 in use | `BACKEND_PORT`, Vite `--port`, `WEB_PORT`, remap in compose |
