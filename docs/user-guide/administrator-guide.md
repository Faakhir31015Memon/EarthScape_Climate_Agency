# Administrator guide

For the `administrator` role. The analyst/viewer walkthrough lives in
[`README.md`](README.md); this file covers `/admin/*`, the settings whitelist, the
alert-rule engine, rate limits and audit retention. Everything below was read out
of the shipped code, so each action names the endpoint it calls.

Access to `/admin` requires the `system:read` capability; the overview screen
renders a refusal panel instead of data if your account lacks it. Several admin
screens additionally require the **administrator role**, not just a capability —
`/admin/settings` and the backup button check both.

---

## 1. Platform overview (`/admin`)

Read-only landing page. It polls `system.status` every 60 s and reads
`alerts.summary`, `notifications.unread` (120 s) and `support.summary`, then links
to the places that need attention: pipelines, tickets, alerts, reports. Nothing
here mutates state — if the overview says a queue is backing up, you act on the
screen it links to.

## 2. Users (`/admin/users`)

Requires `users:manage`.

- **Filter** by free-text search, role, status (`all / active / inactive /
  locked`) and sort field; results are server-paged.
- **Create a user** — role dropdown offers all three roles, defaulting to
  `viewer`. You do not choose the password: the account is created and the
  credential is delivered out of band, then the user changes it at first login.
- **Edit** profile fields and role. A role change asks for confirmation because
  it revokes the target's existing sessions.
- **Activate / deactivate** — deactivation is a soft flag (`is_active: false`);
  accounts are never hard-deleted, so the audit trail keeps resolving the actor.
- **Unlock** an account locked by the failed-login policy (8 attempts, 15 minutes)
  instead of waiting for the timer.
- **Reset password** — generates a new credential out of band; the API never
  returns a password in a response body.
- You cannot deactivate your own account (the UI blocks it and the API rejects it).

Endpoints: `GET/PATCH /users`, `GET /users/<id>`, `POST /users`,
`/users/<id>/activate`, `/users/<id>/deactivate`, `/users/<id>/unlock`,
`/users/<id>/reset-password`, `/users/<id>/role`, `/users/<id>/sessions`.

## 3. Roles and capabilities (`/admin/roles`)

Requires `users:manage`. This screen is a **matrix, not an editor**: it renders
the server's `ROLE_CAPABILITIES` map next to the capability names the frontend
guards on, so you can see drift between the two instead of trusting either.

| Role | Capabilities | Notable absences |
| --- | --- | --- |
| `administrator` | 26 | — |
| `climate_analyst` | 19 | no `users:*`, no `audit:read`, no `system:manage`, no `feedback:manage`, no `support:manage` |
| `viewer` | 7 | `climate:read`, `datasets:read`, `analytics:read`, `alerts:read`, `reports:read`, `feedback:create`, `support:read` — read-only plus self-service |

The **Change a role** panel writes through `users.update({role})` and refuses to
demote the last remaining administrator. Capability lists are defined in
`backend/apps/authentication/views.py` and are not editable at runtime by design;
adding a role is a code change.

## 4. Dataset governance (`/admin/datasets`)

Requires `datasets:manage` to archive and `ingestion:run` to sync.

- Filter by search, status, source, format and sort; select a row for the detail
  panel (row counts, coverage, storage reference, validation state).
- **Re-sync** — only enabled for live-source datasets; starts an ingestion job
  (`POST /ingestion/jobs`).
- **Re-validate** — `datasets.validate`; disabled when the dataset has no
  `storage_uri`, because there is nothing on disk to re-read.
- **Archive** — soft delete. Rows disappear from user lists; the data and its
  audit history stay.
- **Retry** a failed or cancelled ingestion job from the same screen.

## 5. Pipelines (`/admin/pipelines`)

