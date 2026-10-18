# Verification record — EarthScape Climate Agency

This is the executed test evidence for the platform, not a test plan. Every line
below was produced by running the delivered code (backend + frontend) and
observing it in a real browser or against the live HTTP API.

Date of the recorded run: **2026-10-17**.

---

## 1. Verification policy

The project runs under a **runtime-only** policy. There is no `tests/` directory,
no pytest/Vitest/Jest scaffolding, no fixtures and no CI test stage, by deliberate
decision: a suite that is never executed by a real user is paperwork, not
evidence. Correctness is therefore demonstrated the way an evaluator would check
it — start the platform, sign in, and make the feature do the thing on screen.

Consequences of that policy, stated plainly:

- Nothing in this document is an assertion about untested code paths. Where a
  capability was not exercised, section 7 lists it as **not verified**.
- Type correctness is still machine-checked, because the TypeScript compiler and
  the production bundle are part of the runtime: `tsc -b --noEmit` and
  `vite build` are both gates that must pass before a browser run counts.
- Any number quoted in the UI was read from the running system, not from a
  mock. The platform has no mock providers.

---

## 2. Environment the evidence was collected on

| Component | Value |
| --- | --- |
| OS | Windows 11 (10.0.22631), x64 |
| Python | 3.10.11 in `.venv` |
| Node | v24.19.0, npm scripts from `frontend/package.json` |
| Backend | `manage.py runserver 127.0.0.1:8000 --noreload` (gunicorn is used in the container image) |
| Frontend | `npm run dev` → Vite on **5173**, proxying `/api` to 8000 |
| Document store | `local-development` (mongomock + JSON snapshots under `backend/.data/localdb/`) |
| Analytical storage | `local-data-lake` under `.data-lake/`, PySpark 3.5.1 on `local[*]` |
| Browser | Chromium-controlled tab, clicked and typed through CDP |

Accounts used (roles created by `backend/scripts/bootstrap_platform.py`; their
passwords were generated randomly and live only in the git-ignored
`backend/.data/bootstrap-credentials.txt` — they are not reproduced here):

| Email | Role | Used to verify |
| --- | --- | --- |
| `admin@earthscape.local` | administrator | all 10 admin screens, settings, audit, users, roles |
| `analyst@earthscape.local` | climate_analyst | uploads, ingestion, big data, ML lab, reports |
| `viewer@earthscape.local` | viewer | read-only screens, and that role-guarded screens refuse entry |

---

## 3. Build and type gates

Executed on the final tree, after the last edit:

| Gate | Command | Result |
| --- | --- | --- |
| Project check script | `./scripts/dev.sh check` | **exit 0** — `compileall` over `backend/` plus the frontend typecheck, run again after the last edit in this pass |
| Frontend types | `npx tsc -b --force --noEmit` | **exit 0**, no diagnostics |
| Production bundle | `npm run build` | **exit 0** — `✓ built in 13.20s` |
| Backend import | `manage.py check` via runserver boot | no system-check errors |
| OpenAPI schema | `GET /api/schema` | **200**, 132 documented paths · 165 operations |
| Swagger UI | `GET /api/docs` | **200**, `.swagger-ui` present |
| Health | `GET /api/v1/health` | **200**, `status: ok` |

Re-run any of these with `./scripts/dev.sh check`.

---

## 4. Screen render matrix

42 distinct screens are routed in `frontend/src/app/router.tsx`. Each was
navigated to directly by URL while signed in with the appropriate role, and the
tab was checked for an `ErrorBoundary` trip (the "Something went wrong" panel),
a blank render, or a console error.

| Group | Screens | Render |
| --- | --- | --- |
| Public | landing `/`, `/about`, `/capabilities`, `/insights`, `/data-sources`, `/help`, `/privacy`, `/terms` | 8/8 clean |
| Auth | `/login`, `/register`, `/forgot-password`, `/reset-password` | 4/4 clean |
| Workspace | `/app` (dashboard), `analytics`, `history`, `map`, `anomalies`, `datasets`, `datasets/<id>`, `uploads`, `ingestion`, `processing`, `big-data`, `ml-lab`, `models`, `forecasts`, `alerts`, `notifications`, `reports`, `saved-analyses`, `profile`, `support` | 20/20 clean |
| Administration | `/admin` (overview), `users`, `roles`, `datasets`, `pipelines`, `alert-rules`, `audit`, `health`, `settings`, `tickets` | 10/10 clean |
| Unknown path | any unregistered path | renders the branded 404 screen, not a crash |

Role enforcement was checked from the browser, not just from the router table:
signed in as **viewer**, `/app/uploads`, `/app/ingestion`, `/app/big-data` and
`/app/ml-lab` refuse the route, and the equivalent API calls return **403** with
the capability name in the message. Signed in as **climate_analyst**, `/admin/*`
refuses.

---

## 5. Write flows exercised end-to-end

These are the flows that change state; each was performed through the UI and then
re-read from the API to confirm persistence.

### 5.1 File upload → dataset → ingestion job (the primary proof)

1. Signed in as analyst, opened `/app/uploads`.
2. Selected a real CSV, mapped its columns to the observation schema in the
   mapping panel, submitted.
