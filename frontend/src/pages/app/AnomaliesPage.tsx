/**
 * Anomalies — detection across methods with an explanation surface. Each
 * anomaly is shown with the detector's own explanation and the method caveat,
 * verbatim; the σ threshold and baseline are analyst inputs from the request
 * form. The list contains only detections returned by the backend.
 */
import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { AlertTriangle, ExternalLink } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import { useAsync } from '../../hooks/useAsync'
import type { Anomaly, Metric, Region, Severity } from '../../types/api'
import { ALL_METRICS, formatNumber, metricLabel, severityTone } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  KeyValue,
  Modal,
  Panel,
  RefreshButton,
  Select,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { ChartSkeleton } from '../../components/ui/Skeleton'
import { SeverityBar } from '../../components/charts/MiniCharts'
import { DateRangeFields } from './components/data/filters'
import { asList, numberOrNull, parseCsv } from './components/data/normalise'

type Method = 'statistical_baseline' | 'threshold' | 'isolation_forest'

const METHOD_OPTIONS = [
  { value: 'statistical_baseline', label: 'Statistical baseline (σ from a reference period)' },
  { value: 'threshold', label: 'Fixed threshold' },
  { value: 'isolation_forest', label: 'Isolation forest' },
]

const DIRECTION_OPTIONS = [
  { value: 'both', label: 'Above and below' },
  { value: 'above', label: 'Above only' },
  { value: 'below', label: 'Below only' },
]

const SEVERITY_OPTIONS: { value: Severity; label: string }[] = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'critical', label: 'Critical' },
]