Three queues side by side — ingestion, processing, reports — each with its
running/queued counts and recent jobs. From here you can submit a processing
workflow (`processing.start`), **cancel** a queued or running job
(`ingestion.cancel` / `processing.cancel` / `reports.cancel`; cancelling a report
needs `platform:manage`), **retry** an ingestion job, download a finished report,
and open the processing log for a run. Engine status and the workflow catalogue
come from `/api/v1/processing/workflows` and `system.health` (polled every 30 s).
An engine that is not configured shows `not configured` with the environment key
it needs; the action buttons stay disabled.

## 6. Alert rules (`/admin/alert-rules`)

Requires `alerts:manage`. This is the most write-heavy admin screen.

**Rule model** — a rule names a target metric (from `climate.metrics`), a
condition, a severity (`info / warning / critical`), a cooldown in minutes, a
scope (regions and/or datasets), the sources it applies to
(`observations`, `predictions`, or named provider feeds), and recipients by role,
account or "everyone".

**Operators** offered by the engine
(`backend/apps/alerts/rules.py`):

| Operator | Fires when | Needs |
| --- | --- | --- |
| `gt` | value **>** threshold | `value` |
| `lt` | value **<** threshold | `value` |
| `between` | `value` **≤** value **≤** `value_upper` (inclusive band) | `value`, `value_upper` |
| `outside_band` | value falls **outside** `value … value_upper` | `value`, `value_upper` |
| `deviation_gt_sigma` | value is more than σ from the climatology computed from the stored record | `sigma` (default 2.5) |
| `anomaly` | value is flagged anomalous by the detector | `sigma` |

`between` and `outside_band` are opposites and the editor labels them distinctly
("inside a band" / "outside a band") — a band rule whose label contradicts its
operator is how you get alerts that fire in normal weather.

**Templates** (`GET /api/v1/alerts/templates`) seed a working rule in one click:

| Template | Condition | Severity | Cooldown | Recipients |
| --- | --- | --- | --- | --- |
| Extreme heat warning | `temperature_c > 40.0` | critical | 720 min | climate analysts |
| Cold stress alert | `temperature_c < 0.0` | warning | 720 min | climate analysts |
| Heavy rainfall | `precipitation_mm > 80.0` | warning | 360 min | climate analysts |
| Humidity outside comfort band | `relative_humidity_pct` outside 20.0–85.0 | info | 1440 min | viewers |
| Statistical anomaly | `temperature_c` deviation > 2.5 σ | warning | 720 min | climate analysts |

Each template is marked with whether its variable is actually stored yet, so you
cannot silently enable a rule on a metric you have no data for.

**Run engine now** (`POST /api/v1/alerts/scan`) evaluates every enabled rule and
reports real counts: rules evaluated, alerts created, notifications queued,
candidates suppressed by dedupe, rules skipped for cooldown, observations and
predictions scanned, duration. **Test** on a single rule is a dry run — it
returns the candidates it would raise and persists nothing. Rules can be toggled
enabled/disabled and deleted; the scan window is bounded by
`ALERT_SCAN_LOOKBACK_DAYS` (default 7) and `ALERT_SCAN_MAX_ROWS` (default 20 000).

## 7. Audit log (`/admin/audit`)

Requires `audit:read`. Read-only by construction: the API exposes only `GET
/api/v1/system/audit`, and the store has no update or delete path — the audit trail
is append-only. Server-side filter by `action` and `outcome`, then narrow
locally by actor, resource type or date.

Each entry records actor id and role, action, resource type and id, outcome
(`success` / `denied`), scrubbed metadata, `request_id`, source IP, deployment
environment and timestamp. Values under `REDACTED_KEYS` (passwords, tokens,
connection strings) are stored as `[redacted]`, so a log reader cannot recover a
secret even with full access. A red `denied` row means a server-side permission
check failed; the UI could not have granted that action either.

## 8. System health (`/admin/health`)

