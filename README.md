# EarthScape Climate Agency

**Big Data Climate Intelligence, Analytics & Prediction Platform**

A full-stack climate intelligence platform: real observations are ingested from
public providers and user files, stored in **MongoDB Atlas**, processed through
**Hadoop MapReduce and Apache Spark**, analysed, scored by machine-learning
models, turned into alerts, notifications and reports, and explored through a
React dashboard with 42 screens.

---

## Architecture at a glance

```
React 18 + TypeScript + Vite + Tailwind + Recharts + Leaflet
        │  HTTPS JSON (/api/v1/…), JWT access token in memory
        ▼
Django 5 + Django REST Framework  ──  drf-spectacular OpenAPI
        │
        ├─ common/analytics.py      trend, compare, correlation, seasonality,
        │                           anomaly detection, coverage  (shared kernel)
        ├─ apps/*                   auth·users·datasets·ingestion·climate·
        │                           processing·ML·alerts·reports·support·monitoring
        ▼
PyMongo ──► MongoDB Atlas           PRIMARY application database (no ORM,
                                     no relational fallback by design)
        │
        ├─ data-pipeline/mapreduce  Hadoop Streaming mapper/reducer
        ├─ data-pipeline/spark      PySpark 3.5 jobs → Parquet in the data lake
        │        local[*] when no cluster; spark://master:7077 when configured
        ├─ ml/                      feature build, training, evaluation, inference
        └─ Celery + Redis           background jobs; in-process worker pool
                                    when no broker is reachable (and it says so)
```

Document database first: `DATABASES = {}`, no `django.contrib.auth`. Users,
sessions, observations, jobs, models, alerts, reports, tickets and audit entries
are MongoDB collections accessed through PyMongo repositories
(`backend/common/repositories.py`, `backend/common/collections.py`).

---

## Repository layout

| Path | Contents |
| --- | --- |
| `backend/` | Django project: `config/`, `common/` (db, auth, analytics, tasks, data lake), `apps/` (11 feature modules), `scripts/` |
| `frontend/` | Vite + React + TS app: `src/app` (router, nav), `src/pages`, `src/components`, `src/features`, `src/services`, `src/hooks`, `src/lib` |
| `data-pipeline/` | `mapreduce/` Hadoop Streaming jobs + local runner, `spark/` PySpark jobs, `schemas/`, `tools/` |
| `ml/` | `features/`, `training/`, `evaluation/`, `anomaly/`, `inference/` |
| `sample-data/` | **Real retrieved climate only**: six city CSVs pulled from the Open-Meteo archive by `scripts/fetch_real_sample_data.py`. The synthetic stress/generator files that used to live here have been removed, and no workflow uses this directory as an input default — the platform's observations come from MongoDB Atlas (NASA POWER connector or user uploads) |
| `infrastructure/` | Dockerfiles, nginx site, Airflow DAG, backup/restore scripts |
| `docker-compose.yml` | backend · worker · scheduler · frontend · redis · optional Hadoop/Spark profile |
| `scripts/` | `dev.sh` local launch helpers · `md2docx.py` report converter |
| `docs/` | `EarthScape-Climate-Agency-Project-Report.docx` — the consolidated written deliverable: architecture, modules, testing evidence, user and administrator guidance |

`backend/.data/` (document-store snapshots, uploads, generated reports, model
artefacts) and `.data-lake/` (MapReduce/Spark outputs) are local runtime state:
git-ignored, recreated on demand, and safe to delete for a clean start.

---

## Quick start (no Docker required)

Prerequisites: Python 3.10–3.11, Node 20+, network access to NASA POWER for
seeding, and (for the real data path) a MongoDB Atlas cluster.

```bash
cp .env.example .env           # then set MONGODB_URI / DJANGO_SECRET_KEY / JWT_SIGNING_KEY
# Paste the Atlas connection string verbatim (mongodb:// shard list or mongodb+srv form),
# leave its <db_username>/<db_password> placeholders in place and fill the two keys below it:
#   MONGODB_ATLAS_USER=<atlas username>      MONGODB_ATLAS_PASSWORD=<atlas password>
.venv/Scripts/python.exe scripts/verify_atlas.py                   # ping Atlas, show masked URI
.venv/Scripts/python.exe backend/scripts/bootstrap_platform.py   # collections, indexes, accounts
.venv/Scripts/python.exe backend/manage.py runserver 0.0.0.0:8000
.venv/Scripts/python.exe backend/scripts/seed_real_data.py       # real observations only
cd frontend && npm install && npm run dev                        # http://localhost:5173
```

