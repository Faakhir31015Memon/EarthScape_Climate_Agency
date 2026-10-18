# User guide

EarthScape Climate Agency — how to use the platform, screen by screen.

There are three kinds of account. Everything below describes what each role can
actually do; the application enforces the same rules server-side, so a button you
can see is never the only check.

| Role | Gained by | Can do |
| --- | --- | --- |
| **Viewer** | self-registration (default) | explore dashboards, maps, history, forecasts and reports; acknowledge nothing; raise tickets and feedback |
| **Climate analyst** | issued by an administrator | everything a viewer can do, plus upload data, run ingestion and processing jobs, train/evaluate models, run forecasts, configure alert rules, generate reports, work the support queue |
| **Administrator** | issued by an existing administrator or by `bootstrap_platform.py` | everything, plus users and roles, platform settings, alert rules, pipeline configuration, audit log, system health, backups |

Sign in at `/login`. Sessions last 20 minutes with an automatic silent refresh;
signing out revokes the whole refresh-token family. After 8 failed attempts an
account locks for 15 minutes — an administrator can unlock it earlier.

---

## 1. The banner at the top tells you the truth about your install

Before anything else, read the environment strip under the header. It states:

* which document store the API is really using — `atlas` or the labelled
  `local-development` store (the latter means `MONGODB_URI` is unset or
  unreachable, and data is being persisted as JSON snapshots under
  `backend/.data/localdb/`), and
* which processing engines are configured. Anything that is not configured is
  named with the environment key that would enable it.

Nothing in the interface pretends that a missing component exists. When you see
`not configured`, that is the platform reporting honestly, not a bug.

---

## 2. Dashboard (`/app`)

The landing screen for a shift: real-world KPI tiles (latest observation,
station counts, coverage, open alerts, running jobs), a trend chart, a regional
comparison, recent activity, and a link trail into the detail screens.

* Every tile is computed from stored observations. A tile shows a number only
  when there are enough points to compute it.
* Charts offer metric and time-range controls; `Group by` switches between
  region and station series.
* If a series is too short, the chart area explains **why**: how many points
  exist, how many are required, and a link to run ingestion. This is deliberate —
  a flat line invented from nothing would be worse than no line.
* The dashboard refreshes every 30 seconds while the tab is open; use the
  Refresh button to force it.

## 3. Analytics explorer (`/app/analytics`)

The workbench. Choose a metric, a period, regions/locations, and a frequency
(daily, monthly, seasonal). Tabs: **Trend**, **Compare regions**, **Correlation**
(10-measurement matrix with strength labels and the sample size behind each
cell), **Data quality** and **Coverage**.

* Save a configuration with **Save analysis** — only the parameters are stored,
  so re-opening it re-runs the query against current data.
* **Export CSV** streams the current query's rows.
* Correlation cells that are `n/a` genuinely have no overlapping observations.

## 4. Observation history (`/app/history`)

Paginated, filterable table of the underlying records — one row per
station-day, with quality flags and provenance (connector, endpoint, run id,
ingestion job, retrieval time). Click a row for the full document. This is the
screen to use when you need to know *where a number came from*.

## 5. Map (`/app/map`)

Stations plotted by coordinate, coloured by the selected measurement, with a
legend. Toggle between station view and clustered counts; click a marker for the
latest measurement, units and date; pick a station to load its trend beside the
map.

## 6. Anomalies (`/app/anomalies`)

Anomaly explorer with a method selector — statistical baseline, fixed threshold,
or isolation forest — and a deviation-sensitivity control. Each detected anomaly
shows the observed value, the seasonal baseline, the deviation and the z-score,
and links to the observation on the history screen. From here you can create an
alert rule for that pattern (`Create rule`) or file an anomaly report.

## 7. Datasets (`/app/datasets`) → dataset detail (`/app/datasets/<id>`)

Catalogue of every source: provider feeds and uploads, with status, row counts,
coverage and last job. The detail page shows summary statistics, coverage,
column mapping, the job history, recent observations, archive/delete for owners
and administrators, and a **Run ingestion** action.

## 8. Upload (`/app/uploads`) — four steps

1. **Choose a file** — `.csv`, `.json`, `.xlsx`, `.xls`, `.parquet`, `.txt`,
   `.nc`, up to the limit shown on the page.
2. **Inspect** — the platform reports detected format, encoding, header row,
   per-column type and unit guesses, and a preview. Nothing is stored yet.
3. **Map columns** to canonical fields (`observation_date`, `latitude`,
   `longitude`, `temperature_c`, `precipitation_mm`, …), with defaults for
   region/location when the file omits them.
