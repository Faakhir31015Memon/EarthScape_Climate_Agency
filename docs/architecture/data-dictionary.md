# Data dictionary — MongoDB Atlas database `earthscape_climate`

Authoritative source: `backend/common/collections.py` (names, indexes, server-side
JSON-schema validators, `SCHEMA_VERSION = 3`) plus the document builders listed
beside each collection. Atlas enforces the validators; because the labelled
`local-development` fallback store does not, the same rules are re-enforced in
`backend/common/validators.py`, so a document is valid in both modes.

Conventions used everywhere:

* **Identifiers** are Mongo `ObjectId`s, serialised to strings at the API edge;
  application code converts them through `common.repositories.to_object_id`, and a
  malformed id becomes `404 not_found`, never a `500`.
* **Timestamps** are BSON dates in UTC (`USE_TZ = True`, `TIME_ZONE = "UTC"`).
* **Dates used for partitioning** are stored as `%Y-%m-%d` strings plus denormalised
  `year` / `month` integers, so MapReduce and Spark can filter without date maths.
* **A measurement that was not supplied is `null`, never `0`.** Provider missing
  sentinels (`-999` style, `common.regions.MISSING_SENTINELS`) are converted to
  `null` and recorded as an `*_sentinel` info issue.
* **Secrets are never stored in plaintext**: `password_hash` is bcrypt; refresh
  tokens are stored only as `token_hash`.
* **Units are encoded in the field name** (`temperature_c`, `precipitation_mm`,
  `surface_pressure_hpa`) so a value can never be ambiguous.
* **Stored files are referenced by key, never by absolute path.** The four columns
  that point at a file on disk (`datasets.storage_uri`,
  `datasets.source_metadata.storage.manifest`, `model_registry.artifact_uri`,
  `reports.file_path`) hold a POSIX key relative to `MEDIA_ROOT`
  (`backend/.data` by default), for example
  `uploads/93ed815bf5b840d19e19e1195cf2ca3c.csv` or
  `ml-artifacts/models/temperature_c-xgboost-daily-v1.joblib`.
  `backend/common/storage_paths.py` turns a key back into a path at read time, so
  the same snapshot resolves on a second machine, inside a container, or under a
  different `MEDIA_ROOT`. This replaced absolute developer paths that an earlier
  build wrote into those columns (defect 9 of the verification record).

---

## 1. `users`

Built by `apps/authentication/services.py`, managed by `apps/users/services.py`.
Validator: strict. Unique `ux_users_email` on `email_normalized`.

| Field | Type | Meaning / constraint |
| --- | --- | --- |
| `_id` | ObjectId | account id |
| `full_name` | string | required |
| `email` | string | as typed |
| `email_normalized` | string | lower-cased, unique, must match the validator e-mail pattern |
| `password_hash` | string | bcrypt digest; never plaintext, never returned by any endpoint |
| `role` | string | enum `administrator` \| `climate_analyst` \| `viewer` (validator enum) |
| `is_active` | bool | deactivation blocks login and revokes refresh tokens |
| `email_verified` | bool | set by the verification flow |
| `organisation` | string \| null | optional profile |
| `preferences.theme` | string | `light` \| `dark` \| `system` |
| `preferences.units` | object | `{temperature, precipitation, wind}` display set |
| `preferences.dashboard_region` | string \| null | region code, used as the dashboard default filter |
| `preferences.density` | string | `comfortable` \| `compact` |
| `preferences.notification_channels` | object | `{in_app: bool, email: bool}`; `email` is only honoured when SMTP is configured |
| `failed_login_count` | int | reset on success |
| `locked_until` | date \| null | set when `LOGIN_MAX_FAILED_ATTEMPTS` is exceeded |
| `password_changed_at` | date | used to invalidate tokens issued earlier |
| `last_login_at`, `last_login_ip` | date, string | `last_login_ip` is stored truncated/hashed for privacy |
| `created_at`, `updated_at`, `created_by` | date, string, ObjectId | audit |

Roles are defined **server-side only**. Self-registration can only ever create
`viewer` (and `climate_analyst` when `ALLOW_ANALYST_SELF_REGISTRATION=true`);
`administrator` exists only via bootstrap or an existing administrator.

---

