# System architecture (developer documentation)

EarthScape Climate Agency — Big Data Climate Intelligence, Analytics &
Prediction Platform. This document explains how the system is put together,
why each choice was made, and where to look in the code for each behaviour.

Related: [`data-dictionary.md`](data-dictionary.md) ·
[`diagrams.md`](diagrams.md) · [`../api/openapi.yaml`](../api/openapi.yaml)
(the generated schema; the browsable reference is `/api/docs` at runtime)

---

## 1. Architectural decisions

| Decision | Rationale | Where it is enforced |
| --- | --- | --- |
| **MongoDB Atlas is the primary and only application datastore** | The project brief demands a document-database architecture for heterogeneous, schema-evolving climate records. There is deliberately **no relational database** and no ORM: `DATABASES = {}`, `django.contrib.auth` is not installed, and every read/write goes through PyMongo. | `backend/config/settings.py:33`, `common/database.py`, `common/repositories.py` |
| **Django is an API-only tier** | `TEMPLATES = []`, JSON renderer only. The React app is a separate deployable; one API serves public, analyst and admin screens. | `config/settings.py:47,49-66` |
| **One analytics kernel shared by API, jobs, ML and reports** | A trend on the dashboard, a trend in a PDF and the features fed to a model must be the same number. | `common/analytics.py` (720 lines) |
| **Hadoop/Spark are real processors, not decoration** | Jobs export Atlas documents into a lake, run mapper/reducer or Spark against those files, then write aggregates back. The engine actually used is recorded per job. | `data-pipeline/`, `common/data_lake.py`, `apps/processing` |
| **Graceful, *labelled* degradation** | No Atlas ⇒ a `local-development` document store with JSON snapshots. No broker ⇒ an in-process thread pool. No cluster ⇒ Spark `local[*]` and a local lake. Every one of those states is surfaced in the UI instead of being hidden. | `common/database.py`, `common/tasks.py:51-57`, `common/data_lake.py:133-158`, `frontend/src/components/EnvironmentBanner.tsx` |
| **Roles on the server, capabilities on the wire** | The browser only ever gets a hint of what it may do; every mutation is re-checked. | `apps/authentication/views.py:ROLE_CAPABILITIES`, `common/api.py:require_roles` |

---

## 2. Backend module map

`backend/common/` — the frozen core, used by every app:

| File | Responsibility |
| --- | --- |
| `database.py` | Atlas client lifecycle, pooling, `FORCE_ATLAS_CONNECTION`, mongomock fallback, JSON snapshot flush |
| `collections.py` | the 18 collection contracts: names, 48 indexes, JSON-schema validators, `SCHEMA_VERSION` |
| `schema.py` | idempotent schema application + `schema_version` record |
| `repositories.py` | generic document CRUD, ObjectId handling, pagination, aggregation helpers |
| `security.py` | bcrypt hashing, password policy, JWT issue/decode, refresh-token hashing |
| `api.py` | `MongoJWTAuthentication`, `DocumentUser`, `IsAuthenticatedAccount` / `HasRole` / `IsAdministrator` / `AdministratorOrAnalyst`, `ApiError`, the DRF exception handler that emits sanitised errors with a request id |
| `analytics.py` | `trend_series`, `compare_regions`, `correlation_matrix`, `seasonal_baselines`, `detect_anomalies` (statistical baseline / threshold / isolation forest), `coverage_report`, `latest_by_location`, `store_summary`/`read_summaries`, `trend_slope`, `InsufficientData` |
| `data_lake.py` | layered lake (`raw/validated/processed/analytics/ml-features/exports`), WebHDFS or local, partition paths, path sanitisation (`_safe_join`), manifests, `health()` |
| `tasks.py` | background dispatch: `@register` whitelist, `REGISTRY`, Celery-or-threadpool `backend()`, `submit()`, `active_jobs()` |
| `validators.py` | timestamp/coordinate parsing, unit conversion, range limits, missing-value and sentinel handling, `quality_flags_for_row` |
| `audit.py` | append-only audit writes with metadata redaction |
| `middleware.py` | CORS allow-list, correlation ids, security headers, per-user rate limits, local write flush |
| `regions.py` | region/station reference data and `MISSING_SENTINELS` |
| `emailer.py`, `logging_formatter.py`, `views.py` | SMTP (only when configured), structured log lines, shared view helpers |