4. **Commit** — creates a dataset and an ingestion job; watch its progress on the
   ingestion screen.

Rejects are counted with reasons, not silently dropped. Rows with an unparsable
date or coordinates are rejected; everything else is stored and flagged, so a
suspect value stays auditable instead of disappearing. A provider "missing"
sentinel such as `-999` is converted to `null` — never to a measurement of −999.

## 9. Ingestion jobs (`/app/ingestion`)

Every fetch from a provider or file, with `fetched / accepted / rejected /
skipped` counts (they reconcile), schedule controls, start/cancel/retry, and a
log. Re-running the same import is idempotent: the unique key
`dataset|location|date` means duplicates are counted as *skipped*, never
inserted twice.

## 10. Processing monitor (`/app/processing`)

Batch workflows (export to the lake, MapReduce aggregation, Spark aggregation,
analytics rebuild, feature build, export) with the engine each job actually
used, row counts in and out, artifacts written, a live log while running, and
cancel. Engines report `unavailable` with the missing configuration named — the
job then fails honestly rather than reporting a success it did not have.

## 11. Big data view (`/app/big-data`)

The data lake: backend mode (HDFS or local), the `raw → validated → processed →
analytics → ml-features → exports` layers with file counts and bytes, partitions,
and the status of Hadoop, Spark, Kafka and Celery.

## 12. ML lab (`/app/ml-lab`), model registry (`/app/models`)

* **ML lab**: pick an algorithm and target, a scope and a horizon, launch
  training, then watch the job and its metrics. Training uses only stored
  observations; the required minimum row count is enforced and reported.
* **Registry**: every model version with algorithm, target, training window,
  row count, evaluation metrics (MAE / RMSE / R² …), artifact size and status.
  Candidate → **Activate** (retires the previous active version), **Retire**,
  **Retrain**. A model with no real metrics cannot be activated.

## 13. Forecasts (`/app/forecasts`)

Chart of actual observations (solid) versus the active model's forecast (dashed)
with an uncertainty band when the model produces one; a table of horizon points;
an anomaly-scan action over a chosen window. The page always names the model
version that produced the numbers, so a forecast can be traced to its training
data.

## 14. Alerts (`/app/alerts`) and notifications (`/app/notifications`)

Open alerts by severity, with acknowledge/resolve (both recorded with your name
and time), a rules summary, and a manual scan. Notifications list everything the
platform sent you; unread-first, mark as read, mark all read. If e-mail delivery
is not configured, the notification says `suppressed` and gives the reason
instead of implying an e-mail arrived.

## 15. Reports (`/app/reports`)

Choose a report type (executive summary, climate trend, anomaly report, regional
comparison, model performance, data quality), a period and scope, then generate.
Queued reports appear as jobs with row and page counts; **Download** gives the
PDF or CSV. **Instant export** streams CSV directly. Reports are built from the
same analytics kernel as the dashboard, so the numbers agree by construction.

## 16. Saved analyses (`/app/saved-analyses`)

Your stored explorer configurations, with the filters they will re-run; rename,
open, share (owner/admin), or delete.

## 17. Profile (`/app/profile`)

Name, organisation, theme (light/dark/system), default region, preferred units,
notification channels; password change with the live policy checklist; active
sessions and sign-out everywhere.

## 18. Support (`/app/support`)

Raise a ticket (category, priority, description), follow the conversation thread,
and add messages until it is closed. The feedback form on the same page records
a rating and note for the platform team. Ordinary users see only their own
tickets.

---

## 19. Reading the interface

| You see | It means |
| --- | --- |
| `available: false`, "N points, M required" | Not enough stored data for that metric/scope. Run ingestion; the chart refuses to invent a line. |
| `not configured` + an environment key | That engine/feature exists in code but your deployment has not enabled it. |
| `suppressed` on a notification | Deliberately not delivered (e.g. SMTP off); the reason is shown. |
| `completed_with_errors` on a job | Some rows were rejected; open the job for the reasons and counts. |
| `local-development` | The API could not reach Atlas and is using the documented fallback store. |
| A red `denied` audit row | A permission check failed server-side — the UI could not have granted it either. |

## 20. Shortcuts and behaviour notes

* Every list is paginated server-side (default page size 50); filters are
  reflected in the URL on the explorer screens so a view can be shared.
* Modals close with Escape; long-running job panels poll every 5 seconds while a
  job is active and stop when it finishes.
* Tables scroll horizontally on narrow screens; the sidebar collapses into a
  drawer below 1024 px.
* Dark mode follows the OS unless you override it in your profile.

Administrator-facing screens are documented in
[`administrator-guide.md`](administrator-guide.md).