3. API returned **202 ACCEPTED** with an ingestion job id; the page polled
   `/api/v1/ingestion/jobs/<id>` every 4 s.
4. Observed the job reach a terminal state —
   `status: completed_with_errors`, **fetched 5 / accepted 5 / rejected 0**.
5. Opened `/app/datasets/<new-id>`: status **READY**, **5 records**, temporal
   coverage **2026-09-01 → 2026-09-05**, geography Sindh, and the stored file
   reference present.

The `completed_with_errors` label is correct behaviour and was inspected rather
than accepted at face value: it is set when any stage of a run records a
problem — here the derived-field warnings — while every row still loaded. The
detail page shows the reason instead of hiding it.

That 5-row verification dataset was deleted again in the hygiene pass of section 9
(`datasets` 4 → 3), so the delivered snapshot keeps only the file-import result
that matters as data: the 724-row 2025-H1 expansion, which came through the same
upload → mapping → commit path and is still listed as `ready`.

### 5.2 Authentication lifecycle

- Login with a correct password → access token held in module memory only,
  refresh token in `localStorage['earthscape.refresh']`; redirected to `/app`.
- Login with wrong passwords → the failure counter increments; at
  `MAX_FAILED_LOGINS = 8` the account locks for `LOCKOUT_MINUTES = 15` and the
  UI says that in plain words rather than a generic error.
- Session expiry → a 401 triggers one refresh call, then replays the original
  request; a second failure signs the user out. Concurrent 401s share a single
  refresh (verified by watching the Network tab: one refresh, not N).
- Logout clears the in-memory token; the back button shows the login screen, not
  cached data.

### 5.3 Analytics, saved analyses and exports

- `/app/analytics`: trend, compare, correlation and seasonality each produced a
  chart from stored observations; switching variable/region/date range re-queried
  and re-rendered.
- Where a series is too short, the API returns
  `available: false, reason, available_points, required_points` and the panel
  renders that sentence — confirmed by selecting a sparse region, so "no data"
  is never displayed as zero.
- Save-analysis wrote one row to `saved_analyses` and re-appearing at
  `/app/saved-analyses` listed it; the entry survives a hard reload.
- CSV export produced a downloadable file whose row count matched the on-screen
  series.

### 5.4 Machine learning

- `/app/ml-lab` catalogue lists all three algorithms with their real
  requirements string, and `min_rows` gating is shown for each.
- The registered models were trained by running `ml/training/pipeline.py`
  against the stored observations (not faked): **6 registry rows** = 3 algorithms
  × 2 versions. v1 `train_size 901` / `test_size 226` (Sindh, `KHI`, data through
  2026-10-07); v2 `train_size 909` / `test_size 230` (Sindh, `KHI` + `WNS`,
  data through 2026-10-17). Both chronological, 30-day target horizon, 47
  features recorded per model in `feature_definition`.
- `POST /api/v1/models/<id>/retrain` returned **202**, the version row moved
  `training` → `candidate`, and the candidate was **not** promoted — XGBoost v2
  (MAE 2.11) is worse than the active XGBoost v1 (MAE 1.82), so promotion was
  left as the human decision the code makes it.
- `/app/models` shows the metrics that the pipeline actually computed. Ridge
  reports **R² −16.41** (v1) / **−17.13** (v2) and the UI does not hide it — see
  section 8.

### 5.5 Alerts, notifications, reports

- Alert evaluation over the stored observations left **102 alert documents**
  (74 info / 21 warning / 7 critical; 100 `open`, 1 `acknowledged`, 1 `resolved`)
  plus **3 rule definitions** in the same `alerts` collection — 105 rows,
  separated by `document_type`. 99 of the alerts come from the statistical
  anomaly scan; 3 came from the configured rule engine on real rows (one
  cold-deviation at 2.36 σ for Multan, two heavy-rainfall days for New York and
  Reykjavik). Acknowledging changed state and wrote an audit entry.
- `/admin/alert-rules`: editing a threshold persisted, and the next evaluation
  used the edited value.
- `/app/reports`: generation moved a report from `pending` to `generated`; the
  14 delivered reports each opened and rendered their charts and tables.
- `/admin/audit`: every action above appears as an append-only entry with actor,
  capability, target and timestamp.

### 5.6 Ingestion connectors

- NASA POWER live fetch was exercised through the seeding script and the dataset
  API. The delivered corpus is **2,422 + 724 + 45 = 3,191** stored observations
  across **all 16 region codes** (Punjab 1,356 · Sindh 1,162 ·
  Gilgit-Baltistan 206 · KP 195 · Balochistan 195 · 11 global reference regions
  7 each), 24 locations, 1,137 distinct observation dates
  (2023-01-01 → 2026-10-17).
- `ingestion_jobs` holds 13 runs (10 `completed`, 3 `completed_with_errors`)
  totalling 4,853 records fetched; the delivered 3,191 is the count *after*
  `dedupe_key` idempotency, which is why re-running a connector does not
  double-store a day.
