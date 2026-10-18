import { Link } from 'react-router-dom'
import { AlertTriangle, BarChart3, Database, ShieldCheck, UploadCloud, Wand2 } from 'lucide-react'
import { Badge, EmptyState, ErrorState, Hint, ModeBadge, Panel, RefreshButton, StatusBadge, TableSkeleton } from '../../components/ui'
import { useAsync } from '../../hooks/useAsync'
import { api } from '../../services/api'
import { formatCount, normaliseMetrics } from './components/publicData'
import { AnchorNav, ProvenanceNote, PublicContainer, SectionHeading } from './components/PublicSections'

const SECTIONS = [
  { id: 'authentication', label: 'Authentication & RBAC' },
  { id: 'ingestion', label: 'Ingestion' },
  { id: 'storage', label: 'Storage' },
  { id: 'processing', label: 'Distributed processing' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'machine-learning', label: 'Machine learning' },
  { id: 'visualisation', label: 'Visualisation' },
  { id: 'alerts', label: 'Alerts & notifications' },
  { id: 'reports', label: 'Reports & exports' },
  { id: 'operations', label: 'Monitoring, audit & backups' },
  { id: 'documentation', label: 'Documentation' },
]

const ANOMALY_METHODS = [
  {
    name: 'statistical_baseline',
    body: 'Builds a seasonal baseline from the stored record and flags values that depart from it by more than a configurable number of standard deviations. Each hit reports the baseline it was compared against.',
  },
  {
    name: 'threshold',
    body: 'Flags observations above or below an absolute value, or beyond a fixed deviation. Used for physical limits and operator-defined watch levels where a baseline would be misleading.',
  },
  {
    name: 'isolation_forest',
    body: 'Unsupervised isolation-forest screening over the multivariate record. Surfaces combinations that no single-variable rule would catch, and is always run as a recorded job whose sample size is reported.',
  },
]

