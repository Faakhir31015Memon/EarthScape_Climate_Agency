/**
 * Authenticated overview dashboard. Every tile comes from
 * `GET /api/v1/dashboard/summary` (plus `GET /api/v1/climate/quality` for the
 * completeness ring) and is re-pulled every 30 s — nothing here is static or
 * decorative, and unavailable panels state the backend's own reason.
 */
import { Link } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  Bell,
  Boxes,
  CalendarClock,
  Database,
  MapPin,
  Sparkles,
  UploadCloud,
} from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import type { CoverageReport, Metric } from '../../types/api'
import {
  Badge,
  ChartSkeleton,
  EmptyState,
  ErrorState,
  Hint,
  PageHeader,
  Panel,
  RefreshButton,
  StatCard,
  StatusBadge,
} from '../../components/ui'
import { Progress } from '../../components/ui'
import { RegionBarChart } from '../../components/charts/RegionBarChart'
import { ScoreRing } from '../../components/charts/MiniCharts'
import { TrendChart } from '../../components/charts/TrendChart'
import { formatDate, formatNumber, humanise, metricLabel, relativeTime, severityTone } from '../../lib/format'
import {
  anomalyList,
  coverageFrom,
  forecastItems,
  formatDuration,
  freshnessTone,
  normaliseQuickActions,
  regionAggregates,
  trendOutcome,
} from './components/data/normalise'

const COMPLETENESS_PRIORITY: Metric[] = [
  'temperature_c',
  'precipitation_mm',
  'relative_humidity_pct',
  'wind_speed_ms',
  'cloud_coverage_pct',
  'solar_radiation_kwh_m2',
]

function CompletenessRows({ coverage }: { coverage: CoverageReport | null }) {
  if (!coverage || coverage.observation_count === 0 || !Object.keys(coverage.completeness).length) {
    return (
      <p className="text-xs subtle">
        No observations are stored in the reporting window, so completeness cannot be measured.
      </p>
    )
  }
  const wanted = COMPLETENESS_PRIORITY.filter((field) => coverage.completeness[field])
  const rest = Object.keys(coverage.completeness)
    .filter((field) => !wanted.includes(field as Metric))
    .sort((left, right) => (coverage.completeness[right]?.pct ?? 0) - (coverage.completeness[left]?.pct ?? 0))
  const fields = [...wanted, ...rest].slice(0, 6)
  return (
    <ul className="space-y-2.5">
      {fields.map((field) => {
        const entry = coverage.completeness[field]
        return (
          <li key={field}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate font-medium">{metricLabel(field)}</span>
              <span className="mono shrink-0 subtle">
                {formatNumber(entry?.pct ?? null, 1)}% · {formatNumber(entry?.populated ?? 0, 0)}/{formatNumber(coverage.observation_count, 0)}
              </span>
            </div>
            <Progress value={entry?.pct ?? 0} tone="hydro" label={`${metricLabel(field)} completeness`} />
          </li>
        )
      })}
    </ul>
  )
}

