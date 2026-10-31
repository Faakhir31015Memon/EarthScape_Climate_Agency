/**
 * Big Data workspace — the distributed-processing surface. It reports the configured
 * storage mode (local filesystem vs HDFS/data lake), lets an analyst launch a
 * map/reduce or Spark workflow and track it, and lists the Parquet/aggregation
 * jobs. When the cluster is not configured it says so and shows the backend's
 * own setup guidance.
 */
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Boxes, Database, FileStack, PlayCircle } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import type { EngineHealth, ProcessingJob, ProcessingWorkflow } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { formatBytes, formatNumber } from '../../lib/format'
import {
  Badge,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  Panel,
  RefreshButton,
  StatusBadge,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { TableSkeleton } from '../../components/ui/Skeleton'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import { asList, formatDuration, numberOrNull } from './components/data/normalise'

const ACTIVE = new Set(['queued', 'running'])
const BIG_DATA_ENGINES = new Set(['mapreduce', 'pyspark'])
const PARQUET_PATTERN = /parquet|convert|aggregat/i

/** `ProcessingJob.output` is `Record<string, unknown>`; only real string arrays are rendered. */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []
}

function textMetric(metrics: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = metrics?.[key]
  return typeof value === 'string' && value ? value : null
}

export default function BigDataPage() {
  const { can } = useAuth()
  const toast = useToast()
  const canRun = can('processing:run')

  const [trackedJobId, setTrackedJobId] = useState<string | null>(null)
  const [pendingWorkflow, setPendingWorkflow] = useState<ProcessingWorkflow | null>(null)

  const lakeState = useAsync(() => api.processing.dataLake(), [])
  const enginesState = useAsync(() => api.processing.engines(), [])
  const engines = useMemo(() => asList<EngineHealth>(enginesState.data, 'engines'), [enginesState.data])

  const workflowsState = useAsync(() => api.processing.workflows(), [])
  const workflows = useMemo(() => asList<ProcessingWorkflow>(workflowsState.data, 'workflows'), [workflowsState.data])
  const bigDataWorkflows = workflows.filter((w) => BIG_DATA_ENGINES.has(w.engine))
  const parquetWorkflows = workflows.filter((w) => PARQUET_PATTERN.test(w.id) || PARQUET_PATTERN.test(w.label))

  const tracked = useAsync(() => api.processing.get(trackedJobId ?? ''), [trackedJobId], {
    enabled: Boolean(trackedJobId),
    interval: 5000,
  })

  const lake = lakeState.data
  const mode = lake?.mode ?? engines[0]?.current_processing_mode ?? 'unknown'
  const clusterMode = /hadoop|cluster|hdfs|spark/i.test(mode)

  const refreshAll = () => {
    void lakeState.refetch()
    void enginesState.refetch()
    void workflowsState.refetch()
    if (trackedJobId) void tracked.refetch()
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title="Big Data workspace"
        description="Distributed processing, the data lake, and Parquet/aggregation outputs, shown as this deployment is configured."
        actions={<RefreshButton onClick={refreshAll} busy={lakeState.refreshing || enginesState.refreshing} />}
      />

      {/* (d) mode banner */}
      <div
        className="flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3"
        style={{ borderColor: 'var(--border)', backgroundColor: 'var(--surface-muted)' }}
      >
        <Boxes className="h-5 w-5 text-[var(--brand)]" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">
            Current processing mode: <span className="mono">{mode}</span>
          </p>
          <p className="text-xs muted">
            {clusterMode
              ? 'A distributed engine is configured; runs execute on the cluster.'
              : 'Running on the local single-node engine. Large aggregations run in-process instead of on a Hadoop/Spark cluster.'}
          </p>
        </div>
        <Badge tone={clusterMode ? 'info' : 'neutral'}>{clusterMode ? 'cluster' : 'local-development'}</Badge>
      </div>

      {engines.length && engines[0]?.note ? <p className="text-xs italic muted">{engines[0].note}</p> : null}

      {/* (a) data lake */}
      <Panel title="Data lake" description="Storage roots and partitions as this deployment reports them.">
        {lakeState.loading ? <TableSkeleton rows={3} columns={3} /> : null}
        {lakeState.error ? (
          <ErrorState error={lakeState.error} onRetry={() => void lakeState.refetch()} />
        ) : null}
        {lake && lake.roots.length === 0 ? (
          <EmptyState
            icon={<Database className="h-6 w-6" />}
            tone="warning"
            title="No data lake roots are available"
            description={
              <>
                <span>{lake.message ?? 'No readable storage roots were found for the current mode.'}</span>
                {setupInstructions(engines) ? (
                  <span className="mt-1 block text-xs">
                    Setup guidance: {setupInstructions(engines)}
                  </span>
                ) : null}
              </>
            }
            action={
              <Link to="/app/processing" className="btn btn-secondary btn-sm">
                Check engine health
              </Link>
            }
          />
        ) : null}
        {lake && lake.roots.length ? (
          <div className="space-y-4">
            <div className="table-wrap">
              <table className="data">
                <caption className="sr-only">Data lake storage roots</caption>
                <thead>
                  <tr>
                    <th>Path</th>
                    <th className="text-right">Files</th>
                    <th className="text-right">Size</th>
                  </tr>
                </thead>
                <tbody>
                  {lake.roots.map((root) => (
                    <tr key={root.path}>
                      <td className="mono text-xs break-all">{root.path}</td>
                      <td className="text-right mono">{formatNumber(root.files, 0)}</td>
                      <td className="text-right mono">{formatBytes(root.bytes)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {lake.partitions > 0 ? (
              <p className="text-xs subtle">
                Partitions recorded here: <span className="mono">{formatNumber(lake.partitions, 0)}</span>
              </p>
            ) : (
              <p className="text-xs subtle">No partitions reported for this mode. {lake.message || ''}</p>
            )}
          </div>
        ) : null}
      </Panel>

      {/* (b) big-data workflows + run tracking */}
      <Panel title="Distributed workflows" description="MapReduce and Spark jobs. Starting a job polls its status every 5 seconds until it finishes.">
        {workflowsState.loading ? <p className="text-xs subtle">Loading workflows…</p> : null}
        {workflowsState.error ? <ErrorState error={workflowsState.error} onRetry={() => void workflowsState.refetch()} /> : null}
        {!workflowsState.loading && bigDataWorkflows.length === 0 && !workflowsState.error ? (
          <EmptyState title="No distributed workflows registered" description="No map/reduce or Spark workflows are available in this deployment." />
        ) : null}
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {bigDataWorkflows.map((workflow) => (
            <li key={workflow.id} className="card-tight flex flex-col gap-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{workflow.label}</span>
                <Badge tone="neutral">{workflow.engine}</Badge>
              </div>
              <p className="flex-1 text-xs muted">{workflow.description}</p>
              {workflow.available ? (
                canRun ? (
                  <button type="button" className="btn btn-primary btn-sm" onClick={() => setPendingWorkflow(workflow)}>
                    <PlayCircle className="h-4 w-4" aria-hidden="true" /> Run
                  </button>
                ) : (
                  <button type="button" className="btn btn-secondary btn-sm" disabled title="Requires the processing:run capability (analyst or administrator).">
                    Run
                  </button>
                )
              ) : (
                <p className="rounded-lg px-2 py-1.5 text-xs" style={{ backgroundColor: 'var(--surface-muted)' }}>
                  <span className="muted">Unavailable:</span> {workflow.unavailable_reason ?? 'no reason given.'}
                </p>
              )}
            </li>
          ))}
        </ul>

        {trackedJobId ? (
          <div className="mt-4 rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
            {tracked.loading ? (
              <p className="text-xs subtle">Loading job {trackedJobId}…</p>
            ) : tracked.error ? (
              <ErrorState error={tracked.error} onRetry={() => void tracked.refetch()} />
            ) : tracked.data ? (
              <TrackedJob job={tracked.data} onClear={() => setTrackedJobId(null)} />
            ) : null}
          </div>
        ) : null}
      </Panel>

      {/* (c) parquet / conversion */}
      <Panel title="Parquet & aggregation outputs" description="Workflows that convert or aggregate the lake into columnar files.">
        {parquetWorkflows.length ? (
          <ul className="space-y-2">
            {parquetWorkflows.map((workflow) => (
              <li key={workflow.id} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2" style={{ borderColor: 'var(--border)' }}>
                <span className="flex items-center gap-2 min-w-0">
                  <FileStack className="h-4 w-4 shrink-0 text-[var(--brand)]" aria-hidden="true" />
                  <span className="min-w-0">
                    <span className="block truncate text-sm">{workflow.label}</span>
                    <span className="mono block text-xs subtle">{workflow.id} · {workflow.engine}</span>
                  </span>
                </span>
                {workflow.available ? (
                  canRun ? (
                    <button type="button" className="btn btn-secondary btn-sm" onClick={() => setPendingWorkflow(workflow)}>Run</button>
                  ) : (
                    <Badge tone="neutral">read-only</Badge>
                  )
                ) : (
                  <Badge tone="warning">unavailable</Badge>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState
            title="No Parquet/conversion workflows found"
            description="None of the registered workflows target columnar conversion or aggregation. Outputs, when produced, are listed under the processing jobs."
          />
        )}
      </Panel>

      {/* (e) docs */}
      <Panel title="Documentation">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="muted">
            The repository guide covers the overall platform; the pipeline, deployment and machine-learning packages each have their own
            setup notes. Ask your administrator for the copy shipped with this release.
          </span>
        </div>
      </Panel>

      {pendingWorkflow ? (
        <RunWorkflowDialog
          workflow={pendingWorkflow}
          onClose={() => setPendingWorkflow(null)}
          onStarted={(job) => {
            toast.success('Workflow started', `Job ${job.id} is ${job.status}.`)
            setTrackedJobId(job.id)
            setPendingWorkflow(null)
          }}
        />
      ) : null}
    </div>
  )
}

// helpers

function setupInstructions(engines: EngineHealth[]): string | null {
  const engine = engines[0]
  if (!engine) return null
  const hints = [
    engine.hdfs?.detail,
    !engine.hdfs?.configured ? 'HDFS is not configured, so the lake falls back to local storage.' : null,
    engine.spark?.error ?? (!engine.spark?.installed ? 'Spark is not installed on this node.' : null),
    engine.hadoop?.detail ?? (!engine.hadoop?.cluster_configured ? 'No Hadoop cluster is configured.' : null),
  ].filter(Boolean)
  return hints.length ? hints.join(' ') : null
}

// tracked job

function TrackedJob({ job, onClear }: { job: ProcessingJob; onClear: () => void }) {
  const running = ACTIVE.has(job.status)
  const metrics = job.metrics
  const rejected = numberOrNull(metrics?.rejected)
  const mode = job.processing_mode || textMetric(metrics, 'processing_mode') || 'unknown'
  const outputPaths = [
    ...stringList(job.output?.data_lake_uris),
    ...stringList(job.output?.parquet_paths),
    ...stringList(job.output?.summary_keys),
  ]
  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2">
          <StatusBadge value={job.status} kind="job" />
          <span className="mono text-xs subtle">{job.id}</span>
          {running ? <Badge tone="info">live · 5s</Badge> : null}
        </span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClear}>Clear</button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Cell label="Engine" value={job.engine} />
        <Cell label="Mode" value={mode} />
        <Cell label="Rows in" value={formatNumber(numberOrNull(metrics?.rows_in), 0)} />
        <Cell label="Rows out" value={formatNumber(numberOrNull(metrics?.rows_out), 0)} />
        <Cell label="Duration" value={formatDuration(numberOrNull(job.duration_seconds ?? metrics?.duration_seconds))} />
        <Cell label="Rejected" value={formatNumber(rejected, 0)} tone={rejected ? 'danger' : undefined} />
      </div>

      {job.error_message ? <p className="rounded-lg border border-alert-high/40 bg-alert-high/10 px-3 py-2 text-alert-high">{job.error_message}</p> : null}

      {outputPaths.length ? (
        <div className="text-xs">
          <span className="muted">Outputs</span>
          <ul className="mono mt-1 space-y-0.5">
            {outputPaths.map((entry) => <li key={entry} className="break-all">{entry}</li>)}
          </ul>
        </div>
      ) : running ? (
        <p className="text-xs subtle">No outputs yet. The job is still running.</p>
      ) : (
        <p className="text-xs subtle">This job produced no recorded output paths.</p>
      )}
    </div>
  )
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: 'danger' }) {
  return (
    <div className="card-tight p-2">
      <span className="text-xs muted">{label}</span>
      <p className={'mono text-sm ' + (tone === 'danger' ? 'text-alert-high' : '')}>{value}</p>
    </div>
  )
}

// run dialog (params)

function RunWorkflowDialog({
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

  const entries = Object.entries(workflow.parameter_spec ?? {})

  return (
    <Modal
      open
      title={`Run “${workflow.label}”`}
      description={workflow.description || 'Set parameters, then launch the distributed job.'}
      onClose={onClose}
      footer={
        <div className="flex justify-end gap-2">
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={submitting} onClick={submit}>
            {submitting ? 'Starting…' : 'Start job'}
          </button>
        </div>
      }
    >
      <div className="space-y-4">
        {!entries.length ? <p className="text-xs subtle">This workflow takes no parameters.</p> : null}
        {entries.map(([key, spec]) => {
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
              <Field key={key} label={label} htmlFor={`bd-${key}`} required={spec.required} hint={spec.help}>
                <select id={`bd-${key}`} className="select" value={String(values[key] ?? '')} onChange={(event) => setValue(key, event.target.value)}>
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
              <Field key={key} label={label} htmlFor={`bd-${key}`} required={spec.required} hint={spec.help}>
                <input id={`bd-${key}`} type="date" className="input" value={String(values[key] ?? '')} onChange={(event) => setValue(key, event.target.value || undefined)} />
              </Field>
            )
          }
          const numeric = spec.type === 'int' || spec.type === 'float'
          return (
            <Field key={key} label={label} htmlFor={`bd-${key}`} required={spec.required} hint={spec.help ?? (numeric ? `min ${spec.min ?? '—'} · max ${spec.max ?? '—'}` : undefined)}>
              <input
                id={`bd-${key}`}
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
