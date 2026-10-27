import { useEffect, useRef } from 'react'
import api from '../../../../services/api'
import { useAsync } from '../../../../hooks/useAsync'
import { formatNumber, relativeTime } from '../../../../lib/format'
import { Badge, ErrorState, Panel, Progress, Skeleton, StatusBadge } from '../../../../components/ui'
import type { ProcessingJob } from '../../../../types/api'
import { numberOrNull } from '../data/normalise'

/**
 * Polls a ProcessingJob every 5s while it is queued/running and renders only what the
 * API returns — engine, processing_mode, stage (last log line), durations and row
 * counts. A job that does not publish a progress percentage is shown with its stage
 * text and counters instead.
 */

const ACTIVE: ProcessingJob['status'][] = ['queued', 'running']

/** `ProcessingJob.output` is `Record<string, unknown>`; only string arrays are rendered. */
function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []
}

function textMetric(metrics: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = metrics?.[key]
  return typeof value === 'string' && value ? value : null
}

export function JobMonitor({
  jobId,
  label = 'Job progress',
  onFinished,
}: {
  jobId: string | null
  label?: string
  onFinished?: (job: ProcessingJob) => void
}) {
  const terminal = useRef<Set<string>>(new Set())
  const state = useAsync(
    () => api.processing.get(jobId as string),
    [jobId],
    { enabled: Boolean(jobId), interval: 5000 },
  )
  const job = state.data

  useEffect(() => {
    if (job && !ACTIVE.includes(job.status) && !terminal.current.has(job.id)) {
      terminal.current.add(job.id)
      onFinished?.(job)
    }
    // only react to a completed transition; onFinished is a stable-enough callback
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status, job?.id])

  if (!jobId) return null

  if (state.loading && !job) {
    return (
      <Panel title={label}>
        <Skeleton className="h-4 w-40" />
        <div className="mt-3 space-y-2">
          <Skeleton className="h-3 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      </Panel>
    )
  }
  if (state.error && !job) {
    return <ErrorState error={state.error} onRetry={() => void state.refetch()} />
  }
  if (!job) return null

  const lastLog = job.log_lines?.length ? job.log_lines[job.log_lines.length - 1].message : null
  const rowsIn = numberOrNull(job.metrics?.rows_in)
  const rowsOut = numberOrNull(job.metrics?.rows_out)
  const accepted = numberOrNull(job.metrics?.accepted)
  const rejected = numberOrNull(job.metrics?.rejected)
  const duplicates = numberOrNull(job.metrics?.duplicates)
  const peakMemoryMb = numberOrNull(job.metrics?.peak_memory_mb)
  const metricsMode = textMetric(job.metrics, 'processing_mode')
  const lakeOutputs = stringList(job.output?.data_lake_uris)
  const running = ACTIVE.includes(job.status)

  return (
    <Panel
      title={label}
      description={job.workflow_label ?? job.workflow}
      actions={
        <div className="flex items-center gap-2">
          <StatusBadge value={job.status} kind="job" />
          {running ? <span className="text-xs subtle">polling every 5s</span> : null}
        </div>
      }
      footer={
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>Submitted {relativeTime(job.submitted_at)}</span>
          {job.started_at ? <span>Started {relativeTime(job.started_at)}</span> : null}
          {job.finished_at ? <span>Finished {relativeTime(job.finished_at)}</span> : null}
          {job.duration_seconds !== null && job.duration_seconds !== undefined ? (
            <span>Duration {formatNumber(job.duration_seconds, 1)}s</span>
          ) : null}
          {state.refreshing ? <span>Updating…</span> : null}
        </span>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">engine: {job.engine}</Badge>
          {job.processing_mode ? <Badge tone="info">mode: {job.processing_mode}</Badge> : null}
          {metricsMode ? <Badge tone="info">metrics mode: {metricsMode}</Badge> : null}
          {job.dataset_name ? <Badge tone="neutral">{job.dataset_name}</Badge> : null}
        </div>

        {running ? (
          <div>
            {rowsIn !== null && rowsOut !== null ? (
              <Progress value={Math.min(100, (rowsOut / Math.max(rowsIn, 1)) * 100)} label="processed rows" />
            ) : null}
            <p className="mt-2 text-xs muted">{lastLog ?? (job.status === 'queued' ? 'Waiting for a worker slot…' : 'Running…')}</p>
          </div>
        ) : null}

        <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
          <Metric label="Rows in" value={formatNumber(rowsIn, 0)} />
          <Metric label="Rows out" value={formatNumber(rowsOut, 0)} />
          <Metric label="Accepted" value={formatNumber(accepted, 0)} />
          <Metric label="Rejected" value={formatNumber(rejected, 0)} />
          <Metric label="Duplicates" value={formatNumber(duplicates, 0)} />
          <Metric label="Peak memory" value={peakMemoryMb === null ? '—' : `${formatNumber(peakMemoryMb, 0)} MB`} />
        </dl>

        {job.error_message ? (
          <p className="rounded-lg border px-3 py-2 text-xs text-alert-critical" style={{ borderColor: 'var(--border)' }} role="alert">
            {job.error_message}
          </p>
        ) : null}

        {lakeOutputs.length ? (
          <p className="text-xs subtle">
            Output: <span className="mono">{lakeOutputs.join(', ')}</span>
          </p>
        ) : null}
      </div>
    </Panel>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="mono mt-0.5">{value}</dd>
    </div>
  )
}

export default JobMonitor