export default function DashboardPage() {
  const summary = useAsync(() => api.dashboard.summary(), [], { interval: 30_000 })
  const quality = useAsync(() => api.climate.quality(), [], { interval: 30_000 })

  const data = summary.data
  const totals = data?.totals

  return (
    <>
      <PageHeader
        eyebrow="Monitor"
        title="Platform overview"
        description="Live state of datasets, pipelines, current observations and predictions, computed from stored records."
        meta={
          data ? (
            <>
              <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Generated {formatDate(data.generated_at, true)} · {relativeTime(data.generated_at)}</span>
              <span aria-hidden="true">·</span>
              <span>Auto-refresh every 30&nbsp;s</span>
              {summary.refreshing ? <Badge tone="info">refreshing</Badge> : null}
            </>
          ) : null
        }
        actions={<RefreshButton onClick={() => void summary.refetch()} busy={summary.refreshing} />}
      />

      {summary.error && !data ? <ErrorState error={summary.error} onRetry={() => void summary.refetch()} /> : null}

      {!data ? (
        <div className="grid-responsive">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="card">
              <div className="skeleton h-4 w-24" />
              <div className="skeleton mt-3 h-7 w-16" />
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-6">
          {/* ------------------------------------------------------------ KPIs */}
          <section aria-label="Key totals">
            <div className="grid-responsive reveal">
              <StatCard label="Datasets" value={formatNumber(totals?.datasets ?? 0, 0)} icon={<Database className="h-4 w-4" />} to="/app/datasets" hint="Catalogued sources, uploads and samples" />
              <StatCard label="Observations" value={formatNumber(totals?.observations ?? 0, 0)} icon={<Activity className="h-4 w-4" />} to="/app/history" hint="Observations held for this deployment" />
              <StatCard
                label="Locations / regions"
                value={`${formatNumber(totals?.locations ?? 0, 0)} / ${formatNumber(totals?.regions ?? 0, 0)}`}
                icon={<MapPin className="h-4 w-4" />}
                to="/app/map"
              />
              <StatCard
                label="Ingestion: success vs failure"
                value={formatNumber(totals?.ingestion_jobs?.completed ?? 0, 0)}
                tone={(totals?.ingestion_jobs?.failed ?? 0) > 0 ? 'alert' : 'forest'}
                hint={`${formatNumber(totals?.ingestion_jobs?.failed ?? 0, 0)} failed · ${formatNumber(
                  totals?.ingestion_jobs?.completed_with_errors ?? 0,
                  0,
                )} with errors`}
                to="/app/ingestion"
              />
              <StatCard label="Active models" value={formatNumber(totals?.active_models ?? 0, 0)} icon={<Sparkles className="h-4 w-4" />} to="/app/models" />
              <StatCard label="Open alerts" value={formatNumber(totals?.open_alerts ?? 0, 0)} tone={(totals?.open_alerts ?? 0) > 0 ? 'alert' : 'default'} icon={<AlertTriangle className="h-4 w-4" />} to="/app/alerts" />
              <StatCard label="Unread notifications" value={formatNumber(totals?.unread_notifications ?? 0, 0)} icon={<Bell className="h-4 w-4" />} to="/app/notifications" />
              <StatCard label="Predictions generated" value={formatNumber(totals?.predictions_generated ?? 0, 0)} to="/app/forecasts" />
            </div>
          </section>

          {/* --------------------------------------------------------- import */}
          <section aria-label="Import your own data">
            <Panel
              title="Import your own data"
              description="Send a spreadsheet or data file; the platform stores it, detects the format, maps the columns and runs the import against this workspace."
              actions={<Link to="/app/uploads" className="link text-xs">Import page</Link>}
            >
              <div className="flex flex-wrap items-center gap-3">
                <Link to="/app/uploads" className="btn btn-primary btn-sm">
                  <UploadCloud className="h-4 w-4" aria-hidden="true" /> Import a file
                </Link>
                <span className="flex flex-wrap items-center gap-1.5 text-xs subtle">
                  Accepts
                  {['csv', 'xlsx', 'xlsm', 'xltx', 'xltm', 'xls', 'xlsb', 'ods', 'parquet', 'json', 'txt'].map((format) => (
                    <span key={format} className="badge badge-neutral mono">{format}</span>
                  ))}
                </span>
              </div>
            </Panel>
          </section>

          {/* --------------------------------------------- sync + current data */}
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Data currency">
            <Panel
              title="Last successful synchronisation"
              description="Most recent completed ingestion job."
              actions={<Link to="/app/ingestion" className="link text-xs">All jobs</Link>}
            >
              {data.last_successful_sync ? (
                <div className="space-y-1.5">
                  <p className="text-lg font-semibold">
                    {data.last_successful_sync.source_name ?? 'Unknown source'}
                  </p>
                  <p className="text-sm muted">
                    {formatDate(data.last_successful_sync.completed_at, true)} ·{' '}
                    <span className="mono">{formatNumber(data.last_successful_sync.records_accepted ?? 0, 0)}</span> records accepted
                  </p>
                  <p className="text-xs subtle">{relativeTime(data.last_successful_sync.completed_at)}</p>
                </div>
              ) : (
                <EmptyState
                  title="No completed synchronisation yet"
                  description="The ingestion job store has no completed jobs. A first sync will populate this panel."
                  action={
                    <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                      Start an ingestion job
                    </Link>
                  }
                />
              )}
            </Panel>

            <Panel
              title="Current-data availability"
              description="Newest stored observation per source, with its staleness."
              actions={<Link to="/app/map" className="link text-xs">View map</Link>}
            >
              {data.current_data?.available ? (
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <p className="text-lg font-semibold">{formatDate(data.current_data.newest_observation_date)}</p>
                    <Badge tone={freshnessTone(data.current_data.lag_days !== null && data.current_data.lag_days <= 3 ? 'fresh' : 'lagging')}>
                      lag {data.current_data.lag_days ?? '—'} days
                    </Badge>
                  </div>
                  <ul className="mt-3 space-y-1.5">
                    {(data.current_data.per_source ?? []).map((source) => (
                      <li key={source.source_name} className="flex items-center justify-between gap-3 text-sm">
                        <span className="min-w-0 truncate">{source.source_name}</span>
                        <span className="flex shrink-0 items-center gap-2">
                          <span className="mono text-xs subtle">{formatDate(source.newest_date)}</span>
                          <Badge tone={freshnessTone(source.status)}>{humanise(source.status)}</Badge>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : (
                <EmptyState
                  tone="warning"
                  title="No current data stored"
                  description={
                    data.current_data.reason ??
                    'No observations have been stored for this deployment yet, so there is no current data.'
                  }
                  action={
                    <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                      Run an ingestion job
                    </Link>
                  }
                />
              )}
            </Panel>
          </section>

          {/* ------------------------------------------------------------ trends */}
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Climate trends">
            {([['temperature_c', data.temperature_trend, 'Temperature trend'], ['precipitation_mm', data.precipitation_trend, 'Precipitation trend']] as const).map(
              ([metric, payload, title]) => {
                const outcome = trendOutcome(payload, metric)
                return (
                  <Panel
                    key={metric}
                    title={title}
                    description={`Monthly aggregation of stored ${metricLabel(metric).toLowerCase()} values.`}
                    actions={<Link to={`/app/analytics?metric=${metric}`} className="link text-xs">Open in analytics</Link>}
                  >
                    {outcome.kind === 'ready' ? (
                      <TrendChart series={outcome.series} metric={outcome.metric} unit={outcome.unit} label={outcome.label} height={260} showCounts />
                    ) : (
                      <EmptyState
                        tone="warning"
                        title={`Not enough stored data for a ${metricLabel(metric).toLowerCase()} trend`}
                        description={
                          <>
                            <span>{outcome.reason}</span>
                            {outcome.available_points !== undefined || outcome.required_points !== undefined ? (
                              <span className="mono mt-1 block text-xs">
                                This trend needs {outcome.required_points ?? '—'} periods and the record currently holds {outcome.available_points ?? '—'}.
                              </span>
                            ) : null}
                          </>
                        }
                        action={
                          <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                            Run an ingestion job
                          </Link>
                        }
                      />
                    )}
                  </Panel>
                )
              },
            )}
          </section>

          {/* ------------------------------------- regional + anomaly sections */}
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Regional comparison and anomalies">
            <Panel
              title="Regional comparison"
              description={data.regional_comparison ? humanise(String((data.regional_comparison as unknown as { metric?: string }).metric ?? 'temperature_c')) : undefined}
              actions={<Link to="/app/analytics" className="link text-xs">Analytics workbench</Link>}
            >
              {(() => {
                const comparison = regionAggregates(data.regional_comparison)
                if (comparison.regions.length) {
                  return <RegionBarChart regions={comparison.regions} metric={'temperature_c' as Metric} unit={comparison.unit || '°C'} label={comparison.label || undefined} height={300} />
                }
                return (
                  <div className="flex items-center justify-center rounded-lg border border-dashed py-12 text-sm muted" style={{ borderColor: 'var(--border-strong)' }} role="status">
                    {comparison.reason ?? 'No regional aggregates yet. Ingest observations to compare regions.'}
                  </div>
                )
              })()}
            </Panel>

            <Panel
              title="Recent anomalies"
              description="Statistical deviations from stored baselines."
              actions={<Link to="/app/anomalies" className="link text-xs">All anomalies</Link>}
            >
              {(() => {
                const anomalies = anomalyList(data.recent_anomalies)
                if (!anomalies.length) {
                  const reason =
                    data.recent_anomalies && !Array.isArray(data.recent_anomalies)
                      ? String((data.recent_anomalies as unknown as { reason?: string; note?: string }).reason ??
                          (data.recent_anomalies as unknown as { note?: string }).note ??
                          'No anomaly scan results have been saved yet.')
                      : 'No anomalies in the stored record.'
                  return <EmptyState title="Nothing anomalous yet" description={reason} action={<Link to="/app/anomalies" className="btn btn-secondary btn-sm">Run detection</Link>} />
                }
                return (
                  <ul className="space-y-3">
                    {anomalies.slice(0, 6).map((anomaly, index) => (
                      <li key={`${anomaly.observation_date}-${anomaly.location_code ?? 'global'}-${index}`} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
                        <div className="flex flex-wrap items-center gap-2">
                          <Badge tone={severityTone(anomaly.severity)}>{humanise(anomaly.severity)}</Badge>
                          <span className="text-sm font-medium">{anomaly.location_name ?? anomaly.location_code ?? anomaly.region_code ?? 'global'}</span>
                          <span className="mono text-xs subtle">{anomaly.observation_date}</span>
                          <span className="ml-auto mono text-sm">
                            {formatNumber(anomaly.value, 1)} {anomaly.unit} · {formatNumber(anomaly.deviation_score, 2)}σ
                          </span>
                        </div>
                        <p className="mt-1.5 line-clamp-2 text-xs leading-relaxed muted">{anomaly.explanation}</p>
                      </li>
                    ))}
                  </ul>
                )
              })()}
            </Panel>
          </section>

          {/* --------------------------------------- forecasts + data quality */}
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Forecasts and data quality">
            <Panel
              title="Forecast summaries"
              description="Latest stored prediction per model scope."
              actions={<Link to="/app/forecasts" className="link text-xs">Forecast explorer</Link>}
            >
              {(() => {
                const forecasts = forecastItems(data.forecast_summaries)
                if (!forecasts.items.length) {
                  return (
                    <EmptyState
                      title="No forecasts yet"
                      description={forecasts.reason ?? 'No predictions yet. Train and run a model first.'}
                      action={<Link to="/app/models" className="btn btn-secondary btn-sm">Model registry</Link>}
                    />
                  )
                }
                return (
                  <ul className="space-y-2.5">
                    {forecasts.items.slice(0, 6).map((item) => (
                      <li key={item.id || `${item.model_name}-${item.target}`} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2 text-sm last:border-0" style={{ borderColor: 'var(--border)' }}>
                        <span className="min-w-0">
                          <span className="font-medium">{item.model_name}</span>
                          <span className="block text-xs subtle">
                            {metricLabel(item.target as Metric)} · {item.scope} · horizon {item.horizon_days ?? '—'} d
                          </span>
                        </span>
                        <span className="mono text-sm">
                          {item.value === null ? '—' : `${formatNumber(item.value, 1)} ${item.unit}`}
                          <span className="ml-2 block text-right text-[11px] subtle">{relativeTime(item.generated_at)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )
              })()}
            </Panel>

            <Panel
              title="Data quality"
              description="Completeness of stored observations in the reporting window."
              actions={<Link to="/app/datasets" className="link text-xs">Datasets</Link>}
            >
              <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
                <ScoreRing score={quality.data?.completeness_pct ?? null} label="Overall completeness" />
                <div className="min-w-0 flex-1">
                  <CompletenessRows coverage={coverageFrom(data.data_quality) ?? quality.data?.overall ?? null} />
                  {quality.data && quality.data.overall.flagged_row_count ? (
                    <p className="mt-2 text-xs subtle">
                      {formatNumber(quality.data.overall.flagged_row_count, 0)} rows carry quality flags (
                      {formatNumber(quality.data.overall.flagged_row_pct ?? 0, 1)}%).
                    </p>
                  ) : null}
                </div>
              </div>
              <Hint>Scores come from completeness and flag counts. If there's nothing to measure, no score is shown.</Hint>
            </Panel>
          </section>

          {/* --------------------------------------------------------- pipeline */}
          <section aria-label="Pipeline status">
            <Panel
              title="Pipeline status"
              actions={<Link to="/app/processing" className="link text-xs">Processing monitor</Link>}
              footer={
                data.pipeline_status && Object.keys(data.pipeline_status.processing_mode_breakdown ?? {}).length ? (
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="panel-title">Executed by</span>
                    {Object.entries(data.pipeline_status.processing_mode_breakdown).map(([mode, count]) => (
                      <Badge key={mode} tone={mode === 'local-development' ? 'info' : 'success'} title="The engine that ran the job">
                        {humanise(mode)} × {formatNumber(count, 0)}
                      </Badge>
                    ))}
                  </span>
                ) : (
                  <span>No processing jobs have recorded an execution mode yet.</span>
                )
              }
            >
              {data.pipeline_status ? (
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <StatCard label="Running" value={formatNumber(data.pipeline_status.running, 0)} tone="hydro" icon={<Boxes className="h-4 w-4" />} />
                  <StatCard label="Queued" value={formatNumber(data.pipeline_status.queued, 0)} />
                  <StatCard label="Completed" value={formatNumber(data.pipeline_status.completed, 0)} tone="forest" />
                  <StatCard label="Failed" value={formatNumber(data.pipeline_status.failed, 0)} tone={(data.pipeline_status.failed ?? 0) > 0 ? 'alert' : 'default'} />
                </div>
              ) : (
                <p className="text-sm muted">This summary did not include pipeline status.</p>
              )}
              {data.pipeline_status ? (
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <dt className="panel-title">Average duration</dt>
                    <dd className="mono mt-1">{formatDuration(data.pipeline_status.average_duration_seconds)}</dd>
                  </div>
                  <div>
                    <dt className="panel-title">Last run</dt>
                    <dd className="mono mt-1">{formatDuration(data.pipeline_status.last_duration_seconds)}</dd>
                  </div>
                  <div>
                    <dt className="panel-title">Processing jobs</dt>
                    <dd className="mono mt-1">{formatNumber(totals?.processing_jobs?.completed ?? 0, 0)} completed</dd>
                  </div>
                </dl>
              ) : null}
            </Panel>
          </section>

          {/* ------------------------------------------------------------ alerts */}
          <section className="grid gap-4 lg:grid-cols-2" aria-label="Alerts and quick actions">
            <Panel title="Recent alerts" actions={<Link to="/app/alerts" className="link text-xs">Alert desk</Link>}>
              {data.alerts?.length ? (
                <ul className="space-y-2.5">
                  {data.alerts.slice(0, 6).map((alert) => (
                    <li key={alert.id} className="flex items-start justify-between gap-3 text-sm">
                      <span className="min-w-0">
                        <span className="flex flex-wrap items-center gap-2">
                          <Badge tone={severityTone(alert.severity)}>{humanise(alert.severity)}</Badge>
                          <StatusBadge value={alert.status} />
                        </span>
                        <span className="mt-1 block line-clamp-2 text-xs muted">{alert.explanation}</span>
                      </span>
                      <span className="mono shrink-0 text-xs subtle">{relativeTime(alert.triggered_at)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState title="No alerts recorded" description="Alert rules and anomaly scans land here once they fire." action={<Link to="/app/alerts" className="btn btn-secondary btn-sm">Open alert desk</Link>} />
              )}
            </Panel>

            <Panel title="Quick actions" description="Suggested for your role and current data state.">
              {(() => {
                const actions = normaliseQuickActions(data.quick_actions)
                if (!actions.length) {
                  return <p className="text-sm muted">No quick actions were returned for this account state.</p>
                }
                return (
                  <ul className="flex flex-wrap gap-2">
                    {actions.map((action) =>
                      action.enabled ? (
                        <li key={action.id}>
                          <Link to={action.to} className="btn btn-secondary btn-sm">
                            {action.label}
                          </Link>
                        </li>
                      ) : (
                        <li key={action.id}>
                          <button
                            type="button"
                            className="btn btn-secondary btn-sm"
                            disabled
                            title={action.disabled_reason ?? 'Disabled for the current data state'}
                          >
                            {action.label}
                          </button>
                        </li>
                      ),
                    )}
                  </ul>
                )
              })()}
            </Panel>
          </section>

          {summary.error ? (
            <div className="card-tight border-alert-high/40 text-xs text-alert-high" role="status">
              Last refresh failed: {summary.error.message}. Retrying automatically.
            </div>
          ) : null}
        </div>
      )}

      {!summary.data && summary.loading ? (
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <ChartSkeleton height={260} />
          <ChartSkeleton height={260} />
        </div>
      ) : null}
    </>
  )
}
