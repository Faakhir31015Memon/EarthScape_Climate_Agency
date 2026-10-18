import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Activity, ArrowDownLeft, ArrowUpRight, Download, Map as MapIcon, TriangleAlert } from 'lucide-react'
import { Badge, ChartSkeleton, EmptyState, ErrorState, FilterBar, Panel, RefreshButton, Select, Skeleton, type Option } from '../../components/ui'
import { TrendChart } from '../../components/charts/TrendChart'
import { RegionBarChart } from '../../components/charts/RegionBarChart'
import { ClimateMap } from '../../components/maps/ClimateMap'
import { useAsync } from '../../hooks/useAsync'
import api, { type TrendParams } from '../../services/api'
import type { Metric } from '../../types/api'
import { formatCount, normaliseMetrics, normaliseRegions } from './components/publicData'
import { formatNumber } from '../../lib/format'
import { ProvenanceNote, PublicContainer } from './components/PublicSections'
import { cn } from '../../lib/cn'

const ALL_REGIONS = '__all__'
type InsightFrequency = 'monthly' | 'annual'

const FREQUENCY_LABEL: Record<InsightFrequency, string> = { monthly: 'Monthly', annual: 'Annual' }

/** Trend/chart unavailability is a documented API state, not an error: show the reason it returned. */
function InsufficientData({ trend, metric }: { trend: { reason?: string; available_points?: number; required_points?: number; note?: string }; metric: Metric }) {
  return (
    <EmptyState
      icon={<Activity className="h-5 w-5" />}
      tone="warning"
      title="Not enough verified data for this view"
      description={
        <>
          {trend.reason ?? trend.note ?? 'The analytics service declined to build a series for the current selection.'}
          {typeof trend.available_points === 'number' || typeof trend.required_points === 'number' ? (
            <>
              {' '}
              Points available: <strong className="text-[var(--text)]">{formatCount(trend.available_points ?? 0)}</strong> · points required:{' '}
              <strong className="text-[var(--text)]">{formatCount(trend.required_points ?? 0)}</strong>.
            </>
          ) : null}
        </>
      }
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/login" className="btn btn-primary btn-sm">
            Sign in and run ingestion
          </Link>
          <Link to="/data-sources" className="btn btn-secondary btn-sm">
            Where data comes from ({metric})
          </Link>
        </div>
      }
    />
  )
}

