/**
 * Map — the geographic view of stored observations. It renders whatever the map
 * endpoint returns (station points or clusters), plus a drill-down for a single
 * station. It is explicitly NOT a live sensor feed: the panel says so, and the
 * freshness of each station is derived from its real last-observation date.
 */
import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Globe2 } from 'lucide-react'
import { api } from '../../services/api'
import type { MapPoint, Metric, Region } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { ALL_METRICS, formatNumber, metricLabel, metricUnit, relativeTime } from '../../lib/format'
import {
  Badge,
  EmptyState,
  ErrorState,
  Field,
  Panel,
  RefreshButton,
  Select,
  Skeleton,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { ChartSkeleton } from '../../components/ui/Skeleton'
import { ClimateMap } from '../../components/maps/ClimateMap'
import { DateRangeFields, TrendOutcomeView } from './components/data/filters'
import { asList, dateFreshness, trendOutcome } from './components/data/normalise'

export default function MapPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const metric = (searchParams.get('metric') ?? 'temperature_c') as Metric
  const start = searchParams.get('start') ?? undefined
  const end = searchParams.get('end') ?? undefined
  const region = searchParams.get('region') ?? undefined
  const countries = searchParams.get('countries') ?? undefined
  const radius = searchParams.get('radius') ?? undefined
  const selectedCode = searchParams.get('station') ?? undefined

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(changes).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, value)
    })
    setSearchParams(next, { replace: true })
  }

  const regionsState = useAsync(() => api.climate.regions(), [])
  const mapState = useAsync(
    () =>
      api.climate.map({
        metric,
        start,
        end,
        region,
        countries,
        radius_km: radius ? Number(radius) : undefined,
      }),
    [metric, start, end, region, countries, radius],
  )

  const points = useMemo(() => mapState.data?.points ?? [], [mapState.data])
  const clusters = useMemo(() => mapState.data?.clusters ?? [], [mapState.data])
  const countryOptions = useMemo(() => {
    const codes = Array.from(
      new Set(points.map((point) => point.country).filter((code): code is string => Boolean(code))),
    )
    return codes.map((code) => ({ value: code, label: code }))
  }, [points])

  const sortedStations = useMemo(
    () =>
      [...points].sort((left, right) => {
        const l = left.last_observation_date ? new Date(left.last_observation_date).getTime() : 0
        const r = right.last_observation_date ? new Date(right.last_observation_date).getTime() : 0
        return r - l
      }),
    [points],
  )

  const selectedPoint = useMemo(
    () => (selectedCode ? points.find((point) => point.location_code === selectedCode) ?? null : null),
    [points, selectedCode],
  )

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Monitoring"
        title="Climate map"
        description="Spatial distribution of stored observations for the selected metric and period."
        actions={<RefreshButton onClick={() => void mapState.refetch()} busy={mapState.refreshing} />}
      />

      <Panel dense bodyClassName="p-0">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Field label="Metric" htmlFor="map-metric" className="w-52">
            <Select
              id="map-metric"
              value={metric}
              onChange={(value) => update({ metric: value })}
              options={ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) }))}
            />
          </Field>
          <Field label="Region" htmlFor="map-region" className="w-44">
            <Select
              id="map-region"
              value={region ?? ''}
              onChange={(value) => update({ region: value || undefined })}
              options={asList<Region>(regionsState.data, 'regions').map((item) => ({
                value: item.code,
                label: item.name ? `${item.name} (${item.code})` : item.code,
              }))}
              placeholder="All regions"
            />
          </Field>
          <Field label="Countries" htmlFor="map-countries" className="w-44">
            <Select
              id="map-countries"
              value={countries ?? ''}
              onChange={(value) => update({ countries: value || undefined })}
              options={countryOptions}
              placeholder="All countries"
            />
          </Field>
          <Field label="Cluster radius (km)" htmlFor="map-radius" className="w-40" hint="Only affects server grouping.">
            <input
              id="map-radius"
              type="number"
              min={1}
              className="input"
              value={radius ?? ''}
              placeholder="e.g. 50"
              onChange={(event) => update({ radius: event.target.value || undefined })}
            />
          </Field>
          <DateRangeFields start={start ?? ''} end={end ?? ''} onChange={(range) => update(range)} idPrefix="map" />
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <Panel
          title="Observation map"
          description={
            mapState.data
              ? clusters.length && points.length
                ? `${formatNumber(points.length, 0)} stations shown · ${formatNumber(clusters.length, 0)} groups (zoom in to reveal individual stations).`
                : `${formatNumber(points.length, 0)} stations in view.`
              : 'Loading geographic data…'
          }
          dense
          bodyClassName="p-0"
        >
          {mapState.loading ? (
            <div className="p-4">
              <ChartSkeleton height={460} />
            </div>
          ) : null}
          {mapState.error ? (
            <div className="p-4">
              <ErrorState error={mapState.error} onRetry={() => void mapState.refetch()} />
            </div>
          ) : null}
          {mapState.data && points.length === 0 && clusters.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={<Globe2 className="h-6 w-6" />}
                title="No mapped observations"
                description="No stored observations match this metric, region and date range, so there is nothing to plot."
                tone="warning"
                action={
                  <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                    Ingest data
                  </Link>
                }
              />
            </div>
          ) : null}
          {mapState.data && (points.length || clusters.length) ? (
            <ClimateMap
              data={mapState.data}
              metric={metric}
              unit={metricUnit(metric)}
              height={460}
              onSelectPoint={(point) => update({ station: point.location_code })}
            />
          ) : null}

          {mapState.data ? (
            <div className="space-y-1 border-t px-4 py-3 text-xs muted" style={{ borderColor: 'var(--border)' }}>
              <p className="italic">{mapState.data.data_note}</p>
              {mapState.data.bounds ? (
                <p className="mono">
                  bounds (S,W,N,E): {mapState.data.bounds.map((n) => formatNumber(n, 3)).join(', ')}
                </p>
              ) : null}
              <p className="mono">generated at: {relativeTime(mapState.data.generated_at)}</p>
              <p className="subtle">
                Synced from the observations API on each request — this is periodic refresh, not live sensor streaming.
              </p>
            </div>
          ) : null}
        </Panel>

        <Panel
          title="Stations"
          description={
            points.length > 100
              ? `${formatNumber(points.length, 0)} in range · showing 100 most recent. Select one to drill in.`
              : 'Select a station to drill into its history.'
          }
          dense
        >
          {mapState.loading ? (
            <ul className="space-y-2">
              {Array.from({ length: 8 }).map((_, index) => (
                <li key={index}>
                  <Skeleton className="h-12 w-full" />
                </li>
              ))}
            </ul>
          ) : null}
          {!mapState.loading && sortedStations.length === 0 ? (
            <p className="text-xs subtle">No stations in the current view.</p>
          ) : null}
          <ul className="max-h-[30rem] space-y-1 overflow-auto pr-1">
            {sortedStations.slice(0, 100).map((point) => {
              const freshness = dateFreshness(point.last_observation_date)
              const isActive = point.location_code === selectedCode
              return (
                <li key={point.location_code}>
                  <button
                    type="button"
                    onClick={() => update({ station: point.location_code })}
                    className={
                      'w-full rounded-lg border px-2.5 py-2 text-left text-sm transition hover:bg-[var(--surface-muted)] ' +
                      (isActive ? 'border-[var(--brand)]' : 'border-[var(--border)]')
                    }
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">{point.location_name ?? point.location_code}</span>
                      <Badge
                        tone={freshness === 'fresh' ? 'success' : freshness === 'lagging' ? 'warning' : freshness === 'stale' ? 'danger' : 'neutral'}
                        title={`Last observation ${relativeTime(point.last_observation_date)}`}
                      >
                        {freshness}
                      </Badge>
                    </span>
                    <span className="mono block text-xs subtle">
                      {point.location_code} · {relativeTime(point.last_observation_date)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </Panel>
      </div>

      {selectedPoint ? (
        <StationDrill
          point={selectedPoint}
          metric={metric}
          start={start}
          end={end}
          onClose={() => update({ station: undefined })}
        />
      ) : null}
    </div>
  )
}

function StationDrill({
  point,
  metric,
  start,
  end,
  onClose,
}: {
  point: MapPoint
  metric: Metric
  start?: string
  end?: string
  onClose: () => void
}) {
  const trendState = useAsync(
    () =>
      api.climate.trend({
        metric,
        frequency: 'monthly',
        start,
        end,
        location: point.location_code,
        location_codes: point.location_code,
        require_baseline: false,
      }),
    [metric, point.location_code, start, end],
  )

  const measurement = point.measurements?.[metric]

  return (
    <Panel
      title={`${point.location_name ?? point.location_code} — station detail`}
      description={`${point.region_code} · ${point.country} · (${formatNumber(point.latitude, 3)}, ${formatNumber(point.longitude, 3)})`}
      actions={
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <span>
          <span className="muted">Latest {metricLabel(metric).toLowerCase()}:</span>{' '}
          <span className="mono font-semibold">
            {measurement && measurement.value !== null && measurement.value !== undefined
              ? `${formatNumber(measurement.value)} ${measurement.unit || metricUnit(metric)}`
              : 'no reading'}
          </span>
        </span>
        <span>
          <span className="muted">Last observation:</span> {relativeTime(point.last_observation_date)}
        </span>
        {point.observation_count !== undefined ? (
          <span>
            <span className="muted">Stored records:</span> <span className="mono">{formatNumber(point.observation_count, 0)}</span>
          </span>
        ) : null}
        {point.source_name ? (
          <span>
            <span className="muted">Source:</span> {point.source_name}
          </span>
        ) : null}
        {point.quality_flags?.length ? (
          <Badge tone="warning" title={point.quality_flags.join(', ')}>
            {point.quality_flags.length} flag{point.quality_flags.length === 1 ? '' : 's'}
          </Badge>
        ) : null}
        <Link
          className="link"
          to={`/app/history?location=${encodeURIComponent(point.location_code)}&metric=${metric}${start ? `&start=${start}` : ''}${end ? `&end=${end}` : ''}`}
        >
          Open full history
        </Link>
      </div>

      {trendState.loading ? <ChartSkeleton height={280} /> : null}
      {trendState.error ? <ErrorState error={trendState.error} onRetry={() => void trendState.refetch()} /> : null}
      {trendState.data ? (
        <TrendOutcomeView
          outcome={trendOutcome(trendState.data, metric)}
          metric={metric}
          height={280}
          actionHref={`/app/history?location=${encodeURIComponent(point.location_code)}&metric=${metric}`}
          actionLabel="View raw observations"
        />
      ) : null}
    </Panel>
  )
}
