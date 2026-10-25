# Infrastructure

Deployment, cluster, scheduling and backup assets for the EarthScape Climate
Agency platform.

> **Status of these files in this delivery.** The Docker images, the nginx site and
> the Airflow DAG were **authored and reconciled against the code, but not built or
> run** here: Docker and Airflow are not available in the development environment.
> Reconciled means every path, port, environment key, volume and API call in these
> files was checked against what the application actually does — the drifts that
> came out of that pass are recorded and fixed as defects 9, 10 and 11 in
> the repository `README.md`. Nothing in this directory should be presented as
> "verified running" unless you have run it.
>
> The verified, supported way to run the platform today is the **local no-Docker
> path** at the bottom of this file.

---

## Contents

| Path | Purpose |
| --- | --- |
| `docker-compose.yml` (repo root) | One-command stack: backend, Celery worker, beat scheduler, frontend, Redis, and optional Hadoop/Spark |
| `docker/Dockerfile.backend` | Python 3.11 + OpenJDK 17 image for the Django API and the workers |
| `docker/Dockerfile.frontend` | Multi-stage Node build served by nginx |
| `nginx/earthscape.conf` | Production server block: SPA fallback, `/api/` proxy, security headers, upload limit |
| `airflow/dags/earthscape_daily_sync.py` | External-scheduler DAG that drives the platform over REST only |
| `backup/backup.sh`, `backup/restore.sh` | `mongodump` snapshot + checksum-verified restore |

---

## 1. Compose stack

From the repository root:

```bash
cp .env.example .env      # then set real values, see "Required settings"
docker compose up --build
```

Services started by default:

| Service | Host port | Notes |
| --- | --- | --- |
| `frontend` | `8080` (override `WEB_PORT`) | nginx serves the built SPA and proxies `/api/` to `backend` |
| `backend` | not published | reached through nginx; healthcheck hits `/api/v1/ready` |
| `worker` | not published | `celery -A apps.monitoring.celery_tasks worker` |
| `scheduler` | not published | same module, `celery beat` |
| `redis` | not published | Celery broker/results and optional cache |

Named volumes: `backend-data` (uploads, reports, exports and the model artefacts,
which live under `MEDIA_ROOT`), `data-lake` (the `raw/validated/processed/
analytics/ml-features/exports` tree the pipeline writes), and `redis-data`.

Stored-file locations are recorded as **keys relative to `MEDIA_ROOT`**, not as
absolute paths — see `backend/common/storage_paths.py`. That is what lets a
snapshot taken on a laptop restore correctly inside a container, and it is why the
artefact directory is mounted at `/app/backend/.data/ml-artifacts` inside the
`backend-data` volume instead of a separate one.

### Cluster profile (Hadoop + Spark)

Hadoop and Spark are **not** started by default, because claiming a cluster is
up when it is not is worse than not having one:

```bash
docker compose --profile cluster up --build
```

That adds `namenode` (HDFS UI on `http://localhost:9870`, RPC `8020`),
`datanode`, `spark-master` (`8085` for the Spark UI on the host, `7077` for
workers) and `spark-worker`. With the profile running, set in `.env`:

```
HDFS_ENDPOINT=http://namenode:9870
HDFS_ROOT=/user/earthscape
DATA_LAKE_BACKEND=hdfs
SPARK_MASTER=spark://spark-master:7077
```

The web tier reads these and reports the engines as *available* on the admin
Health page. Without them it reports them *not configured* — the UI never
fabricates cluster activity.

### MongoDB is not containerised

The application database is **MongoDB Atlas**, reached over TLS with
`MONGODB_URI`. Compose deliberately does not ship a `mongod` container in the
default path; there is a commented block in `docker-compose.yml` if you want a
throwaway local `mongod` for offline testing. Leave `FORCE_ATLAS_CONNECTION=true`
in any environment that faces real users so the code cannot silently fall back
to the in-process development store.

---

## 2. Required settings

Values live in `.env` (git-ignored). `.env.example` documents every key.
Never commit real credentials, and never put an Atlas connection string in a
commit, an issue, or a screenshot.