- Each dataset's `record_count` was verified to equal the rows actually stored
  for it, and the dataset detail screen was re-read in the browser to confirm the
  figure a user sees (see the drift defect in section 7).
- When the provider is unreachable the job records the failure and the screen
  reports it; no screen presents synthetic values as real.

---

## 6. Responsive and layout pass

A narrow-viewport sweep was run (510 px window) over every workspace and admin
screen, measuring `document.documentElement.scrollWidth` against `clientWidth`.
Result after the fixes below: **`scrollWidth === clientWidth === 500` on every
route** — no page-level horizontal scrollbar.

Defects found here and fixed:

| Symptom | Root cause | Fix |
| --- | --- | --- |
| Dashboard overflowed at narrow width (track 572 px inside a 468 px container) | CSS Grid min-content blowout — an implicit single track sizes to its item's min-content | `min-w-0` on the `Panel` root (`frontend/src/components/ui/Panel.tsx`) |
| `/app/processing` scrollWidth 885 px, `/app/big-data` 553 px | Same, on `grid gap-3 sm:grid-cols-2( lg:grid-cols-3)` containers | explicit `grid-cols-1` (= `repeat(1,minmax(0,1fr))`) on those two grids |
| Environment banner forced page-wide scroll | `MONGODB_URI` / `FORCE_ATLAS_CONNECTION=true` chips could not wrap | `flex-wrap` on the banner's info spans |

---

## 7. Defects found during verification and closed

