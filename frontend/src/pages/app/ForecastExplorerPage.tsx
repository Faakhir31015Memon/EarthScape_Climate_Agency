import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Radar, Trash2, TrendingUp } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import {
  Badge,
  Column,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Panel,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
} from '../../components/ui'
import { ForecastChart } from '../../components/charts/ForecastChart'
import { formatNumber, metricLabel, relativeTime } from '../../lib/format'
import type { AnomalyScanResponse, Metric, PredictionCreateBody, PredictionListItem, PredictionRecord } from '../../types/api'

type ScopeType = 'region' | 'location'

/** Renders a forecast's scope honestly from the region_code / location_code fields the list omits values for. */
function scopeLabel(record: { region_code?: string | null; location_code?: string | null }): string {
  if (record.location_code) return `Station ${record.location_code}`
  if (record.region_code) return `Region ${record.region_code}`
  return 'Global'
}

export default function ForecastExplorerPage() {
  const { can } = useAuth()
  const canRun = can('predictions:run')

  const regions = useAsync(() => api.climate.regions(), [])
  const activeModels = useAsync(() => api.models.list({ status: 'active', page_size: 50 }), [])
  // GET /predictions/ returns {items, note} with no pagination keys.
  const history = useAsync(() => api.predictions.list({ limit: 25 }), [])
  const catalogue = useAsync(() => api.models.catalogue(), [])

  const [scopeType, setScopeType] = useState<ScopeType>('region')
  const [scopeValue, setScopeValue] = useState('')
  const [horizon, setHorizon] = useState(14)
  const [modelId, setModelId] = useState('')
  const [generating, setGenerating] = useState(false)
  const [forecastError, setForecastError] = useState<ApiRequestError | null>(null)
  const [result, setResult] = useState<PredictionRecord | null>(null)

  const [scanResult, setScanResult] = useState<AnomalyScanResponse | null>(null)
  const [deleting, setDeleting] = useState<PredictionListItem | null>(null)

  const regionOptions = useMemo(() => (regions.data?.regions ?? []).map((region) => ({ value: region.code, label: region.name, hint: region.country ?? undefined })), [regions.data])
  const locationOptions = useMemo(() => (regions.data?.regions ?? []).flatMap((region) => region.locations.map((loc) => ({ value: loc.code, label: loc.name, hint: region.code }))), [regions.data])
  const modelOptions = useMemo(
    () => (activeModels.data?.items ?? []).map((model) => ({ value: model.id, label: `${model.name} · v${model.version}`, hint: metricLabel(model.target_variable) })),
    [activeModels.data],
  )

  const scopeInvalid = !scopeValue
  const canGenerate = canRun && Boolean(modelId) && Boolean(scopeValue) && !generating

  // The backend publishes the ceiling it will accept; a forecast beyond the model's
  // skillful lead is served by the climatology anchor, which is stated per run.
  const maxHorizon = catalogue.data?.horizon.max_forecast_horizon_days ?? 90
  const setHorizonDays = (days: number) => {
    if (!Number.isFinite(days)) return
    setHorizon(Math.min(Math.max(Math.round(days), 1), maxHorizon))
  }
  const horizonSuffix = horizon >= 365 ? ` (${(horizon / 365.25).toFixed(1)} years)` : ''

  const historyRows = history.data?.items ?? []

  const trend = useAsync(
    () => {
      if (!result) return Promise.resolve(null)
      const base: { metric: Metric; frequency: 'daily'; start?: string; end?: string } = {
        metric: result.target_variable,
        frequency: 'daily',
        start: result.forecast_origin_date,
        end: result.forecast_origin_date,
      }
      return result.location_code
        ? api.climate.trend({ ...base, location: result.location_code })
        : api.climate.trend({ ...base, region: result.region_code ?? undefined })
    },
    [result?.id],
    { enabled: Boolean(result) },
  )

  const historySeries = trend.data?.series ?? []

  async function generate() {
    if (!modelId || !scopeValue) return
    setGenerating(true)
    setForecastError(null)
    try {
      // POST /predictions/ requires a model_id; the target metric is a property of that model.
      const payload: PredictionCreateBody = {
        model_id: modelId,
        horizon_days: horizon,
        scope: scopeType === 'region' ? { region_code: scopeValue } : { location_code: scopeValue },
      }
      const record = await api.predictions.forecast(payload)
      setResult(record)
      void history.refetch()
    } catch (error) {
      setForecastError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setGenerating(false)
    }
  }

  async function runScan() {
    setForecastError(null)
    setScanResult(null)
    try {
      // POST /climate/anomalies/scan returns the scan + detection summary synchronously (no job).
      const response = await api.predictions.anomalyScan({})
      setScanResult(response)
    } catch (error) {
      setForecastError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    }
  }

  async function confirmDelete() {
    if (!deleting) return
    try {
      await api.predictions.remove(deleting.id)
      if (result?.id === deleting.id) setResult(null)
      setDeleting(null)
      void history.refetch()
    } catch (error) {
      setForecastError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
      setDeleting(null)
    }
  }

  // Reset the scope selection when the scope type switches.
  useEffect(() => {
    setScopeValue('')
  }, [scopeType])

  const noActiveModels = !activeModels.loading && modelOptions.length === 0

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Models &amp; forecasts</p>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Forecast explorer</h1>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
              Request a model projection for a region or station. A forecast is a statistical projection, never an actual outcome — the solid line is real
              observed history, the dashed line is what the model projects forward with whatever uncertainty the model actually publishes.
            </p>
          </div>
          <Link to="/app/anomalies" className="btn btn-secondary btn-sm">
            <Activity className="h-3.5 w-3.5" /> Anomaly detection
          </Link>
        </div>
      </header>

      <Panel
        title="New forecast"
        actions={
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => void runScan()}
            disabled={!canRun}
            title={!canRun ? 'Requires climate analyst or administrator role.' : 'Scan stored observations for anomalies (POST /climate/anomalies/scan)'}
          >
            <Radar className="h-4 w-4" /> Anomaly scan
          </button>
        }
      >
        {!canRun ? (
          <EmptyState
            title="Read-only access"
            description="Viewers can browse stored forecasts but cannot generate new ones. The controls below are disabled."
            tone="warning"
          />
        ) : null}

        {noActiveModels ? (
          <EmptyState
            title="No active models yet"
            description="A forecast needs an active model to project from, and none are active right now. Train a model in the ML lab, then activate it in the registry before generating a forecast."
            action={
              <div className="flex flex-wrap gap-2">
                <Link to="/app/ml-lab" className="btn btn-primary btn-sm">Train a model</Link>
                <Link to="/app/models" className="btn btn-secondary btn-sm">Model registry</Link>
              </div>
            }
          />
        ) : null}

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Field label="Model" htmlFor="fx-model" hint="A forecast is always produced by a specific active model.">
            <Select
              id="fx-model"
              options={modelOptions}
              value={modelId}
              onChange={setModelId}
              placeholder={activeModels.loading ? 'Loading models…' : 'Select a model'}
              disabled={!canRun || noActiveModels}
            />
          </Field>
          <Field label="Scope" htmlFor="fx-scope-type">
            <Select
              id="fx-scope-type"
              options={[{ value: 'region', label: 'Region' }, { value: 'location', label: 'Location / station' }]}
              value={scopeType}
              onChange={(value) => setScopeType(value as ScopeType)}
              disabled={!canRun}
            />
          </Field>
          <Field label={scopeType === 'region' ? 'Region' : 'Location'} htmlFor="fx-scope" error={scopeInvalid && generating ? 'Required' : null}>
            {scopeType === 'region' ? (
              <SingleSelectFallback options={regionOptions} value={scopeValue} onChange={setScopeValue} disabled={!canRun} placeholder="Select a region" />
            ) : (
              <SingleSelectFallback options={locationOptions} value={scopeValue} onChange={setScopeValue} disabled={!canRun} placeholder="Select a station" />
            )}
          </Field>
        </div>

        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <Field label={`Forecast horizon: ${horizon} day${horizon === 1 ? '' : 's'}${horizonSuffix}`} htmlFor="fx-horizon" hint={`This deployment accepts 1–${maxHorizon} days.`}>
            <input id="fx-horizon" type="range" min={1} max={maxHorizon} value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} disabled={!canRun} className="w-full accent-[var(--brand)]" />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input id="fx-horizon-days" type="number" min={1} max={maxHorizon} value={horizon} onChange={(event) => setHorizonDays(Number(event.target.value))} disabled={!canRun} className="input w-28" aria-label="Forecast horizon in days" />
              {[365, 730, 1095].filter((days) => days <= maxHorizon).map((days) => (
                <button key={days} type="button" className="btn btn-ghost btn-sm" onClick={() => setHorizonDays(days)} disabled={!canRun} title={`${days / 365} year ahead`}>{days / 365}y</button>
              ))}
            </div>
          </Field>
          <div className="flex items-end">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void generate()}
              disabled={!canGenerate}
              title={!canRun ? 'Read-only access.' : !modelId ? 'Select an active model first.' : !scopeValue ? 'Choose a region or station.' : undefined}
            >
              <TrendingUp className="h-4 w-4" /> {generating ? 'Projecting…' : 'Generate forecast'}
            </button>
          </div>
        </div>
      </Panel>

      {forecastError ? <ErrorState error={forecastError} /> : null}

      {scanResult ? (
        <Panel title="Anomaly scan" description="Live result of POST /climate/anomalies/scan." dense>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={scanResult.scan.dry_run ? 'info' : 'success'}>{scanResult.scan.dry_run ? 'dry run' : 'alerts persisted'}</Badge>
            <span>Anomalies detected: <span className="mono">{formatNumber(scanResult.scan.anomalies_detected, 0)}</span></span>
            <span>·</span>
            <span>Alerts persisted: <span className="mono">{formatNumber(scanResult.scan.alerts_persisted, 0)}</span></span>
            {scanResult.sample_anomalies.length ? (
              <>
                <span>·</span>
                <Link to="/app/anomalies" className="link">View anomalies</Link>
              </>
            ) : null}
          </div>
        </Panel>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <Panel
          title={result ? 'Forecast projection' : 'Forecast'}
          description={result ? `${result.model_name} · v${result.model_version} · ${scopeLabel(result)}` : 'Generate or select a stored forecast to plot it.'}
          actions={result ? <ForecastBadges record={result} /> : null}
        >
          {result ? (
            <div className="space-y-4">
              {trend.loading && !trend.data ? <Skeleton className="h-64 w-full" /> : null}
              <ForecastChart history={historySeries} forecast={result.values} unit={result.units} modelLabel={`${result.model_name} v${result.model_version}`} />
              <UncertaintyAndCaveats record={result} />
            </div>
          ) : (
            <EmptyState title="No forecast selected" description="Generate one above, or pick a stored forecast from the history table." />
          )}
        </Panel>

        <div className="space-y-6">
          {result ? <ForecastStats record={result} /> : null}
          <Panel title="Freshness &amp; provenance" dense>
            {result ? (
              <dl className="space-y-1.5 text-xs">
                <Row label="Data freshness">
                  <span className={dataFreshnessTone(result.data_freshness_days)}>
                    {result.data_freshness_days == null ? 'not reported' : `${formatNumber(result.data_freshness_days, 0)} day(s) stale at request time`}
                  </span>
                </Row>
                <Row label="Forecast origin">{result.forecast_origin_date}</Row>
                <Row label="Generated">{relativeTime(result.generated_at)}</Row>
                <Row label="Method">{result.method ?? '—'}</Row>
                <Row label="Requested by">{result.provenance?.requested_by_name ?? result.provenance?.requested_by ?? '—'}</Row>
                <Row label="Observations used">{result.provenance?.actual_observations_used != null ? formatNumber(result.provenance.actual_observations_used, 0) : '—'}</Row>
              </dl>
            ) : (
              <p className="text-sm muted">Select a forecast to see how stale its input data was.</p>
            )}
          </Panel>
        </div>
      </div>

      <Panel
        title="Stored forecasts"
        description="Previous projections for comparison. The list omits the full value series — open a row to load and plot it."
        actions={<span className="text-xs subtle">{historyRows.length} forecast{historyRows.length === 1 ? '' : 's'}</span>}
      >
        {history.loading ? <Skeleton className="h-40 w-full" /> : null}
        {history.error ? <ErrorState error={history.error} onRetry={() => void history.refetch()} /> : null}
        {history.data?.note ? <p className="mb-2 text-xs subtle">{history.data.note}</p> : null}
        {!history.loading && historyRows.length === 0 ? (
          <EmptyState title="No forecasts stored yet" description="No projection has been generated. Pick an active model above and generate a forecast — it will be persisted here." />
        ) : null}
        {historyRows.length ? (
          <DataTable
            columns={historyColumns((record) => void openStored(record), setDeleting, canRun)}
            rows={historyRows}
            rowKey={(record) => record.id}
            empty="No records"
          />
        ) : null}
      </Panel>

      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete forecast"
        tone="danger"
        confirmLabel="Delete"
        message={<>Delete the stored projection for <strong>{deleting ? scopeLabel(deleting) : ''}</strong> ({deleting ? `${deleting.model_name} v${deleting.model_version}` : ''})? This removes the record from the forecast history; the model and its observations are untouched.</>}
        onClose={() => setDeleting(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  )

  async function openStored(record: PredictionListItem) {
    try {
      const full = await api.predictions.get(record.id)
      setResult(full)
    } catch (error) {
      setForecastError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    }
  }
}

function SingleSelectFallback({
  options,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  options: { value: string; label: string; hint?: string }[]
  value: string
  onChange: (next: string) => void
  placeholder: string
  disabled?: boolean
}) {
  // A searchable picker would help for large station lists, but MultiSelect is
  // inherently plural; single-value selection uses the native Select for clarity.
  const [filter, setFilter] = useState('')
  const filtered = useMemo(() => {
    const needle = filter.toLowerCase()
    return needle ? options.filter((option) => option.label.toLowerCase().includes(needle) || option.value.toLowerCase().includes(needle)) : options
  }, [options, filter])

  if (options.length > 25) {
    return (
      <div className="space-y-1.5">
        <input className="input mb-1.5" placeholder="Filter options…" value={filter} onChange={(event) => setFilter(event.target.value)} aria-label={placeholder} disabled={disabled} />
        <Select options={filtered.map((option) => ({ value: option.value, label: option.hint ? `${option.label} (${option.hint})` : option.label }))} value={value} onChange={onChange} placeholder={placeholder} disabled={disabled} />
      </div>
    )
  }
  return (
    <Select
      options={options.map((option) => ({ value: option.value, label: option.hint ? `${option.label} (${option.hint})` : option.label }))}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      disabled={disabled}
    />
  )
}

function ForecastBadges({ record }: { record: PredictionRecord }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      {record.status ? <StatusBadge value={record.status} /> : null}
      <Badge tone="neutral">{record.horizon_days}-day horizon</Badge>
      {record.data_freshness_days != null ? <Badge tone={dataFreshnessTone(record.data_freshness_days) === 'text-alert-high' ? 'warning' : 'info'}>data {record.data_freshness_days}d stale</Badge> : null}
    </div>
  )
}

function ForecastStats({ record }: { record: PredictionRecord }) {
  const numeric = record.values.map((value) => value.value).filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const first = numeric.length ? numeric[0] : null
  const last = numeric.length ? numeric[numeric.length - 1] : null
  const peak = numeric.length ? Math.max(...numeric) : null
  const trough = numeric.length ? Math.min(...numeric) : null
  return (
    <div className="grid-responsive">
      <StatCard label="First projected" value={<StatValue value={first} unit={record.units} />} hint={`Day +1 · ${metricLabel(record.target_variable)}`} tone="hydro" />
      <StatCard label="Final projected" value={<StatValue value={last} unit={record.units} />} hint={`Day +${record.horizon_days}`} tone="forest" />
      <StatCard label="Range" value={<span className="text-[18px]"><StatValue value={trough} unit="" /> – <StatValue value={peak} unit={record.units} /></span>} tone="solar" hint="Projected min–max over the horizon" />
    </div>
  )
}

function StatValue({ value, unit }: { value: number | null; unit: string }) {
  if (value === null) return <span className="subtle">—</span>
  return <span>{formatNumber(value, 1)}{unit ? <span className="ml-1 text-sm muted">{unit}</span> : null}</span>
}

function UncertaintyAndCaveats({ record }: { record: PredictionRecord }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
        <p className="panel-title mb-1">Uncertainty</p>
        {record.uncertainty.available ? (
          <div className="space-y-1">
            <p className="muted">
              Method: <span className="mono">{record.uncertainty.method ?? 'not reported'}</span>
              {record.uncertainty.level != null ? <> · Level: <span className="mono">{record.uncertainty.level}</span></> : null}
            </p>
            <p className="text-sm">{record.uncertainty.note ?? 'Prediction intervals are shown where the model published them.'}</p>
            {record.values.some((row) => row.lower_bound == null || row.upper_bound == null) ? (
              <p className="subtle">Note: some steps have no published bounds, so the band is only drawn where bounds exist.</p>
            ) : null}
          </div>
        ) : (
          <p className="muted">{record.uncertainty.note ?? 'This model does not publish prediction intervals — the band is intentionally absent rather than estimated.'}</p>
        )}
      </div>
      <div>
        <p className="panel-title mb-1">Caveats</p>
        {record.caveats?.length ? (
          <ul className="list-disc space-y-1 pl-5 text-xs muted">
            {record.caveats.map((caveat, index) => (
              <li key={index}>{caveat}</li>
            ))}
          </ul>
        ) : (
          <p className="text-xs subtle">No caveats returned for this forecast.</p>
        )}
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b pb-1" style={{ borderColor: 'var(--border)' }}>
      <dt className="subtle">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  )
}