Requires `system:read` to view, `system:manage` + administrator role to back up.
Live cards for the document store, data lake, Spark, Hadoop, HDFS, Kafka, Airflow,
Redis/Celery and SMTP; each is `ok`, `degraded`, or `not-configured` with the
reason and the environment key that would fix it. Also shows dependency versions,
service endpoints, counters, and the collection row counts. **Run backup** triggers
a snapshot on the API host — see `infrastructure/backup/backup.sh` for the
file-level path.

## 9. Settings (`/admin/settings`)

Requires `system:manage` + administrator role. Only a whitelist is editable; the
rest of the configuration is environment-derived and shown read-only, because a
dashboard must not be able to turn off TLS or repoint the database.

| Key | Type | Limit |
| --- | --- | --- |
| `platform_display_name` | text | ≤ 80 chars |
| `platform_tagline` | text | ≤ 160 chars |
| `default_theme` | choice | `light` / `dark` / `system` |
| `report_retention_days` | int | 1–3650 (default 30) |
| `report_max_active_per_user` | int | 1–200 (default 10) |
| `alert_cooldown_minutes` | int | 1–1440 |
| `data_lake_retention_days` | int | 1–3650 |
| `self_service_registration_note` | text | ≤ 200 chars |

**Save** sends exactly one `PATCH /api/v1/system/settings` containing only the
keys you changed; **Cancel** reverts the form locally. These keys are never
editable through the API and stay env-only:
`FORCE_ATLAS_CONNECTION`, `MONGODB_URI`, `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`,
`JWT_SIGNING_KEY`, `SMTP_PASSWORD`, `NOAA_API_KEY`, `REDIS_URL`,
`CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `AIRFLOW_BASE_URL`.

## 10. Tickets and feedback (`/admin/tickets`)

Two tabs. **Tickets**: filter by status, category, priority and assignee plus
search; open one to read the thread, change priority/status/assignee, add a
message, or close with a note. Assigning a ticket requires the administrator
role; triage requires `support:manage`. **Feedback**: list and filter ratings,
then triage with a status change and a reply (`feedback:manage`). Ordinary users
only ever see their own tickets.

---

## 11. Rate limits you should plan around

Enforced per user id (or client IP when anonymous) by a process-local sliding
window in `backend/common/middleware.py`; exceeding a limit returns **429** with
`Retry-After` and `X-RateLimit-*` headers.

| Scope | Limit |
| --- | --- |
| `POST /auth/login` | 10 per 60 s |
| `POST /auth/register` | 5 per 300 s |
| password reset | 5 per 900 s |
| `POST /datasets/upload` | 20 per 300 s |
| feedback | 10 per 300 s |
| support tickets | 10 per 300 s |
| everything else under `/api/` | 600 per 60 s |

The NOAA Climate Data Online connector additionally self-limits to 30 requests
per minute, because that is the provider's published ceiling.

## 12. Report lifecycle

`create_report` counts a user's queued + running reports and rejects with
`conflict` once the active limit is reached — default **10**, overridable in
Settings, clamped 1–200. Generated reports carry an `expires_at` from
`report_retention_days` (default **30**), and `prune_expired()` deletes past-due
rows at most once every 600 s. Files are written under the reports storage path
and are never served by a path a client supplies.

## 13. Operational checklist

1. `/admin/health` first: any `not-configured` engine is a missing environment
   key, not a bug — fix it in `.env` and restart, or accept the degraded mode.
2. `/admin/settings`: set display name, registration note, retention windows and
   the global alert cooldown before inviting users.
3. `/admin/alert-rules`: start from the templates, then tune thresholds; press
   **Test** (dry run) before enabling.
4. `/admin/users`: promote analysts; do not hand out `administrator`.
5. `/admin/audit`: review `denied` rows weekly — they are either a misconfigured
   role or an attempted abuse.
6. Back up through `/admin/health` or `infrastructure/backup/backup.sh` on a
   schedule you can actually restore from.