`backend/apps/` — feature modules (lines are the current code volume):

| App | ~Lines | Owns | API surface |
| --- | --- | --- | --- |
| `authentication` | 682 | register, login, refresh rotation + reuse revocation, logout, password reset, profile, me, auth configuration, **role→capability map** | `/api/v1/auth/*` |
| `users` | 570 | user administration, role catalogue with live counts, activate/deactivate, admin password set, unlock | `/api/v1/users/*` |
| `datasets` | 1,981 | dataset CRUD/archive, upload inspect → column mapping → commit, `row_to_observation`, bulk idempotent upsert, coverage computation, format documentation | `/api/v1/datasets/*` |
| `ingestion` | 933 | connector orchestration (NASA POWER, NOAA CDO, Open-Meteo), job documents, retries, cancel, schedule, stats | `/api/v1/ingestion/*` |
| `climate` | 4,172 | observation search/history, metrics catalogue, regions, map layer, quality, trend/compare/anomaly/correlation endpoints, dashboard summary + activity, saved analyses, instant CSV export | `/api/v1/climate/*`, `/api/v1/dashboard/*`, `/api/v1/analytics/*` |
| `processing` | — | workflow registry, engine probes, processing-job lifecycle, lake views, cancellable execution through `common.tasks` | `/api/v1/processing/*` |
| `machine_learning` | 969 | model catalogue, train/evaluate/activate/retire/retrain, forecast and anomaly-scan endpoints backed by `ml/` | `/api/v1/models/*`, `/api/v1/predictions/*` |
| `alerts` | 2,230 | threshold rules CRUD, rule evaluation monitor, alert lifecycle (ack/resolve), notifications, delivery status, templates, scan | `/api/v1/alerts/*`, `/api/v1/notifications/*` |
| `reports` | 1,742 | report types, queued generation, reportlab PDF + CSV writers, download with ownership checks, cancel | `/api/v1/reports/*` |
| `support` | 732+ | ticket queue, threaded messages, internal notes, categories, summary; feedback records and triage | `/api/v1/support/*`, `/api/v1/feedback/*` |
| `monitoring` | growing | `/health`, `/ready`, API index, `/system/status`, `/metrics`, `/endpoints`, `/audit`, `/settings`, `/backups`, `/dependencies`, `/services`, Celery app + beat schedule | `/api/v1/health`, `/api/v1/system/*` |

## 3. Request lifecycle

```
HTTP request
 └─ CORSAllowListMiddleware      exact-origin allow list from FRONTEND_ORIGIN
 └─ CorrelationIdMiddleware      assigns/echoes X-Request-Id; stored on audit rows
 └─ SecurityHeadersMiddleware    nosniff, frame deny, referrer policy, cache no-store on JSON
 └─ RateLimitMiddleware          per-user + per-IP buckets; stricter for login/register/reset/upload
 └─ LocalWriteFlushMiddleware    flushes the labelled local store after writes
 └─ DRF view
     ├─ MongoJWTAuthentication   signature, exp, jti revocation, user is_active
     ├─ require_roles(...)       server-side RBAC; denial is audited as outcome="denied"
     ├─ serializer / validation  pydantic + common.validators for anything measured
     ├─ repositories / analytics PyMongo aggregation against Atlas
     └─ error_response()         ApiError → {code, message, details, request_id};
                                 unexpected exceptions logged with traceback server-side,
                                 client gets a stable code only — never a stack trace
```

Responses are generated at the edge with ISO-8601 UTC strings and ObjectIds
converted to strings, so the TypeScript types in
`frontend/src/types/api.ts` mirror the wire format exactly.

## 4. Data processing workflows

1. **Acquire** — connectors (`apps/ingestion/connectors/*`) page a provider and
   emit raw rows; uploads are sniffed for encoding/header/units in
   `apps/datasets/file_import.py`.
