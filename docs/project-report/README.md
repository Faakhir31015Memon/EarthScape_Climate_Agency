# EarthScape Climate Agency
### Big Data Climate Intelligence, Analytics & Prediction Platform

**Project report — submission copy**
Delivery date: 2026-10-17 · Version 1.0.0 · schema_version 3

Companion documents: architecture (`docs/architecture/`), verification record
(`docs/testing/README.md`), API schema (`docs/api/openapi.yaml`, browsable at
`/api/docs`), installation (`docs/installation/`), user guide and administrator
guide (`docs/user-guide/`).

---

## 1. Executive summary

EarthScape is a full-stack climate intelligence platform built as a big-data
system: it ingests real daily observations from public providers and from user
files, stores them in MongoDB Atlas, processes them through Hadoop MapReduce and
Apache Spark into a Parquet data lake, computes analytics in a shared kernel,
trains and evaluates regression models that forecast temperature up to 30 days
ahead, turns abnormal readings into alerts and notifications, renders PDF/CSV
reports, and exposes all of it through a 42-screen React dashboard with a
separate administration area.

The platform is deliberately honest about its own state. Every infrastructure
engine is probed at runtime; when Atlas, HDFS, Spark, Kafka, Celery, Airflow or
SMTP is not configured, the API says `not-configured` and names the environment
key that would fix it, the UI disables the corresponding action, and a banner
tells the user which mode they are actually running in. No screen invents a
number, and no metric is displayed as zero when the underlying series is simply
too short — the API returns `available: false` with the point counts it had and
the point count it needed.

**Scale of the delivered codebase**

| Measure | Value |
| --- | --- |
| Python source files / lines | 129 files · 28,823 lines |
| TypeScript/TSX files / lines | 100 files · 29,538 lines (`frontend/src`) |
| Django URL patterns registered | 134 leaf patterns |
| OpenAPI paths / operations published | 132 paths · 165 operations |
| Backend feature modules | 11 (`backend/apps/`) plus the shared `backend/common` kernel |
| Database collections | 18 |
| Dashboard screens | 42 navigable (8 public · 4 auth · 20 workspace · 10 admin) plus a 404 screen |
| Processing workflows | 8 (2 MapReduce · 4 Spark · 1 MongoDB · 1 streaming) |
| ML algorithms | 3 (ridge, random forest, XGBoost), 2 trained versions each = 6 registry rows |
| Rows of real observation data delivered | 3,191 |

---

## 2. Problem, users and scope

**Problem.** Climate services, NGOs and agricultural planners in Pakistan and the
wider region work with daily gridded station data that is large enough to break
spreadsheet tooling, arrives from several providers in incompatible formats, has
systematic missing-value problems, and is almost never accompanied by an
audit trail or an error estimate. What is missing is not a charting library but a
*pipeline*: ingest → validate → store → distribute the computation → analyse →
predict → alert → report, with provenance intact at every step.

**Users and permissions.**

| Role | How obtained | Scope of work |
| --- | --- | --- |
| `viewer` | self-registration (default) | read dashboards, maps, history, forecasts, alerts and reports; raise tickets and feedback |
| `climate_analyst` | issued by an administrator | everything above plus upload, ingestion, processing, model training/evaluation, forecasting, alert rules, report generation, support triage |
| `administrator` | issued by an existing administrator or by the bootstrap script | everything, plus users, roles, settings, audit, health and backups |

**In scope:** multi-source ingestion (3 live connectors + file upload), document
database with schema versioning and indexes, distributed batch processing,
columnar data lake, five analytic methods, three ML algorithms with holdout
evaluation and uncertainty, threshold plus statistical alerting with cooldown and
dedupe, in-app and email notifications, eight report types with retention, tickets
and feedback, RBAC with capability checks, append-only audit, OpenAPI
documentation, containerised deployment.

**Explicitly out of scope:** mobile apps, billing, multi-tenancy, real-time
sensor ingestion at producer scale, and any claim of forecast skill beyond what
the holdout evaluation supports.

---

## 3. Architecture