export default function AnomaliesPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { can, hasRole } = useAuth()

  const metric = (searchParams.get('metric') ?? 'temperature_c') as Metric
  const method = (searchParams.get('method') ?? 'statistical_baseline') as Method
  const sigma = Number(searchParams.get('sigma') ?? '2.5') || 2.5
  const direction = searchParams.get('direction') ?? 'both'
  const region = searchParams.get('region') ?? undefined
  const location = searchParams.get('location') ?? undefined
  const start = searchParams.get('start') ?? undefined
  const end = searchParams.get('end') ?? undefined
  const threshold = searchParams.get('threshold') ?? undefined

  const [ruleFor, setRuleFor] = useState<Anomaly | null>(null)

  const update = (changes: Record<string, string | undefined>) => {
    const next = new URLSearchParams(searchParams)
    Object.entries(changes).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, value)
    })
    setSearchParams(next, { replace: true })
  }

  const regionsState = useAsync(() => api.climate.regions(), [])

  const state = useAsync(
    () =>
      api.climate.anomalies({
        metric,
        method,
        threshold_sigma: method === 'statistical_baseline' ? sigma : undefined,
        absolute_threshold: method === 'threshold' && threshold ? Number(threshold) : undefined,
        direction: direction as 'both' | 'above' | 'below',
        region,
        location,
        location_codes: location,
        start,
        end,
        limit: 200,
      }),
    [metric, method, sigma, direction, region, location, start, end, threshold],
  )

  const anomalies = useMemo(() => asList<Anomaly>(state.data?.anomalies, 'anomalies'), [state.data])

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Monitoring"
        title="Anomaly detection"
        description="Deviation detection over stored observations. Every flagged record carries its own explanation and the method's caveat."
        actions={
          <div className="flex items-center gap-2">
            <ExportButton
              enabled={can('reports:manage')}
              busy={false}
              onExport={() =>
                api.reports.create({
                  report_type: 'anomaly',
                  filters: { metric, method, threshold_sigma: sigma, direction, region, location, start, end },
                })
              }
            />
            <RefreshButton onClick={() => void state.refetch()} busy={state.refreshing} />
          </div>
        }
      />

      <Panel dense bodyClassName="p-0">
        <div className="flex flex-wrap items-end gap-4 p-4">
          <Field label="Metric" htmlFor="anom-metric" className="w-52">
            <Select
              id="anom-metric"
              value={metric}
              onChange={(value) => update({ metric: value })}
              options={ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) }))}
            />
          </Field>
          <Field label="Method" htmlFor="anom-method" className="w-64">
            <Select
              id="anom-method"
              value={method}
              onChange={(value) => update({ method: value })}
              options={METHOD_OPTIONS}
            />
          </Field>

          {method === 'statistical_baseline' ? (
            <Field label={`Deviation threshold: ${sigma.toFixed(1)}σ`} htmlFor="anom-sigma" className="w-56">
              <input
                id="anom-sigma"
                type="range"
                min={1}
                max={6}
                step={0.5}
                value={sigma}
                onChange={(event) => update({ sigma: event.target.value })}
                className="w-full accent-[var(--brand)]"
              />
            </Field>
          ) : null}
          {method === 'threshold' ? (
            <Field label="Absolute threshold" htmlFor="anom-threshold" className="w-44" hint="Plain value in metric units.">
              <input
                id="anom-threshold"
                type="number"
                step="any"
                className="input"
                value={threshold ?? ''}
                placeholder={metricLabel(metric)}
                onChange={(event) => update({ threshold: event.target.value || undefined })}
              />
            </Field>
          ) : null}

          <Field label="Direction" htmlFor="anom-direction" className="w-40">
            <Select
              id="anom-direction"
              value={direction}
              onChange={(value) => update({ direction: value })}
              options={DIRECTION_OPTIONS}
            />
          </Field>
          <Field label="Region" htmlFor="anom-region" className="w-40">
            <Select
              id="anom-region"
              value={region ?? ''}
              onChange={(value) => update({ region: value || undefined })}
              options={asList<Region>(regionsState.data, 'regions').map((item) => ({
                value: item.code,
                label: item.name ? `${item.name} (${item.code})` : item.code,
              }))}
              placeholder="All regions"
            />
          </Field>
          <Field label="Location" htmlFor="anom-location" className="w-40">
            <input
              id="anom-location"
              className="input"
              value={location ?? ''}
              placeholder="Station code(s)"
              onChange={(event) => update({ location: event.target.value.trim() || undefined })}
            />
          </Field>
          <DateRangeFields start={start ?? ''} end={end ?? ''} onChange={(range) => update(range)} idPrefix="anom" />
        </div>
      </Panel>

      <div className="grid-responsive">
        <Panel title="Severity breakdown">
          {state.loading ? <ChartSkeleton height={80} /> : null}
          {state.data ? (
            <SeverityBar counts={state.data.severity_counts ?? {}} total={state.data.anomaly_count} />
          ) : !state.loading ? (
            <p className="text-xs subtle">No breakdown available.</p>
          ) : null}
        </Panel>

        <Panel
          title="Baseline & scope"
          footer={<p className="text-xs">Evaluated observations: <span className="mono">{formatNumber(state.data?.evaluated_observations ?? 0, 0)}</span></p>}
        >
          {state.loading ? <ChartSkeleton height={80} /> : null}
          {state.data?.baseline ? (
            <KeyValue
              columns={2}
              items={Object.entries(state.data.baseline).map(([key, value]) => ({
                label: key.replace(/_/g, ' '),
                value:
                  typeof value === 'number' ? (
                    <span className="mono">{formatNumber(value)}</span>
                  ) : typeof value === 'string' || value === null ? (
                    <span className="mono">{String(value ?? '—')}</span>
                  ) : (
                    <span className="mono">{JSON.stringify(value)}</span>
                  ),
              }))}
            />
          ) : state.data ? (
            <p className="text-xs subtle">
              No baseline is reported for the <span className="mono">{state.data.method}</span> method.
            </p>
          ) : null}
        </Panel>
      </div>

      {state.error ? <Panel><ErrorState error={state.error} onRetry={() => void state.refetch()} /></Panel> : null}

      {state.data && state.data.truncated ? (
        <div
          className="flex items-center gap-2 rounded-lg border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--border)', backgroundColor: 'var(--surface-muted)' }}
        >
          <AlertTriangle className="h-4 w-4 shrink-0 text-solar-600" aria-hidden="true" />
          <span>
            Results were truncated to the returned limit, so the count below isn't the full period.
          </span>
        </div>
      ) : null}

      <Panel
        title={`Detected anomalies (${formatNumber(anomalies.length, 0)})`}
        description="Top detections for the current method. Each explanation is the detector's own text."
        footer={
          state.data?.caveat ? (
            <p className="text-xs italic muted">Caveat: {state.data.caveat}</p>
          ) : (
            <p className="text-xs subtle">No method caveat was returned with this result set.</p>
          )
        }
      >
        {state.loading ? (
          <ChartSkeleton height={200} />
        ) : anomalies.length === 0 ? (
          <EmptyState
            title="No anomalies detected"
            description={
              state.data?.note ??
              `No ${metricLabel(metric).toLowerCase()} records exceeded the configured ${method.replace(/_/g, ' ')} criteria in this window. Loosen the threshold or widen the date range.`
            }
            action={
              <Link to="/app/history" className="btn btn-secondary btn-sm">
                Inspect raw history
              </Link>
            }
          />
        ) : (
          <DataTable
            columns={anomalyColumns({
              onRaiseRule: (anomaly) => setRuleFor(anomaly),
              can,
              hasRole,
            })}
            rows={anomalies}
            rowKey={(row, index) => `${row.location_code ?? 'x'}-${row.observation_date}-${row.metric}-${index}`}
            compact
            empty={<EmptyState title="No anomalies" description="The detector returned no rows." />}
          />
        )}
      </Panel>

      {ruleFor ? (
        <RaiseRuleModal
          anomaly={ruleFor}
          defaultSigma={sigma}
          onClose={() => setRuleFor(null)}
        />
      ) : null}
    </div>
  )
}