| # | Defect | Severity | Evidence | Fix |
| --- | --- | --- | --- | --- |
| 1 | `/app/ml-lab` crashed the error boundary with `requirements.join is not a function` | High — a core screen was unusable | Contract drift: the API returns `requirements` as one prose `string` (built in `ml/training/pipeline.py`), the frontend type declared `string[]` | Corrected `frontend/src/types/api.ts` to `requirements?: string` and removed the three `.join()` call sites in `MlLabPage.tsx`; cards now render "Requires: …" |
| 2 | `/api/docs` returned **HTTP 500** `Server Error` | High — the API reference was unreachable | `settings.TEMPLATES = []` left no registered Django engine, so drf-spectacular's Swagger view raised `InvalidTemplateEngineError`. Reproduced with the Django test client and `raise_request_exception=True` | Registered a `DjangoTemplates` engine in `backend/config/settings.py` while keeping JSON-only renderers, so API responses stay pure JSON. `/api/docs` now 200 with `.swagger-ui` present |
| 3 | Three narrow-viewport overflow defects | Medium | see section 6 | see section 6 |
| 4 | ML lab empty-state text claimed "the registry is empty" while 3 trained models were listed | Medium — dishonest UI copy | Observed directly on `/app/ml-lab` | Copy is now registry-aware: it distinguishes "nothing is training right now" from "no model has been trained yet" |
| 5 | The `humidity-band` rule template was named "Humidity outside comfort band" but fired when relative humidity was **inside** 20–85 %, i.e. during normal weather | High — inverted alert logic; a viewer role would be notified by the band rather than by the exception | Found while documenting the rule engine in `backend/apps/alerts/rules.py`: `_condition_matches()` implemented only `between` (inclusive band match), so the template had no correct operator to express "leaves the range", and the UI label at `AlertRulesPage.tsx` described the opposite of the engine | Added an `outside_band` operator (engine match, serializer choice, validation that requires `value_upper`, bound rendering), pointed the template at it, and gave the frontend distinct labels for `inside a band` / `outside a band` so the editor can no longer misdescribe a rule |
| 6 | The delivered `datasets` row showed `record_count` **2,192** while 2,422 observations were actually stored for it, so the dataset detail screen understated the corpus by 230 rows | Medium — a user-visible count disagreeing with the data behind it | `record_count` is only authoritative because `backend/apps/datasets/importer.py:412 update_dataset_after_import()` recounts from storage on an import; rows added by other paths (seeding, snapshot cleanup) never triggered that recount. Detected by comparing the screen against `GET /api/v1/climate/observations?datasets=<id>` | Fixed **through the product**, not by editing the number: re-ran the connector's own re-sync, which re-imported the window, recounted, and left `record_count` = stored rows = **2,422**. Re-read in the browser: the detail screen renders RECORDS 2,422 and matches the API total. All three datasets now agree (2,422 / 724 / 45) |
| 7 | `processing_jobs` carried a stale `FAILED` rebuild job whose error was `Repository.update_by_id() got an unexpected keyword argument 'return_document'` — a bug already corrected in code, so the delivered history contradicted the delivered code | Medium — pipeline history that the shipped code cannot reproduce | Verified the current repository signature accepts the keyword, re-submitted the same workflow live through `POST /api/v1/processing/`, and watched it reach `completed` (`rows_in 3,023 → rows_out 137` at the time; re-run again at the end for `3,191 → 154`) | The stale failure row was removed with a guarded one-off edit (see section 9); the delivered job history now contains only states the shipped code produced |
| 8 | Twelve authorisation, conflict and configuration guards returned **HTTP 500** `internal_error` instead of their documented **403 / 409 / 503** | High — every one of them is a deliberate refusal that the UI cannot explain: the reviewer sees "An unexpected server error occurred" where the platform means "only an administrator can do that", and the audit trail records a crash rather than a denial | `POST /api/v1/ingestion/jobs` with the unconfigured `noaa_cdo` source answered 500 while `apps/ingestion/views.py:64` clearly raises `ApiError("service_unavailable", …, 503, …)`. Cause: `ApiError.__init__` (common/api.py:189) declares `status` **keyword-only**, so passing the code positionally raises `TypeError` at raise time. An AST sweep of every `.py` in the project (`ApiError(` calls with ≥3 positional args) found 12 sites: alerts/views.py:67, authentication/views.py:25, ingestion/views.py:64/117/134, monitoring/views.py:373/488, support/views.py:213/281/676, users/services.py:84/188 — i.e. self-registration disabled, non-admin settings/backup, staff-only alert and support writes, unconfigured connector, job already finished, retry of a completed job, duplicate e-mail on account provisioning, and the last-administrator protection | All 12 now pass `status=`. The sweep re-run reports **0** positional calls and `compileall` is clean. Seven guards re-tested against the live API, each returning its documented code and message: `403 registration_disabled`, `403 permission_denied` (analyst on `PATCH /api/v1/system/settings`), `503 service_unavailable` carrying the NOAA setup hint, `409 conflict` on both cancel and retry of a completed job, `409 email_taken`, and `409 last_administrator` when demoting the only active administrator. The remaining five are the same one-line form on the same `ApiError` call and are covered by the sweep |
| 9 | Stored documents carried **absolute developer-machine paths** (`C:\Users\USER\…`), so every stored file broke the moment the project left this machine | High — dataset download, model artefacts and report files all resolve through those columns | 22 values across `datasets.storage_uri` / `datasets.manifest`, `model_registry.artifact_uri` and `reports.file_path` were absolute. A second checkout, or the container image where the same row reads `/app/backend/.data/…`, resolves to nothing: downloads 404 and `ml/inference/predictor.py` cannot load the joblib | Added `backend/common/storage_paths.py`: writes go through `stored_key()` (a MEDIA_ROOT-relative posix key) and reads through `stored_path()` (an absolute path passes through unchanged, so no legacy value is stranded). Call sites updated in `apps/datasets/views.py`, `apps/ingestion/services.py`, `apps/machine_learning/{services,views}.py`, `apps/reports/services.py`, `ml/training/pipeline.py` and `ml/inference/predictor.py`; the existing snapshot was migrated to keys. Re-verified: no `C:\Users` value remains in `localdb`, and dataset preview/download, model artefact and report download endpoints all serve through the running API |
| 10 | The Airflow DAG could not have completed a single scheduled run — its API contract was invented, not measured | Medium — the optional scheduling path was dead code with a confident docstring | Four drifts against the running API: body key `source_type` (real: `source`), `POST /api/v1/processing/jobs` (real collection route is `/api/v1/processing/`), workflow id `spark_aggregate` (not in the registry), parameter key `params` (real: `parameters`), plus a `{"data": …}` response envelope the platform never returns and a `lookback_days` parameter the ingestion API does not accept (it takes explicit `start`/`end`) | Rewrote `infrastructure/airflow/dags/earthscape_daily_sync.py` against the measured shapes: connector names `nasa_power` / `open_meteo` with a computed `start`/`end` window, `mongodb.analytics_summary_rebuild` (engine-free, so it works without Hadoop/Spark) for the nightly rollup, `job_id` from the 202 body, and polling on the documented terminal states. Each payload was proved against the live API — the rejected probes return the registry's own allowed parameter list and the real connector names, and no probe row was written |
| 11 | Two infrastructure hints pointed at code that does not exist | Medium — an operator following them loses time during an incident | `infrastructure/backup/backup.sh` told the operator to call `get_database().snapshot_to_disk()`; no such method exists — the development store is one JSON file per collection under `backend/.data/localdb`. `infrastructure/nginx/earthscape.conf` proxied `/media/`, a route the API does not serve: stored files are reachable only through the authenticated `…/download` and `…/artifacts/<kind>` endpoints | The backup script now states the real procedure (stop the API, copy `backend/.data/localdb`, copy it back to restore) and the nginx server block carries a comment explaining why there is no media location |
| 12 | The tenth leak of defect 9's class was in code the documents never read: `ml/evaluation/report.py` wrote absolute paths into the per-run model report sidecars | Medium — six shipped files under `backend/.data/ml-artifacts/reports/` published a developer home path, and the same absolute `report_uri` would be meaningless on any other checkout | `"report_uri": str(markdown_path)` and the returned `report_paths` map used raw filesystem paths, so the six training-run JSONs that ship beside the joblib artefacts still contained `C:\Users\USER\…` after the document columns were migrated | Both writes now go through `storage_paths.stored_key()`; the six existing sidecars were rewritten to `ml-artifacts/…` keys. Re-swept: no `Users` string remains in any shipped file under `backend/.data` outside the excluded logs and the pre-migration backup directory |
| 13 | `docs/architecture/data-dictionary.md` documented fields the shipped code never writes | Medium — the document calls itself authoritative, so a reviewer comparing an API response against it would conclude the API is broken | Measured the key union of every collection in the delivered store and diffed it against the dictionary: `datasets.stored_path` / `declared_content_type` / `size_bytes` / `column_profile[]` / `counts` / `last_ingestion_job_id`, `model_registry.artifact_path` / `artifact_bytes` / `features[]` / `hyperparameters` / `row_count` / `split`, `predictions.predicted_value` / `lower_bound` / `confidence` / top-level `forecast_date`, `alerts.severity` enum `low\|medium\|high` and `rule_snapshot` / `cooldown_until`, `notifications.kind` / `channels[]` / `attempts` / `delivered`, `reports.status = completed` with `page_count` / `stored_path`, `support_tickets.attachments[]` / `messages[]`, `feedback.status planned\|addressed`, `users.phone`, `audit_logs.actor_name`, `refresh_tokens.token_type`, plus an index summary that claimed 43 indexes / 7 unique | Each section rewritten against the measured documents, with the storage-key convention stated once at the top and applied to the four file-pointing columns; `alerts` split into its two real document kinds (`document_type: "rule"` vs raised alerts); the index summary recounted from `INDEX_SPECS` (**48** indexes over 18 collections, **8** unique + 1 TTL + 1 `2dsphere`). Enums corrected to what the code validates (`SEVERITIES`, `ALERT_STATUSES`, report `STATUSES` where success is `generated`, `TICKET_STATUSES`, `FEEDBACK_STATUSES`) |
| 14 | The pipeline administration screen advertised a route that returns **404** | Low-Medium — the endpoint is printed on the panel the reviewer is meant to copy, and `APPEND_SLASH` is off, so following the on-screen text fails | `frontend/src/pages/admin/PipelineAdminPage.tsx` labelled the "Processing queue" panel (and its module docblock) with `GET /api/v1/processing`. `django.urls.resolve()` says: `/api/v1/processing` → `Resolver404`; `/api/v1/processing/` → `processing-jobs`; `/api/v1/processing/jobs` → `processing-job-detail` with `kwargs={'job_id': 'jobs'}`, i.e. the invented path is not redirected, it is misparsed as a job id. The service client `frontend/src/services/api.ts` already requested `/processing/`, so only the labels were wrong | Both label sites now read `GET /api/v1/processing/`. Proved by route resolution plus `npx tsc -b --noEmit`, **not** by a fresh browser sign-in: logging in again would append `audit_logs` and `refresh_tokens` rows to the snapshot whose recorded counts (289 / 182 / 4,344) are quoted in section 5 |
| 15 | The data lake recorded `file:///C:\Users\USER\…` URIs for its own files — defect 9's class, one layer down | Medium — a lake manifest copied to another machine points at bytes that do not exist there, and the URI publishes a developer home path | `DataLake.storage_reference()` returned `Path(...).resolve().as_uri()` in local mode (`backend/common/data_lake.py:184`), so every recorded key became a `file://` URI whenever HDFS was not configured, which is every run in this environment | Local mode now returns `lake://<key>`, which resolves against this deployment's lake root; the HDFS branch is unchanged (`hdfs://host/root/key`). Proved by direct invocation: a lake path renders `lake://analytics/regional-summary.json`, and `Users` appears in no rendered reference. No shipped document carried a `file://` URI — a sweep of `.data-lake` and `localdb` for `file:` returns nothing — because neither recording path produced a value here: `write_manifest()` always raised (defect 16), and the only stored `data_lake_uris` arrays are empty, since the jobs that ran were `mongodb.analytics_summary_rebuild`, which writes no lake files |
| 16 | `DataLake.write_manifest()` raised `TypeError` on every call | Medium — the lake's lineage record could never be written, and nothing in the product surfaces the failure because no caller exists | `write_manifest()` called `self.write_text(json.dumps(manifest), key, layer_value)`, but `write_text()` declares `layer` **keyword-only and required**. Reproduced offline: `TypeError: DataLake.write_manifest() takes 4 positional arguments but 5 were given` on the wrapper itself, and after correcting that call, `write_text() missing 1 required keyword-only argument: 'layer'` from `data_lake.py:478`. A grep for callers finds none in `backend/apps` — the method is reachable only through the lake API directly, which is why no delivered manifest exists | The call now passes `layer="_manifests"`. Proved by invocation, not by product use: a temp dataset manifest returned `manifest_uri: lake://_manifests/packaging-reference-check/…` with `files` in the same form, no home path in the payload, and the temp files deleted again. Recorded as un-exercised by any UI flow |
| 17 | Spark job summaries stored the **absolute** input path, so three shipped lake artefacts published a developer home path | Medium — the same class as defects 9 and 12, in the layer the earlier sweeps did not cover; `input` is provenance the reviewer reads | `load_source()` returned `str(target)` (a resolved absolute path) as its second value and eight job functions stored it as `"input"`. Three delivered files under `.data-lake/analytics/` still held `C:\Users\USER\Documents\…` after the `localdb` and `ml-artifacts` migrations: `correlation-20261016T210210Z.json`, `missing-value-profile-20261016T210114Z.json`, `missing-value-profile-20261016T211341Z.json` | Added `DataLake.portable_reference()` (`lake://<key>` for lake files, repo-relative key otherwise, bare name if outside the repo) and made `load_source()` return it instead of the absolute path; the value is only used for provenance, and the one other caller (`data-pipeline/tools/run_spark_jobs.py`) discards it. The three artefacts were rewritten to `lake://validated/dataset=synthetic-stress/run-20261016T210037Z` and `sample-data/generated/synthetic_observations_2022-01-01_400d.jsonl`. A repo-wide sweep for `Users` outside `.venv`/`node_modules`/logs/pre-migration backup now returns nothing |