function MetricTable() {
  const state = useAsync(() => api.climate.metrics(), [])
  const metrics = normaliseMetrics(state.data)

  return (
    <Panel
      title="Supported metrics, units and validation ranges"
      description="Read live from the metric catalogue, which is generated from the same canonical range table the validators use."
      actions={<RefreshButton onClick={() => void state.refetch()} busy={state.refreshing} />}
      footer={
        metrics.length ? (
          <span>
            Ranges outside these bounds are rejected at ingestion and recorded as a row-level issue. A value the source did not provide is stored
            as <span className="mono">null</span> and excluded from aggregates — it is never converted to zero.
          </span>
        ) : null
      }
    >
      {state.loading ? (
        <TableSkeleton rows={6} columns={5} />
      ) : state.error ? (
        <ErrorState error={state.error} onRetry={() => void state.refetch()} />
      ) : metrics.length === 0 ? (
        <EmptyState
          title="The metric catalogue returned no entries"
          description="The endpoint answered with an empty list, so this page shows nothing rather than a hard-coded table."
          action={
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void state.refetch()}>
              Retry
            </button>
          }
        />
      ) : (
        <div className="table-wrap">
          <table className="data">
            <caption className="sr-only">Canonical measurement fields with unit, aggregation and accepted range</caption>
            <thead>
              <tr>
                <th scope="col">Field</th>
                <th scope="col">Metric</th>
                <th scope="col">Unit</th>
                <th scope="col">Aggregation</th>
                <th scope="col">Valid range</th>
                <th scope="col">Populated</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((metric) => (
                <tr key={metric.field}>
                  <td className="mono whitespace-nowrap">{metric.field}</td>
                  <td>{metric.label}</td>
                  <td>{metric.unit}</td>
                  <td>
                    <Badge tone={metric.aggregation === 'sum' ? 'info' : 'neutral'}>{metric.aggregation === 'sum' ? 'sum' : 'mean'}</Badge>
                  </td>
                  <td className="mono whitespace-nowrap">
                    {metric.range_min === null && metric.range_max === null ? (
                      <span className="subtle">not published</span>
                    ) : (
                      <>{formatCount(metric.range_min)} … {formatCount(metric.range_max)}</>
                    )}
                  </td>
                  <td>
                    {metric.totalCount === null ? (
                      <span className="subtle">not reported</span>
                    ) : (
                      <span title={`${formatCount(metric.populatedCount)} of ${formatCount(metric.totalCount)} records carry this field`}>
                        {metric.hasData ? (
                          <>
                            {formatCount(metric.populatedCount)}
                            <span className="subtle"> / {formatCount(metric.totalCount)}</span>
                          </>
                        ) : (
                          <span className="subtle">no records yet</span>
                        )}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

export function CapabilitiesPage() {
  const health = useAsync(() => api.system.health(), [])

  return (
    <PublicContainer>
      <header className="mb-8 max-w-3xl">
        <p className="panel-title mb-2">Platform capabilities</p>
        <h1 className="font-display text-[30px] font-semibold leading-tight sm:text-[36px]">Every module, described as it behaves</h1>
        <p className="mt-4 text-[15px] leading-relaxed muted">
          One section per module of the EarthScape platform. Statements below describe implemented behaviour: what an operator can do, what the
          API enforces, and what happens when an optional dependency is missing. Where a figure belongs to the data record rather than the
          software, it is read from the API on this page and marked as such.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          {health.data ? (
            <>
              <StatusBadge value={health.data.status} label={`API ${health.data.status}`} />
              <ModeBadge mode={health.data.database.mode} detail={health.data.database.ok ? 'reachable' : 'unreachable'} />
              <Badge tone="info">processing: {health.data.processing_mode}</Badge>
            </>
          ) : health.error ? (
            <Badge tone="neutral">service health not reachable</Badge>
          ) : (
            <Badge tone="neutral">reading /api/v1/health…</Badge>
          )}
        </div>
      </header>

      <AnchorNav items={SECTIONS} />

      <div className="space-y-10">
        <section id="authentication" aria-labelledby="authentication-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 1" title="Authentication & role-based access" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Credentials and sessions">
              <ul className="space-y-2 text-sm leading-relaxed muted">
                {[
                  'Passwords are hashed with bcrypt (cost 12) and never stored or logged in plaintext; inputs longer than the bcrypt limit are rejected rather than silently truncated.',
                  'JWT access tokens expire after the configured lifetime (20 minutes by default); refresh tokens rotate on every use.',
                  'Reuse of a rotated refresh token revokes the whole token family, so a stolen token cannot be replayed.',
                  'Eight consecutive failed sign-ins lock the account for fifteen minutes; an administrator can unlock it earlier.',
                  'Self-registration and password recovery are deployment switches, published by /api/v1/auth/configuration.',
                ].map((line) => (
                  <li key={line} className="flex items-start gap-2">
                    <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            </Panel>
            <Panel title="Roles, capabilities and audit">
              <p className="text-sm leading-relaxed muted">
                Three roles exist: <span className="mono">administrator</span>, <span className="mono">climate_analyst</span> and{' '}
                <span className="mono">viewer</span>. Every endpoint declares the role or capability it requires, and the check runs on the server
                for each request — hiding a control in the browser is never the authorisation. Mutating actions and authentication events are
                written to the audit log with actor, role, resource, outcome, source IP and correlation id.
              </p>
              <Hint>
                Self-registration can only produce the lowest-privilege role. Elevation happens when an administrator changes it explicitly.
              </Hint>
            </Panel>
          </div>
        </section>

        <section id="ingestion" aria-labelledby="ingestion-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 2" title="Data ingestion" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Provider connectors">
              <ul className="space-y-2 text-sm leading-relaxed muted">
                <li className="flex items-start gap-2">
                  <UploadCloud className="mt-0.5 h-4 w-4 shrink-0 text-hydro-600 dark:text-hydro-300" aria-hidden="true" />
                  <span>
                    <strong className="text-[var(--text)]">NASA POWER</strong> — daily point API, no credentials required. Requests are batched per
                    station and parameter, and every response is mapped through the canonical field table.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <BarChart3 className="mt-0.5 h-4 w-4 shrink-0 text-hydro-600 dark:text-hydro-300" aria-hidden="true" />
                  <span>
                    <strong className="text-[var(--text)]">NOAA Climate Data Online</strong> — station dataset search requiring an API token. The
                    connector reports itself as needing configuration when the token is absent, and ingestion is offered only when it is present.
                  </span>
                </li>
                <li className="flex items-start gap-2">
                  <Wand2 className="mt-0.5 h-4 w-4 shrink-0 text-hydro-600 dark:text-hydro-300" aria-hidden="true" />
                  <span>
                    <strong className="text-[var(--text)]">Live provider interface</strong> — a key-free weather API registered as a{' '}
                    <span className="mono">live_api</span> source for the near-real-time layer, behind the same connector contract so additional
                    providers can be added without touching the pipeline.
                  </span>
                </li>
              </ul>
              <Hint>Connector status, credentials required and last sync outcome are exposed to signed-in analysts by /api/v1/datasets/connectors.</Hint>
            </Panel>

            <Panel title="Uploads: CSV, Excel and JSON">
              <p className="text-sm leading-relaxed muted">
                Uploads are inspected before anything is committed. The inspector reads headers, infers column types and completeness, and proposes
                a mapping to canonical fields from a synonym table (<span className="mono">t2m</span>,{' '}
                <span className="mono">prcp</span>, <span className="mono">rh2m</span>, <span className="mono">ws10m</span> and many others).
                The analyst can correct the mapping; a preview then shows which rows would be accepted and which rejected.
              </p>
              <p className="mt-3 text-sm leading-relaxed muted">
                Row-level validation covers:
              </p>
              <ul className="mt-2 space-y-1.5 text-sm leading-relaxed muted">
                {[
                  'timestamp parsing, including Excel serial dates, POSIX epochs and compact YYYYMMDD forms',
                  'coordinate bounds, plus a warning when a latitude or longitude is exactly zero',
                  'unit conversion to the canonical unit, with the original unit retained in provenance',
                  'physical range checks — out-of-range values are rejected with a per-row issue code',
                  'missing-token detection so sentinels such as -999, N/A or NaN never become measurements',
                  'duplicate key counting and a completeness profile per column',
                ].map((line) => (
                  <li key={line} className="flex items-start gap-2">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-forest-600 dark:bg-forest-400" aria-hidden="true" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
              <Hint>A file verdict is valid, valid with warnings, or invalid; warnings are attached to the dataset so the record stays reviewable.</Hint>
            </Panel>
          </div>
        </section>

        <section id="storage" aria-labelledby="storage-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 3" title="Storage: document store and data lake" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="MongoDB Atlas application database">
              <p className="text-sm leading-relaxed muted">
                Atlas holds the operational record: accounts and refresh tokens, regions, datasets, ingestion and processing jobs, analytics
                summaries, model registry entries, predictions, alerts, notifications, reports, saved analyses, audit logs, tickets, feedback and
                platform settings. Collections are indexed by the migration script, which also stamps the schema version the API reports.
              </p>
              <p className="mt-3 text-sm leading-relaxed muted">
                When Atlas is not configured, development mode keeps the same document shapes in a local store and reports the mode in every
                health and status response.
              </p>
            </Panel>
            <Panel title="Layered lake layout">
              <p className="text-sm leading-relaxed muted">
                Bulk tabular data lives in a layered lake, addressed through the WebHDFS endpoint when a NameNode answers and through a local
                directory tree otherwise. Layers and their purpose:
              </p>
              <ul className="mt-3 space-y-1.5 text-sm leading-relaxed muted">
                {[
                  ['raw', 'files exactly as received, never rewritten'],
                  ['validated', 'canonical observations that passed row-level checks'],
                  ['processed', 'cleaned, partitioned Parquet by year and region'],
                  ['analytics', 'aggregated series, regional comparisons and quality summaries'],
                  ['ml-features', 'feature windows used for training and scoring'],
                  ['exports', 'generated report and download artefacts'],
                ].map(([layer, purpose]) => (
                  <li key={layer} className="flex items-start gap-2">
                    <span className="mono shrink-0 text-[13px] font-semibold text-forest-700 dark:text-forest-300">{layer}</span>
                    <span>— {purpose}</span>
                  </li>
                ))}
              </ul>
              <Hint>Job records store the lake URIs they wrote, so an aggregate can be traced back to the files and inputs that produced it.</Hint>
            </Panel>
          </div>
        </section>

        <section id="processing" aria-labelledby="processing-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 4" title="Distributed processing" />
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title="Hadoop Streaming MapReduce">
              <p className="text-sm leading-relaxed muted">
                Python mapper and reducer scripts speak the Streaming protocol: tab-separated key/value records in, aggregated records out, with a
                local runner that executes the identical scripts without a cluster. Counting and temperature-aggregation jobs are shipped in the
                repository as runnable examples of the contract.
              </p>
            </Panel>
            <Panel title="PySpark batch jobs">
              <p className="text-sm leading-relaxed muted">
                Spark dataframes read the validated and processed layers, produce per-station and per-region aggregates, seasonal statistics and
                ML feature tables, and write Parquet back to the lake. Job records capture rows in, rows out, rejected counts, duration and the
                engine version used.
              </p>
            </Panel>
            <Panel title="Structured streaming and Kafka">
              <p className="text-sm leading-relaxed muted">
                The near-real-time layer can consume a Kafka topic and emit continuous aggregates. When Kafka is disabled the platform falls back
                to scheduled micro-batch ingestion, and the workflow catalogue marks streaming as unavailable with the reason.
              </p>
            </Panel>
          </div>
          <div className="mt-4">
            <Panel title="Engine health is a first-class answer" dense>
              <p className="text-sm leading-relaxed muted">
                Workflows declare the capabilities they require. The API reports HDFS reachability, whether Spark is importable and startable, the
                Hadoop binaries and cluster configuration, Kafka and Airflow enablement, the active data-lake root and the current processing mode.
                A workflow that cannot run is listed as unavailable rather than offered and failed later.
              </p>
            </Panel>
          </div>
        </section>

        <section id="analytics" aria-labelledby="analytics-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 5" title="Analytics" />
          <div className="space-y-4">
            <MetricTable />
            <div className="grid gap-4 lg:grid-cols-2">
              <Panel title="Trends, baselines and comparison">
                <p className="text-sm leading-relaxed muted">
                  Series are aggregated at daily, weekly, monthly or annual frequency, optionally grouped by region, station or country. Additive
                  metrics such as precipitation are summed; everything else is averaged, and the aggregation actually applied is returned with the
                  series. Seasonal baselines come from the stored record and drive anomaly work and forecast skill.
                </p>
                <p className="mt-3 text-sm leading-relaxed muted">
                  Regional comparison returns mean, median, minimum, maximum, standard deviation and observation count per region, plus the spread
                  between highest and lowest. Regions with no records are omitted, not drawn as zero.
                </p>
              </Panel>
              <Panel title="Anomaly detection methods">
                <ul className="space-y-3">
                  {ANOMALY_METHODS.map((method) => (
                    <li key={method.name}>
                      <p className="mono text-[13px] font-semibold text-forest-700 dark:text-forest-300">{method.name}</p>
                      <p className="mt-1 text-sm leading-relaxed muted">{method.body}</p>
                    </li>
                  ))}
                </ul>
                <Hint>Every anomaly carries a plain-language explanation of the comparison made, its deviation score, direction and severity.</Hint>
              </Panel>
            </div>
            <Panel title="Correlation">
              <p className="text-sm leading-relaxed muted">
                Pairwise Pearson coefficients across the populated metric set, each cell reporting its sample size and a strength label. Coverage
                differs per field, so cells state how many shared observations were used.
              </p>
              <p className="mt-3 flex items-start gap-2 text-sm leading-relaxed">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-solar-600 dark:text-solar-300" aria-hidden="true" />
                <span className="muted">
                  <strong className="text-[var(--text)]">Correlation is not causation.</strong> Nothing in this module establishes a causal
                  relationship: seasonality, latitude, elevation and shared reanalysis ancestry confound the pairs, and the API returns that caveat
                  with the matrix rather than leaving it to the reader.
                </span>
              </p>
            </Panel>
          </div>
        </section>

        <section id="machine-learning" aria-labelledby="machine-learning-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 6" title="Machine learning" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Targets, features and algorithms">
              <p className="text-sm leading-relaxed muted">
                Targets are any canonical measurement field; the catalogue marks each one trainable or reports why it is not, based on how many
                populated observations exist. Feature windows combine the target lag structure with calendar terms, normals and deviations, and the
                exact feature definition is stored with the model. Imputation and scaling are fitted on the training split only.
              </p>
              <p className="mt-3 text-sm leading-relaxed muted">
                Algorithms available in this build: ridge regression (linear, requires imputation and scaling), random forest regression (native
                NaN handling, 200 trees) and gradient boosting with XGBoost (requires the package; reported as unavailable when it is absent).
                Each declares a minimum training-row count.
              </p>
            </Panel>
            <Panel title="Evaluation, registry, versioning and uncertainty">
              <ul className="space-y-2 text-sm leading-relaxed muted">
                <li>
                  <strong className="text-[var(--text)]">Metrics:</strong> MAE, RMSE, MAPE and R² on a held-out split, plus the naive baseline&rsquo;s
                  MAE and skill versus that baseline — a model is only useful if it beats persistence.
                </li>
                <li>
                  <strong className="text-[var(--text)]">Registry:</strong> name, version, algorithm, target, feature definition, training period,
                  train/test sizes, metrics, artefact URI, status (training, candidate, active, retired, failed) and known limitations.
                </li>
                <li>
                  <strong className="text-[var(--text)]">Versioning:</strong> retraining creates the next version; activation retires the previous
                  one, so any forecast can state which model version produced it.
                </li>
                <li>
                  <strong className="text-[var(--text)]">Uncertainty:</strong> forecasts carry bounds where the method supports them and an explicit
                  note where it does not. A prediction without uncertainty is labelled as such rather than implied to be exact.
                </li>
              </ul>
            </Panel>
          </div>
        </section>

        <section id="visualisation" aria-labelledby="visualisation-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 7" title="Visualisation" />
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title="Series and comparisons" dense>
              <p className="text-sm leading-relaxed muted">
                Multi-series trend charts with anomaly markers, horizontal regional comparison bars ordered by value, correlation heatmaps with
                sample-size tooltips, forecast charts with uncertainty bands, and coverage-completeness visuals. Chart footers always state how many
                periods and values are plotted.
              </p>
            </Panel>
            <Panel title="Geographic view" dense>
              <p className="text-sm leading-relaxed muted">
                A station map clusters locations at low zoom and switches to individual markers when you zoom in. Each point reports the metric
                value, unit, observation date, source and quality flags; colour is scaled to the values actually present.
              </p>
            </Panel>
            <Panel title="Accessibility discipline" dense>
              <p className="text-sm leading-relaxed muted">
                Charts expose a text label and role, tables have header cells, filters have labels, colour is paired with text badges for severity,
                and the palette is checked against the colour-blind-safe series used by the chart kit. Light and dark themes are both supported.
              </p>
            </Panel>
          </div>
        </section>

        <section id="alerts" aria-labelledby="alerts-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 8" title="Alerts & notifications" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Rules and evaluation">
              <p className="text-sm leading-relaxed muted">
                Rules declare a target metric, a scope (regions, stations, countries or datasets), a condition — greater than, less than, between,
                deviation beyond <span className="mono">n</span> sigma, or anomaly detected — a severity, a cooldown and recipients. Scans run as
                recorded jobs; each run reports how many observations were evaluated, how many fired, how many were suppressed by cooldown, and
                whether the sample was too small to decide.
              </p>
              <Hint>Rule templates ship for the common cases (heat thresholds, extreme precipitation, deviation-based watch levels).</Hint>
            </Panel>
            <Panel title="Alerts, delivery and acknowledgement">
              <p className="text-sm leading-relaxed muted">
                Alerts are persisted with the value, threshold or baseline, deviation score, method, location and an explanation, then move through
                open, acknowledged and resolved with actor and timestamp. Notifications are delivered in-app and by email when SMTP is configured;
                each carries a delivery status, attempt count and failure reason, so a suppressed message is visible rather than silently missing.
              </p>
            </Panel>
          </div>
        </section>

        <section id="reports" aria-labelledby="reports-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 9" title="Reports & exports" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Generated reports" dense>
              <p className="text-sm leading-relaxed muted">
                Report jobs cover climate trends, regional comparison, dataset quality, anomalies, forecasts, processing performance, ingestion
                history and audit summaries. Output formats are CSV, JSON and PDF. A report records who requested it, the filters used, units, the
                date range, row count and expiry, and it fails honestly with an insufficient-data reason when the record cannot support it.
              </p>
            </Panel>
            <Panel title="Instant exports" dense>
              <p className="text-sm leading-relaxed muted">
                Analytical views export the exact series currently displayed, with units in the header row and the same filters that produced the
                chart. The catalogue marks which report types are computable against the data present right now.
              </p>
            </Panel>
          </div>
        </section>

        <section id="operations" aria-labelledby="operations-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 10" title="Monitoring, audit & backups" />
          <div className="grid gap-4 lg:grid-cols-3">
            <Panel title="Health and status" dense>
              <p className="text-sm leading-relaxed muted">
                <span className="mono">/health</span> reports database mode and reachability, schema version, uptime, processing mode, component
                status and background queue depth. <span className="mono">/system/status</span> adds collection counts, job and model breakdowns,
                storage roots and limits; <span className="mono">/system/metrics</span> adds request totals, status classes, error rate and latency
                percentiles.
              </p>
            </Panel>
            <Panel title="Audit trail" dense>
              <p className="text-sm leading-relaxed muted">
                Authentication, authorisation failures, data changes, job submissions and administrative actions are logged with actor, role,
                resource, outcome, request id and source address, and are filterable by an administrator.
              </p>
            </Panel>
            <Panel title="Backups" dense>
              <p className="text-sm leading-relaxed muted">
                Backup runs produce a recorded artefact listing the collections covered, document count, file name, size and a SHA-256 digest, with
                a restore-verification flag. With Atlas configured the run uses a mongodump; in development mode it writes a local JSON snapshot.
                Both report the mode they used.
              </p>
            </Panel>
          </div>
        </section>

        <section id="documentation" aria-labelledby="documentation-heading" className="scroll-mt-24">
          <SectionHeading eyebrow="Module 11" title="Documentation & delivery" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="What ships with the code">
              <p className="text-sm leading-relaxed muted">
                The repository carries the layout and quick start in <span className="mono">README.md</span>, every setting documented in{' '}
                <span className="mono">.env.example</span>, the container and cluster stack in{' '}
                <span className="mono">infrastructure/</span>, and each pipeline module&rsquo;s own README next to the code it describes. Collection
                index specifications and the migration script live with the backend modules they belong to.
              </p>
            </Panel>
            <Panel title="Project report">
              <p className="text-sm leading-relaxed muted">
                The written architecture, installation, testing and user-guide material is consolidated in{' '}
                <span className="mono">docs/EarthScape-Climate-Agency-Project-Report.docx</span>. This page describes implemented behaviour only;
                where a figure belongs to the stored record rather than the software, it is read from this deployment.
              </p>
            </Panel>
          </div>
          <div className="mt-6 flex flex-wrap gap-2">
            <Link to="/insights" className="btn btn-secondary btn-sm">
              See the analytics on live data
            </Link>
            <Link to="/data-sources" className="btn btn-secondary btn-sm">
              Sources, fields & provenance
            </Link>
            <Link to="/help" className="btn btn-secondary btn-sm">
              Help &amp; getting started
            </Link>
            <Link to="/register" className="btn btn-primary btn-sm">
              <Database className="h-3.5 w-3.5" aria-hidden="true" /> Create an account
            </Link>
          </div>
        </section>
      </div>

      <div className="mt-10">
        <ProvenanceNote>
          This page describes software behaviour. The only data it renders — metric ranges, completeness counters and service status — is read
          from the running API, and shows an empty or error state when the API does not answer.
        </ProvenanceNote>
      </div>
    </PublicContainer>
  )
}

export default CapabilitiesPage
