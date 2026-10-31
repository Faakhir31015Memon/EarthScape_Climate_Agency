/**
 * Ingestion jobs — the pipeline run log. The table polls every 5s only while a
 * job is active, errors are surfaced verbatim from the API, and each run records
 * the processing mode it used, so a local run is distinguishable from a cluster job.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CalendarClock, PlayCircle, RotateCw } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import type { ConnectorStatus, IngestionJob, IngestionStartResponse, Region } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { daysAgo, formatDate, formatNumber, relativeTime } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  MultiSelect,
  Panel,
  RefreshButton,
  Select,
  StatCard,
  StatusBadge,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import { DateRangeFields } from './components/data/filters'
import { asList, formatDuration, numberEntries, parseCsv } from './components/data/normalise'

const ACTIVE = new Set(['queued', 'running'])

/**
 * Elapsed time is derived from the two timestamps the job document actually carries;
 * a job without both is shown as an em dash rather than a guessed duration.
 */
function elapsedSeconds(job: IngestionJob): number | null {
  if (!job.started_at || !job.completed_at) return null
  const start = new Date(job.started_at).getTime()
  const end = new Date(job.completed_at).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return null
  return Math.round((end - start) / 1000)
}

export default function IngestionJobsPage() {
  const { can } = useAuth()
  const toast = useToast()
  const canRun = can('ingestion:run')

  const [status, setStatus] = useState<string | undefined>(undefined)
  const [selected, setSelected] = useState<IngestionJob | null>(null)
  const [showStart, setShowStart] = useState(false)
  const [showSchedule, setShowSchedule] = useState(false)

  const state = useAsync(() => api.ingestion.list({ status, page: 1, page_size: 50 }), [status])
  const jobs = useMemo(() => state.data?.items ?? [], [state.data])
  const hasActive = jobs.some((job) => ACTIVE.has(job.status))

  const poll = useAsync(() => api.ingestion.list({ status, page: 1, page_size: 50 }), [status], {
    enabled: hasActive,
    interval: 5000,
  })
  const liveJobs = hasActive ? poll.data?.items ?? jobs : jobs

  // There is no /ingestion/stats route: the counts below are tallied from the jobs
  // actually listed (most recent 50) so the strip can never disagree with the table.
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    liveJobs.forEach((job) => {
      counts[job.status] = (counts[job.status] ?? 0) + 1
    })
    return counts
  }, [liveJobs])
  const stats = useMemo(() => numberEntries(statusCounts), [statusCounts])

  const refreshAll = () => {
    void state.refetch()
    if (hasActive) void poll.refetch()
  }

  const runAction = (
    label: string,
    action: () => Promise<unknown>,
  ) => {
    action()
      .then(() => {
        toast.success(label, 'The job list will refresh on the next poll.')
        refreshAll()
      })
      .catch((error: unknown) =>
        toast.danger(`${label} failed`, error instanceof ApiRequestError ? error.message : String(error)),
      )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title="Ingestion jobs"
        description="Every data-fetch run with its record counts and provenance. Active jobs refresh automatically."
        actions={
          <div className="flex items-center gap-2">
            <RefreshButton onClick={refreshAll} busy={state.refreshing || poll.refreshing} />
            {canRun ? (
              <>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowSchedule(true)}
                  title="Queue the same bounded recent-window sync the server scheduler runs, marked as schedule-triggered."
                >
                  <CalendarClock className="h-4 w-4" aria-hidden="true" /> Lookback sync
                </button>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => setShowStart(true)}>
                  <PlayCircle className="h-4 w-4" aria-hidden="true" /> Start a job
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled
                title="Starting ingestion requires the ingestion:run capability (analyst or administrator)."
              >
                <PlayCircle className="h-4 w-4" aria-hidden="true" /> Start a job
              </button>
            )}
          </div>
        }
      />

      {hasActive ? (
        <p className="flex items-center gap-2 text-xs">
          <Badge tone="info">live</Badge>
          <span className="muted">A queued/running job is being polled every 5 seconds.</span>
        </p>
      ) : null}

      {stats.length ? (
        <div className="space-y-2">
          <p className="text-xs subtle">
            Statuses counted across the {formatNumber(liveJobs.length, 0)} most recent jobs listed below. No overall job-statistics
            figure is stored.
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {stats.slice(0, 8).map((stat) => (
              <StatCard
                key={`${stat.parent ?? ''}-${stat.label}`}
                label={stat.parent ? `${stat.parent} · ${stat.label}` : stat.label}
                value={formatNumber(stat.value, 0)}
                tone={/fail|reject|error/i.test(stat.label) ? 'alert' : 'default'}
              />
            ))}
          </div>
        </div>
      ) : null}

      <Panel
        title="Job history"
        description="Most recent runs first."
        actions={
          <Select
            value={status ?? ''}
            onChange={(value) => setStatus(value || undefined)}
            ariaLabel="Filter by status"
            options={[
              { value: '', label: 'All statuses' },
              { value: 'queued', label: 'Queued' },
              { value: 'running', label: 'Running' },
              { value: 'completed', label: 'Completed' },
              { value: 'completed_with_errors', label: 'Completed w/ errors' },
              { value: 'failed', label: 'Failed' },
              { value: 'cancelled', label: 'Cancelled' },
            ]}
          />
        }
      >
        <DataTable
          columns={jobColumns({ canRun, onOpen: setSelected, onCancel: (job) => runAction('Cancel requested', () => api.ingestion.cancel(job.id)), onRetry: (job) => runAction('Retry queued', () => api.ingestion.retry(job.id)) })}
          rows={liveJobs}
          rowKey={(job) => job.id}
          loading={state.loading}
          onRowClick={(job) => setSelected(job)}
          empty={
            state.error ? (
              <ErrorState error={state.error} onRetry={() => void state.refetch()} />
            ) : (
              <EmptyState
                title="No ingestion jobs"
                description="No data has been fetched yet. Start a job to pull from a connected source."
                action={
                  canRun ? (
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => setShowStart(true)}>
                      Start a job
                    </button>
                  ) : undefined
                }
              />
            )
          }
        />
      </Panel>

      {selected ? (
        <JobDetailModal
          job={liveJobs.find((job) => job.id === selected.id) ?? selected}
          canRun={canRun}
          onClose={() => setSelected(null)}
          onCancel={(job) => runAction('Cancel requested', () => api.ingestion.cancel(job.id))}
          onRetry={(job) => runAction('Retry queued', () => api.ingestion.retry(job.id))}
        />
      ) : null}

      {showStart ? (
        <StartJobDialog
          onClose={() => setShowStart(false)}
          onStarted={(ack) => {
            toast.success('Ingestion job queued', `Job ${ack.job_id} was accepted (status ${ack.status}).`)
            setShowStart(false)
            refreshAll()
          }}
        />
      ) : null}

      {showSchedule ? (
        <ScheduleDialog
          onClose={() => setShowSchedule(false)}
          onScheduled={(ack) => {
            toast.success('Lookback sync queued', `Job ${ack.job_id} was accepted (status ${ack.status}).`)
            setShowSchedule(false)
            refreshAll()
          }}
        />
      ) : null}
    </div>
  )
}