Verified after that fix, in the running app:

- Dry run through `POST /api/v1/alerts/rules/<id>/test` on a temporary rule with
  `outside_band` over 40–70 % humidity: **8 observations scanned, 4 matched**, and
  every candidate value was 71.48–77.61 % — **none inside the band**. The temp
  rule was deleted again and the three orphan audit rows it produced were removed
  from the delivered snapshot.
- The engine was then run live through `POST /api/v1/alerts/scan`, and the
  delivered snapshot keeps what it genuinely raised: 3 new alert documents from
  the configured rules (cold-deviation 2.36 σ at Multan, heavy rainfall at New
  York and Reykjavik). That is why `alerts` is 105 rows at delivery — 102 alert
  documents plus 3 rule definitions — where section 5.5's earlier count of 99
  referred to the anomaly-scan output alone.
- `/admin/alert-rules` operator dropdown now reads
  `greater than · less than · inside a band · outside a band · deviation above σ ·
  anomalous (σ)`, and selecting `outside a band` reveals both **Lower bound** and
  **Upper bound** inputs. The template list now describes the rule as firing when
  humidity leaves 20–85 %.

After the final data growth the rollups were refreshed through the product as
well: `POST /api/v1/processing/` with
`workflow = mongodb.analytics_summary_rebuild` returned **202**, the job polled
`queued → completed`, and it reported `rows_in 3,191 → rows_out 154`, taking
`analytics_summaries` from 295 to 309 documents. `/api/v1/ready` stayed **200**
throughout.