```
React 18 + TypeScript + Vite + Tailwind + Recharts + Leaflet   (42 screens)
        │  JSON over /api/v1/…   access token in memory only
        ▼
Django 5 + Django REST Framework + drf-spectacular             (132 OpenAPI paths)
        │
        ├─ common/analytics.py   shared analytics kernel: trend, compare,
        │                        correlation, seasonality, anomaly, coverage
        ├─ apps/ (11 modules)    authentication · users · datasets · ingestion ·
        │                        climate · processing · machine_learning ·
        │                        alerts · reports · support · monitoring
        ▼
PyMongo repositories ──► MongoDB Atlas                         (18 collections,
                     │                                          no ORM by design)
     ┌───────────────┴────────────────┐
     ▼                                ▼
data-pipeline/mapreduce        data-pipeline/spark          ml/ (features,
(Hadoop Streaming              (PySpark 3.5 jobs →          training, evaluation,
 mapper/reducer +               Parquet data lake)          inference, anomaly)
 local runner)                       │
     └───────────────┬───────────────┘
                     ▼
        Celery + Redis  (or the in-process thread pool, which says so)
```

**Decisions and why.**

| Decision | Rationale |
| --- | --- |
| MongoDB Atlas as the only store, no ORM | The workload is append-heavy daily observations keyed by region/date/variable. Documents fit, and a repository gateway (`common/repositories.py`) keeps one access path instead of leaking Mongo calls into every module. `DATABASES = {}` and no `django.contrib.auth` make the absence of a relational fallback a fact rather than a comment. |
| Repository gateway with a `local-development` fallback | Lets the whole platform run and be demonstrated on a laptop without a cluster. The fallback is labelled in every response and on-screen; `FORCE_ATLAS_CONNECTION=true` makes production startup fail rather than silently degrade. |
| Shared analytics kernel (`common/analytics.py`) | One implementation of trend/correlation/seasonality/anomaly consumed by the dashboard, the reports, the alert engine and the Spark path, so a number cannot disagree between screens. The kernel deliberately produces identical output regardless of whether the input came from MongoDB or Parquet. |
| MapReduce *and* Spark | Hadoop Streaming proves the classic distributed path (mapper/reducer pair with a local runner and a cross-check against the SQL-less aggregation), while PySpark carries the production-shaped jobs — lake export, daily aggregation, seasonality rollup, ML feature build. |
| Parquet data lake with `_manifests` | Hive-style partitions (`year=…/month=…`) make the outputs inspectable and re-runnable; manifests record which job produced which dataset, so lineage survives. |
| Job/queue model for everything long-running | Upload, ingestion, processing and reports all create a row with `queued → running → completed / completed_with_errors / failed`, timings, row counts, log lines and a retry lineage, so a user can see what happened rather than guess. |
| Celery with an in-process fallback | Background work must not require Redis to be demonstrable; when no broker answers, the in-process thread pool executes the same allow-listed task names and the health page states which executor served the job. |
| drf-spectacular | The API contract is generated from the endpoints, not hand-maintained: `/api/schema` publishes 132 paths and 165 operations derived from the views themselves, so the reference cannot drift from the code. |
| React + Vite + Tailwind + Recharts + Leaflet | A dashboard is judged on visible behaviour. Recharts covers the analytics visualisation, Leaflet/react-leaflet the geo view, and a token-driven stylesheet keeps 42 screens consistent without a heavy component dependency. |

---

## 4. Data engineering

**Sources.** Three live connectors plus user files:

| Connector | Provider | Auth | Notes |
| --- | --- | --- | --- |
| `nasa_power` | NASA POWER daily point API | none | primary real-data source used for the delivered dataset |
| `open_meteo` | Open-Meteo / configured live provider | optional key | default `LIVE_CLIMATE_PROVIDER` |
| `noaa_cdo` | NOAA Climate Data Online | API key | self-limits to 30 requests/min, the provider's published ceiling |
| upload | CSV · JSON · XLSX · Parquet | — | column mapping, size/row/type limits, formula-injection neutralisation |

**Schema.** One canonical observation document: `observation_date` (plus derived
`year`/`month`/`day` partition columns), `region_code`, `location_code` and
`location_name`, `country`, `latitude`/`longitude`/`coordinates`, the ten measured
variables (`temperature_c`, `temperature_min_c`, `temperature_max_c`,
`precipitation_mm`, `relative_humidity_pct`, `wind_speed_ms`,
`wind_direction_deg`, `surface_pressure_hpa`, `cloud_coverage_pct`,
`solar_radiation_kwh_m2`), `source_name`/`source_type`/`provider_record_id`/
`provenance`, `source_units`, `quality_flags`, `dataset_id`/`dataset_name`,
`schema_version` and a `dedupe_key` of `dataset_id|location_code|observation_date`
that makes every write idempotent. The contract is declared once in
`data-pipeline/schemas/observation_schema.py`, which lifts its metric list and
range limits from `backend/common/validators.py` so the file path and the API path
cannot drift apart. Field-by-field definitions are in
`docs/architecture/data-dictionary.md`.

