/**
 * Processing monitor — the heavy-computation side of the pipeline. Workflow
 * catalogues, live job runs, and the real engine topology. Engines are only
 * shown as healthy when the backend actually reports them reachable; a local
 * single-node deployment is stated plainly rather than dressed up as a cluster.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { PlayCircle } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import type { EngineHealth, ProcessingJob, ProcessingWorkflow } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { formatBytes, formatDate, formatNumber, relativeTime } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  Panel,
  RefreshButton,
  StatusBadge,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import { asList, formatDuration, numberOrNull } from './components/data/normalise'

const ACTIVE = new Set(['queued', 'running'])

/** `ProcessingJob.output` is `Record<string, unknown>`; only real string arrays are rendered. */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []
}

function textMetric(metrics: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = metrics?.[key]
  return typeof value === 'string' && value ? value : null
}

export default function ProcessingMonitorPage() {
  const { can } = useAuth()
  const toast = useToast()
  const canRun = can('processing:run')

  const [status, setStatus] = useState<string | undefined>(undefined)
  const [selected, setSelected] = useState<ProcessingJob | null>(null)
  const [startWorkflow, setStartWorkflow] = useState<ProcessingWorkflow | null>(null)

  const workflowsState = useAsync(() => api.processing.workflows(), [])
  const workflows = useMemo(() => asList<ProcessingWorkflow>(workflowsState.data, 'workflows'), [workflowsState.data])

  const jobsState = useAsync(() => api.processing.list({ status, page: 1, page_size: 50 }), [status])
  const jobs = useMemo(() => jobsState.data?.items ?? [], [jobsState.data])
  const hasActive = jobs.some((job) => ACTIVE.has(job.status))

  const poll = useAsync(() => api.processing.list({ status, page: 1, page_size: 50 }), [status], {
    enabled: hasActive,
    interval: 5000,
  })
  const liveJobs = hasActive ? poll.data?.items ?? jobs : jobs

  const enginesState = useAsync(() => api.processing.engines(), [])
  const engines = useMemo(() => asList<EngineHealth>(enginesState.data, 'engines'), [enginesState.data])

  const refreshAll = () => {
    void jobsState.refetch()
    if (hasActive) void poll.refetch()
  }

  const cancelJob = (job: ProcessingJob) =>
    api.processing
      .cancel(job.id)
      .then(() => {
        toast.success('Cancellation requested', `Job ${job.id}.`)
        refreshAll()
      })
      .catch((error: unknown) => toast.danger('Cancel failed', error instanceof ApiRequestError ? error.message : String(error)))

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title="Processing monitor"
        description="MapReduce / Spark / streaming workflows, their runs and the engines behind them."
        actions={
          <div className="flex items-center gap-2">
            <RefreshButton onClick={refreshAll} busy={jobsState.refreshing || poll.refreshing} />
            <Select status={status} onChange={setStatus} />
          </div>
        }
      />

      <Panel title="Engine health" description="Checked live. An engine is marked available only when it is reachable.">
        {enginesState.loading ? <p className="text-xs subtle">Querying engines…</p> : null}
        {enginesState.error ? <ErrorState error={enginesState.error} onRetry={() => void enginesState.refetch()} /> : null}
        {!enginesState.loading && engines.length === 0 && !enginesState.error ? (
          <EmptyState title="No engine health reported" description="No engine status came back, so cluster status is unknown." />
        ) : null}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {engines.map((engine, index) => (
            <EngineCard key={index} engine={engine} />
          ))}
        </div>
      </Panel>

      {hasActive ? (
        <p className="flex items-center gap-2 text-xs">
          <Badge tone="info">live</Badge>
          <span className="muted">An active job is being polled every 5 seconds.</span>
        </p>
      ) : null}

      <Panel title="Workflows" description="Available batch and stream workflows. Unavailable ones explain why.">
        {workflowsState.loading ? <p className="text-xs subtle">Loading workflows…</p> : null}
        {workflowsState.error ? <ErrorState error={workflowsState.error} onRetry={() => void workflowsState.refetch()} /> : null}
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {workflows.map((workflow) => (
            <li key={workflow.id} className="card-tight flex flex-col gap-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{workflow.label}</span>
                <Badge tone="neutral">{workflow.engine}</Badge>
              </div>
              <p className="flex-1 text-xs muted">{workflow.description}</p>
              {workflow.requires?.length ? (
                <p className="text-[11px] subtle">
                  Requires: <span className="mono">{workflow.requires.join(', ')}</span>
                </p>
              ) : null}
              {workflow.available ? (
                canRun ? (
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => setStartWorkflow(workflow)}>
                    <PlayCircle className="h-4 w-4" aria-hidden="true" /> Run workflow
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    disabled
                    title="Running processing workflows requires the processing:run capability (analyst or administrator)."
                  >
                    Run workflow
                  </button>
                )
              ) : (
                <p className="rounded-lg px-2 py-1.5 text-xs" style={{ backgroundColor: 'var(--surface-muted)' }}>
                  <span className="muted">Unavailable:</span> {workflow.unavailable_reason ?? 'no reason was given.'}
                </p>
              )}
            </li>
          ))}
        </ul>
        {!workflowsState.loading && workflows.length === 0 && !workflowsState.error ? (
          <EmptyState title="No workflows registered" description="The processing app has not exposed any workflows to run." />
        ) : null}
      </Panel>

      <Panel title="Processing jobs" description="Batch and streaming runs, newest first.">
        <DataTable
          columns={jobColumns({ canRun, onOpen: setSelected, onCancel: cancelJob })}
          rows={liveJobs}
          rowKey={(job) => job.id}
          loading={jobsState.loading}
          onRowClick={(job) => setSelected(job)}
          empty={
            jobsState.error ? (
              <ErrorState error={jobsState.error} onRetry={() => void jobsState.refetch()} />
            ) : (
              <EmptyState
                title="No processing jobs"
                description="Aggregation and transformation runs will appear here once a workflow executes."
                action={
                  <Link to="/app/big-data" className="btn btn-secondary btn-sm">
                    Open the Big Data workspace
                  </Link>
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
          onCancel={cancelJob}
        />
      ) : null}

      {startWorkflow ? (
        <StartWorkflowDialog
          workflow={startWorkflow}
          onClose={() => setStartWorkflow(null)}
          onStarted={(job) => {
            toast.success('Workflow started', `Job ${job.id} is ${job.status}.`)
            setStartWorkflow(null)
            refreshAll()
          }}
        />
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------- small bits */

function Select({ status, onChange }: { status: string | undefined; onChange: (next?: string) => void }) {
  return (
    <select
      className="select w-44"
      value={status ?? ''}
      onChange={(event) => onChange(event.target.value || undefined)}
      aria-label="Filter by status"
    >
      <option value="">All statuses</option>
      <option value="queued">Queued</option>
      <option value="running">Running</option>
      <option value="completed">Completed</option>
      <option value="completed_with_errors">Completed w/ errors</option>
      <option value="failed">Failed</option>
      <option value="cancelled">Cancelled</option>
    </select>
  )
}

function EngineCard({ engine }: { engine: EngineHealth }) {
  const hdfsOk = engine.hdfs?.configured && engine.hdfs?.reachable
  const sparkOk = engine.spark?.installed && engine.spark?.session_startable
  return (
    <div className="card-tight p-3 text-xs">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="panel-title">Processing node</span>
        <Badge tone="info" title={engine.note}>{engine.current_processing_mode ?? 'unknown mode'}</Badge>
      </div>
      <ul className="space-y-1.5">
        <EngineRow label="HDFS" ok={Boolean(hdfsOk)} detail={engine.hdfs?.detail ?? (engine.hdfs?.configured ? 'reachable' : 'not configured')} configured={Boolean(engine.hdfs?.configured)} />
        <EngineRow label="Spark" ok={Boolean(sparkOk)} detail={engine.spark?.installed ? engine.spark.session_startable ? `Spark ${engine.spark.version ?? ''}`.trim() : (engine.spark.error ?? 'session cannot start') : 'not installed'} configured={Boolean(engine.spark?.installed)} />
        <EngineRow label="Hadoop" ok={Boolean(engine.hadoop?.bin_configured && engine.hadoop?.cluster_configured)} detail={engine.hadoop?.detail ?? (engine.hadoop?.bin_configured ? 'bin available' : 'hadoop bin not configured')} configured={Boolean(engine.hadoop?.bin_configured)} />
        <EngineRow
          label="Data lake"
          ok={Boolean(engine.data_lake?.reachable)}
          detail={`${engine.data_lake?.mode ?? 'unknown'} · ${engine.data_lake?.root ?? 'no root'}${engine.data_lake?.free_bytes != null ? ` · ${formatBytes(engine.data_lake.free_bytes)} free` : ''}`}
          configured={Boolean(engine.data_lake?.root)}
        />
      </ul>
      {engine.note ? <p className="mt-2 subtle italic">{engine.note}</p> : null}
    </div>
  )
}

function EngineRow({ label, ok, detail, configured }: { label: string; ok: boolean; detail: string; configured: boolean }) {
  // Never greened unless actually available; unconfigured is a neutral state.
  const tone = ok ? 'success' : configured ? 'warning' : 'neutral'
  const status = ok ? 'available' : configured ? 'unavailable' : 'not-configured'
  return (
    <li className="flex items-start justify-between gap-2">
      <span className="min-w-0">
        <span className="font-medium">{label}</span>
        <span className="block truncate subtle">{detail}</span>
      </span>
      <Badge tone={tone}>{status}</Badge>
    </li>
  )
}

/* ------------------------------------------------------------- columns */

function jobColumns({
  canRun,
  onOpen,
  onCancel,
}: {
  canRun: boolean
  onOpen: (job: ProcessingJob) => void
  onCancel: (job: ProcessingJob) => void
}): Column<ProcessingJob>[] {
  return [
    {
      key: 'workflow',
      header: 'Workflow',
      cell: (job) => (
        <div className="min-w-0 text-xs">
          <span className="block truncate font-medium">{job.workflow_label ?? job.workflow}</span>
          <span className="mono block subtle">{job.id}</span>
        </div>
      ),
    },
    { key: 'engine', header: 'Engine', cell: (job) => <Badge tone="neutral">{job.engine}</Badge> },
    { key: 'status', header: 'Status', cell: (job) => <StatusBadge value={job.status} kind="job" /> },
    {
      key: 'rows',
      header: 'Rows in/out',
      align: 'right',
      cell: (job) => (
        <span className="mono text-xs">
          {formatNumber(numberOrNull(job.metrics?.rows_in), 0)} / {formatNumber(numberOrNull(job.metrics?.rows_out), 0)}
        </span>
      ),
    },
    {
      key: 'mode',
      header: 'Mode',
      cell: (job) => {
        const mode = job.processing_mode || textMetric(job.metrics, 'processing_mode')
        return mode ? <Badge tone="neutral">{mode}</Badge> : <span className="text-xs subtle">—</span>
      },
    },
    { key: 'submitted', header: 'Submitted', cell: (job) => <span className="text-xs">{job.submitted_at ? relativeTime(job.submitted_at) : '—'}</span> },
    { key: 'duration', header: 'Duration', align: 'right', cell: (job) => <span className="mono text-xs">{formatDuration(numberOrNull(job.duration_seconds ?? job.metrics?.duration_seconds))}</span> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (job) => (
        <div className="flex justify-end gap-1">
          <button type="button" className="btn btn-ghost btn-sm" onClick={(event) => { event.stopPropagation(); onOpen(job) }}>Detail</button>
          {canRun && ACTIVE.has(job.status) ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={(event) => { event.stopPropagation(); onCancel(job) }}>Cancel</button>
          ) : null}
        </div>
      ),
    },
  ]
}

/* ---------------------------------------------------------- detail modal */

function JobDetailModal({
  job,
  canRun,
  onClose,
  onCancel,
}: {
  job: ProcessingJob
  canRun: boolean
  onClose: () => void
  onCancel: (job: ProcessingJob) => void
}) {
  const running = ACTIVE.has(job.status)
  const metrics = job.metrics
  const rejected = numberOrNull(metrics?.rejected)
  const peakMemoryMb = numberOrNull(metrics?.peak_memory_mb)
  const engineVersion = textMetric(metrics, 'engine_version')
  const processingMode = job.processing_mode || textMetric(metrics, 'processing_mode') || 'unknown'
  const outputPaths = [
    ...stringList(job.output?.data_lake_uris),
    ...stringList(job.output?.parquet_paths),
    ...stringList(job.output?.summary_keys),
  ]
  const logsState = useAsync(() => api.processing.logs(job.id), [job.id], { enabled: running, interval: running ? 5000 : undefined })
  const logLines = useMemo(() => {
    if (logsState.data?.lines) {
      return logsState.data.lines.map((line) => ({ at: line.timestamp, level: line.level, message: line.message }))
    }
    return (job.log_lines ?? []).map((line) => ({ at: line.at, level: line.level, message: line.message }))
  }, [logsState.data, job.log_lines])

  return (
    <Modal
      open
      width="max-w-3xl"
      title={job.workflow_label ?? job.workflow}
      description={<span className="flex items-center gap-2"><StatusBadge value={job.status} kind="job" /> <Badge tone="neutral">{job.engine}</Badge> <span className="mono subtle">{job.id}</span></span>}
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          {canRun && running ? <button type="button" className="btn btn-secondary btn-sm" onClick={() => onCancel(job)}>Cancel</button> : null}
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
        </div>
      }
    >
      <div className="space-y-4 text-sm">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Metric label="Rows in" value={formatNumber(numberOrNull(metrics?.rows_in), 0)} />
          <Metric label="Rows out" value={formatNumber(numberOrNull(metrics?.rows_out), 0)} />
          <Metric label="Accepted" value={formatNumber(numberOrNull(metrics?.accepted), 0)} />
          <Metric label="Rejected" value={formatNumber(rejected, 0)} tone={rejected ? 'danger' : undefined} />
          <Metric label="Duplicates" value={formatNumber(numberOrNull(metrics?.duplicates), 0)} />
          <Metric label="Peak memory" value={peakMemoryMb === null ? '—' : `${formatNumber(peakMemoryMb, 0)} MB`} />
          <Metric label="Engine version" value={engineVersion ?? '—'} />
          <Metric label="Processing mode" value={processingMode} />
        </div>

        {job.dataset_id ? (
          <p className="text-xs">
            <span className="muted">Dataset:</span>{' '}
            <Link className="link" to={`/app/datasets/${job.dataset_id}`}>{job.dataset_name ?? job.dataset_id}</Link>
          </p>
        ) : null}

        {job.error_message ? (
          <p className="rounded-lg border border-alert-high/40 bg-alert-high/10 px-3 py-2 text-sm text-alert-high">{job.error_message}</p>
        ) : null}

        {outputPaths.length ? (
          <div className="text-xs">
            <span className="muted">Outputs</span>
            <ul className="mono mt-1 space-y-0.5">
              {outputPaths.map((entry) => <li key={entry} className="break-all">{entry}</li>)}
            </ul>
          </div>
        ) : null}

        <div>
          <p className="panel-title mb-1 flex items-center gap-2">
            Logs {running ? <Badge tone="info">live · 5s</Badge> : null}
          </p>
          {logLines.length ? (
            <div className="mono max-h-64 overflow-auto rounded-lg border p-2 text-[11px]" style={{ borderColor: 'var(--border)' }}>
              {logLines.map((line, index) => (
                <div key={index} className="flex gap-2">
                  <span className="subtle shrink-0">{line.at ? formatDate(line.at, true) : ''}</span>
                  <span className={line.level === 'error' ? 'text-alert-high' : line.level === 'warning' ? 'text-solar-600' : ''}>
                    [{line.level}] {line.message}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs subtle">{logsState.loading ? 'Loading logs…' : 'No log lines available for this run.'}</p>
          )}
        </div>
      </div>
    </Modal>
  )
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: 'danger' }) {
  return (
    <div className="card-tight p-2">
      <span className="text-xs muted">{label}</span>
      <p className={'mono text-sm ' + (tone === 'danger' ? 'text-alert-high' : '')}>{value}</p>
    </div>
  )
}

/* ------------------------------------------------------ start workflow dialog */

function StartWorkflowDialog({
  workflow,
  onClose,
  onStarted,
}: {
  workflow: ProcessingWorkflow
  onClose: () => void
  onStarted: (job: ProcessingJob) => void
}) {
  const toast = useToast()
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const initial: Record<string, unknown> = {}
    Object.entries(workflow.parameter_spec ?? {}).forEach(([key, spec]) => {
      if (spec.default !== undefined) initial[key] = spec.default
    })
    return initial
  })
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const setValue = (key: string, value: unknown) => setValues((prev) => ({ ...prev, [key]: value }))

  const submit = () => {
    setSubmitting(true)
    setError(null)
    api.processing
      .start({ workflow: workflow.id, parameters: values })
      .then(onStarted)
      .catch((err: unknown) => {
        const message = err instanceof ApiRequestError ? err.message : String(err)
        setError(message)
        toast.danger('Could not start the workflow', message)
      })
      .finally(() => setSubmitting(false))
  }

  return (
    <Modal
      open
      title={`Run “${workflow.label}”`}
      description={workflow.description || 'Configure the parameters, then start the job.'}
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? 'Starting…' : 'Start'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        {Object.keys(values).length === 0 && !Object.keys(workflow.parameter_spec ?? {}).length ? (
          <p className="text-xs subtle">This workflow takes no parameters.</p>
        ) : null}
        {Object.entries(workflow.parameter_spec ?? {}).map(([key, spec]) => {
          const label = spec.label ?? key.replace(/_/g, ' ')
          if (spec.type === 'bool') {
            return (
              <label key={key} className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={Boolean(values[key])} onChange={(event) => setValue(key, event.target.checked)} />
                <span>{label}</span>
              </label>
            )
          }
          if (spec.type === 'choice') {
            return (
              <Field key={key} label={label} htmlFor={`wf-${key}`} required={spec.required} hint={spec.help}>
                <select id={`wf-${key}`} className="select" value={String(values[key] ?? '')} onChange={(event) => setValue(key, event.target.value)}>
                  <option value="">Select…</option>
                  {(spec.choices ?? []).map((choice) => (
                    <option key={choice} value={choice}>{choice}</option>
                  ))}
                </select>
              </Field>
            )
          }
          if (spec.type === 'date') {
            return (
              <Field key={key} label={label} htmlFor={`wf-${key}`} required={spec.required} hint={spec.help}>
                <input id={`wf-${key}`} type="date" className="input" value={String(values[key] ?? '')} onChange={(event) => setValue(key, event.target.value || undefined)} />
              </Field>
            )
          }
          const numeric = spec.type === 'int' || spec.type === 'float'
          return (
            <Field key={key} label={label} htmlFor={`wf-${key}`} required={spec.required} hint={spec.help ?? (numeric ? `min ${spec.min ?? '—'} · max ${spec.max ?? '—'}` : undefined)}>
              <input
                id={`wf-${key}`}
                type={numeric ? 'number' : 'text'}
                step={spec.type === 'float' ? 'any' : spec.type === 'int' ? '1' : undefined}
                min={spec.min}
                max={spec.max}
                className="input"
                value={values[key] === undefined || values[key] === null ? '' : String(values[key])}
                onChange={(event) => setValue(key, numeric ? (event.target.value === '' ? undefined : Number(event.target.value)) : event.target.value)}
              />
            </Field>
          )
        })}
        {error ? <p className="text-sm text-alert-high">{error}</p> : null}
      </div>
    </Modal>
  )
}
