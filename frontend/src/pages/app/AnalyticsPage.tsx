/**
 * Climate analytics workbench. All filters live in the URL query string so any
 * view is shareable and reloadable; charts render only what `GET /climate/trends`
 * and `GET /climate/correlations` actually computed, including their honest
 * `available:false` reason objects.
 */
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { BarChart3, Download, Save } from 'lucide-react'
import api from '../../services/api'
import type { TrendParams } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import type { Frequency, GroupBy, Metric, MetricDefinition, Region, SeriesPoint } from '../../types/api'
import { ALL_METRICS } from '../../lib/format'
import { cn } from '../../lib/cn'
import { formatNumber, humanise, metricLabel } from '../../lib/format'
import {
  Badge,
  ChartSkeleton,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  FilterBar,
  Hint,
  Modal,
  MultiSelect,
  PageHeader,
  Panel,
  Progress,
  RefreshButton,
  Select,
  type Column,
} from '../../components/ui'
import { CorrelationHeatmap } from '../../components/charts/CorrelationHeatmap'
import { ScoreRing } from '../../components/charts/MiniCharts'
import { DateRangeFields, TrendOutcomeView } from './components/data/filters'
import { asList, freshnessTone, joinCsv, parseCsv, trendOutcome } from './components/data/normalise'

const FREQUENCIES: readonly Frequency[] = ['daily', 'weekly', 'monthly', 'annual']
const GROUPS: readonly GroupBy[] = ['none', 'region', 'location', 'country']

function validMetric(raw: string | null): Metric {
  return raw && (ALL_METRICS as string[]).includes(raw) ? (raw as Metric) : 'temperature_c'
}

function pickFrequency(raw: string | null): Frequency {
  return FREQUENCIES.find((value) => value === raw) ?? 'monthly'
}

function pickGroup(raw: string | null): GroupBy {
  return GROUPS.find((value) => value === raw) ?? 'none'
}