// columns

function jobColumns({
  canRun,
  onOpen,
  onCancel,
  onRetry,
}: {
  canRun: boolean
  onOpen: (job: IngestionJob) => void
  onCancel: (job: IngestionJob) => void
  onRetry: (job: IngestionJob) => void
}): Column<IngestionJob>[] {
  return [
    {
      key: 'source',
      header: 'Source',
      cell: (job) => (
        <div className="min-w-0 text-xs">
          <span className="block truncate font-medium">{job.source_name}</span>
          <span className="subtle">{job.source_type?.replace(/_/g, ' ') ?? '—'}</span>
        </div>
      ),
    },
    { key: 'job_type', header: 'Type', cell: (job) => <span className="text-xs">{job.job_type?.replace(/_/g, ' ') ?? '—'}</span> },
    { key: 'status', header: 'Status', cell: (job) => <StatusBadge value={job.status} kind="job" /> },
    {
      key: 'records',
      header: 'Accepted / Rejected / Fetched',
      align: 'right',
      cell: (job) => (
        <span className="mono text-xs">
          <span className="text-forest-600 dark:text-forest-300">{formatNumber(job.records_accepted, 0)}</span>
          {' / '}
          <span className={job.records_rejected ? 'text-alert-high' : ''}>{formatNumber(job.records_rejected, 0)}</span>
          {' / '}
          {formatNumber(job.records_fetched, 0)}
        </span>
      ),
    },
    {
      key: 'retry',
      header: 'Retries',
      align: 'right',
      cell: (job) => <span className="mono text-xs">{formatNumber(job.retry_count, 0)}{job.max_retries ? `/${job.max_retries}` : ''}</span>,
    },
    { key: 'mode', header: 'Mode', cell: (job) => (job.processing_mode ? <Badge tone="neutral">{job.processing_mode}</Badge> : <span className="text-xs subtle">—</span>) },
    { key: 'started', header: 'Started', cell: (job) => <span className="text-xs">{job.started_at ? relativeTime(job.started_at) : '—'}</span> },
    {
      key: 'duration',
      header: 'Elapsed',
      align: 'right',
      cell: (job) => (
        <span className="mono text-xs" title={elapsedSeconds(job) === null ? 'Started or completion timestamp missing' : undefined}>
          {formatDuration(elapsedSeconds(job))}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (job) => (
        <div className="flex justify-end gap-1">
          <button type="button" className="btn btn-ghost btn-sm" onClick={(event) => { event.stopPropagation(); onOpen(job) }}>
            Detail
          </button>
          {canRun && ACTIVE.has(job.status) ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={(event) => { event.stopPropagation(); onCancel(job) }} title="Request cancellation">
              Cancel
            </button>
          ) : null}
          {canRun && (job.status === 'failed' || job.status === 'cancelled') ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={(event) => { event.stopPropagation(); onRetry(job) }} title="Retry this job">
              <RotateCw className="h-3.5 w-3.5" aria-hidden="true" /> Retry
            </button>
          ) : null}
        </div>
      ),
    },
  ]
}