function dataFreshnessTone(days?: number | null): string {
  if (days == null) return 'subtle'
  if (days <= 2) return 'text-forest-600 dark:text-forest-300'
  if (days <= 7) return 'muted'
  return 'text-alert-high'
}

function historyColumns(
  onOpen: (record: PredictionListItem) => void,
  onDelete: (record: PredictionListItem) => void,
  canRun: boolean,
): Column<PredictionListItem>[] {
  return [
    {
      key: 'target',
      header: 'Forecast',
      cell: (record) => (
        <button type="button" className="link text-left" onClick={() => onOpen(record)}>
          <span className="block max-w-[16rem] truncate font-medium">{metricLabel(record.target_variable)}</span>
          <span className="text-xs subtle">{scopeLabel(record)}</span>
        </button>
      ),
    },
    { key: 'model', header: 'Model', cell: (record) => <span className="text-xs">{record.model_name} · v{record.model_version}</span> },
    { key: 'horizon', header: 'Horizon', align: 'right', cell: (record) => <span className="mono text-xs">{record.horizon_days}d</span> },
    { key: 'generated', header: 'Generated', cell: (record) => <span className="text-xs" title={record.generated_at}>{relativeTime(record.generated_at)}</span> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (record) =>
        canRun ? (
          <button type="button" className="btn btn-ghost btn-icon" onClick={(event) => { event.stopPropagation(); onDelete(record) }} aria-label="Delete forecast" title="Delete forecast">
            <Trash2 className="h-4 w-4 text-alert-high" />
          </button>
        ) : (
          <span className="text-xs subtle" title="Deletion requires climate analyst or administrator role.">—</span>
        ),
    },
  ]
}