## 2. `refresh_tokens`

Written by `apps/authentication/services.py`. Only refresh tokens are stored;
access tokens are stateless JWTs. TTL index `ttl_refresh_expiry` on
`expires_at` (`expireAfterSeconds: 0`) lets Atlas delete expired sessions.

| Field | Type | Meaning |
| --- | --- | --- |
| `token_hash` | string | unique; SHA-256 of the presented token — the raw token is never stored |
| `user_id` | ObjectId | owner |
| `family_id` | string | rotation family; reuse detection revokes the whole family |
| `kind` | string | absent for session tokens; `password_reset` rows are written by the recovery flow with `family_id: "reset"` |
| `issued_at`, `expires_at` | date | 20-minute access / 7-day refresh lifetimes from env (reset rows use `PASSWORD_RESET_TTL_MINUTES`) |
| `used_at`, `revoked_at`, `revocation_reason` | date, string | rotation and revocation audit |
| `replaced_by` | ObjectId \| string | successor token id after rotation |
| `source_ip`, `user_agent` | string | session context for the security review |
| `created_at` | date | |

---

## 3. `regions`

Reference data seeded from `common/regions.py`; unique on `code`.

`code`, `name`, `country`, `climate_zone`, `centroid {latitude, longitude}`,
`locations[] {code, name, latitude, longitude, station_id}`, `location_count`.

---

## 4. `datasets`

Created by `apps/datasets/views.py` (`_create_dataset_document`) and the connector
flow in `apps/datasets/importer.py`. Validator: moderate — `source_type`,
`format` and `status` are enum-constrained. Unique `(name, source_type)`.

| Field | Type | Meaning |
| --- | --- | --- |
| `name`, `description` | string | human label |
| `source_type` | enum | `nasa_power` \| `noaa` \| `live_api` \| `university_upload` \| `sample` |
| `format` | enum | `csv` \| `json` \| `xlsx` \| `parquet` \| `api` |
| `status` | enum | `draft` → `validating` → `validated` → `ingesting` → `ready`, or `failed`, `archived` |
| `file_name`, `storage_uri` | string | upload provenance. `file_name` is the basename of what the client sent; `storage_uri` is the **key relative to `MEDIA_ROOT`** where the bytes actually live (`uploads/<uuid>.<ext>`), never client-supplied and never absolute |
| `source_metadata` | object | `{column_mapping, location_defaults, storage}` — header → canonical field (validated against the canonical list), the `default_region_code` / `default_country` / `default_location_code` / `default_latitude` / `default_longitude` used when a row omits them, and the storage block below. The inspect step's per-column guesses (`{header, guessed_dtype, guessed_unit, canonical_field, missing_percentage_estimate}`) are returned to the client but folded into `column_mapping` on commit |
| `source_metadata.storage` | object | `{sha256, size_bytes, manifest}` — `manifest` is the key of the sidecar JSON written next to the file, holding `original_file_name`, `declared_content_type`, `detected_format`, `headers`, `column_mapping` and the uploader |
| `units` | object | declared unit per measurement, for conversion |
| `source_url`, `source_name` | string | endpoint or file identity |
| `record_count` | int | observations this dataset contributed |
| `geographic_coverage` | object | `{regions[], location_codes[], countries[], bbox[]}` |
| `temporal_coverage` | object | `{start, end, years[], frequency}` |
| `validation_report` | object | `{generated_at, format_detected, headers, …}` from the import run, plus its issue counts |
| `ingestion_job_ids` | ObjectId[] | link to `ingestion_jobs` |
| `tags` | string[] | free labels |
| `is_public` | bool | visibility to non-owners |
| `schema_version` | int | `3` |
| `created_at`, `updated_at`, `uploaded_by` | | audit |

---

## 5. `ingestion_jobs`

`apps/ingestion/services.py` (`new_job_document`). Validator enforces the status
enum and integer counters. Indexed on `(status, started_at desc)`, `dataset_id`,
`(source_name, completed_at desc)`.