#### Importing your own files

`.csv`, `.txt`, `.json`/NDJSON, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, `.xls`, `.xlsb`,
`.ods` (LibreOffice), `.parquet` and password-protected workbooks can all be uploaded
from the app or imported straight into MongoDB from the shell:

```bash
.venv/Scripts/python.exe scripts/import_spreadsheet.py path/to/file.xlsb --collection raw_imports
.venv/Scripts/python.exe scripts/import_spreadsheet.py path/to/*.ods --dry-run   # read, don't write
```

Encrypted uploads need `UPLOAD_FILE_PASSWORD` in `.env`; macros in `.xlsm`/`.xltm` are
never executed. Upload formats are proved with real files — no fixture copies ship in
the repository.

Sign-in credentials are generated randomly by `bootstrap_platform.py` and
printed once into `backend/.data/bootstrap-credentials.txt` (git-ignored). There
is no default or hardcoded administrator password in this repository — and that
file is deliberately **not** part of the delivered archive, so signing into a
received copy means setting your own:

```bash
.venv/Scripts/python.exe backend/scripts/recover_account_password.py --role climate_analyst
```

Stop `runserver` first (the development store is per-process and would overwrite
the change), then sign in with the single printed password. The same procedure,
and what a delivered archive ships or omits, is written up in
`docs/EarthScape-Climate-Agency-Project-Report.docx`.

Container deployment, cluster profile, backups and Airflow are covered in
[`infrastructure/README.md`](infrastructure/README.md).

---

## Configuration and honesty rules

`.env.example` documents every setting. The platform reports its own state
truthfully rather than assuming infrastructure exists:

- A `MONGODB_URI` that still contains a `<db_username>` / `<password>` placeholder is
  treated as **not configured** rather than sent to the server as a literal username;
  `/api/v1/health` and `scripts/verify_atlas.py` say which key is missing.
- If Atlas is unreachable and `FORCE_ATLAS_CONNECTION=false`, the API uses the
  clearly labelled `local-development` document store with JSON snapshots, and
  every screen plus the EnvironmentBanner says so. Production should set
  `FORCE_ATLAS_CONNECTION=true` so startup fails instead.
- Hadoop, Spark, Kafka, Celery/Redis, Airflow, SMTP and HDFS are probed. When
  not configured the admin Health page names the missing environment key and
  the corresponding UI actions are disabled. Nothing claims a job ran on a
  cluster that is not there.
- Metrics on dashboards are computed from stored observations. Where a series
  lacks the minimum number of points the API returns
  `available: false, reason, available_points, required_points`, and the UI
  renders that instead of a number.

Security posture: JWT access (20 min) + rotating refresh (7 days) with reuse
detection, bcrypt password hashing, login lockout, server-side RBAC
(`administrator`, `climate_analyst`, `viewer`), capability checks on every
endpoint, per-user request rate limits, an append-only audit trail, allow-listed
background tasks (no arbitrary command execution from the dashboard), upload
size/row/type limits, CSV/Excel formula-injection neutralisation, and no
stack traces in API responses. Credentials and connection strings are never
logged and never committed. The browser only ever talks to the Django API.

---

## Feature map

**Public** — landing, about, capabilities, insights preview, data sources, help,
privacy, terms.
**Auth** — login, register (viewer-only unless admin-created), forgot/reset
password, profile & preferences.
**Analyst app** — dashboard, analytics explorer, observation history, map view,
anomaly explorer, datasets, dataset detail, CSV/JSON/Excel/ODS/Parquet upload,
ingestion jobs, processing monitor, big-data (HDFS/Spark) page, ML lab, model
registry, forecast explorer, alerts, notifications, reports, saved analyses,
support tickets.
**Administration** — platform overview, users, roles & capabilities, dataset
governance, pipeline configuration, alert rules, audit log, system health,
settings, ticket queue.

---

## Documentation

| Document | Path |
| --- | --- |
| Project report (submission copy) | `docs/EarthScape-Climate-Agency-Project-Report.docx` |
| Architecture & module design | same report, plus `docs` notes in each module README |
| Data dictionary (collections & fields) | same report; live ranges are read from the running deployment |
| Installation & first start | this file, `scripts/dev.sh`, `infrastructure/README.md` |
| Deployment, cluster profile, backups, Airflow | `infrastructure/` |
| MapReduce / Spark job layout | `data-pipeline/mapreduce/README.md`, `data-pipeline/spark/` |
| ML methodology & conventions | `ml/README.md` |
| Synthetic sample data provenance | `sample-data/README.md` |
