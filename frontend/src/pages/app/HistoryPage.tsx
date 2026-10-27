/**
 * History — the audited record view: raw observations, the daily series, a
 * location comparison, and base-period deviations. The base period the deltas
 * are measured against is always shown explicitly, because "deviation" is
 * meaningless without stating what "normal" was.
 */
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../services/api'
import type {
  HistoryGroup,
  HistoryYearEntry,
  Metric,
  Observation,
  ObservationsListResponse,
  Region,
  SeriesPoint,
} from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import type { AsyncState } from '../../hooks/useAsync'
import { ALL_METRICS, formatMetric, formatNumber, metricLabel, metricUnit } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  KeyValue,
  Modal,
  Pagination,
  Panel,
  RefreshButton,
  Select,
  TableSkeleton,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { TrendChart } from '../../components/charts/TrendChart'
import { ChartSkeleton } from '../../components/ui/Skeleton'
import { DateRangeFields, TrendOutcomeView } from './components/data/filters'
import { asList, numberOrNull, parseCsv, trendOutcome } from './components/data/normalise'

const PAGE_SIZE = 25

const TABS = [
  { id: 'raw', label: 'Raw observations' },
  { id: 'daily', label: 'Daily series' },
  { id: 'locations', label: 'Location comparison' },
  { id: 'deviations', label: 'Base-period deviations' },
] as const

type TabId = (typeof TABS)[number]['id']

export default function HistoryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [tab, setTab] = useState<TabId>('raw')

  const metric = (searchParams.get('metric') ?? 'temperature_c') as Metric
  const start = searchParams.get('start') ?? undefined
  const end = searchParams.get('end') ?? undefined
  const region = searchParams.get('region') ?? undefined
  const location = searchParams.get('location') ?? undefined
  const page = Number(searchParams.get('page') ?? '1') || 1

  const update = (changes: Record<string, string | undefined>, resetPage = false) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(changes).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, value)
    })
    if (resetPage) next.delete('page')
    setSearchParams(next, { replace: true })
  }

  const regionsState = useAsync(() => api.climate.regions(), [])
  const regionsData = regionsState.data

  const regionOptions = useMemo(
    () =>
      asList<Region>(regionsData, 'regions')
        .flatMap((item) => item.code ? [{ value: item.code, label: item.name ? `${item.name} (${item.code})` : item.code }] : []),
    [regionsData],
  )

  const locationOptions = useMemo(
    () =>
      asList<Region>(regionsData, 'regions').flatMap((item) =>
        (item.locations ?? []).map((station) => ({
          value: station.code,
          label: station.name ? `${station.name} (${station.code})` : station.code,
        })),
      ),
    [regionsData],
  )

  const observations = useAsync(
    () =>
      api.climate.observations({
        page,
        page_size: PAGE_SIZE,
        start,
        end,
        region,
        // `/climate/observations` filters on `metric` (singular) and
        // `location_codes`; the declared `metrics` key is not read by the view.
        metric,
        location_codes: location,
      }),
    [page, metric, start, end, region, location],
  )

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Monitoring"
        title="Observation history"
        description="Paginated raw records, the daily series, and base-period deviations. Every value comes from a stored observation."
        actions={<RefreshButton onClick={() => void observations.refetch()} busy={observations.refreshing} />}
      />

      <Panel dense bodyClassName="p-0">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <Field label="Metric" htmlFor="history-metric" className="w-52">
            <Select
              id="history-metric"
              value={metric}
              onChange={(value) => update({ metric: value })}
              options={ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) }))}
            />
          </Field>
          <Field label="Region" htmlFor="history-region" className="w-44">
            <Select
              id="history-region"
              value={region ?? ''}
              onChange={(value) => update({ region: value || undefined })}
              options={regionOptions}
              placeholder="All regions"
            />
          </Field>
          <Field label="Location" htmlFor="history-location" className="w-44">
            <Select
              id="history-location"
              value={location ?? ''}
              onChange={(value) => update({ location: value || undefined })}
              options={locationOptions}
              placeholder="All locations"
            />
          </Field>
          <DateRangeFields start={start ?? ''} end={end ?? ''} onChange={(range) => update(range)} idPrefix="history" />
        </div>
      </Panel>

      <div className="flex flex-wrap gap-1 border-b" style={{ borderColor: 'var(--border)' }}>
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => setTab(item.id)}
            aria-pressed={tab === item.id}
            className={
              'border-b-2 px-3 py-2 text-sm font-medium transition ' +
              (tab === item.id
                ? 'border-[var(--brand)] text-[var(--text)]'
                : 'border-transparent muted hover:text-[var(--text)]')
            }
          >
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'raw' ? (
        <RawObservations metric={metric} observations={observations} onPage={(next) => update({ page: String(next) })} />
      ) : null}
      {tab === 'daily' ? <DailySeries metric={metric} start={start} end={end} region={region} location={location} /> : null}
      {tab === 'locations' ? <LocationComparison metric={metric} start={start} end={end} region={region} /> : null}
      {tab === 'deviations' ? (
        <Deviations metric={metric} start={start} end={end} region={region} locationCodes={location ? parseCsv(location) : []} />
      ) : null}
    </div>
  )
}