// detail modal

function JobDetailModal({
  job,
  canRun,
  onClose,
  onCancel,
  onRetry,
}: {
  job: IngestionJob
  canRun: boolean
  onClose: () => void
  onCancel: (job: IngestionJob) => void
  onRetry: (job: IngestionJob) => void
}) {
  const logs = job.log_lines ?? []
  return (
    <Modal
      open
      width="max-w-3xl"
      title={`${job.source_name} — ${job.job_type?.replace(/_/g, ' ') ?? 'job'}`}
      description={<span className="flex items-center gap-2"><StatusBadge value={job.status} kind="job" /> <span className="mono subtle">{job.id}</span></span>}
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          {canRun && ACTIVE.has(job.status) ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => onCancel(job)}>Cancel job</button>
          ) : null}
          {canRun && (job.status === 'failed' || job.status === 'cancelled') ? (
            <button type="button" className="btn btn-primary btn-sm" onClick={() => onRetry(job)}>Retry</button>
          ) : null}
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
      }
    >
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <RecordStat label="Accepted" value={job.records_accepted} tone="success" />
          <RecordStat label="Rejected" value={job.records_rejected} tone={job.records_rejected ? 'danger' : 'neutral'} />
          <RecordStat label="Skipped" value={job.records_skipped} tone="neutral" />
          <RecordStat label="Fetched" value={job.records_fetched} tone="neutral" />
          <RecordStat label="Duplicates" value={job.duplicate_count ?? 0} tone="neutral" />
          <div className="card-tight p-2 text-xs">
            <span className="muted">Elapsed</span>
            <p className="mono text-base">{formatDuration(elapsedSeconds(job))}</p>
          </div>
        </div>

        {job.error_message ? (
          <p className="rounded-lg border border-alert-high/40 bg-alert-high/10 px-3 py-2 text-sm text-alert-high">
            <span className="font-semibold">Error:</span> {job.error_message}
          </p>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 text-xs">
          <div>
            <span className="muted">Data period</span>
            <p className="mono">
              {job.data_period?.start ? formatDate(job.data_period.start) : '—'} → {job.data_period?.end ? formatDate(job.data_period.end) : '—'}
            </p>
          </div>
          <div>
            <span className="muted">Dataset</span>
            <p>
              {job.dataset_id ? (
                // The job document carries the id only — no dataset name is resolvable here.
                <Link className="link mono" to={`/app/datasets/${job.dataset_id}`}>
                  {job.dataset_id}
                </Link>
              ) : (
                '—'
              )}
            </p>
          </div>
        </div>

        {job.geographic_coverage && Object.keys(job.geographic_coverage).length ? (
          <div className="text-xs">
            <span className="muted">Geographic coverage</span>
            <pre className="mono mt-1 max-h-32 overflow-auto rounded-lg p-2" style={{ backgroundColor: 'var(--surface-muted)' }}>
              {JSON.stringify(job.geographic_coverage, null, 2)}
            </pre>
          </div>
        ) : null}

        <div className="text-xs">
          <span className="muted">Provenance</span>
          <ul className="mt-1 space-y-0.5">
            <li>Requested by (user id): <span className="mono">{job.requested_by || '—'}</span></li>
            <li>Triggered by: {job.triggered_by ?? '—'}</li>
            <li>Executor: {job.executor ?? '—'}</li>
            <li>
              Processing mode: <Badge tone="neutral">{job.processing_mode ?? 'unknown'}</Badge>
            </li>
            <li>Started: {job.started_at ? formatDate(job.started_at, true) : '—'} · Completed: {job.completed_at ? formatDate(job.completed_at, true) : '—'}</li>
          </ul>
        </div>

        <div>
          <p className="panel-title mb-1">Log lines</p>
          {logs.length ? (
            <div className="mono max-h-64 overflow-auto rounded-lg border p-2 text-[11px]" style={{ borderColor: 'var(--border)' }}>
              {logs.map((line, index) => (
                <div key={index} className="flex gap-2">
                  <span className="subtle shrink-0">{line.at ? relativeTime(line.at) : ''}</span>
                  <span className={line.level === 'error' ? 'text-alert-high' : line.level === 'warning' ? 'text-solar-600' : ''}>
                    [{line.level}] {line.message}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs subtle">No log lines captured for this run.</p>
          )}
        </div>
      </div>
    </Modal>
  )
}

function RecordStat({ label, value, tone }: { label: string; value: number; tone: 'success' | 'danger' | 'neutral' }) {
  return (
    <div className="card-tight p-2 text-xs">
      <span className="muted">{label}</span>
      <p className={'mono text-base ' + (tone === 'danger' ? 'text-alert-high' : tone === 'success' ? 'text-forest-600 dark:text-forest-300' : '')}>
        {formatNumber(value, 0)}
      </p>
    </div>
  )
}

// start dialog

/**
 * `POST /ingestion/jobs` takes `source` = a connector name from
 * `GET /ingestion/connectors`, plus an optional `parameters` object
 * (`location_codes`, `start`, `end`). It answers with a queue acknowledgement.
 */
function useConnectorPicker() {
  const connectorsState = useAsync(() => api.ingestion.connectors(), [])
  const connectors = useMemo(() => asList<ConnectorStatus>(connectorsState.data, 'connectors'), [connectorsState.data])
  const options = useMemo(
    () =>
      connectors.map((connector) => ({
        value: connector.connector,
        label: connector.configured ? connector.connector : `${connector.connector} (needs configuration)`,
        hint: connector.setup_hint,
      })),
    [connectors],
  )
  return { connectorsState, connectors, options }
}

function StartJobDialog({ onClose, onStarted }: { onClose: () => void; onStarted: (ack: IngestionStartResponse) => void }) {
  const toast = useToast()
  const { connectorsState, options } = useConnectorPicker()
  const [source, setSource] = useState('')
  const [locations, setLocations] = useState<string[]>([])
  const [range, setRange] = useState<{ start?: string; end?: string }>({})
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const chosen = options.find((option) => option.value === source) ?? options[0] ?? null
  const regionsState = useAsync(() => api.climate.regions(), [])
  const locationOptions = useMemo(
    () =>
      asList<Region>(regionsState.data, 'regions').flatMap((region) =>
        (region.locations ?? []).map((station) => ({ value: station.code, label: station.name ? `${station.name} (${station.code})` : station.code })),
      ),
    [regionsState.data],
  )

  const submit = () => {
    if (!chosen) return
    setSubmitting(true)
    setError(null)
    api.ingestion
      .start({
        source: chosen.value,
        triggered_by: 'manual',
        parameters: {
          location_codes: locations.length ? locations : undefined,
          start: range.start,
          end: range.end,
        },
      })
      .then(onStarted)
      .catch((err: unknown) => {
        const message = err instanceof ApiRequestError ? err.message : String(err)
        setError(message)
        toast.danger('Could not start the job', message)
      })
      .finally(() => setSubmitting(false))
  }

  return (
    <Modal
      open
      title="Start an ingestion job"
      description="Queues a fresh fetch. Each run records the engine that processed it and its record counts."
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={submitting || !chosen}
            onClick={submit}
          >
            {submitting ? 'Starting…' : 'Start job'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <Field label="Connector" htmlFor="ing-source" hint="Only connectors available in this deployment can be queued.">
          <Select
            id="ing-source"
            value={chosen?.value ?? ''}
            onChange={(value) => setSource(value)}
            options={options}
            placeholder={connectorsState.loading ? 'Loading connectors…' : 'No connector reported'}
          />
        </Field>

        {chosen ? (
          <p className="text-xs subtle">
            {chosen.hint || 'No setup hint is available.'} The connector uses its own default parameters; there's no
            per-job metric choice.
          </p>
        ) : null}

        <Field label="Locations" hint="Leave empty to let the connector use its default coverage.">
          <MultiSelect options={locationOptions} value={locations} onChange={setLocations} placeholder="All locations" label="Locations" searchable maxChips={4} />
        </Field>

        <div>
          <span className="label">Date range (optional)</span>
          <DateRangeFields start={range.start ?? ''} end={range.end ?? ''} onChange={setRange} idPrefix="ing" />
        </div>

        {error ? <p className="text-sm text-alert-high">{error}</p> : null}
      </div>
    </Modal>
  )
}

// lookback dialog

/**
 * There is no scheduler route to trigger. The equivalent is the same
 * `POST /ingestion/jobs` call the server scheduler makes: a bounded recent window
 * recorded with `triggered_by: "schedule"`.
 */
function ScheduleDialog({ onClose, onScheduled }: { onClose: () => void; onScheduled: (ack: IngestionStartResponse) => void }) {
  const toast = useToast()
  const { connectorsState, options } = useConnectorPicker()
  const [source, setSource] = useState('')
  const [lookback, setLookback] = useState('7')
  const [locationInput, setLocationInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const chosen = options.find((option) => option.value === source) ?? options[0] ?? null
  const days = numberFrom(lookback)
  const window = useMemo(() => {
    const span = days && days > 0 ? Math.round(days) : null
    if (span === null) return null
    return { start: daysAgo(span), end: daysAgo(1) }
  }, [days])

  const submit = () => {
    if (!chosen || !window) return
    setSubmitting(true)
    setError(null)
    const locations = parseCsv(locationInput)
    api.ingestion
      .start({
        source: chosen.value,
        triggered_by: 'schedule',
        parameters: { location_codes: locations.length ? locations : undefined, start: window.start, end: window.end },
      })
      .then(onScheduled)
      .catch((err: unknown) => {
        const message = err instanceof ApiRequestError ? err.message : String(err)
        setError(message)
        toast.danger('Could not trigger the sync', message)
      })
      .finally(() => setSubmitting(false))
  }

  return (
    <Modal
      open
      title="Run a lookback sync"
      description="Queues the same bounded recent-window job the schedule would run, recorded as schedule-triggered. The schedule itself cannot be edited from here."
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={submitting || !chosen || !window} onClick={submit}>
            {submitting ? 'Triggering…' : 'Run now'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        <Field label="Connector" htmlFor="sched-source">
          <Select
            id="sched-source"
            value={chosen?.value ?? ''}
            onChange={(value) => setSource(value)}
            options={options}
            placeholder={connectorsState.loading ? 'Loading connectors…' : 'No connector reported'}
          />
        </Field>
        <Field label="Lookback (days)" htmlFor="sched-lookback" hint="Re-fetch the last N days up to yesterday.">
          <input id="sched-lookback" type="number" min={1} max={3650} className="input" value={lookback} onChange={(event) => setLookback(event.target.value)} />
        </Field>
        <p className="text-xs subtle">
          {window ? `Fetch window: ${window.start} → ${window.end}.` : 'Enter a positive lookback in days.'}
        </p>
        <Field label="Locations" htmlFor="sched-locations" hint="Comma-separated station codes. Leave empty for defaults.">
          <input id="sched-locations" className="input mono text-xs" value={locationInput} onChange={(event) => setLocationInput(event.target.value)} placeholder="e.g. KJFK,LAX,EGGW" />
        </Field>
        {error ? <p className="text-sm text-alert-high">{error}</p> : null}
      </div>
    </Modal>
  )
}

function numberFrom(value: string): number | null {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}