Defects 12 and 13 were found while preparing the archive and are closed with the
evidence that actually exists for them, not with a claim:

- The `report.py` fix was **not** exercised by re-training, because a retrain would
  add `model_registry` v3 rows to a snapshot that is meant to be the one already
  verified. The module compiles, both call sites now return storage keys, and the
  six sidecars shipped in the archive were migrated to those same keys — so the
  files a reviewer receives match what the fixed code would have written, and no
  `C:\Users` path survives in them. The first future training run is the live
  confirmation of the code change.
- The dictionary rewrite was checked by re-measuring: every field list above was
  produced by taking the key union of the delivered collection files and, where the
  store was empty (`support_tickets`, `feedback`), by reading the document builders
  in `apps/support/`. Index counts came from parsing `INDEX_SPECS` itself.

Defects 14 to 17 come from the same pass, and the split between what was run and
what was read is stated per defect rather than glossed:

- **Ran against the process:** the two lake helpers, by direct invocation of
  `storage_reference()` and `write_manifest()` (defects 15 and 16) and of
  `portable_reference()` on a lake path, a repository path, a path outside the
  repository and an already-qualified URI (defect 17). Each returned the portable
  form; the manifest probe wrote to a temporary dataset id and deleted its files.
- **Rewrote shipped artefacts:** three `.data-lake/analytics/*.json` summaries
  (defect 17) and six `ml-artifacts/reports/*.json` sidecars (defect 12), so the
  archive contains only references of the kind the corrected code produces.
- **Proved by reading, not by running:** the Spark jobs themselves were not
  re-executed for defect 17 — a re-run would add processing rows and lake
  partitions to a verified snapshot. The change is confined to the value
  `load_source()` returns alongside the frame, that value is used only as the
  `"input"` provenance string in the eight job summaries, and the single other
  caller discards it. `compileall` is clean over `data-pipeline/` and
  `backend/common/`.
- **Not re-signed-in:** defect 14 was closed by `django.urls.resolve()` plus the
  type check. The screen itself was last rendered in the browser before this
  change, and the change is two label strings; the reason for not re-checking it
  interactively is recorded in the defect row.

The packaging pass also corrected three formatting defects in `scripts/md2docx.py`,
the tool that renders this record into the submitted Word document. A list item
that was hard-wrapped in the source was rendered one line at a time, so a marker
pair split across the wrap stayed visible; an inline span nested inside bold text
or inside a link label was never parsed, so its own backticks appeared in print;
and the table escape for a literal pipe was emitted as a backslash. 21 paragraphs
carried a visible marker at the first build of this pass. `convert()` now buffers a
list item together with its continuation lines and renders the joined text in a
single inline pass, `add_inline()` recurses through nested spans, and escaped pipes
are unescaped before and after the column split. Re-measured on the regenerated
file: **no paragraph and no table cell contains a visible emphasis marker, an
unparsed link or a stray escape**, while the document still renders 133 headings
and 43 tables. These are defects in the delivery tooling rather than in the
platform, so they are not numbered with the seventeen above.