| Field | Type | Meaning |
| --- | --- | --- |
| `source_name`, `source_type`, `job_type` | string | provider identity, the connector id, and `api_sync` \| `file_import` |
| `status` | enum | `queued` \| `running` \| `completed` \| `completed_with_errors` \| `failed` \| `cancelled` |
| `dataset_id` | ObjectId \| null | target dataset |
| `requested_by`, `triggered_by` | string / enum | owner account, and `manual` \| `schedule` (the value a scheduler posts; every stored row in this delivery is `manual`) |
| `records_fetched`, `records_accepted`, `records_rejected`, `records_skipped`, `duplicate_count` | int | real counters; they reconcile (`fetched = accepted + rejected + skipped`, duplicates counted separately on the accepted side) |
| `validation_errors[]` | array | sample of `{row, field, code, message, count}` — capped, for diagnosability; `count` is how many rows hit the same code |
| `parameters` | object | sanitised request parameters (no credentials), e.g. the requested NASA parameters and community |
| `data_period` | object | `{start, end}` the run was asked to cover |
| `geographic_coverage` | object | `{regions[], location_codes[], countries[]}` actually touched |
| `log_lines[]` | array | `{at, level, message}` structured job log |
| `executor`, `processing_mode` | string | `in-process-thread-pool` / `celery` / `script`, and `live-api` / `uploaded-file` |
| `retry_count`, `max_retries` | int | bounded retries |
| `error_message` | string \| null | sanitised, never a stack trace |
| `started_at`, `completed_at` | date | durations are computed, not stored |

---

## 6. `climate_observations` — the core collection

Built field-for-field in `apps/datasets/importer.py::row_to_observation`.
Validator: strict. **Unique `ux_obs_dedupe` on `dedupe_key`**, plus
`(observation_date desc, region_code)`, `(region_code, year, month)`,
`(location_code, observation_date desc)`, `dataset_id`, a sparse `2dsphere`
index on `coordinates`, and a sparse index on `quality_flags`.

| Field | Type | Constraint / meaning |
| --- | --- | --- |
| `dedupe_key` | string | `dataset_id\|location_code\|observation_date` — idempotency anchor |
| `dataset_id` | string | owning dataset |
| `observation_date` | string `YYYY-MM-DD` | calendar day |
| `observed_at` | date | exact UTC instant as supplied |
| `year`, `month`, `day` | int | denormalised for pipeline partitioning |
| `location_code`, `location_name` | string \| null | station identity |
| `region_code`, `country` | string \| null | resolved from coordinates/region reference |
| `latitude`, `longitude` | double | validator bounds −90…90 / −180…180 |
| `coordinates` | GeoJSON Point | `[longitude, latitude]`, 2dsphere-indexed |
| `temperature_c` | double \| null | 2 m air temperature, °C |
| `temperature_min_c`, `temperature_max_c` | double \| null | daily min/max, °C |
| `precipitation_mm` | double \| null | ≥ 0, millimetres |
| `relative_humidity_pct` | double \| null | 0…100 |
| `wind_speed_ms` | double \| null | m/s |
| `wind_direction_deg` | double \| null | 0…360 degrees from true north |
| `surface_pressure_hpa` | double \| null | hPa |
| `cloud_coverage_pct` | double \| null | 0…100 |
| `solar_radiation_kwh_m2` | double \| null | kWh/m² |
| `source_units` | object | unit the value arrived in, before conversion — keeps conversions auditable |
| `quality_flags` | string[] | e.g. `unit_converted`, `range_suspect`, `*_sentinel`, `interpolated_location` |
| `source_name`, `source_type`, `dataset_name` | string | denormalised for cheap dashboard reads |
| `provider_record_id` | string \| null | upstream id, when the provider gives one |
| `provenance.connector` | string | `nasa_power`, `noaa`, `open_meteo`, `file_import` |
| `provenance.endpoint` | string | URL or stored file name |
| `provenance.run_id`, `provenance.ingestion_job_id` | string | the run that wrote it |
| `provenance.retrieved_at` | date | when the provider answered |
| `provenance.processing_mode` | string | datastore/lake mode at write time |
| `provenance.request_parameters` | object | sanitised — credentials are stripped before storage |
| `ingested_at` | date | write time |
| `schema_version` | int | validator requires BSON int; currently `3` |

Synthetic pipeline-verification records are distinguishable by
`dataset_id = "synthetic-stress"`, `source_name = "synthetic-generator"` and
`unit_audit = "synthetic-canonical"`; no supported code path writes them here.