export function InsightsPage() {
  const [metric, setMetric] = useState<Metric>('temperature_c')
  const [region, setRegion] = useState<string>(ALL_REGIONS)
  const [frequency, setFrequency] = useState<InsightFrequency>('monthly')

  const metricsState = useAsync(() => api.climate.metrics(), [])
  const regionsState = useAsync(() => api.climate.regions(), [])

  const metrics = useMemo(() => normaliseMetrics(metricsState.data), [metricsState.data])
  const regions = useMemo(() => normaliseRegions(regionsState.data), [regionsState.data])

  const metricOptions = useMemo<Option[]>(
    () =>
      metrics.map((entry) => ({
        value: entry.field,
        label: entry.totalCount === null ? entry.label : `${entry.label} · ${formatCount(entry.populatedCount ?? 0)}/${formatCount(entry.totalCount)} records`,
        hint: entry.unit,
      })),
    [metrics],
  )

  const regionOptions = useMemo<Option[]>(
    () => [
      { value: ALL_REGIONS, label: 'All regions' },
      ...regions.map((entry) => ({
        value: entry.code,
        label: entry.observationCount ? `${entry.code} · ${formatCount(entry.observationCount)} obs` : `${entry.code} (no observations yet)`,
        hint: entry.country || entry.climate_zone || undefined,
      })),
    ],
    [regions],
  )

  const activeMetric = metrics.find((entry) => entry.field === metric)
  const metricUnit = activeMetric?.unit ?? ''
  const metricLabel = activeMetric?.label ?? metric.replace(/_/g, ' ')
  const regionsParam = region === ALL_REGIONS ? undefined : region

  const trendParams: TrendParams = useMemo(
    () => ({
      metric,
      region: regionsParam,
      // The backend frequency vocabulary is `annual`; the typed client declares `yearly`.
      frequency: (frequency === 'annual' ? 'annual' : 'monthly') as TrendParams['frequency'],
    }),
    [metric, regionsParam, frequency],
  )

  const trend = useAsync(() => api.climate.trend(trendParams), [trendParams])
  const comparison = useAsync(() => api.climate.compareRegions({ metrics: [metric], region: regionsParam }), [metric, regionsParam])
  const anomalies = useAsync(() => api.climate.anomalies({ metric, region: regionsParam, limit: 8 }), [metric, regionsParam])
  const mapData = useAsync(() => api.climate.map({ metric, region: regionsParam }), [metric, regionsParam])

  const mapPayload = useMemo(() => {
    const data = mapData.data
    if (!data) return null
    return {
      points: data.points ?? [],
      clusters: data.clusters ?? [],
      bounds: data.bounds ?? null,
      metrics: data.metrics ?? [metric],
      data_note: data.data_note ?? 'Station map: the most recent stored value per monitored location.',
    }
  }, [mapData.data, metric])

  const anomalyItems = anomalies.data?.anomalies ?? []
  const shownAnomalies = anomalyItems.slice(0, 8)
  const comparisonPayload = comparison.data
  const comparisonMetricMismatch = Boolean(comparisonPayload && comparisonPayload.metric !== metric)

  const refetchAll = () => {
    void trend.refetch()
    void comparison.refetch()
    void anomalies.refetch()
    void mapData.refetch()
  }

  return (
    <PublicContainer>
      <header className="mb-6 max-w-3xl">
        <p className="panel-title mb-2">Climate insights</p>
        <h1 className="font-display text-[28px] font-semibold leading-tight sm:text-[34px]">Insights overview built only on verified data</h1>
        <p className="mt-3 text-sm leading-relaxed muted">
          Every series, bar, marker and station value below is the result of an API query against observations this deployment has ingested and
          validated. Where the record is too thin to support a view, the reason the API returned is displayed instead of a chart.
        </p>
      </header>

      <FilterBar>
        <div className="w-full min-w-[15rem] max-w-[24rem]">
          <label className="label" htmlFor="insight-metric">
            Metric
          </label>
          {metricsState.loading && metricOptions.length === 0 ? (
            <Skeleton className="h-[38px] w-full" />
          ) : (
            <Select
              id="insight-metric"
              options={metricOptions}
              value={metric}
              onChange={(next) => setMetric(next as Metric)}
              disabled={metricOptions.length === 0}
              ariaLabel="Select the metric to analyse"
            />
          )}
          {metricsState.error ? (
            <p className="mt-1.5 text-xs leading-relaxed text-alert-critical">
              Metric catalogue unavailable — {metricsState.error.message}{' '}
              <button type="button" className="link" onClick={() => void metricsState.refetch()}>
                Retry
              </button>
            </p>
          ) : (
            <p className="mt-1.5 text-xs subtle">
              {metricOptions.length ? `${formatCount(metricOptions.length)} canonical fields, sourced from /api/v1/climate/metrics` : 'No metric fields were returned.'}
            </p>
          )}
        </div>

        <div className="w-full min-w-[15rem] max-w-[22rem]">
          <label className="label" htmlFor="insight-region">
            Region
          </label>
          {regionsState.loading && regions.length === 0 ? (
            <Skeleton className="h-[38px] w-full" />
          ) : (
            <Select
              id="insight-region"
              options={regionOptions}
              value={region}
              onChange={setRegion}
              disabled={regionOptions.length === 1}
              ariaLabel="Select a region scope"
            />
          )}
          <p className="mt-1.5 text-xs subtle">
            {regionsState.error
              ? 'Region catalogue unavailable; the scope stays global.'
              : `${formatCount(regions.filter((entry) => entry.hasData).length)} of ${formatCount(regions.length)} regions currently hold observations.`}
          </p>
        </div>

        <div>
          <span className="label" id="insight-frequency-label">
            Aggregation frequency
          </span>
          <div role="group" aria-labelledby="insight-frequency-label" className="flex gap-1">
            {(['monthly', 'annual'] as InsightFrequency[]).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={frequency === option}
                onClick={() => setFrequency(option)}
                className={cn('btn btn-sm', frequency === option ? 'btn-primary' : 'btn-secondary')}
              >
                {FREQUENCY_LABEL[option]}
              </button>
            ))}
          </div>
          <p className="mt-1.5 text-xs subtle">Precipitation is summed; other fields are averaged.</p>
        </div>

        <RefreshButton onClick={refetchAll} busy={trend.refreshing || comparison.refreshing || anomalies.refreshing || mapData.refreshing} label="Refresh views" />
      </FilterBar>

      <div className="space-y-6">
        <Panel
          title={`${metricLabel} · ${FREQUENCY_LABEL[frequency]}${region === ALL_REGIONS ? ' · all regions' : ` · ${region}`}`}
          description="Aggregated from validated station-day observations held by the platform."
          footer={
            trend.data?.available && trend.data.series.length ? (
              <span>
                {trend.data.note ?? `Series covers ${trend.data.first_period ?? '—'} to ${trend.data.last_period ?? '—'}.`}
                {trend.data.coverage
                  ? ` Coverage: ${formatCount(trend.data.coverage.observation_count)} observations across ${formatCount(trend.data.coverage.locations)} locations and ${formatCount(trend.data.coverage.regions)} regions.`
                  : null}
              </span>
            ) : (
              'The chart appears only when the aggregation pipeline returns usable periods.'
            )
          }
        >
          {trend.loading ? (
            <ChartSkeleton height={320} />
          ) : trend.error ? (
            <ErrorState error={trend.error} onRetry={() => void trend.refetch()} />
          ) : trend.data?.available === false ? (
            <InsufficientData trend={trend.data} metric={metric} />
          ) : !trend.data || trend.data.series.length === 0 ? (
            <EmptyState
              title="No periods returned"
              description={`The query for ${metricLabel} at ${FREQUENCY_LABEL[frequency].toLowerCase()} frequency answered with an empty series.`}
              action={<RefreshButton onClick={() => void trend.refetch()} label="Retry query" />}
            />
          ) : (
            <TrendChart
              series={trend.data.series}
              metric={trend.data.metric}
              unit={trend.data.unit || metricUnit}
              label={trend.data.label || metricLabel}
              variant="line"
              height={320}
              showCounts
            />
          )}
        </Panel>

        <div className="grid gap-6 lg:grid-cols-2">
          <Panel
            title="Regional comparison"
            description="Mean (or total, for additive metrics) per region, with the observation count behind each bar."
            footer={
              comparisonPayload?.regions?.length
                ? `Regions without observations are omitted. ${formatCount(comparisonPayload.spread?.difference ?? null)} ${comparisonPayload.unit || metricUnit} separates the highest from the lowest region shown.`
                : 'Comparison needs at least two regions with populated values.'
            }
          >
            {comparison.loading ? (
              <ChartSkeleton height={300} />
            ) : comparison.error ? (
              <ErrorState error={comparison.error} onRetry={() => void comparison.refetch()} />
            ) : !comparisonPayload || !comparisonPayload.regions?.length ? (
              <EmptyState
                title="No regional aggregates yet"
                description={
                  (comparisonPayload as { reason?: string } | undefined)?.reason ??
                  'No stored observation carries a value for this metric inside the requested scope, so regions cannot be compared.'
                }
                action={
                  <Link to="/login" className="btn btn-secondary btn-sm">
                    Sign in to ingest data
                  </Link>
                }
              />
            ) : (
              <>
                {comparisonMetricMismatch ? (
                  <p className="mb-3 flex items-start gap-2 text-xs leading-relaxed text-solar-700 dark:text-solar-300">
                    <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span>
                      The comparison endpoint returned <span className="mono">{comparisonPayload.metric}</span> rather than the requested{' '}
                      <span className="mono">{metric}</span>; the chart below is labelled with what the API actually computed.
                    </span>
                  </p>
                ) : null}
                <RegionBarChart
                  regions={comparisonPayload.regions}
                  metric={comparisonPayload.metric}
                  unit={comparisonPayload.unit || metricUnit}
                  label={comparisonPayload.label || metricLabel}
                  height={300}
                />
              </>
            )}
          </Panel>

          <Panel
            title="Recent anomalies"
            description="Detection results with the comparison that produced them."
            footer={
              anomalies.data?.caveat ??
              'Anomalies describe departures from the baseline or threshold in force; they are measurements of the record, not verdicts about the climate.'
            }
          >
            {anomalies.loading ? (
              <div className="space-y-2.5">
                {Array.from({ length: 5 }).map((_, index) => (
                  <Skeleton key={index} className="h-16 w-full" />
                ))}
              </div>
            ) : anomalies.error ? (
              <ErrorState error={anomalies.error} onRetry={() => void anomalies.refetch()} />
            ) : anomalyItems.length === 0 ? (
              <EmptyState
                title="No anomalies flagged for this selection"
                description={
                  anomalies.data
                    ? `${formatCount(anomalies.data.evaluated_observations)} observations were evaluated with the ${anomalies.data.method} method${
                        anomalies.data.date_range?.start ? ` between ${anomalies.data.date_range.start} and ${anomalies.data.date_range.end}` : ''
                      }. Nothing crossed the detection limit.`
                    : 'The anomaly service returned no results.'
                }
              />
            ) : (
              <>
                {anomalies.data?.evaluated_observations !== undefined ? (
                  <p className="mb-3 text-xs subtle">
                    {formatCount(anomalies.data.evaluated_observations)} observations evaluated · {formatCount(anomalies.data.anomaly_count)} flagged ·
                    method <span className="mono">{anomalies.data.method}</span> · direction <span className="mono">{anomalies.data.direction}</span>
                    {anomalyItems.length > shownAnomalies.length ? ` · showing first ${shownAnomalies.length}` : ''}
                  </p>
                ) : null}
                <ul className="space-y-3">
                  {shownAnomalies.map((anomaly, index) => (
                    <li key={`${anomaly.observation_date}-${anomaly.location_code ?? anomaly.region_code ?? index}`} className="card-tight">
                      <div className="flex flex-wrap items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold">
                            {anomaly.location_name ?? anomaly.location_code ?? anomaly.region_code ?? 'Global record'}
                            <span className="ml-2 text-xs font-normal subtle">{anomaly.observation_date}</span>
                          </p>
                          <p className="mt-0.5 text-xs subtle">
                            {anomaly.metric_label} · <span className="mono">{formatNumber(anomaly.value, 1)} {anomaly.unit}</span> ·{' '}
                            deviation {formatNumber(anomaly.deviation_score, 2)} sigma
                          </p>
                        </div>
                        <div className="flex shrink-0 items-center gap-2">
                          <Badge tone={anomaly.direction === 'above' ? 'danger' : 'info'}>
                            {anomaly.direction === 'above' ? (
                              <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
                            ) : (
                              <ArrowDownLeft className="h-3 w-3" aria-hidden="true" />
                            )}
                            {anomaly.direction}
                          </Badge>
                          <Badge tone={anomaly.severity === 'critical' || anomaly.severity === 'high' ? 'danger' : anomaly.severity === 'medium' ? 'warning' : 'neutral'}>
                            {anomaly.severity}
                          </Badge>
                        </div>
                      </div>
                      <p className="mt-2 text-sm leading-relaxed muted">{anomaly.explanation}</p>
                      {anomaly.source_name ? <p className="mt-1.5 text-xs subtle">Source: {anomaly.source_name}</p> : null}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Panel>
        </div>

        <Panel
          title="Station view"
          description="The most recent stored measurement per monitored location, with clusters at low zoom."
          actions={
            <Link to="/app/map" className="btn btn-secondary btn-sm">
              <MapIcon className="h-3.5 w-3.5" aria-hidden="true" /> Full map in the app
            </Link>
          }
          footer={mapPayload?.data_note ?? 'Map values come from /api/v1/climate/map and carry their observation date and source.'}
        >
          {mapData.loading ? (
            <ChartSkeleton height={420} />
          ) : mapData.error ? (
            <ErrorState error={mapData.error} onRetry={() => void mapData.refetch()} />
          ) : !mapPayload || (mapPayload.points.length === 0 && mapPayload.clusters.length === 0) ? (
            <EmptyState
              title="No stations hold a value for this selection"
              description={
                mapPayload
                  ? 'The map layer answered with zero points, so nothing is plotted.'
                  : 'The map endpoint did not return a payload.'
              }
              action={
                <div className="flex flex-wrap gap-2">
                  <RefreshButton onClick={() => void mapData.refetch()} label="Retry map query" />
                  <Link to="/login" className="btn btn-secondary btn-sm">
                    Sign in to run ingestion
                  </Link>
                </div>
              }
            />
          ) : (
            <ClimateMap data={mapPayload} metric={metric} unit={metricUnit} height={420} />
          )}
        </Panel>

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,20rem)]">
          <ProvenanceNote>
            Availability is part of the contract: <span className="mono">TrendResponse</span> carries <span className="mono">note</span>,{' '}
            <span className="mono">reason</span>, <span className="mono">available_points</span> and <span className="mono">required_points</span>;
            anomaly results carry a standing <span className="mono">caveat</span>; the map layer carries{' '}
            <span className="mono">data_note</span>. Those strings are shown verbatim when they arrive.
          </ProvenanceNote>
          <Panel title="Take it further" dense>
            <p className="text-sm leading-relaxed muted">
              Signed-in analysts get correlations, historical normals, saved analyses and CSV export against the same record.
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Link to="/login" className="btn btn-primary btn-sm">
                Sign in
              </Link>
              <Link to="/app/analytics" className="btn btn-secondary btn-sm">
                <Download className="h-3.5 w-3.5" aria-hidden="true" /> Analytics workspace
              </Link>
            </div>
          </Panel>
        </div>
      </div>
    </PublicContainer>
  )
}

export default InsightsPage