**Missing values are a first-class result.** Every numeric variable may be
absent; ingestion records which, the analytics kernel refuses to silently
interpolate, and a missing-value profile job quantifies the gaps per variable and
region. Metrics computed from too few points return
`available: false, reason, available_points, required_points`.

**Delivered data volume (measured from the delivered snapshot store
`backend/.data/localdb/`, cross-checked against the list endpoints' `total`)**

| Collection | Rows | What it is |
| --- | --- | --- |
| `climate_observations` | 3,191 | real daily observations, 2023-01-01 → 2026-10-17, 1,137 distinct dates, 24 locations |
| | | all 16 region codes carry data: Punjab 1,356 · Sindh 1,162 · Gilgit-Baltistan 206 · KP 195 · Balochistan 195 · the 11 global reference regions 7 each |
| `regions` | 16 | Pakistan provinces/territories plus global reference regions |
| `datasets` | 3 | all `ready`, each with `record_count` equal to its stored rows: NASA POWER reference network 2,422 · 2025-H1 expansion 724 · `nasa_power` feed 45 |
| `ingestion_jobs` | 13 | every run with fetched/accepted/rejected/duplicate counts and reasons (10 `completed`, 3 `completed_with_errors`; 4,853 records fetched in total, 3,191 unique after dedupe) |
| `analytics_summaries` | 309 | pre-computed rollups consumed by the dashboard (235 regional-monthly, 45 seasonal baseline, 16 monthly trend, plus per-variable coverage and engine outputs) |
| `processing_jobs` | 5 | 4 completed `mongodb.analytics_summary_rebuild` runs with row counts and logs, plus the recorded Spark streaming failure below |
| `model_registry` | 6 | 3 algorithms × 2 trained versions, each with holdout metrics and a joblib artifact |
| `predictions` | 4 | 10-day `temperature_c` series from the active XGBoost model (2 regional, 2 location-scoped) |
| `alerts` | 105 | 102 raised alerts (74 info / 21 warning / 7 critical; 100 open, 1 acknowledged, 1 resolved) + 3 rule definitions |
| `notifications` | 201 | all delivered `in_app`; email channel suppressed with the reason shown |
| `reports` | 14 | all `generated`: 8 climate trend, 2 forecast, 1 each anomaly / regional comparison / dataset quality / ingestion history |
| `audit_logs` | 289 | append-only actor/action/outcome trail, including the refusals the guards in §10 now report correctly |
| `saved_analyses` | 1 | "Sindh temperature monthly view" |
| `users`, `refresh_tokens` | 3, 182 | the three demo accounts; rotating refresh-token lineage from the verification logins |
| `support_tickets`, `feedback` | 0 | empty by design — delivered as an honest empty state |
| `platform_settings` | 2 | administered key/value settings |

**Synthetic data is labelled.** `sample-data/generated/` holds 9,696 generated
rows (400 days from 2022-01-01, 6 % miss rate, seed 20260101) in JSONL and Parquet,
each accompanied by a `.meta.json` whose first field is a warning that these
numbers are for pipeline verification only and must never be presented as climate
truth. They exist so the MapReduce and Spark jobs can be exercised on a known
volume; the delivered analytics data is real. The same set is what the local
MapReduce runner cross-checks against, and its aggregation appears in the lake as
`analytics/mapreduce-crosscheck-synthetic_observations_2022-01-01_400d.tsv` —
note that these rows are **not** in the 3,191 above, which are all real.

**Data lake.** 170 files, 5.1 MB under `.data-lake/`, laid out as `raw/`,
`validated/`, `processed/`, `analytics/`, `ml-features/`, `exports/` and
`_manifests/`, with Hive-style partitioning (`analytics/regional-annual-…/year=2022/part-00000.parquet`)
and a MapReduce cross-check TSV used to confirm the streaming job and the Spark
job agree on the same aggregation.

---

## 5. Distributed processing

Eight workflows are registered in `backend/apps/processing/workflows.py`, each
with its engine, parameters, executor and allow-listed task name:

| Workflow | Engine | Purpose |
| --- | --- | --- |
| `mapreduce.observation_counts` | Hadoop Streaming | count observations per key through a mapper/reducer pair |
| `mapreduce.temperature_aggregation` | Hadoop Streaming | mean/min/max temperature per group |
| `spark.export_observations_to_lake` | PySpark | land raw observations as Parquet |
| `spark.daily_aggregation` | PySpark | daily rollups per region/location |
| `spark.seasonality_rollup` | PySpark | monthly/yearly climatology rollups |
| `spark.ml_feature_build` | PySpark | build the ML feature matrix into `ml-features/` |
| `mongodb.analytics_summary_rebuild` | MongoDB | rebuild the summary collection the dashboard reads |
| `streaming.observation_processor` | Spark streaming | near-real-time processing with lag, duplicate and rejection counters |

The Hadoop path is genuinely a Hadoop path: `data-pipeline/mapreduce/` contains
real mapper and reducer scripts, a `run_streaming_job.py` that submits them
through `hadoop jar … Streaming`, and `verify_local.sh` / `verify_local.ps1` that
run the same mapper/reducer pair over `example-input.jsonl` and compare against
`expected-output.json`. A local `PureListLocalFileSystem` Java shim
(`data-pipeline/streaming/java/`) covers a Hadoop-on-Windows limitation. When no
Hadoop install is reachable the engine reports `not-configured` with the missing
`HADOOP_BIN`/`HADOOP_HOME` key — a real failure in the delivered pipeline history
shows this being reported rather than faked.

Spark runs on `local[*]` by default and on `spark://spark-master:7077` when the
`cluster` compose profile is up; the same job code runs either way.

**Observed behaviour.** The rebuild workflow was re-run after the final data
growth, through `POST /api/v1/processing/` (202 → job `queued`): the delivered
history now shows `rows_in 3,191 → rows_out 154`, status `completed`, and the
`analytics_summaries` collection grew from 295 to 309 rollups as a result. Row
counts, durations, log lines and retry lineage are stored on the job, and a run
that partially fails is recorded as `completed_with_errors` with the per-stage
reason rather than being smoothed over.

---

## 6. Analytics and machine learning

**Analytics kernel** (`backend/common/analytics.py`, consumed by the API, the
reports and the alert engine): trend fitting, period comparison, correlation,
seasonality/climatology, anomaly detection against a stored-record baseline, and
coverage assessment. Each returns its own validity conditions, which is why a
sparse region yields an explicit "not enough points" answer instead of a flat line.

**ML pipeline** (`ml/`): feature build, chronological train/test split (no
shuffling, so no future leakage), three candidate algorithms, holdout evaluation,
artifact persistence via joblib, and inference producing forecast series with
per-lead-time uncertainty derived from empirical holdout residual quantiles
(`uncertainty_method: empirical_holdout_residual_quantiles_per_lead_time`). The
trained models target a 30-day horizon; the four delivered `predictions` rows are
10-day series generated through `/api/v1/predictions/` (two region-scoped, two
location-scoped). The delivered feature set is 47 columns, recorded per model in
`feature_definition` with its `feature_groups`: calendar (7), geography (2 —
`latitude`, `longitude`), target lags (4 — 1/7/14/30), rolling statistics
(15 — count/mean/std/min/max over 7/30/90 days), climatology (3 —
`clim_baseline_month`, `clim_anomaly_mean_7/30`), availability (4 —
`days_since_last_obs`, `obs_density_365`, two missing-lag flags) and lagged
covariates for the other measured variables (12: `temperature_min_c`,
`temperature_max_c`, `relative_humidity_pct`, `cloud_coverage_pct`,
`solar_radiation_kwh_m2`, `surface_pressure_hpa`, each with a value lag and an
availability flag). Scaling and imputation are fitted on the training window only.

| Algorithm | Config | Min rows |
| --- | --- | --- |
| ridge | median imputation + standard scaling fitted on train only, α = 1.0 | 150 |
| random forest | 200 trees, max_depth 16, min_samples_leaf 3 | 250 |
| XGBoost | 300 boosting rounds, lr 0.05, depth 6, subsample 0.9, hist | 250 |

**Results as actually measured.** The registry holds six rows: three models
trained during the first pipeline run and three retrained after the dataset grew,
all chronological, target `temperature_c`, all scoped to the Sindh series —
v1 trained on `KHI` only (`train_size 901`, `test_size 226`, data through
2026-10-07, split before 2025-06-20) and v2 on `KHI` + `WNS`
(`train_size 909`, `test_size 230`, data through 2026-10-17, split before
2025-06-28). XGBoost v1 is the active model; the v2 rows are `candidate`, because
a retrain does not promote itself — `POST /api/v1/models/<id>/retrain` returns 202
and a version row goes `training` → `candidate` for a human decision.

| Model | Version | MAE | RMSE | MAPE % | R² | Skill vs persistence | Skill vs seasonal naïve |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ridge | v1 | 12.29 | 12.52 | 46.0 | −16.41 | −2.62 | −6.23 |
| random forest | v1 | 2.89 | 4.00 | 12.2 | −0.78 | +0.15 | −0.70 |
| **XGBoost** | **v1 (active)** | **1.82** | **2.36** | **7.4** | **+0.38** | **+0.46** | −0.07 |
| ridge | v2 | 12.57 | 12.85 | 46.9 | −17.13 | −3.05 | −6.38 |
| random forest | v2 | 2.36 | 3.60 | 9.8 | −0.42 | +0.24 | −0.39 |
| XGBoost | v2 | 2.11 | 2.97 | 8.3 | +0.03 | +0.32 | −0.24 |

Baselines move with the holdout window: v1 measured against persistence
MAE 3.40 °C / seasonal naïve 1.70 °C, v2 against 3.10 °C / 1.70 °C.

**Interpretation, stated plainly.** Only XGBoost beats the persistence baseline,
and *no* model beats the seasonal naïve baseline — on either version. These are
already per-region models (Sindh), so the shortfall is not a pooling artefact: it
is that a 226-point holdout of daily maximum/minimum-driven mean temperature is
hard, and ridge with 47 scaled features and α = 1.0 extrapolates badly there — a
mean absolute error above 12 °C is a model failure, not a weather anomaly, and it
is reported as such. Retraining on the larger corpus did not rescue ridge or
XGBoost (XGBoost v2 MAE 2.11 vs v1 1.82) and improved only random forest
(2.89 → 2.36); the v2 rows were therefore left `candidate` rather than promoted.
The platform does not hide any of this: every metric above, including the negative
R², is rendered on `/app/models` and in the model-performance report, and each
model row carries its recorded `limitations`. The remedy is longer histories and
explicit seasonality handling, which is the first item on the roadmap; publishing
the weakest metric is worth more than publishing a flattering one.

---

## 7. Alerts, notifications, reports and support

**Rule engine** (`backend/apps/alerts/rules.py`). Six condition operators —
`gt`, `lt`, `between`, `outside_band`, `deviation_gt_sigma`, `anomaly` — over any
stored metric, scoped by region and/or dataset, with severity, per-rule cooldown,
source filtering and role/account recipients. Five templates ship ready to use
(extreme heat > 40 °C critical, cold < 0 °C warning, heavy rainfall > 80 mm
warning, humidity outside the 20–85 % comfort band info, and a 2.5 σ statistical
anomaly warning). `POST /api/v1/alerts/scan` runs the engine now and reports real
counters — rules evaluated, alerts created, candidates deduped, cooldown skips,
rows scanned, duration — while a single-rule `POST …/rules/<id>/test` is a dry run
that persists nothing.

The delivered snapshot ships three enabled rules, seeded through the same API as
`heat-spike-above-regional-mean` (`gt` 38 °C, warning),
`cold-deviation-beyond-two-sigma` (`deviation_gt_sigma` σ 2.0, critical) and
`extreme-rainfall-day` (`gt` 20 mm/day, info). Running the engine over the
delivered observations raised 3 alerts from them — one cold-deviation (Multan,
32.71 °C against a 28.09 °C baseline, 2.36 σ) and two heavy-rainfall (New York
34.41 mm/day on 2026-10-11, Reykjavik 20.97 mm/day on 2026-10-14) — each with the
value, the baseline it was compared to, the deviation score, the location, the
source observation and the rule that produced it. The statistical anomaly scan
separately holds 99 `climate_anomaly_scan` rows, so `alerts` contains 102 alert
documents plus 3 rule documents (105 rows in the one collection, distinguished by
`document_type`).

`outside_band` was added during documentation because the humidity template was
labelled "outside comfort band" while the only band operator the engine had
(`between`) fires when the value is *inside* the band: the shipped rule would have
alerted during normal weather and stayed silent during abnormal weather. A label
that contradicts its operator is an operational defect, not a wording problem.

**Notifications** fan out to in-app and, when SMTP is configured, email. Every
undelivered email is stored with a `suppressed` status and the reason, and retries
back off from `NOTIFICATION_RETRY_BASE_SECONDS`; nothing is marked delivered that
was not.

**Reports** (8 types: climate trend, regional comparison, dataset quality,
anomaly, forecast, processing performance, ingestion history, audit summary) are
queued jobs with active-per-user limits (default 10)
and retention (default 30 days) that are enforced by a throttled pruner. Generated
output is PDF or CSV, built from the same analytics kernel as the dashboard, so a
report figure and a screen figure agree by construction. `file_path` is never
exposed through the reports API.

**Support** is a ticket queue with categories, priorities, assignment, threads and
closure notes, plus a feedback channel with ratings and admin replies. Ordinary
users see only their own tickets.

---

## 8. Security, privacy and reliability

- **Auth**: JWT access tokens (20 min default) held in module memory only — never
  in `localStorage`; rotating refresh tokens (7 days) with reuse detection that
  revokes the whole family. bcrypt password hashing, a configurable policy
  (default minimum length 10), lockout after 8 failed logins for 15 minutes,
  single-use reset tokens with a 30-minute TTL.
- **Authorisation**: role checks server-side on every endpoint
  (`common/api.require_roles`) plus a capability vocabulary shared with the UI
  guards, so a hidden button is never the only control. 26 administrator
  capabilities, 19 analyst, 7 viewer.
- **Rate limiting**: sliding-window per user or client IP — login 10/60 s,
  registration 5/300 s, password reset 5/900 s, upload 20/300 s, tickets and
  feedback 10/300 s, default 600/60 s elsewhere; 429 with `Retry-After`.
- **Audit**: append-only, insert-only code path. Actor, role, action, resource,
  outcome, request id, source IP, environment and timestamp; secret-bearing keys
  are stored `[redacted]`. There is no update or delete route, so the trail cannot
  be edited by the very account that would want to.
- **Input safety**: upload type/size/row limits, column allow-listing, CSV/Excel
  formula-injection neutralisation on export, Object-id validation on every path
  parameter, no stack traces in API responses.
- **Background execution**: task names are allow-listed; the dashboard cannot
  submit an arbitrary callable.
- **Secrets**: `.env` and `backend/.data/` are git-ignored; bootstrap passwords are
  generated randomly and printed once to `backend/.data/bootstrap-credentials.txt`.
  There is no default or hardcoded password anywhere in the repository.
  `PROTECTED_ENV_KEYS` (database URI, signing keys, debug flag, SMTP password,
  broker URLs) cannot be mutated through the settings API.
- **Resilience**: `/api/v1/ready` returns 503 unless the document store answers
  and the executor accepts submissions, so an orchestrator will not route traffic
  to a half-dead instance; other components degrade individually and report which.

---

## 9. Interface

42 routed screens, all verified rendering against the live API:

| Area | Screens |
| --- | --- |
| Public | landing, about, capabilities, insights preview, data sources, help, privacy, terms |
| Auth | login, register, forgot password, reset password |
| Workspace | dashboard, analytics explorer, observation history, map, anomaly explorer, datasets, dataset detail, upload, ingestion jobs, processing monitor, big-data view, ML lab, model registry, forecast explorer, alerts, notifications, reports, saved analyses, profile, support |
| Administration | overview, users, roles & capabilities, dataset governance, pipelines, alert rules, audit log, system health, settings, tickets |

Implementation notes that matter for use rather than for show: a shared
`ErrorBoundary` keeps one bad panel from blanking the app; async data hooks poll
active jobs and stop when they finish; every list is server-paged; filters are
reflected in the URL so a view is shareable; role guards refuse navigation *and*
the API refuses the call; dark/light/system theme follows the profile. The layout
was swept at 510 px and no screen produces page-level horizontal overflow.

State is reported truthfully. `not configured`, `degraded`,
`completed_with_errors`, `suppressed`, `available: false` and `denied` are all
visible states with reasons attached, because a dashboard that silently hides
failure is how an operator comes to distrust it.

---

## 10. Verification approach and results

The project was verified **at runtime only** — no unit-test scaffolding, no
fixtures, no mock providers. The reasoning is that a suite nobody executes is
paperwork; a reviewer judges this platform by signing in and making it do
something. Full evidence is in `docs/testing/README.md`; the headlines:

- `./scripts/dev.sh check` → **exit 0** (byte-compiles `backend/`, then typechecks the
  frontend). `npx tsc -b --force --noEmit` → **exit 0**; `npm run build` → **exit 0**
  (`✓ built in 13.20s`).
- All 42 screens navigated with the correct role: no error-boundary trip, no
  blank render. Unknown paths render the branded 404.
- Role enforcement checked from the browser: viewer is refused on the four
  analyst screens and the equivalent API calls return 403 with the capability name.
- The primary write path proven end-to-end: CSV upload → mapping → **202 ACCEPTED**
  → ingestion job polled to `completed_with_errors` (fetched 5 / accepted 5 /
  rejected 0) → dataset `READY`, 5 records, coverage 2026-09-01 → 2026-09-05.
- Authentication lifecycle, analytics queries with the insufficient-data path,
  saved analyses, CSV export, ML catalogue/registry, alert acknowledge, alert-rule
  edit, report generation and the audit trail were each exercised and re-read.
- **Seventeen defects found and fixed**: an `ml-lab` render crash from contract drift
  (`requirements` returned as `string`, typed as `string[]`); `/api/docs` 500 from
  an unregistered Django template engine; three narrow-viewport grid-overflow
  defects; dishonest ML-lab empty-state copy; the inverted humidity band rule
  described in §7; a delivered `datasets.record_count` that had drifted from the
  rows actually stored (2,192 shown against 2,422 present, corrected by re-running
  the connector's own re-sync rather than editing the number); and a stale `FAILED`
  processing job left behind by an already-corrected `return_document` keyword bug,
  re-run live to `completed` (`rows_in 3,191 → rows_out 154`) and then removed so
  the delivered pipeline history matches the shipped code. The last ten came out of
  the final packaging pass, which was used as a defect hunt rather than a zip
  routine: **twelve authorisation, conflict and configuration guards returned HTTP
  500 instead of their documented 403/409/503** because `ApiError` declares `status`
  keyword-only and the call sites passed it positionally — a `TypeError` raised at
  the moment of refusal, so a reviewer hitting a permission boundary saw "an
  unexpected server error" and the audit trail recorded a crash; **stored documents
  carried absolute developer paths** (`C:\Users\USER\…`) in the four columns that
  point at files, which no second machine or container could resolve — now every
  stored file is kept as a key relative to `MEDIA_ROOT` and resolved at read time by
  `backend/common/storage_paths.py`, including the per-run training report that the
  document migration had missed because nothing reads it back, the Spark job
  summaries that recorded their absolute input path, and the data lake's own
  `file:///C:\Users\…` URIs, which are now `lake://<key>` references that resolve
  against whichever lake root the deployment uses; **`DataLake.write_manifest()`
  raised `TypeError` on every call** because it passed the keyword-only `layer`
  positionally, so the lake's lineage record had never actually been written by any
  version of this code; **the pipeline administration screen advertised
  `GET /api/v1/processing`**, a route that 404s because `APPEND_SLASH` is off, while
  the real collection route is `/api/v1/processing/`; **the Airflow DAG
  could not have completed one scheduled run**, its payload keys, routes, workflow id
  and response envelope all invented rather than measured; **two operator hints named
  code that does not exist** — a `snapshot_to_disk()` call in `backup.sh`, and an
  nginx `/media/` proxy for a route the API deliberately does not have; and
  **`docs/architecture/data-dictionary.md` documented fields the shipped code never
  writes** (`stored_path`, `artifact_path`, `predicted_value`, `rule_snapshot`,
  `page_count`, `attachments[]`, a `low/medium/high` severity enum, a "43 indexes /
  7 unique" summary) — every collection section was rebuilt from the key union of the
  delivered store, and the index summary recounted from `INDEX_SPECS` (**48 indexes,
  8 unique**). The evidence trail for all seventeen is in `docs/testing/README.md` §7.
- **Not verified here** because the infrastructure is absent: live Atlas, HDFS
  reads/writes, Hadoop `bin`, Spark cluster mode, Kafka, Celery/Redis, running the
  Airflow DAG on a scheduler, SMTP, and container image builds. Each is named as
  such in the record, and each reports itself as unconfigured in the running app.
  The DAG is the one partial case: it was never scheduled, but every call it makes
  was exercised against the live API, which is exactly how its four contract drifts
  were found.

---

## 11. Deployment

`.env.example` documents **121** keys (the 94 originally listed plus 27 runtime
tunables the code reads). `docker-compose.yml` orchestrates `backend` (gunicorn),
`worker` and `scheduler` (Celery worker/beat against Redis), `frontend` (nginx
serving the built SPA, proxying `/api`), and `redis`; the heavyweight
Hadoop/Spark services sit behind `docker compose --profile cluster` so nobody
mistakes a developer machine for a cluster, and a commented `mongod` service makes
it explicit that Atlas — not a container — is the production data path.

Health checks gate the orchestrator (`/api/v1/ready`), nginx serves the SPA with a
history-API fallback, and `infrastructure/airflow/dags/earthscape_daily_sync.py`
schedules the daily sync when `AIRFLOW_ENABLED` is on. Backup/restore scripts live
in `infrastructure/backup/`. The compose stack and Dockerfiles were reconciled
line-by-line against the code (env keys read, celery app path, gunicorn module,
upstream host/port); the images themselves were **not** built here because Docker
was unavailable — `infrastructure/README.md` records the exact commands to verify
them.

Quick start without containers:

```bash
cp .env.example .env
.venv/Scripts/python.exe backend/scripts/bootstrap_platform.py
.venv/Scripts/python.exe backend/manage.py runserver 127.0.0.1:8000
.venv/Scripts/python.exe backend/scripts/seed_real_data.py
cd frontend && npm install && npm run dev
```

---

## 12. Limitations and next work

**Known limitations**

1. No real cluster or Atlas was available in this environment, so the distributed
   paths are exercised locally (`local[*]`, local data lake, local document store)
   and the platform says so on every screen.
2. Forecast skill is thin: only XGBoost beats persistence and none beat the
   seasonal naïve baseline (§6). The registry and the UI publish that rather than
   concealing it.
3. 3,191 observations across 16 region codes is a demonstration corpus, not a
   climate record; the 9,696-row synthetic set exists only to prove the pipeline.
   Trained models cover one region only (Sindh, `KHI` + `WNS`); the other 15
   region codes have data, analytics and alerts but no forecast model.
4. `/api/docs` pulls the Swagger asset bundle from a CDN, so the browsable
   reference needs internet; `/api/schema` does not.
5. The dataset detail screen exposes the absolute server `STORAGE URI`. The
   reports module deliberately never exposes a path; this one does, and it is
   flagged for a product decision rather than changed silently.
6. No automated test suite by policy (§10) — correctness evidence is runtime
   observation, which cannot cover untested code paths and does not claim to.

**Next work, in order of value**

1. Train the same three algorithms for the other populated regions (Punjab has
   1,356 rows across 5 locations) and put the region in the model name, so
   `/app/models` can show a per-region comparison instead of one Sindh series;
   then re-baseline each against its own seasonal naïve. Longer histories matter
   more than more algorithms — the current holdout is 226–230 points.
2. Add a bias-corrected quantile head so the forecast uncertainty bands are
   calibrated rather than empirical.
3. Move the heavy workflows onto a real Spark cluster with HDFS and record the
   speed-up against `local[*]` as measured evidence.
4. Vendor the Swagger bundle and generate a static API reference so `/api/docs`
   works offline.
5. Add station-level metadata (installed/removed dates) so missing data can be
   attributed to the station rather than treated as a gap.
6. Close the `STORAGE URI` disclosure and add per-dataset access control beyond
   the role model.

---

## 13. What was built, in one table

| Layer | Delivered |
| --- | --- |
| Ingestion | 3 live connectors + 4 upload formats, column mapping, idempotent writes through `dedupe_key`, per-run counts and rejection reasons |
| Storage | 18 Mongo collections, schema version 3 with indexes, documented local fallback, Parquet data lake with manifests |
| Processing | 8 workflows across Hadoop Streaming, PySpark, MongoDB and streaming; job state machine with logs, timings, retries |
| Analytics | shared kernel: trend, compare, correlate, seasonality, anomaly, coverage — identical output from either backend |
| ML | 47-column feature builder, chronological split, 3 algorithms × 2 trained versions (6 registry rows), joblib artifacts, holdout metrics with persistence and seasonal-naïve skill, recursive multi-day forecasts with per-lead-time residual-quantile uncertainty, retrain → `candidate` promotion path |
| Alerting | 6 operators, 5 templates, cooldown + dedupe, dry-run testing, scan counters, statistical anomaly scan, in-app and (configurable) email fan-out |
| Reporting | 8 report types, queue limits, retention pruning, PDF/CSV output, path non-disclosure |
| Governance | 3 roles / 26-19-7 capabilities, rate limits, append-only redacted audit, protected env keys |
| Interface | 42 screens, 132 OpenAPI paths, honest status vocabulary, narrow-viewport clean |
| Operations | compose stack with profiles, nginx SPA, health/readiness gates, Airflow DAG, backup/restore scripts |

*Prepared as the submission copy for the EarthScape Climate Agency project.
Numbers in this report were read from the running platform on 2026-10-17.*