---

## 7. `analytics_summaries`

Written by `common/analytics.py` rollups and `apps/climate/services.py`.
Unique on `summary_key` (a deterministic hash of metric + scope + window), so a
rebuild replaces rather than duplicates.

`summary_key`, `metric`, `workflow`, `scope`, `payload`, `processing_mode`,
`source_datasets[]`, `source_dataset_versions`, `computed_at`, `schema_version`.

`summary_key` is the deterministic `kind|dimension=value|…` hash (for example
`regional_monthly_climate|month=2|region_code=Balochistan|year=2024`), so a
rebuild of the same window replaces the row instead of duplicating it. `metric`
names the rollup family rather than a single variable:
`regional_monthly_climate` (235 rows), `seasonal_baseline_temperature_c` (45),
`trend_monthly_temperature_c` (16), `coverage_<metric>`, and the bare variable
name for engine output. `workflow` records which job produced the row
(`spark.export_processed_parquet`, `analytics.summary_rebuild`,
`mapreduce.temperature_aggregation`, `analytics.recompute`,
`climate.anomaly_scan`), and `processing_mode` the engine that actually ran
(`mongodb` \| `pyspark` \| `local-streaming-simulated`). `scope` holds only the
dimensions that key the rollup (e.g. `{region_code, year, month}`), and `payload`
the computed numbers — means, minima/maxima, sums, per-variable value counts,
`quality_flag_counts`, the run id and rows skipped within the group.
`source_datasets` / `source_dataset_versions` name the inputs and the datastore
instant they were read at.

Every number here is derived from `climate_observations`; a summary is never
hand-entered.

---

## 8. `processing_jobs`

Created by `apps/processing` (workflow registry → `common/tasks` dispatch).
Indexed `(status, submitted_at desc)`, `dataset_id`, `workflow`.

`workflow`, `workflow_label`, `status`, `engine` (the engine actually used),
`executor`, `processing_mode`, `parameters` (validated, server-side copy),
`dataset_id`, `dataset_name`, `output`, `metrics`, `log_lines[]`,
`error_message`, `attempt`, `retry_of`, `is_stream_status`, `submitted_by`,
`submitted_by_role`, `submitted_at`, `started_at`, `finished_at`,
`duration_seconds`, `schema_version`.

`metrics` is the engine's own counters, not a fixed schema: the MongoDB rollup
reports `{rows_in, rows_out, processing_mode}`, the streaming health document
`{events_processed, accepted, rejected, duplicates, late, lag_seconds_or_null,
last_batch_id, …}`. `log_lines[]` are plain strings. A single document carries
`is_stream_status: true` — it is the recorded streaming health state rather than a
submitted workflow, which is why it has no `submitted_by`.

A job that could not reach its engine ends `failed` with the engine's own message
rather than reporting success.

---

## 9. `model_registry`

`apps/machine_learning/services.py`. Validator: moderate, `status` enum
`training` \| `candidate` \| `active` \| `retired` \| `failed`; unique
`(name, version)`.

`name`, `version`, `algorithm`, `target_variable`, `run_id`, `status`,
`requested_by`, `trained_by`, `training_options {target_variable, regions,
location_codes, start, end, horizon_days, algorithms, …}` as submitted,
`training_period {start, end, locations, regions, train_before}` (the window the
rows were actually found in, `train_before` = the holdout split date),
`train_size`, `test_size`, `horizon_days`, `dataset_ids[]`,
`retrain_of_previous`, `feature_definition {features, …}` — the exact column order
and imputation constants required at inference — `feature_importance[]
{feature, importance}`, `uncertainty_method`
(`empirical_holdout_residual_quantiles_per_lead_time`), `evaluation_metrics`
`{mae, rmse, r2, mape_or_null, evaluated_points, baseline_mae,
skill_vs_baseline, …}` — computed, never defaulted, `limitations[]`,
`artifact_uri`, `artifact_format`, `duration_seconds`, `progress_stage`,
`progress_at`, `error_message`, `last_retrain_requested_at`, `activated_at`, `retired_at` (set
when a version is superseded or retired; absent on rows that never reached that
state), `created_at`, `training_started_at`, `notes`.