/* --------------------------------------------------------------- columns */

function anomalyColumns({
  onRaiseRule,
  can,
  hasRole,
}: {
  onRaiseRule: (anomaly: Anomaly) => void
  can: (capability: string) => boolean
  hasRole: (...roles: Parameters<ReturnType<typeof useAuth>['hasRole']>[number][]) => boolean
}): Column<Anomaly>[] {
  const canRaise = can('alerts:manage')
  return [
    {
      key: 'observation_date',
      header: 'Date',
      cell: (row) => <span className="mono text-xs">{row.observation_date}</span>,
    },
    {
      key: 'location',
      header: 'Where',
      cell: (row) => (
        <div className="min-w-0 text-xs">
          <span className="block truncate">{row.location_name ?? row.location_code ?? row.region_code ?? 'global'}</span>
          {row.location_code ? <span className="mono block subtle">{row.location_code}</span> : null}
        </div>
      ),
    },
    {
      key: 'severity',
      header: 'Severity',
      cell: (row) => <Badge tone={severityTone(row.severity)}>{row.severity}</Badge>,
    },
    {
      key: 'value',
      header: 'Reading',
      align: 'right',
      cell: (row) => (
        <span className="text-xs">
          <span className="mono">
            {formatNumber(row.value)} {row.unit}
          </span>
          <span className="subtle block">
            {row.direction === 'above' ? '▲' : '▼'} {formatNumber(Math.abs(row.deviation_score), 1)}σ
          </span>
        </span>
      ),
    },
    {
      key: 'explanation',
      header: 'Why flagged',
      cell: (row) => (
        <div className="text-xs">
          <span className="line-clamp-2">{row.explanation}</span>
          <span className="mono block subtle">{row.metric_label} · {row.method.replace(/_/g, ' ')}</span>
        </div>
      ),
    },
    {
      key: 'action',
      header: 'Alert rule',
      align: 'right',
      cell: (row) => {
        if (hasRole('administrator')) {
          return (
            <Link to="/admin/alert-rules" className="btn btn-secondary btn-sm inline-flex items-center gap-1">
              Configure <ExternalLink className="h-3 w-3" />
            </Link>
          )
        }
        if (canRaise) {
          return (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => onRaiseRule(row)}>
              Raise a rule
            </button>
          )
        }
        return (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled
            title="Viewers cannot create alert rules. Ask an analyst or administrator to raise one from this anomaly."
          >
            Raise a rule
          </button>
        )
      },
    },
  ]
}

/* ------------------------------------------------------------- export btn */