2. **Normalise & validate** — `apps/datasets/importer.py::row_to_observation`
   converts units, range-checks, maps sentinels to `null`, builds `dedupe_key`,
   `year/month`, GeoJSON `coordinates` and a `provenance` block.
3. **Store** — `upsert_observations` writes with `UpdateOne(..., upsert=True)` in
   batches, so a re-run yields `records_skipped` rather than duplicates.
4. **Lake export** — `common/data_lake.py` writes `raw/` then `validated/`
   partitioned by `region_code/year/month` (WebHDFS or local, same logical keys).
5. **Distributed compute** — `data-pipeline/mapreduce/run_streaming_job.py`
   submits a real Hadoop Streaming job (mapper/reducer pairs in the same folder);
   `data-pipeline/spark/jobs.py` runs PySpark aggregation, seasonality and
   feature assembly, writing Parquet. `run_spark_jobs.py` is the driver used when
   no cluster exists (`local[*]`).
6. **Roll up** — results become `analytics_summaries` documents keyed by
   `summary_key`, plus `analytics/` and `ml-features/` lake layers.
7. **Stream (conditional)** — when `KAFKA_ENABLED` and brokers answer, the same
   Spark reading path consumes `climate.observations.v1`; otherwise the batch
   path is the system of record and the UI says streaming is not configured.
8. **Schedule** — Celery beat or the in-process scheduler (`apps/ingestion/
   scheduler.py`) triggers ingest, alert scan, analytics rebuild, backup, and
   retrain checks.

## 5. Machine-learning design

`ml/` is a plain Python package the Django app imports (no external service):

* `features/build.py` — lag, rolling mean/min/max, seasonal index, calendar
  features, target shift; returns the exact column order and imputation
  constants, which are stored on the model as `feature_spec`.
* `training/pipeline.py` — chronological train/validation split (no shuffling of
  a time series), scikit-learn pipelines plus XGBoost when available, fitted on
  real rows only.
* `evaluation/metrics.py` + `report.py` — MAE, RMSE, R², MAPE, residual and
  back-test output; a model without metrics cannot be activated.
* `anomaly/detector.py` — IsolationForest with a contamination rate from env,
  used by `common/analytics.detect_anomalies` when the method is
  `isolation_forest`, always falling back to the statistical baseline when there
  are too few rows.
* `inference/predictor.py` — loads the active artifact, re-applies
  `feature_spec`, produces values plus an uncertainty band, and refuses to score
  rows whose features are incomplete.
* Registry/versioning lives in `model_registry`; artifacts on disk under
  `MODEL_ARTIFACT_DIR`. `notebooks/exploratory_climate_analysis.ipynb` documents
  the EDA the feature set came from.

## 6. Frontend architecture

| Layer | Files | Notes |
| --- | --- | --- |
| Routing | `src/app/router.tsx`, `navigation.tsx` | 39 lazy routes in 4 zones: public, `/app`, `/admin`, auth. Route guards wrap the tree, not individual pages |
| Services | `src/services/http.ts`, `api.ts` | axios instance; access token held **in memory only**, refresh token in `localStorage`; single-flight refresh on 401 with one retry; typed client mirroring the OpenAPI surface |
| State | `src/features/auth/AuthContext.tsx`, `Guards.tsx`, `src/hooks/useAsync.ts`, theme + toast providers | `Can` hides what the server would refuse; `useAsync` gives loading/refreshing/error and optional polling without refetch storms |
| UI kit | `src/components/ui/*` | 16 primitives (DataTable, Modal, ConfirmDialog, StatCard, StatusBadge, Skeletons, EmptyState, ErrorState, Pagination, Tabs, MultiSelect, Field, Toast…) |
| Charts | `src/components/charts/*` | Recharts wrappers: `TrendChart` (line/area/bar, groupBy region/location, anomaly markers, baseline reference line), `RegionBarChart`, `CorrelationHeatmap`, `ForecastChart` (actual vs forecast vs band), `MiniCharts` |
| Map | `src/components/maps/ClimateMap.tsx` | Leaflet + OSM, colour ramp per metric, clustering toggle, in-map legend, popup measurements |
| Pages | `src/pages/{public,app,admin}` | each page composes the kit; no page renders a value the API did not return |