/* ------------------------------------------------------------------ sections */

type ObservationsState = AsyncState<ObservationsListResponse>

function RawObservations({
  metric,
  observations,
  onPage,
}: {
  metric: Metric
  observations: ObservationsState
  onPage: (page: number) => void
}) {
  const [selected, setSelected] = useState<Observation | null>(null)

  const columns = useMemo<Column<Observation>[]>(
    () => [
      {
        key: 'observation_date',
        header: 'Date',
        sortKey: 'observation_date',
        cell: (row) => <span className="mono text-xs">{row.observation_date}</span>,
      },
      {
        key: 'location',
        header: 'Location',
        cell: (row) => (
          <div className="min-w-0">
            <span className="block truncate">{row.location_name ?? row.location_code}</span>
            <span className="mono block text-xs subtle">{row.location_code}</span>
          </div>
        ),
      },
      { key: 'region_code', header: 'Region', cell: (row) => <span className="mono text-xs">{row.region_code}</span> },
      {
        key: 'value',
        header: metricLabel(metric),
        align: 'right',
        cell: (row) => {
          const value = numberOrNull((row as unknown as Record<string, unknown>)[metric])
          return <span>{value === null ? '—' : formatMetric(value, metric)}</span>
        },
      },
      {
        key: 'source',
        header: 'Source',
        cell: (row) => (
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-xs muted">{row.source_name ?? row.source_type ?? '—'}</span>
            {(row.quality_flags ?? []).length ? (
              <Badge tone="warning" title={row.quality_flags?.join(', ')}>
                {row.quality_flags?.length} flag{row.quality_flags?.length === 1 ? '' : 's'}
              </Badge>
            ) : (
              <Badge tone="success">clean</Badge>
            )}
          </div>
        ),
      },
    ],
    [metric],
  )

  return (
    <>
      <Panel title="Raw observations" description={`Stored records matching the current selection (${metricLabel(metric)}).`}>
        <DataTable
          columns={columns}
          rows={observations.data?.items ?? []}
          rowKey={(row, index) => row.id ?? `${row.observation_date}-${row.location_code}-${index}`}
          loading={observations.loading}
          onRowClick={(row) => setSelected(row)}
          compact
          empty={
            observations.error ? (
              <ErrorState error={observations.error} onRetry={() => void observations.refetch()} />
            ) : (
              <EmptyState
                title="No observations for this selection"
                description="The stored record set has no rows that match the metric, region, location and date range. Widen the range or clear a filter."
                action={
                  <Link to="/app/ingestion" className="btn btn-secondary btn-sm">
                    Ingest data
                  </Link>
                }
              />
            )
          }
        />
        {observations.data ? (
          <Pagination
            meta={observations.data}
            onPage={onPage}
            disabled={observations.refreshing}
            label={`${observations.data.total.toLocaleString()} records`}
          />
        ) : null}
      </Panel>

      <Modal
        open={Boolean(selected)}
        title="Observation detail"
        description={selected ? `${selected.location_name ?? selected.location_code} · ${selected.observation_date}` : undefined}
        onClose={() => setSelected(null)}
        width="max-w-2xl"
      >
        {selected ? <ObservationDetail row={selected} /> : null}
      </Modal>
    </>
  )
}