export default function AnalyticsPage() {
  const [params, setParams] = useSearchParams()
  const { can } = useAuth()
  const toast = useToast()
  const [saveOpen, setSaveOpen] = useState(false)
  const [saveName, setSaveName] = useState('')
  const [saving, setSaving] = useState(false)
  const [exporting, setExporting] = useState(false)

  const metric = validMetric(params.get('metric'))
  const frequency = pickFrequency(params.get('frequency'))
  const groupBy = pickGroup(params.get('group_by'))
  const chartParam = params.get('chart')
  const variant = chartParam === 'area' || chartParam === 'bar' ? chartParam : 'line'
  const start = params.get('start') ?? ''
  const end = params.get('end') ?? ''
  const regions = parseCsv(params.get('regions'))
  const locations = parseCsv(params.get('locations'))
  const correlationMetrics = parseCsv(params.get('corr')).filter((value): value is Metric => (ALL_METRICS as string[]).includes(value))

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params)
    Object.entries(changes).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, value)
    })
    setParams(next, { replace: false })
  }

  const metricsCatalog = useAsync(() => api.climate.metrics(), [])
  const metricDefinitions = asList<MetricDefinition>(metricsCatalog.data, 'metrics')
  const regionsState = useAsync(() => api.climate.regions(), [])
  const regionList = asList<Region>(regionsState.data, 'regions')

  const regionOptions = useMemo(
    () => regionList.map((region) => ({ value: region.code, label: `${region.name} (${region.code}) — ${region.location_count} locations` })),
    [regionList],
  )
  const locationOptions = useMemo(
    () =>
      regionList.flatMap((region) =>
        (region.locations ?? []).map((location) => ({
          value: location.code,
          label: `${location.name} — ${region.code}`,
          hint: location.code,
        })),
      ),
    [regionList],
  )

  const trendParams = useMemo<TrendParams>(
    () => ({
      metric,
      frequency,
      group_by: groupBy,
      start: start || undefined,
      end: end || undefined,
      region: joinCsv(regions),
      regions: joinCsv(regions),
      location: joinCsv(locations),
      location_codes: joinCsv(locations),
      require_baseline: false,
    }),
    [metric, frequency, groupBy, start, end, regions.join('|'), locations.join('|')],
  )

  const trend = useAsync(() => api.climate.trend(trendParams), [JSON.stringify(trendParams)])
  const trendOutcomeResult = trendOutcome(trend.data, metric)

  const correlationParams = useMemo(
    () => ({
      metrics: correlationMetrics.length >= 2 ? correlationMetrics : [metric],
      start: start || undefined,
      end: end || undefined,
      region: joinCsv(regions),
      regions: joinCsv(regions),
    }),
    [correlationMetrics.join('|'), metric, start, end, regions.join('|')],
  )
  const correlation = useAsync(
    () => api.climate.correlation(correlationParams),
    [JSON.stringify(correlationParams)],
    { enabled: correlationMetrics.length >= 2 },
  )

  const quality = useAsync(
    () => api.climate.quality({ start: start || undefined, end: end || undefined, region: joinCsv(regions) }),
    [start, end, regions.join('|')],
  )
  const coverage = useAsync(() => api.ingestion.coverage(), [])

  const canExport = can('analytics:export')

  const saveAnalysis = async () => {
    if (!saveName.trim()) return
    setSaving(true)
    try {
      await api.analytics.save({
        name: saveName.trim(),
        filters: {
          metric,
          frequency,
          group_by: groupBy,
          regions,
          locations,
          start,
          end,
        },
        chart_config: { chart: variant, corr: correlationMetrics },
      })
      toast.success('Analysis saved', 'Find it under Saved analyses.')
      setSaveOpen(false)
      setSaveName('')
    } catch (error) {
      toast.danger('Could not save analysis', error instanceof ApiRequestError ? error.message : String(error))
    } finally {
      setSaving(false)
    }
  }

  const exportCsv = async () => {
    setExporting(true)
    try {
      await api.analytics.exportCsv({
        metric,
        frequency,
        start: start || undefined,
        end: end || undefined,
        region: joinCsv(regions),
      })
    } catch (error) {
      toast.danger('Export failed', error instanceof ApiRequestError ? error.message : String(error))
    } finally {
      setExporting(false)
    }
  }

  type SeriesRow = SeriesPoint & { _group?: string }
  const seriesColumns: Column<SeriesRow>[] = [
    { key: 'period', header: 'Period', cell: (row) => <span className="mono">{row.period}</span> },
    {
      key: 'group',
      header: groupBy === 'none' ? 'Scope' : humanise(groupBy),
      cell: (row) => <span>{row.region_code ?? row.location_code ?? row.country ?? 'all locations'}</span>,
    },
    {
      key: 'value',
      header: 'Value',
      align: 'right',
      cell: (row) => (
        <span className="mono">
          {formatNumber(row.value, 2)} {trendOutcomeResult.kind === 'ready' ? trendOutcomeResult.unit : ''}
        </span>
      ),
    },
    { key: 'count', header: 'Obs.', align: 'right', cell: (row) => <span className="mono">{formatNumber(row.observation_count, 0)}</span> },
    { key: 'min', header: 'Min', align: 'right', cell: (row) => <span className="mono">{formatNumber(row.min, 2)}</span> },
    { key: 'max', header: 'Max', align: 'right', cell: (row) => <span className="mono">{formatNumber(row.max, 2)}</span> },
    { key: 'std', header: 'Std', align: 'right', cell: (row) => <span className="mono">{formatNumber(row.std, 2)}</span> },
  ]

  const coverageReport = trendOutcomeResult.kind === 'ready' ? trendOutcomeResult.coverage : undefined

  return (
    <>
      <PageHeader
        eyebrow="Monitor"
        title="Climate analytics workbench"
        description="Trends, regional comparisons and correlation matrices computed from stored observations. The URL carries the full query, so any view is shareable."
        actions={
          <>
            <button
              type="button"
              className={cn('btn btn-secondary btn-sm', !canExport && 'opacity-60')}
              onClick={() => setSaveOpen(true)}
              disabled={!canExport}
              title={canExport ? 'Save the exact query and chart configuration' : 'Saving analyses requires the climate analyst or administrator role.'}
            >
              <Save className="h-3.5 w-3.5" /> Save this analysis
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => void exportCsv()}
              disabled={!canExport || exporting}
              title={canExport ? 'Download filtered series as CSV' : 'CSV export requires the climate analyst or administrator role.'}
            >
              <Download className="h-3.5 w-3.5" /> {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
          </>
        }
      />

      <FilterBar>
        <Field label="Metric" htmlFor="analytics-metric" className="w-52">
          <Select
            id="analytics-metric"
            ariaLabel="Metric"
            options={
              metricDefinitions.length
                ? metricDefinitions.map((definition) => ({ value: definition.field, label: `${definition.label} (${definition.unit})` }))
                : ALL_METRICS.map((field) => ({ value: field, label: `${metricLabel(field)} (${humanise(field)})` }))
            }
            value={metric}
            onChange={(value) => update({ metric: value })}
          />
        </Field>
        <Field label="Frequency" htmlFor="analytics-frequency" className="w-36">
          <Select
            id="analytics-frequency"
            ariaLabel="Frequency"
            options={FREQUENCIES.map((value) => ({ value, label: humanise(value) }))}
            value={frequency}
            onChange={(value) => update({ frequency: value })}
          />
        </Field>
        <Field label="Group by" htmlFor="analytics-group" className="w-36">
          <Select
            id="analytics-group"
            ariaLabel="Group by"
            options={GROUPS.map((value) => ({ value, label: value === 'none' ? 'No grouping' : humanise(value) }))}
            value={groupBy}
            onChange={(value) => update({ group_by: value })}
          />
        </Field>
        <div className="w-56">
          <MultiSelect
            label="Regions"
            placeholder="All regions"
            options={regionOptions}
            value={regions}
            onChange={(next) => update({ regions: joinCsv(next) })}
          />
        </div>
        <div className="w-64">
          <MultiSelect
            label="Locations"
            placeholder="All locations"
            options={locationOptions}
            value={locations}
            onChange={(next) => update({ locations: joinCsv(next) })}
          />
        </div>
        <DateRangeFields idPrefix="analytics" start={start} end={end} onChange={(next) => update({ start: next.start, end: next.end })} />
        <RefreshButton
          onClick={() => {
            void trend.refetch()
            void correlation.refetch()
            void quality.refetch()
          }}
          busy={trend.refreshing}
        />
      </FilterBar>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-4">
          {trend.error && !trend.data ? <ErrorState error={trend.error} onRetry={() => void trend.refetch()} /> : null}

          <Panel
            title={`${metricLabel(metric)} — ${frequency} ${groupBy === 'none' ? '' : `by ${groupBy}`}`}
            description={
              trendOutcomeResult.kind === 'ready'
                ? `${trendOutcomeResult.series.length} periods · unit ${trendOutcomeResult.unit || '—'}` +
                  (trend.data?.truncated
                    ? ` · the backend returned the first ${trendOutcomeResult.series.length} of ${trend.data.point_count}`
                    : '')
                : 'The backend reports this series as not computable.'
            }
            actions={
              <div role="group" aria-label="Chart type" className="flex gap-1">
                {(['line', 'area', 'bar'] as const).map((option) => (
                  <button
                    key={option}
                    type="button"
                    className={`btn btn-sm ${variant === option ? 'btn-primary' : 'btn-secondary'}`}
                    aria-pressed={variant === option}
                    onClick={() => update({ chart: option })}
                  >
                    {humanise(option)}
                  </button>
                ))}
              </div>
            }
          >
            {trend.loading && !trend.data ? (
              <ChartSkeleton height={320} />
            ) : (
              <TrendOutcomeView
                outcome={trendOutcomeResult}
                metric={metric}
                groupBy={groupBy as 'none' | 'region' | 'location'}
                variant={variant}
                height={320}
                actionLabel="Run an ingestion job"
              />
            )}
            {trendOutcomeResult.kind === 'ready' && trendOutcomeResult.note ? (
              <Hint>{trendOutcomeResult.note}</Hint>
            ) : null}
          </Panel>

          <Panel title="Series data" description="Exactly the points returned by the trend endpoint.">
            {trendOutcomeResult.kind === 'ready' ? (
              <DataTable
                columns={seriesColumns}
                rows={trendOutcomeResult.series}
                rowKey={(row, index) => `${row.period}-${row.region_code ?? row.location_code ?? 'all'}-${index}`}
                compact
                caption="Trend series points"
                empty="No periods were returned."
              />
            ) : (
              <p className="text-sm muted">No series to tabulate for the current selection.</p>
            )}
          </Panel>

          <Panel
            title="Correlation matrix"
            description="Pearson coefficients between metrics over the shared scope of stored observations."
            actions={
              <div className="w-72">
                <MultiSelect
                  label="Correlate metrics"
                  placeholder="Pick ≥2 metrics"
                  options={(metricDefinitions.length ? metricDefinitions.map((definition) => ({ value: definition.field, label: definition.label })) : ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) })))}
                  value={correlationMetrics.length >= 2 ? correlationMetrics : [metric]}
                  onChange={(next) => update({ corr: joinCsv(next) })}
                  maxChips={2}
                />
              </div>
            }
          >
            {correlationMetrics.length < 2 ? (
              <EmptyState
                icon={<BarChart3 className="h-5 w-5" />}
                title="Select at least two metrics"
                description="A correlation matrix needs two or more metrics with overlapping populated observations."
              />
            ) : correlation.error && !correlation.data ? (
              <ErrorState error={correlation.error} onRetry={() => void correlation.refetch()} />
            ) : correlation.loading && !correlation.data ? (
              <ChartSkeleton height={240} />
            ) : correlation.data ? (
              (() => {
                const response = correlation.data
                if (response.available === false || !response.cells.length) {
                  return (
                    <EmptyState
                      tone="warning"
                      title="Correlations are not computable for this scope"
                      description={
                        response.reason ??
                        response.note ??
                        'The backend found too few paired, complete observations to compute coefficients.'
                      }
                    />
                  )
                }
                return <CorrelationHeatmap response={response} />
              })()
            ) : null}
          </Panel>
        </div>

        {/* --------------------------------------------------------- sidebar */}
        <aside className="space-y-4">
          <Panel title="Data quality" description="Filtered by the current scope.">
            {quality.loading && !quality.data ? (
              <div className="skeleton h-24 w-full" />
            ) : quality.error && !quality.data ? (
              <ErrorState error={quality.error} />
            ) : quality.data ? (
              <div className="space-y-3">
                <ScoreRing score={quality.data.available ? quality.data.completeness_pct : null} label="Completeness" size={80} />
                <ul className="space-y-1.5 text-xs">
                  {quality.data.per_source.slice(0, 6).map((source) => (
                    <li key={source.source_name} className="flex items-center justify-between gap-2">
                      <span className="truncate">{source.source_name}</span>
                      <Badge tone={freshnessTone(source.status)}>{humanise(source.status)}</Badge>
                    </li>
                  ))}
                </ul>
                {!quality.data.available && quality.data.reason ? <p className="text-xs subtle">{quality.data.reason}</p> : null}
              </div>
            ) : null}
          </Panel>

          <Panel title="Coverage" description="Completeness of the trend scope.">
            {coverageReport ? (
              <ul className="space-y-2">
                {[metric, 'temperature_c', 'precipitation_mm']
                  .filter((field, index, list) => coverageReport.completeness[field] && list.indexOf(field) === index)
                  .slice(0, 4)
                  .map((field) => {
                    const entry = coverageReport.completeness[field]
                    return (
                      <li key={field}>
                        <div className="mb-1 flex justify-between text-xs">
                          <span className="truncate">{metricLabel(field)}</span>
                          <span className="mono">{formatNumber(entry.pct, 1)}%</span>
                        </div>
                        <Progress value={entry.pct} tone="forest" label={`${metricLabel(field)} completeness`} />
                      </li>
                    )
                  })}
                <li className="pt-1 text-xs subtle">
                  {formatNumber(coverageReport.observation_count, 0)} observations ·{' '}
                  {coverageReport.date_range ? `${coverageReport.date_range.start} → ${coverageReport.date_range.end}` : 'no date range'}
                </li>
              </ul>
            ) : coverage.data ? (
              <div className="space-y-2">
                <ul className="space-y-1.5 text-xs">
                  {coverage.data.sources.slice(0, 6).map((source) => (
                    <li key={source.source_name} className="flex items-center justify-between gap-2">
                      <span className="truncate">{source.source_name}</span>
                      <span className="mono subtle">{formatNumber(source.observation_count, 0)} obs</span>
                    </li>
                  ))}
                </ul>
                {coverage.data.sources.length > 6 ? <p className="text-xs subtle">+{coverage.data.sources.length - 6} more sources.</p> : null}
                {coverage.data.note ? <p className="text-xs subtle">{coverage.data.note}</p> : null}
              </div>
            ) : coverage.error ? (
              <p className="text-xs subtle">
                The coverage endpoint returned no data ({coverage.error.code}). The trend response embeds its own coverage report above when available.
              </p>
            ) : (
              <p className="text-xs subtle">No coverage report for the current selection.</p>
            )}
          </Panel>

          <Panel title="Share this view">
            <p className="text-xs muted">Filters, grouping and chart type are encoded in the URL — copy it from the browser bar, or save the analysis (analyst role) to record the exact query.</p>
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <Link to="/app/history" className="link">Historical explorer</Link>
              <span aria-hidden="true">·</span>
              <Link to="/app/anomalies" className="link">Anomaly detection</Link>
              <span aria-hidden="true">·</span>
              <Link to="/app/saved-analyses" className="link">Saved analyses</Link>
            </div>
          </Panel>
        </aside>
      </div>

      <Modal
        open={saveOpen}
        title="Save this analysis"
        description="Stores the exact trend query and chart configuration shown on screen."
        onClose={() => setSaveOpen(false)}
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => setSaveOpen(false)} disabled={saving}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void saveAnalysis()} disabled={saving || !saveName.trim()}>
              {saving ? 'Saving…' : 'Save analysis'}
            </button>
          </>
        }
      >
        <Field label="Name" htmlFor="save-name" required hint="Saved analyses are listed under Saved analyses for your role.">
          <input id="save-name" className="input" value={saveName} onChange={(event) => setSaveName(event.target.value)} placeholder="e.g. Region temperature trends 2024" />
        </Field>
        <pre className="mono mt-3 max-h-40 overflow-auto rounded-lg border p-2 text-xs" style={{ borderColor: 'var(--border)' }}>
          {JSON.stringify({ ...trendParams, chart: variant }, null, 2)}
        </pre>
      </Modal>
    </>
  )
}
