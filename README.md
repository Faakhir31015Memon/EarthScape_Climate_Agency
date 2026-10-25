# EarthScape Climate Agency

**Big Data Climate Intelligence, Analytics & Prediction Platform**

A full-stack climate intelligence platform: real observations are ingested from
public providers and user files, stored in **MongoDB Atlas**, processed through
**Hadoop MapReduce and Apache Spark**, analysed, scored by machine-learning
models, turned into alerts, notifications and reports, and explored through a
React dashboard with 43 screens.

---

## Architecture at a glance

```
React 18 + TypeScript + Vite + Tailwind + Recharts + Leaflet
        │  HTTPS JSON (/api/v1/…), JWT access token in memory
        ▼
Django 5 + Django REST Framework  ──  drf-spectacular OpenAPI
        │
        ├─ middleware limits        per-client request budget → identical heavy
        │                           reads joined into one query → a cap on how
        │                           many aggregations may run at once
        ├─ MongoDB aggregation      trend buckets, coverage, the newest reading per
        │                           station and per-year group totals ($group /
        │                           $facet), so wide windows never cross the network
        ├─ common/analytics.py      compare, correlation, seasonality, anomaly
        │                           detection, and the fallback for any trend the
        │                           pipelines do not model            (shared kernel)
        ├─ apps/*                   auth·users·datasets·ingestion·climate·
        │                           processing·ML·alerts·reports·support·monitoring
        ▼
PyMongo ──► MongoDB Atlas           PRIMARY application database (no ORM,
                                     no relational fallback by design)
        │
        ├─ backend/data-pipeline/mapreduce  Hadoop Streaming mapper/reducer
        ├─ backend/data-pipeline/spark      PySpark 3.5 jobs → Parquet in the data lake
        │        local[*] when no cluster; spark://master:7077 when configured
        ├─ backend/ml                       feature build, training, evaluation, inference
        └─ Celery + Redis                   background jobs; in-process worker pool
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
| `backend/` | Everything the server owns |
| `backend/config/` `backend/common/` | Django settings; shared kernel — db, auth, analytics, tasks, data lake |
| `backend/apps/` | 11 feature modules: auth·users·datasets·ingestion·climate·processing·ML·alerts·reports·support·monitoring |
| `backend/scripts/` | `bootstrap_platform.py` (schema, regions, admin account) · `migrate_mongo.py` · `seed_real_data.py` · `provision_admin.py` · `recover_account_password.py` |
| `backend/data-pipeline/` | `mapreduce/` Hadoop Streaming jobs + local runner, `spark/` PySpark jobs, `schemas/`, `tools/` |
| `backend/ml/` | `features/`, `training/`, `evaluation/`, `anomaly/`, `inference/` |
| `backend/artifacts/` | Data-lake layers (`datalake/`), training logs, checkpoints, generated reports |
| `backend/sample-data/` | **Real retrieved climate only**: six city CSVs pulled from the Open-Meteo archive. Nothing reads this directory as an input default — the platform's observations come from MongoDB Atlas (NASA POWER connector or user uploads) |
| `backend/infrastructure/` | Dockerfiles, nginx site, Airflow DAG, Hadoop/Kafka/Spark config, backup/restore scripts |
| `frontend/` | Vite + React + TS app: `src/app` (router, nav), `src/pages`, `src/components`, `src/features`, `src/services`, `src/hooks`, `src/lib` |
| `docker-compose.yml` | backend · worker · scheduler · frontend · redis · optional Hadoop/Spark profile |
| `backend/.env` / `backend/.env.example` | Local secrets (git-ignored) and the documented setting list — read by `backend/common/env.py` |
| `LICENSE` · `NOTICE` · `TRADEMARKS.md` | MIT licence for the code (© Faakhir Memon), the third-party terms it bundles, and the name/mark policy the licence deliberately does not cover |

`backend/.data/` (uploads, generated reports, model artefacts) and
`backend/.data-lake/` (MapReduce/Spark outputs) are local runtime state:
git-ignored, recreated on demand, and safe to delete for a clean start.

---

## Quick start (no Docker required)

Prerequisites: Python 3.10–3.11, Node 20+, network access to NASA POWER for
seeding, and (for the real data path) a MongoDB Atlas cluster.

```bash
cp backend/.env.example backend/.env   # then set MONGODB_URI / DJANGO_SECRET_KEY / JWT_SIGNING_KEY
# Paste the Atlas connection string verbatim (mongodb:// shard list or mongodb+srv form),
# leave its <db_username>/<db_password> placeholders in place and fill the two keys below it:
#   MONGODB_ATLAS_USER=<atlas username>      MONGODB_ATLAS_PASSWORD=<atlas password>
.venv/Scripts/python.exe backend/scripts/bootstrap_platform.py   # collections, indexes, admin account
.venv/Scripts/python.exe backend/manage.py runserver 0.0.0.0:8000
.venv/Scripts/python.exe backend/scripts/seed_real_data.py       # real observations only
cd frontend && npm install && npm run dev                        # http://localhost:5173
```

`GET /api/v1/health` reports the database mode and names the environment key that
is missing when Atlas is not configured, so it doubles as the connection check.

#### Importing your own files

`.csv`, `.txt`, `.json`/NDJSON, `.xlsx`, `.xlsm`, `.xltx`, `.xltm`, `.xls`, `.xlsb`,
`.ods` (LibreOffice), `.parquet` and password-protected workbooks are uploaded from
**Datasets → Upload** in the app, mapped column-by-column, previewed and then written
into MongoDB by an ingestion job.

Encrypted uploads need `UPLOAD_FILE_PASSWORD` in `backend/.env`; macros in `.xlsm`/`.xltm` are
never executed. Upload formats are proved with real files — no fixture copies ship in
the repository.

The platform ships exactly one account: the administrator named by
`BOOTSTRAP_ADMIN_EMAIL`, whose password comes from `BOOTSTRAP_ADMIN_PASSWORD` in
`backend/.env` (git-ignored) or, when that key is left blank, from a strong password
generated once and written to `backend/.data/bootstrap-credentials.txt`. There is no
hardcoded password anywhere in the repository, and self-registration is switched off
by default (`ENABLE_SELF_REGISTRATION=false`).

If the credential file is not available, reset one account to a freshly generated
password:

```bash
.venv/Scripts/python.exe backend/scripts/recover_account_password.py --role administrator
```

Stop `runserver` first (the development store is per-process and would overwrite
the change), then sign in with the single printed password.

Container deployment, cluster profile, backups and Airflow are covered in
[`backend/infrastructure/README.md`](backend/infrastructure/README.md).

---

## Configuration and honesty rules

`backend/.env.example` documents every setting. The platform reports its own state
truthfully rather than assuming infrastructure exists:

- A `MONGODB_URI` that still contains a `<db_username>` / `<password>` placeholder is
  treated as **not configured** rather than sent to the server as a literal username;
  `/api/v1/health` says which key is missing.
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
  renders that instead of a number. When a series is cut at the returned-point
  limit, the explorer says how many periods the backend actually built.
- The map layer and the historical explorer are grouped inside MongoDB
  (`$project` → `$sort` → `$group` with `$first`, and `$sum`/`$avg`/`$min`/`$max`
  per group-year) instead of shipping 37,290 documents to Python. Both answers
  were checked field-by-field against the old pandas build before switching.

### One client cannot load the platform

Three limits stack on every `/api/` call, in this order:

| Limit | What it stops | Response when exceeded |
| --- | --- | --- |
| `RateLimitMiddleware` | volume — a per-client budget per minute (600 overall, `RATE_LIMIT_ANALYTICS` = 60 for `/climate/` and `/dashboard/`, 10 logins) | `429 rate_limited` + `Retry-After` |
| `RequestCoalescingMiddleware` | duplicates — an identical heavy read already running is **joined**, not re-run; one finished inside `REQUEST_COALESCE_WINDOW_SECONDS` is replayed from memory | `200` with `X-Request-Coalesced: coalesced` / `cached`, no second MongoDB read |
| `ANALYTICS_MAX_CONCURRENT` | pile-up — how many heavy aggregation pipelines may run per process | `429 server_busy` + `Retry-After`, so Atlas is never queue-fed |

Measured on the live store: eight identical anomaly scans fired together produced
**one** database read (26 s) and seven instant replays; the same scan asked for
again inside the window answered in 21 ms; twelve *different* heavy reads at once
let four run and refused eight. A write clears the replay cache, so a cached
answer can never hide the effect of an import — verified by re-reading after a
`PATCH`. The React client does its half: a GET that is already in flight is
shared, so a double-mounted component or an impatient refresh click sends one
request instead of several (eight refresh presses on the map = one round trip).

Security posture: JWT access (20 min) + rotating refresh (7 days) with reuse
detection, bcrypt password hashing, login lockout, server-side RBAC
(`administrator`, `climate_analyst`, `viewer`), capability checks on every
endpoint, the request limits above, an append-only audit trail, allow-listed
background tasks (no arbitrary command execution from the dashboard), upload
size/row/type limits, CSV/Excel formula-injection neutralisation, and no
stack traces in API responses. Credentials and connection strings are never
logged and never committed. The browser only ever talks to the Django API.

---

## Feature map

**Public** — landing, about, capabilities, insights preview, data sources, help,
privacy, terms.
**Auth** — login, optional registration (viewer-only, and off by default),
forgot/reset password, profile & preferences.
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

| Topic | Path |
| --- | --- |
| Layout, quick start, configuration and honesty rules | this file |
| Deployment, cluster profile, backups, Airflow | `backend/infrastructure/README.md` |
| MapReduce streaming contract and local runner | `backend/data-pipeline/mapreduce/README.md` |
| ML methodology & conventions | `backend/ml/README.md` |
| Sample data provenance (real, provider-retrieved) | `backend/sample-data/README.md` |
| Every environment setting and its default | `backend/.env.example` |
| Live schema, collections and field ranges | `/api/v1/health`, `/api/v1/system/status` and the module docstrings in `backend/common/` |

---

## Licence and trademarks

The code in this repository is released under the **MIT Licence**, copyright
(c) 2026 **Faakhir Memon** — see [`LICENSE`](LICENSE). `NOTICE` lists the
third-party components it bundles, which stay under their own terms.

The **name and marks are not part of that grant**. "EarthScape" and
"EarthScape Climate Agency" identify this implementation and belong to the mark
owner; [`TRADEMARKS.md`](TRADEMARKS.md) states what you may say about a
deployment and what a fork must not be called. A modified copy should be renamed
before distribution.

Neither document transfers any rights over stored data. Observations, forecasts
and uploads remain governed by the agreement under which they were collected.