There is no `hyperparameters` or `row_count` field: the configuration that
actually matters is captured in `training_options` plus `feature_definition`, and
the row counts are the measured `train_size` / `test_size` of the split that was
evaluated.

`artifact_uri` is the joblib bundle as a **key relative to `MEDIA_ROOT`**
(`ml-artifacts/models/<name>-v<version>.joblib`), written by
`ml/training/pipeline.py` through `storage_paths.stored_key` and resolved by the
inference path before loading; a row whose artifact cannot be resolved is reported
as unavailable rather than failing the request. `artifact_format` (`joblib`) and
`duration_seconds` are recorded alongside it. Training and inference report metrics
as computed values only — a metric is never defaulted.

---

## 10. `predictions`

`apps/machine_learning/prediction_views.py` + `ml/inference/predictor.py`.
Indexed `(model_id, generated_at desc)` and `(target_variable, region_code,
forecast_date)`.

`model_id`, `model_name`, `model_version`, `algorithm` (in `method`),
`target_variable`, `scope {kind: region \| location, code, locations[]}`,
`region_code`, `location_code`, `horizon_days`, `forecast_origin_date`,
`values[] {forecast_date, value, unit, lower_bound, upper_bound}`, `units`,
`method`, `status`, `training_period`, `evaluation_metrics_snapshot` (the holdout
metrics of the model that produced the series, copied so a stale forecast cannot
be read as a better one), `data_freshness_days`, `uncertainty {available, method,
level, note}`, `caveats[]`, `provenance {requested_by, feature_source,
actual_observations_used}`, `generated_at`, `created_at`.

A forecast is one document per (model, scope, run) with the whole series inside
`values[]` — the API never returns a point without its band. Note that the
declared `ix_pred_scope` index keys `forecast_date`, which is per-point inside
`values[]` rather than a top-level field; the working lookup path is
`ix_predictions_model`. Anomaly findings are **not** stored here — they live in
`alerts` with `source = "climate_anomaly_scan"`.

Predictions are always traceable to the model version that produced them.

---

## 11. `alerts`

`apps/alerts/rules.py` + `monitor.py`, and `apps/climate/services.py`
(`anomaly_alert_document`). Indexed `(rule_key, triggered_at desc)`,
`(status, severity)`.

The collection holds **two document kinds**, separated by `document_type`:

*Rule definitions* (`document_type: "rule"` — 3 rows here, the built-in defaults
are seeded by the alert engine): `rule_key`, `name`, `description`, `is_enabled`,
`target_variable`, `scope {regions[], location_codes[], countries[], datasets[]}`
(empty array = all), `condition {operator, value, value_upper, sigma}`,
`applicable_sources[]`, `severity`, `cooldown_minutes`, `recipients[]`,
`created_by`, `created_at`, `updated_at`, `last_evaluated_at`,
`last_triggered_at`, `evaluation_state {last_run_at, persisted,
observations_scanned, predictions_scanned, matched, …}`.

*Raised alerts* (`document_type` absent — 102 rows): `rule_key` (the rule or
detector that fired), `kind` (`anomaly` for detector output), `source`
(`climate_anomaly_scan` \| `anomaly_scan` \| `rule_engine`), `status` (`open` \|
`acknowledged` \| `resolved` \| `dismissed`), `severity` and `anomaly_severity`
(**`info` \| `warning` \| `critical`** — the only severity enum the API accepts),
`title`, `message`, `description`, `explanation`, `caveat`, `dedupe_key`,
`metric`, `metric_label`, `unit`, `value`, `deviation_score`, `direction`
(`above` \| `below`), `method`, `threshold_or_baseline`, `baseline {region_code,
month, mean, std, sample_size, p05, p95, metric}`, `region_code`, `location_code`,
`location_name`, `latitude`, `longitude`, `observation_date`, `dataset_id`,
`source_name`, `rule_id` and `prediction_id` (set when a stored rule document or a
specific forecast triggered it), `scan {workflow, scope, …}` (the run that produced
it), `notification_ids[]`, `acknowledged_by`/`_at`/`_by_role`,
`resolved_by`/`_at`/`_by_role`, `resolution_note`, `metadata`, `created_at`,
`updated_at`, `schema_version`.