function ExportButton({
  enabled,
  busy,
  onExport,
}: {
  enabled: boolean
  busy: boolean
  onExport: () => Promise<unknown>
}) {
  const toast = useToast()
  const [pending, setPending] = useState(false)
  const disabled = !enabled || pending || busy

  return (
    <button
      type="button"
      className="btn btn-secondary btn-sm"
      disabled={disabled}
      title={
        enabled
          ? 'Queue an anomaly report using the current filters.'
          : 'Report generation needs the reports:manage capability (analyst or administrator).'
      }
      onClick={() => {
        if (!enabled) return
        setPending(true)
        onExport()
          .then(() => toast.success('Anomaly report queued', 'Track its progress on the Reports page.'))
          .catch((error: unknown) =>
            toast.danger('Report could not be queued', error instanceof ApiRequestError ? error.message : String(error)),
          )
          .finally(() => setPending(false))
      }}
    >
      Export report
    </button>
  )
}

/* ---------------------------------------------------------- raise rule modal */

function RaiseRuleModal({
  anomaly,
  defaultSigma,
  onClose,
}: {
  anomaly: Anomaly
  defaultSigma: number
  onClose: () => void
}) {
  const toast = useToast()
  const [name, setName] = useState(
    `${metricLabel(anomaly.metric)} anomaly at ${anomaly.location_name ?? anomaly.location_code ?? anomaly.region_code ?? 'scope'}`,
  )
  const [sigmaValue, setSigmaValue] = useState<string>(String(Math.max(1, Math.round(anomaly.deviation_score * 2) / 2 || defaultSigma)))
  const [severity, setSeverity] = useState<Severity>(anomaly.severity ?? 'high')
  const [submitting, setSubmitting] = useState(false)

  const locationCodes = anomaly.location_code ? parseCsv(anomaly.location_code) : []

  const submit = () => {
    setSubmitting(true)
    api.alerts
      .createRule({
        name: name.trim(),
        target_variable: anomaly.metric,
        scope: locationCodes.length ? { location_codes: locationCodes } : anomaly.region_code ? { regions: [anomaly.region_code] } : {},
        condition: { operator: 'deviation_gt_sigma', sigma: numberOrNull(sigmaValue) ?? defaultSigma },
        severity,
        cooldown_minutes: 60,
        recipients: [],
        description: `Raised from a ${anomaly.method.replace(/_/g, ' ')} detection (${formatNumber(anomaly.deviation_score, 1)}σ on ${anomaly.observation_date}).`,
      })
      .then(() => {
        toast.success('Alert rule created', 'It will be evaluated on the next scan.')
        onClose()
      })
      .catch((error: unknown) =>
        toast.danger('Could not create the rule', error instanceof ApiRequestError ? error.message : String(error)),
      )
      .finally(() => setSubmitting(false))
  }

  return (
    <Modal
      open
      title="Raise an alert rule from this anomaly"
      description="Persists a deviation rule so future breaches notify instead of only appearing here."
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" disabled={submitting || !name.trim()} onClick={submit}>
            {submitting ? 'Creating…' : 'Create rule'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <KeyValue
          columns={2}
          items={[
            { label: 'Metric', value: metricLabel(anomaly.metric) },
            { label: 'Detected deviation', value: <span className="mono">{formatNumber(anomaly.deviation_score, 1)}σ</span> },
            { label: 'Location', value: anomaly.location_code ?? anomaly.region_code ?? 'global' },
            { label: 'Date', value: <span className="mono">{anomaly.observation_date}</span> },
          ]}
        />
        <Field label="Rule name" htmlFor="rule-name">
          <input id="rule-name" className="input" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Trigger sigma (σ)" htmlFor="rule-sigma" hint="Alert fires when deviation exceeds this many standard deviations.">
            <input
              id="rule-sigma"
              type="number"
              step="0.1"
              min="1"
              max="6"
              className="input"
              value={sigmaValue}
              onChange={(event) => setSigmaValue(event.target.value)}
            />
          </Field>
          <Field label="Severity" htmlFor="rule-severity">
            <Select id="rule-severity" value={severity} onChange={(value) => setSeverity(value as Severity)} options={SEVERITY_OPTIONS} />
          </Field>
        </div>
      </div>
    </Modal>
  )
}