Design system: Tailwind with `forest/earth/hydro/solar/alert` palettes and CSS
variables for light/dark surfaces (`src/index.css`), `darkMode: 'class'`.
Accessibility: skip link, focus-visible rings, labelled inputs, aria-live toast
region, keyboard-dismissible modals, colour + text for severity, chart legends
and `title`/`aria-label` on interactive graphics.

## 7. Security design

* **Authentication** — JWT HS256 with `JWT_SIGNING_KEY`; 20-minute access tokens,
  7-day refresh tokens stored **as hashes** with family rotation; presenting a
  rotated token revokes the entire family. Logout revokes the family.
* **Passwords** — bcrypt; policy from env (`PASSWORD_MIN_LENGTH`, complexity);
  lockout after `LOGIN_MAX_FAILED_ATTEMPTS` for `LOGIN_LOCKOUT_MINUTES`;
  `password_changed_at` invalidates earlier tokens.
* **Authorisation** — server-side role checks on every endpoint
  (`require_roles`), capability map issued for UI hints only, object-level checks
  (owner/admin) for datasets, tickets, saved analyses and reports.
* **No hardcoded credentials** — bootstrap generates random passwords and prints
  them once to a git-ignored file; registration can only create the
  least-privileged role.
* **Secrets hygiene** — structured log formatter and the audit writer redact
  password/token/key/connection-string fields; error responses carry a code and
  a request id, not internals.
* **Injection & execution safety** — PyMongo parameterised documents (no string
  query assembly from user input), lake paths pass through `_safe_join`, uploads
  are extension/size/row limited and parsed by type-sniffed readers, background
  work is limited to the `common.tasks.REGISTRY` whitelist, shell invocations
  (when a real `hadoop`/`mongodump` binary is used) take argument lists with
  `shell=False` and no client-controlled tokens, and CSV/Excel exports neutralise
  formula-leading cells.
* **Transport & headers** — TLS to Atlas; nginx adds HSTS/CSP-ish hardening,
  `X-Content-Type-Options`, `X-Frame-Options: DENY`, referrer policy; CORS is an
  exact origin allow list.
* **Data protection** — TTL index deletes expired sessions, backups are
  checksummed, retention prunes old archives, audit is append-only, personal
  fields (organisation, last IP) are minimal and documented.

## 8. Reliability, performance, scalability

* Health (`/api/v1/health`) and readiness (`/api/v1/ready`) are separate; the
  container healthcheck uses readiness.
* `/api/v1/system/metrics` reports request counts, latency percentiles, error
  rate, job throughput and host disk/memory headroom from real counters, with an
  explicit `unavailable[]` list for anything not instrumented.
* Indexes are designed around the actual query shapes (date+region, region+year+
  month, location+date, dataset), plus a `2dsphere` index for the map.
* Reads that can be bounded are paginated server-side; exports stream.
* Horizontal scaling: stateless API tier (tokens are self-contained, sessions in
  Atlas) behind nginx; workers scaled separately; Spark/Hadoop scale by adding
  executors/DataNodes; Atlas scales by cluster tier. `docker-compose.yml`
  demonstrates the topology.
* Backups: `infrastructure/backup/backup.sh` (mongodump + SHA256 + retention),
  restore guarded behind `--yes-destroy-data` with checksum verification.

## 9. Known limitations (stated plainly)

* Docker images and the Hadoop/Spark cluster profile were **authored but not
  built** in the delivery environment (Docker was unavailable). See
  `infrastructure/README.md`.
* `apps/monitoring/import_compat.py` untangles a DRF import cycle (settings
  reference `common.api.MongoJWTAuthentication`, which imports
  `rest_framework.views`). It is a deliberate, idempotent shim whose result is
  published through `/api/v1/health`, because the app was written against a
  frozen `common/`. Fixing the cycle in `common/api.py` instead would remove the
  need for it.
* Real-time streaming is implemented against Kafka but was not exercised
  without a broker; the platform reports streaming as not configured in that
  case rather than pretending.
* Satellite imagery and environmental-sensor feeds are represented through
  station/observation records and provider metadata; no raster/image pipeline is
  implemented, and the UI does not claim one.