| Key | Required for | Effect if missing |
| --- | --- | --- |
| `MONGODB_URI` | production use | API refuses to start when `FORCE_ATLAS_CONNECTION=true` |
| `MONGODB_DATABASE` | both | defaults to `earthscape_climate` |
| `DJANGO_SECRET_KEY` | both | refuse to start |
| `JWT_SIGNING_KEY` | both | refuse to start |
| `FORCE_ATLAS_CONNECTION` | production | should be `true` |
| `REDIS_URL` / `CELERY_BROKER_URL` | Celery | jobs run on the in-process thread executor, and the UI says so |
| `HDFS_ENDPOINT` | HDFS writes | data lake stays on local disk |
| `SPARK_MASTER` | cluster Spark | PySpark runs `local[*]` |
| `KAFKA_BROKERS` | event stream | ingestion stays pull-based (`KAFKA_ENABLED=false`) |
| `AIRFLOW_ENABLED` | DAG ownership | `false` by default; the platform's own scheduler is used |
| `MODEL_ARTIFACT_DIR` | relocating trained models | defaults to `<MEDIA_ROOT>/ml-artifacts` |

`backend/scripts/bootstrap_platform.py` creates the indexes and the first accounts and
prints their **randomly generated** passwords once to
`backend/.data/bootstrap-credentials.txt`. There is no default or hardcoded
administrator password anywhere in this repository. That file is git-ignored and
excluded from the delivered archive, so an operator who has the data directory but
not the passwords uses
`backend/scripts/recover_account_password.py` (see the installation guide, §2.4) to
mint one fresh password per account.

---

## 3. Backup and restore

Backups use `mongodump` against Atlas and require network access plus the
connection string:

```bash
MONGODB_URI="mongodb+srv://…" ./infrastructure/backup/backup.sh
```

The script refuses placeholder URIs, writes one gzip archive per run plus
`SHA256SUMS`, and keeps the 14 newest. Restore is deliberately destructive and
guarded:

```bash
./infrastructure/backup/restore.sh <archive> --yes-destroy-data
python backend/scripts/bootstrap_platform.py    # re-apply schema after --drop
```

In local-development mode (no Atlas) the platform does not have a server to dump:
the document store is one JSON file per collection under
`backend/.data/localdb/`. `backup.sh` says so and points at that directory rather
than pretending a dump succeeded — to back the development store up, stop the API
(it flushes its in-process copy on every request, so a running server will
overwrite files you copy), copy `backend/.data/localdb/`, and copy it back to
restore.

---

## 4. Airflow

`airflow/dags/earthscape_daily_sync.py` is written for an **external** Airflow
installation. It calls the platform's REST API only — it never imports Django
code and never touches the database directly. The nightly chain is
`sync_nasa_power` + `sync_live_provider` → `rebuild_analytics` → `evaluate_alerts`,
driving `POST /api/v1/ingestion/jobs`, `POST /api/v1/processing/` and
`POST /api/v1/alerts/scan`, and polling each job document until it reaches a
terminal status. It needs `EARTHSCAPE_API_TOKEN`: a bearer token for a service
account with the **climate_analyst** (or administrator) role, because both write
endpoints reject every other role. Get it from `POST /api/v1/auth/login` rather
than from a stored password. If `AIRFLOW_ENABLED` is `false` the admin Health page
reports Airflow as *not configured*, even if the DAG file exists.

The DAG's request and response shapes are taken from the running API, not from
memory: each one was checked against the live service during the packaging pass,
and the corrections are recorded as defect 10 in
the repository `README.md`. What has **not** been done is executing it on a
scheduler — there is no Airflow on this machine, so the task-graph wiring, the
`schedule_interval` and Airflow's own retry semantics are unverified.

---

## 5. Local run without Docker (verified path)

```bash
# 1. Backend — from the repository root, using the checked-in venv
#    (`bootstrap_platform.py` creates the collections, indexes and accounts;
#     there are no Django SQL migrations because the datastore is a document db)
.venv/Scripts/python.exe backend/scripts/bootstrap_platform.py
.venv/Scripts/python.exe backend/manage.py runserver 0.0.0.0:8000

# 2. Data — real NASA POWER observations, no invented rows
.venv/Scripts/python.exe backend/scripts/seed_real_data.py

# 3. Frontend
cd frontend && npm install && npm run dev     # http://localhost:5173, proxies /api to :8000
```

Open `http://localhost:5173`, sign in with a bootstrapped account, and the
EnvironmentBanner states honestly whether the document store is Atlas or the
labelled local fallback, and which processing engines are unavailable.