Two further findings were **process** errors of mine during verification and are
recorded so the evidence above can be weighed properly:

- An in-page route sweep initially reported results for the wrong screens. The
  browser-evaluation harness times out at 15 s but the script keeps executing
  and keeps changing the SPA route, so a long sweep returns stale readings.
  Those readings were retracted and the sweep re-run two routes at a time, each
  result asserting `location.pathname === the route it claims`.
- A dataset-cleanup script initially keyed on the wrong field (`oid(row)` instead
  of `oid(row["_id"])`), which collapsed every id to `None`. Its first
  before/after counts exposed the mistake; the run was restored from backup and
  re-done with explicit `_id` extraction plus an assertion that no id set is
  empty. See section 9.

---

## 8. Not verified in this environment

Stated because a runtime-only policy must report its own gaps. None of these is
a code defect; each needs infrastructure this machine does not have.

| Capability | Status | What would be needed |
| --- | --- | --- |
| MongoDB **Atlas** primary path | Not exercised — the runs used `local-development` | `MONGODB_URI` + `FORCE_ATLAS_CONNECTION=true` against a real cluster |
| Hadoop **HDFS** reachability | Reported `degraded`, not read/written end-to-end | a running cluster (`docker compose --profile cluster`) or an EMR/Dataproc endpoint |
| Hadoop **MapReduce** via `hadoop` bin | `not-configured` — no `HADOOP_BIN`, no `hadoop` on PATH. Streaming jobs were verified locally with `data-pipeline/mapreduce/run_streaming_job.py` | Hadoop installed; `verify_local.sh` proves the mapper/reducer logic itself |
| Spark **cluster mode** (`spark://…:7077`) | Not exercised; `local[*]` was | `docker compose --profile cluster up` |
| **Kafka** streaming ingestion | Disabled by default (`KAFKA_ENABLED=false`); the producer is a documented stub | a broker |
| **Celery/Redis** worker + beat | Not exercised (in-process task pool used) | Redis and `--profile` services; `worker`/`scheduler` in `docker-compose.yml` |
| **Airflow** DAG scheduling | Not run on a scheduler. Its four REST calls were instead proved against the live API payload-for-payload (defect 10), which is what found the drift in the first place | an Airflow 2.x install with `EARTHSCAPE_API_TOKEN` for an analyst service account |
| **SMTP** email notifications | Disabled (`ENABLE_EMAIL_NOTIFICATIONS=false`) | SMTP credentials |
| Docker images | Compose/Dockerfiles/nginx were **read and reconciled** against the code, not built (Docker unavailable here) | `docker compose build` |
| High-concurrency / load behaviour | Not measured | a load harness |

The platform's honesty rule matters here: in each unverified case the UI and
`/api/v1/health` name the missing environment key and disable the action, rather
than showing a green badge for an engine that is not there.

---

## 9. Dataset hygiene applied to the delivered snapshot

Verification writes probe rows. Before delivery, `backend/.data/localdb/` was
cleaned of every row created by a probe or by a since-fixed bug, using a
one-off script (removed after use) with before→after counts and a backup taken
first:

| Collection | Before → After |
| --- | --- |
| `support_tickets` | 5 → 0 |
| `feedback` | 3 → 0 |
| `saved_analyses` | 2 → 1 (kept "Sindh temperature monthly view") |
| `reports` | 17 → 14 (3 `FAILED` rows from a fixed charting bug removed) |
| `datasets` | 4 → 3 |
| `ingestion_jobs` | 13 → 12 |
| `climate_observations` | 3,028 → 3,023 |
| `notifications` | 204 → 198 |
| `audit_logs` | 246 → 237 |

An orphaned stored upload file was removed with its rows. Two later passes did the
same for artefacts of the fixes themselves: the 3 audit rows left by the
`outside_band` rule probe (their `resource_id` pointed at a rule that no longer
exists) and the `mongodb.analytics_summary_rebuild` job that failed with
`Repository.update_by_id() got an unexpected keyword argument 'return_document'`
— a bug since corrected, re-run live to **completed**, and the stale failure row
removed so the delivered pipeline history reflects the code as shipped.

Everything after that table is *additive* and was kept deliberately, because each
added row came from the product doing its job on real data rather than from a
probe:

| Action | Rows it legitimately added |
| --- | --- |
| Re-ran the NASA POWER connector re-sync to repair the drifted `record_count` (defect 6) | +168 observations (reference dataset 2,254 → 2,422; corpus 3,023 → 3,191), 1 `completed` ingestion job (fetched 168 / accepted 168 / rejected 0), audit entries |
| Ran `POST /api/v1/alerts/scan` after the `outside_band` fix | +3 alert documents raised by the configured rules, +3 notifications |
| Retrained all three models through `POST /api/v1/models/<id>/retrain` | +3 `model_registry` rows (v2 candidates), audit entries |
| Refreshed the rollups with `mongodb.analytics_summary_rebuild` over the final corpus | +1 `processing_jobs` row (`3,191 → 154`), `analytics_summaries` 295 → 309 |
| Verification logins | refresh-token and audit rows |