function ObservationDetail({ row }: { row: Observation }) {
  const populated = ALL_METRICS.filter((key) => typeof (row as unknown as Record<string, unknown>)[key] === 'number')

  return (
    <div className="space-y-4">
      <KeyValue
        columns={2}
        items={[
          { label: 'Observation date', value: <span className="mono">{row.observation_date}</span> },
          { label: 'Location', value: `${row.location_name ?? row.location_code} (${row.location_code})` },
          { label: 'Region', value: row.region_code },
          { label: 'Country', value: row.country ?? '—' },
          { label: 'Latitude', value: formatNumber(row.latitude, 4) },
          { label: 'Longitude', value: formatNumber(row.longitude, 4) },
        ]}
      />

      <Panel title="Populated readings" dense>
        {populated.length ? (
          <ul className="grid gap-1 text-sm sm:grid-cols-2">
            {populated.map((key) => (
              <li
                key={key}
                className="flex items-center justify-between gap-3 rounded-lg px-2 py-1"
                style={{ backgroundColor: 'var(--surface-muted)' }}
              >
                <span className="muted">{metricLabel(key)}</span>
                <span className="mono">{formatMetric(numberOrNull((row as unknown as Record<string, unknown>)[key]), key)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs subtle">No metric readings were stored on this row.</p>
        )}
      </Panel>

      <Panel title="Provenance" dense>
        <div className="space-y-2 text-xs">
          <p>
            <span className="muted">Source:</span> {row.source_name ?? '—'}{' '}
            {row.source_type ? <Badge tone="neutral">{row.source_type}</Badge> : null}
          </p>
          <div className="flex flex-wrap items-center gap-1">
            <span className="muted">Quality flags:</span>
            {row.quality_flags?.length ? (
              row.quality_flags.map((flag) => (
                <Badge key={flag} tone="warning">
                  {flag}
                </Badge>
              ))
            ) : (
              <Badge tone="success">none</Badge>
            )}
          </div>
          {row.dataset_id ? (
            <p>
              <span className="muted">Dataset:</span>{' '}
              <Link className="link" to={`/app/datasets/${row.dataset_id}`}>
                {row.dataset_id}
              </Link>
            </p>
          ) : null}
          {row.ingested_at ? (
            <p>
              <span className="muted">Ingested:</span> <span className="mono">{row.ingested_at}</span>
            </p>
          ) : null}
          {row.provenance ? (
            <pre
              className="mono mt-1 max-h-48 overflow-auto rounded-lg p-2 text-[11px]"
              style={{ backgroundColor: 'var(--surface-muted)' }}
            >
              {JSON.stringify(row.provenance, null, 2)}
            </pre>
          ) : null}
        </div>
      </Panel>
    </div>
  )
}

/* --------------------------------------------------------------- daily tab */

function DailySeries({
  metric,
  start,
  end,
  region,
  location,
}: {
  metric: Metric
  start?: string
  end?: string
  region?: string
  location?: string
}) {
  const state = useAsync(
    () =>
      api.climate.trend({
        metric,
        frequency: 'daily',
        start,
        end,
        region,
        location_codes: location,
        require_baseline: false,
      }),
    [metric, start, end, region, location],
  )

  return (
    <Panel
      title={`Daily ${metricLabel(metric).toLowerCase()}`}
      description="One point per day, averaged from the stored observations."
    >
      {state.loading ? <ChartSkeleton height={320} /> : null}
      {state.error ? <ErrorState error={state.error} onRetry={() => void state.refetch()} /> : null}
      {state.data ? <TrendOutcomeView outcome={trendOutcome(state.data, metric)} metric={metric} height={340} /> : null}
    </Panel>
  )
}

/* ---------------------------------------------------------- location compare */

function LocationComparison({
  metric,
  start,
  end,
  region,
}: {
  metric: Metric
  start?: string
  end?: string
  region?: string
}) {
  const state = useAsync(
    () => api.climate.trend({ metric, group_by: 'location', start, end, region, frequency: 'monthly' }),
    [metric, start, end, region],
  )
  const outcome = state.data ? trendOutcome(state.data, metric) : null

  return (
    <Panel
      title="Location comparison"
      description={`Monthly ${metricLabel(metric).toLowerCase()} grouped by station over the same period.`}
    >
      {state.loading ? <ChartSkeleton height={320} /> : null}
      {state.error ? <ErrorState error={state.error} onRetry={() => void state.refetch()} /> : null}
      {outcome ? (
        <TrendOutcomeView
          outcome={outcome}
          metric={metric}
          groupBy="location"
          height={360}
          actionHref="/app/map"
          actionLabel="Open the map"
        />
      ) : null}
      {outcome?.kind === 'ready' ? (
        <div className="mt-4">
          <DataTable
            columns={[
              {
                key: 'period',
                header: 'Period',
                cell: (row: SeriesPoint) => <span className="mono text-xs">{row.period}</span>,
              },
              {
                key: 'location',
                header: 'Location',
                cell: (row: SeriesPoint) => (
                  <Link className="link mono text-xs" to={`/app/history?location=${row.location_code ?? ''}&metric=${metric}`}>
                    {row.location_code ?? '—'}
                  </Link>
                ),
              },
              {
                key: 'value',
                header: metricLabel(metric),
                align: 'right',
                cell: (row: SeriesPoint) => (
                  <span>{row.value === null ? '—' : `${formatNumber(row.value)} ${outcome.unit}`}</span>
                ),
              },
              {
                key: 'count',
                header: 'Obs',
                align: 'right',
                cell: (row: SeriesPoint) => <span className="mono">{formatNumber(row.observation_count, 0)}</span>,
              },
            ]}
            rows={outcome.series.slice(0, 100)}
            rowKey={(row, index) => `${row.period}-${row.location_code}-${index}`}
            compact
            empty={<EmptyState title="No grouped periods" description="Grouping by location produced no rows for this range." />}
          />
        </div>
      ) : null}
    </Panel>
  )
}

/* ---------------------------------------------------------- deviations tab */

function Deviations({
  metric,
  start,
  end,
  region,
  locationCodes,
}: {
  metric: Metric
  start?: string
  end?: string
  region?: string
  locationCodes: string[]
}) {
  const state = useAsync(
    () =>
      api.climate.history({
        metric,
        start,
        end,
        region,
        // `/climate/history` reads `location_codes` (comma-separated) and `group_by`.
        location_codes: locationCodes.length ? locationCodes.join(',') : undefined,
        group_by: locationCodes.length ? 'location' : 'region',
      }),
    [metric, start, end, region, locationCodes.join(',')],
  )
  const history = state.data
  const groups = history ? asList<HistoryGroup>(history.groups, 'groups') : []
  const referencePeriod = history?.reference_period
  const baseLabel =
    referencePeriod && (referencePeriod.start || referencePeriod.end)
      ? `${referencePeriod.start ?? '?'}–${referencePeriod.end ?? '?'}`
      : null
  const unit = history?.unit ?? metricUnit(metric)

  return (
    <Panel
      title="Base-period deviations"
      description="Deltas measured against a fixed climatological baseline."
      footer={
        <div className="space-y-1 text-xs">
          <p>
            <span className="muted">Reference period:</span>{' '}
            {baseLabel ? (
              <span className="mono font-semibold">{baseLabel}</span>
            ) : (
              <span className="subtle">no reference period is available, so deviations would be uninterpretable.</span>
            )}
          </p>
          {history?.method ? <p className="subtle">{history.method}.</p> : null}
          {history?.caveat ? <p className="subtle">{history.caveat}</p> : null}
        </div>
      }
    >
      {state.loading ? <TableSkeleton rows={5} columns={4} /> : null}
      {state.error ? <ErrorState error={state.error} onRetry={() => void state.refetch()} /> : null}

      {!state.loading && !state.error && history && groups.length === 0 ? (
        <EmptyState
          title="No deviation periods available"
          description={
            history.reason ??
            history.caveat ??
            'There are not enough stored observations in the selected window to compute a reference normal.'
          }
          tone="warning"
          action={
            <Link to="/app/ingestion" className="btn btn-primary btn-sm">
              Ingest more history
            </Link>
          }
        />
      ) : null}

      {groups.map((group) => (
        <GroupDeviations
          key={`${group.group_by}:${group.group}`}
          group={group}
          metric={metric}
          unit={unit}
          fallbackBaseLabel={baseLabel}
        />
      ))}

      {!state.loading && !state.error && !history ? (
        <EmptyState
          title="No historical baseline is available"
          description="No reference period came back for this selection. Try again, or ingest data first."
          tone="warning"
          action={
            <Link to="/app/ingestion" className="btn btn-secondary btn-sm">
              Go to ingestion
            </Link>
          }
        />
      ) : null}
    </Panel>
  )
}

/** One `HistoryGroup`: the reference normal it rests on, then per-year deviations. */
function GroupDeviations({
  group,
  metric,
  unit,
  fallbackBaseLabel,
}: {
  group: HistoryGroup
  metric: Metric
  unit: string
  fallbackBaseLabel: string | null
}) {
  const years = asList<HistoryYearEntry>(group.years, 'years')
  const reference = group.reference
  const normal = numberOrNull(reference?.normal)
  const referenceYears = numberOrNull(reference?.years_in_reference_period)
  const delta = numberOrNull(group.change?.delta)
  const relativePct = numberOrNull(group.change?.relative_pct)

  const period = reference?.reference_period
  const baseLabel =
    period && (period.start || period.end)
      ? `${period.start ?? '?'}–${period.end ?? '?'}`
      : fallbackBaseLabel ?? 'reference period'

  const deviationSeries: SeriesPoint[] = years
    .filter((entry) => numberOrNull(entry.anomaly_vs_reference) !== null)
    .map((entry) => ({
      period: String(entry.year),
      value: numberOrNull(entry.anomaly_vs_reference),
      observation_count: numberOrNull(entry.observations) ?? 0,
    }))

  const referenceNote = reference?.note ?? reference?.reason ?? null

  return (
    <div className="mb-6 last:mb-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="panel-title">
          {group.group} <span className="mono text-xs font-normal muted">{group.group_by}</span>
        </h3>
        <div className="flex flex-wrap items-center gap-3 text-xs muted">
          <span>
            Normal vs {baseLabel}:{' '}
            <span className="mono">{normal === null ? '—' : `${formatNumber(normal)} ${unit}`}</span>
          </span>
          <span>
            Reference years: <span className="mono">{referenceYears === null ? '—' : formatNumber(referenceYears, 0)}</span>
          </span>
          {reference?.stable ? (
            <Badge tone="success">stable normal</Badge>
          ) : (
            <Badge tone="warning">provisional</Badge>
          )}
        </div>
      </div>

      {referenceNote ? <p className="mb-2 text-xs subtle">{referenceNote}</p> : null}

      {delta === null ? (
        <p className="mb-3 text-xs subtle">
          No change delta is computable for this scope: {reference?.reason ?? 'no reference normal was computed.'}
        </p>
      ) : (
        <p className="mb-3 text-xs">
          <span className="muted">Change:</span>{' '}
          <span className="mono">
            {delta > 0 ? '+' : ''}
            {formatNumber(delta)} {unit}
          </span>{' '}
          <span className="subtle">
            — latest stored year
            {group.change?.to_year ? ` (${group.change.to_year})` : ''} against the {baseLabel} normal
            {relativePct !== null ? ` (${relativePct > 0 ? '+' : ''}${formatNumber(relativePct, 1)}%)` : ''}.
          </span>
        </p>
      )}

      <div className="mb-3 flex flex-wrap gap-4 text-xs muted">
        <span>
          Years covered: <span className="mono">{formatNumber(group.years_covered, 0)}</span>
          {group.first_year || group.last_year ? ` (${group.first_year}–${group.last_year})` : ''}
        </span>
        {group.record_high ? (
          <span>
            Record high: <span className="mono">{group.record_high.year}</span> —{' '}
            <span className="mono">{formatMetric(group.record_high.value, metric)}</span>
          </span>
        ) : null}
        {group.record_low ? (
          <span>
            Record low: <span className="mono">{group.record_low.year}</span> —{' '}
            <span className="mono">{formatMetric(group.record_low.value, metric)}</span>
          </span>
        ) : null}
      </div>

      {deviationSeries.length ? (
        <TrendChart
          series={deviationSeries}
          metric={metric}
          unit={`Δ ${unit}`}
          label={`Deviation vs ${baseLabel}`}
          height={220}
        />
      ) : (
        <p className="text-xs subtle">
          {reference?.reason ??
            `No stored year in this scope falls inside ${baseLabel}, so no deviation can be plotted.`}
        </p>
      )}

      <div className="mt-3">
        <DataTable
          columns={[
            {
              key: 'year',
              header: 'Year',
              cell: (row: HistoryYearEntry) => <span className="mono text-xs">{row.year}</span>,
            },
            {
              key: 'value',
              header: 'Value',
              align: 'right',
              cell: (row: HistoryYearEntry) => (
                <span>{numberOrNull(row.value) === null ? '—' : `${formatNumber(row.value)} ${unit}`}</span>
              ),
            },
            {
              key: 'anomaly',
              header: `Deviation vs ${baseLabel}`,
              align: 'right',
              cell: (row: HistoryYearEntry) => {
                const deviation = numberOrNull(row.anomaly_vs_reference)
                if (deviation === null) return <span>—</span>
                const tone = deviation > 0 ? 'danger' : deviation < 0 ? 'info' : 'neutral'
                return (
                  <span className="inline-flex items-center gap-1">
                    <Badge tone={tone}>{deviation > 0 ? 'warm' : deviation < 0 ? 'cool' : 'normal'}</Badge>
                    <span className="mono">
                      {deviation > 0 ? '+' : ''}
                      {formatNumber(deviation)} {unit}
                    </span>
                  </span>
                )
              },
            },
            {
              key: 'count',
              header: 'Obs',
              align: 'right',
              cell: (row: HistoryYearEntry) => (
                <span className="mono">{formatNumber(numberOrNull(row.observations) ?? 0, 0)}</span>
              ),
            },
            {
              key: 'range',
              header: 'Min / max',
              align: 'right',
              cell: (row: HistoryYearEntry) => (
                <span className="mono">
                  {numberOrNull(row.min) === null || numberOrNull(row.max) === null
                    ? '—'
                    : `${formatNumber(row.min)} / ${formatNumber(row.max)}`}
                </span>
              ),
            },
          ]}
          rows={years.slice(0, 60)}
          rowKey={(row, index) => `${group.group}-${row.year}-${index}`}
          compact
          empty={
            <EmptyState
              title="No annual values"
              description="No stored year in this scope carries a value for this metric, so nothing can be compared against the normal."
            />
          }
        />
      </div>
    </div>
  )
}