`rule_snapshot`, `cooldown_until` and `dedupe_window` are not stored fields: the
rule's `condition` and `baseline`/`scan` blocks are what a raised alert carries,
and the cooldown lives on the rule. Every alert row keeps the numbers it compared
(`value` against `baseline` or `threshold_or_baseline`) so the UI can show its own
working.

---

## 12. `notifications`

`apps/alerts/notifications.py`. Indexed `(recipient_user_id, read_at)`,
`(dedupe_key, created_at desc)`, sparse `(delivery_status, next_retry_at)`.

`recipient_user_id`, `channel` (**one channel per row**: `in_app` \| `email`),
`type` (`alert` \| `report` \| `system` \| `ticket` — every stored row in this
delivery is `alert`), `title`, `body`, `dedupe_key`
(`alert:<id>:<recipient>:<channel>`), `related_resource {type, id}`,
`severity` (carried from the alert so unread counters can flag criticals without
joining back), `delivery_status` (`pending` \| `sent` \| `suppressed` \|
`failed`), `attempt_count`, `next_retry_at`, `failure_reason` (sanitised),
`read_at`, `acknowledged_at`, `created_at`, `sent_at`.

A duplicate inside the cooldown window is not inserted: `create_notification()`
returns the existing row with `suppressed_as_duplicate: true` (a response field,
not a stored one). The e-mail channel is only fanned out when the account's
`preferences.notification_channels.email` **and** `ENABLE_EMAIL_NOTIFICATIONS`
are on, so every stored row in this delivery is `in_app` and none claims an e-mail
was sent. When e-mail is enabled but cannot be delivered, the row is stored with
`delivery_status: "suppressed"` or `"failed"` and a `failure_reason` (no address on
file, SMTP refused) rather than a fake `sent`.

---

## 13. `reports`

`apps/reports/services.py`. Indexed `(requested_by, created_at desc)`, `status`.

`report_type` (`climate_trend` \| `regional_comparison` \| `dataset_quality` \|
`anomaly` \| `forecast` \| `processing_performance` \| `ingestion_history` \|
`audit_summary` — the registry in `generators.py`), `label`, `title`, `format`
(`csv` \| `json` \| `pdf`), `status` (**`queued` \| `running` \| `generated` \|
`failed` \| `expired` \| `cancelled`** — the success state is `generated`, not
`completed`), `requested_by`, `requested_by_role`, `filters` (the scope and period
as submitted), `executor`, `row_count`, `artefact_rows_written`, `columns[]`,
`insufficient_data` + `reason` (set instead of fabricated numbers when the window
holds too few observations), `generation_metadata` (source, engine and provenance
of the run), `download_name`, `file_path`, `file_size_bytes`, `download_count`,
`error_message`, `created_at`, `started_at`, `completed_at`, `expires_at`,
`schema_version`.

`file_path` is the **key relative to `MEDIA_ROOT`**
(`reports/<report id>/<sha256-ish>.<ext>`), resolved at download time by
`common/storage_paths.py`; a row whose file is gone reports the file as missing
rather than serving a stale byte stream. Charts in a PDF are rendered from the
same analytics kernel the dashboard uses.

---

## 14. `saved_analyses`

`apps/climate/analytics_views.py`. Indexed `(owner_user_id, updated_at desc)`.

`name`, `description`, `notes`, `owner_user_id`, `filters`, `chart_config`,
`is_shared`, `created_at`, `updated_at`, `schema_version`. Restoring one replays
the stored filters and chart configuration through the analytics endpoints; the
saved document holds parameters only, never results.

---

## 15. `audit_logs`

`common/audit.py` — append-only; there is no delete path in the application.
Validator: strict; `outcome` enum `success` \| `failure` \| `denied`. Indexed
`created_at desc`, `(actor_user_id, created_at desc)`, `(resource_type,
resource_id)`.

`action` (e.g. `auth.login`, `users.create`, `datasets.commit`,
`processing.job_start`, `settings.update`), `resource_type`, `resource_id`,
`actor_user_id`, `actor_role`, `outcome`, `environment` (`DEPLOYMENT_ENVIRONMENT`,
stamped on every row), `metadata` (free-form but
**redacted on write and again on read**: password hashes, tokens, API keys and
connection strings are stripped), `request_id` (correlation id from
`CorrelationIdMiddleware`), `source_ip`, `created_at`.