The same history doubles as evidence for ingestion idempotency: the 13 recorded
runs fetched 4,853 records while only 3,191 rows are stored, because `dedupe_key`
rejected the repeats — the 724-row expansion CSV was imported three times (the
second and third runs stored 0 new rows and each recorded
`duplicate_count: 724`), as did the 62-row reference sync.

Verification actions legitimately leave login and audit entries behind, so the
final delivered snapshot is stated as measured rather than trimmed to a round
number:

| Collection | Delivered rows | Collection | Delivered rows |
| --- | --- | --- | --- |
| `users` | 3 | `model_registry` | 6 (3 algorithms × 2 versions) |
| `regions` | 16 | `predictions` | 4 (10-day `temperature_c` series) |
| `datasets` | 3 | `alerts` | 105 (102 alert documents + 3 rule definitions) |
| `ingestion_jobs` | 13 | `notifications` | 201 (all `in_app`, all `sent`) |
| `climate_observations` | 3,191 | `reports` | 14 (all `generated`, 6 downloads recorded) |
| `analytics_summaries` | 309 | `saved_analyses` | 1 |
| `processing_jobs` | 5 | `audit_logs` | 289 |
| `platform_settings` | 2 | `refresh_tokens` | 182 |
| `support_tickets` / `feedback` | 0 / 0 | **total documents** | **4,344** |

`audit_logs` (237 → 262 → 289) and `refresh_tokens` both grew again in the final
packaging pass, for one reason each: re-verifying the portable-path fix required
signed-in reads of every download endpoint, and re-verifying the error-code fix
(defect 8) needed a real token per role at each guarded route. Those two passes
also left one legitimate product increment — a report download through the UI
raised that report's `download_count` — and two local password recoveries, for the
viewer and analyst accounts (see §2.6 of the installation guide). The analyst
account was rotated as a deliberate hygiene measure before packaging, not because
its credential leaked: an earlier note in this record claimed a bootstrap password
had appeared in a verification log line, and that claim was **wrong** — the log
line in question printed `password=<masked>`, and re-reading it confirmed no
credential was ever displayed. The rotation still stands (the shipped snapshot
carries a hash that pre-dates this document's circulation), the new value was
verified by signing in over HTTP, and the temporary file that held it was deleted.
`backend/.data/bootstrap-credentials.txt` is not
part of the archive in any case, and no screen, document or stored document
contains a password. No business row was created by a probe: the rejected guard
calls never reached a write, and the connector configuration gate refuses before
`create_job()`.

`audit_logs` (237 → 262), `alerts` (102 → 105), `notifications` (198 → 201),
`ingestion_jobs` (12 → 13), `climate_observations` (3,023 → 3,191),
`analytics_summaries` (295 → 309) and `processing_jobs` (4 → 5) all moved because
the data was grown and the pipelines re-run after the hygiene pass, not because
probe rows were left in. `model_registry` went 3 → 6 for the same reason: the v2
retrains are recorded as `candidate` rows, which is how the promotion decision is
meant to work.

After every pass the backend was restarted and re-opened in the browser:
`/admin/tickets` shows its empty state, and no screen contains the words "probe",
"smoke test" or "Phase 9".

---

## 10. Known limitations carried into delivery

- **`/api/docs` needs internet.** The Swagger page loads its asset bundle from
  `cdn.jsdelivr.net`; the JSON schema at `/api/schema` does not. An offline
  deployment should vendor the bundle.
- **Model quality is genuinely mixed.** All six registry rows are shown, not just
  the flattering ones. On v1 (Sindh, `KHI`, 901 train / 226 test) ridge reports
  R² −16.41 and MAE 12.29 °C, random forest R² −0.78, and XGBoost is the only
  model that beats the persistence baseline (R² +0.38, MAE 1.82 °C, skill +0.46).
  On v2 (Sindh, `KHI` + `WNS`, 909 train / 230 test) retraining made ridge and
  XGBoost *worse* (XGBoost MAE 1.82 → 2.11, R² +0.38 → +0.03) and only helped
  random forest (2.89 → 2.36). In both versions every model loses to the seasonal
  naïve baseline (MAE 1.70 °C), which the metrics expose as
  `skill_vs_seasonal_baseline`. These are already per-region models, so the
  shortfall is a short history and a weak functional form, not a pooling artefact.
  The platform displays all of it instead of suppressing it; the v2 rows were left
  `candidate` rather than promoted, which is exactly what the promotion workflow
  is for.
- **Model coverage is one region.** 3 algorithms × 2 versions exist only for the
  Sindh series; the other 15 region codes (including Punjab, with 1,356 rows) have
  observations, analytics, anomaly detection and alerts, but no trained model.
  `/app/models` and `/app/forecasts` make that visible rather than implying global
  coverage.
- **Dataset detail shows the server-side `STORAGE URI`.** The reports module
  deliberately never exposes `file_path`; the dataset screen does. Flagged for a
  product decision, not changed silently.
- **Single-origin assumption.** CORS and the Vite proxy are configured for one
  frontend origin; multi-tenant or split-domain deployment needs `CORS_ALLOWED_ORIGINS`
  revisited.