---

## 16. `support_tickets`

`apps/support` (queue). Indexed `(status, priority)`, `created_by`, sparse
`assigned_to`.

`subject`, `description`, `category` (`bug` \| `data_request` \| `access` \|
`feature` \| `other`), `priority` (`low` \| `medium` \| `high` \| `urgent`),
`status` (`open` \| `in_progress` \| `resolved` \| `closed`), `created_by`,
`created_by_role`, `assigned_to`, `page_context` (the screen the report came from),
`attached_dataset_id` (a ticket points at a stored dataset instead of carrying its
own uploaded file), `comments[] {author_user_id, author_name, author_role, body,
created_at, internal}` — internal notes are never returned to non-staff,
`resolution_note`, `first_response_at`, `resolved_at`, `closed_at`,
`created_at`, `updated_at`, `schema_version`.

There is no human-readable `reference` number and no per-ticket `attachments[]`:
a staff reply sets `first_response_at` and moves `open` → `in_progress` with the
same write, and a viewer replying to a `resolved`/`closed` ticket re-opens it —
both recorded in the audit log rather than in a separate activity list.

## 17. `feedback`

`apps/support` (feedback flow). Indexed `(created_by, created_at desc)`, `status`.

`rating` (1–5), `category`, `message`, `page_context`, `created_by`,
`created_by_name`, `status` (`new` \| `reviewed` \| `actioned`),
`administrator_reply`, `replied_by`, `replied_at`, `created_at`, `updated_at`,
`schema_version`.

## 18. `platform_settings`

`common/schema.py` writes the `schema_version` marker on bootstrap; every other
row is created by an administrator through
`PATCH /api/v1/system/settings`, validated against the `editable_keys` whitelist
returned by `GET /api/v1/system/settings`. Unique on `key`.

`key`, `value`, `label`, `description`, `mode`, `applied_at`, `updated_at`,
`updated_by`. `schema_version` is written by the bootstrap itself (`mode` records
which datastore it was applied to) and carries no `label`; an administrator-edited
row carries `label`, `updated_by` and `updated_at` — `description` is not written
today, and the settings screen renders whatever subset a row has. Values an
administrator may set cover display defaults, alerting defaults, ingestion
schedule and retention; security-critical environment values are deliberately
**not** editable here.

This delivery ships **two rows**: the `schema_version: 3` marker and
`platform_display_name: "EarthScape Alpha"`, which was set through the admin UI
during verification. Display defaults, alerting thresholds and retention otherwise
come from `common/env.py` defaults, and the settings screen says so per row rather
than inventing stored values.

## 19. `backups` (filesystem + registry)

`BackupRecord` per `frontend/src/types/api.ts`: `mode`
(`atlas-mongodump` \| `local-json-snapshot`), `collections[]`, `document_count`,
`file_name`, `size_bytes`, `sha256`, `created_by`, `restore_verified` (set only
after a verification read actually ran), `restored_at`, `note`.

---

## Collection relationship map

```
users 1─┬─N refresh_tokens            users ─N audit_logs
        ├─N datasets ─N ingestion_jobs ─N climate_observations
        │                                  │
        ├─N processing_jobs ─ artifacts ────┤
        ├─N model_registry 1─N predictions ─┘
        ├─N alerts 1─N notifications
        ├─N reports
        ├─N saved_analyses
        ├─N support_tickets ─N comments (embedded array)
        └─N feedback
regions ── referenced by ── climate_observations.region_code / location_code
platform_settings ── read by ── every process at request time
```

## Index summary

18 collections, 48 indexes declared in `INDEX_SPECS`: 8 unique
(`ux_users_email`, `ux_refresh_hash`, `ux_regions_code`,
`ux_datasets_name_source`, `ux_obs_dedupe`, `ux_summary_key`,
`ux_model_name_version`, `ux_settings_key`), 1 TTL (expired refresh tokens), 1
geospatial `2dsphere` (sparse, so records without coordinates still store), the
remainder compound read paths. The unique observation dedupe key is the mechanism
that makes every re-run of an ingestion job safe.
