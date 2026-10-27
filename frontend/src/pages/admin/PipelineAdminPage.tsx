import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Boxes, CircleSlash, Play, RefreshCw, RotateCcw, Server } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Hint,
  Modal,
  PageHeader,
  Panel,
  Pagination,
  RefreshButton,
  Select,
  StatusBadge,
  TableSkeleton,
  type Column,
  type Option,
} from '../../components/ui'
import { ActionFeedback, Breakdown, CodeCell, DurationCell, MetadataView, type Feedback } from './components/AdminBits'
import { PAGE_SIZE, describeError, formatDuration, noteFrom, unwrapOne } from './components/adminUtils'
import { formatBytes, formatDate, formatNumber, humanise, relativeTime } from '../../lib/format'
import type { EngineHealth, IngestionJob, ProcessingJob, ProcessingWorkflow, ReportRecord } from '../../types/api'

type JobKind = 'ingestion' | 'processing' | 'report'

interface UnifiedJob {
  kind: JobKind
  id: string
  label: string
  subject: string
  datasetName?: string | null
  datasetId?: string | null
  statusValue: string
  badgeValue: string
  startedAt?: string | null
  finishedAt?: string | null
  durationSeconds?: number | null
  requestedBy?: string | null
  rowsIn?: number | null
  rowsOut?: number | null
  accepted?: number | null
  rejected?: number | null
  skipped?: number | null
  duplicateCount?: number | null
  retryCount?: number
  maxRetries?: number
  errorMessage?: string | null
  engine?: string | null
  executor?: string | null
  parameters?: Record<string, unknown>
  ingestion?: IngestionJob
  processing?: ProcessingJob
  report?: ReportRecord
}

/** Report statuses are their own vocabulary; map them onto job tones for the badge. */
function reportBadgeValue(status: string): string {
  if (status === 'generated') return 'completed'
  if (status === 'expired') return 'cancelled'
  return status
}

/**
 * Job error text is shown as returned. The API never emits stack traces
 * (`backend/common/middleware.py` converts unhandled errors into the standard envelope)
 * and the audit layer redacts secret-looking fields, so there is nothing to scrub
 * client-side beyond collapsing whitespace; the raw string is kept in a title attribute.
 */
function displayError(message?: string | null): string {
  if (!message) return ''
  return message.replace(/\s+/g, ' ').trim().slice(0, 400)
}

/**
 * Elapsed seconds between two timestamps. Only computed when the server stored BOTH —
 * an unfinished or clock-less run shows "—" rather than an implied duration.
 */
function elapsedSeconds(start?: string | null, end?: string | null): number | null {
  if (!start || !end) return null
  const from = Date.parse(start)
  const to = Date.parse(end)
  if (Number.isNaN(from) || Number.isNaN(to)) return null
  return Math.max(0, Math.round((to - from) / 1000))
}

/** Processing metrics are an open record; only genuine numbers are read out of it. */
function metricNumber(metrics: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = metrics?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function toUnified(job: IngestionJob): UnifiedJob {
  return {
    kind: 'ingestion',
    id: job.id,
    label: job.source_name,
    subject: `${humanise(job.job_type)} · ${job.source_name}`,
    datasetId: job.dataset_id,
    statusValue: job.status,
    badgeValue: job.status,
    startedAt: job.started_at,
    finishedAt: job.completed_at,
    durationSeconds: elapsedSeconds(job.started_at, job.completed_at),
    requestedBy: job.requested_by ?? null,
    rowsIn: job.records_fetched,
    accepted: job.records_accepted,
    rejected: job.records_rejected,
    skipped: job.records_skipped,
    duplicateCount: job.duplicate_count,
    retryCount: job.retry_count,
    maxRetries: job.max_retries,
    errorMessage: job.error_message,
    executor: job.executor,
    parameters: job.parameters,
    ingestion: job,
  }
}

function toUnifiedProcessing(job: ProcessingJob): UnifiedJob {
  return {
    kind: 'processing',
    id: job.id,
    label: job.workflow_label || job.workflow,
    subject: `${job.engine} · ${job.workflow}`,
    datasetName: job.dataset_name,
    datasetId: job.dataset_id,
    statusValue: job.status,
    badgeValue: job.status,
    startedAt: job.started_at ?? job.submitted_at,
    finishedAt: job.finished_at,
    durationSeconds: job.duration_seconds ?? metricNumber(job.metrics, 'duration_seconds') ?? elapsedSeconds(job.started_at ?? job.submitted_at, job.finished_at),
    requestedBy: job.submitted_by_name ?? job.submitted_by,
    rowsIn: metricNumber(job.metrics, 'rows_in'),
    rowsOut: metricNumber(job.metrics, 'rows_out'),
    accepted: metricNumber(job.metrics, 'accepted'),
    rejected: metricNumber(job.metrics, 'rejected'),
    duplicateCount: metricNumber(job.metrics, 'duplicates'),
    errorMessage: job.error_message,
    engine: job.engine,
    parameters: job.parameters,
    processing: job,
  }
}

function toUnifiedReport(report: ReportRecord): UnifiedJob {
  return {
    kind: 'report',
    id: report.id,
    label: report.title || report.report_type,
    subject: `${report.report_type} · ${report.format}`,
    statusValue: report.status,
    badgeValue: reportBadgeValue(report.status),
    startedAt: report.created_at,
    finishedAt: report.completed_at,
    durationSeconds: elapsedSeconds(report.started_at, report.completed_at),
    rowsOut: report.row_count ?? null,
    requestedBy: report.requested_by,
    errorMessage: report.error_message,
    executor: report.executor,
    parameters: report.filters,
    report,
  }
}

const KIND_LABEL: Record<JobKind, string> = { ingestion: 'Ingestion', processing: 'Processing', report: 'Report' }

/** Status histogram of the rows currently loaded — derived from real API data only. */
function countByStatus(statuses: (string | undefined)[] | undefined): Record<string, number> {
  const counts: Record<string, number> = {}
  ;(statuses ?? []).forEach((value) => {
    if (!value) return
    counts[value] = (counts[value] ?? 0) + 1
  })
  return counts
}

/**
 * Pipelines & jobs.
 *
 * One table over the three job stores the platform actually runs:
 * `GET /api/v1/ingestion/jobs`, `GET /api/v1/processing/` and `GET /api/v1/reports`.
 * Rows are re-read every 5 seconds while anything is queued or running, and the
 * background executor is reported exactly as `GET /api/v1/health` describes it.
 */
export default function PipelineAdminPage() {
  const { can } = useAuth()
  const canRunIngestion = can('ingestion:run')
  const canRunProcessing = can('processing:run')
  const canManageReports = can('platform:manage')

  const [kind, setKind] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<UnifiedJob | null>(null)
  const [confirmCancel, setConfirmCancel] = useState<UnifiedJob | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState(false)

  const ingestion = useAsync(
    () =>
      api.ingestion.list({
        page: kind === '' || kind === 'ingestion' ? page : 1,
        page_size: PAGE_SIZE,
        status: status || undefined,
        // The client documents `source`; the Django view reads `source_name`.
        source: undefined,
      }),
    [page, status, kind],
    { interval: 5_000 },
  )

  const processing = useAsync(
    () =>
      kind === 'report' || kind === 'ingestion'
        ? Promise.resolve(null)
        : api.processing.list({ page, page_size: PAGE_SIZE, status: status || undefined }),
    [page, status, kind],
    { interval: 5_000 },
  )

  const reports = useAsync(
    () =>
      kind !== '' && kind !== 'report'
        ? Promise.resolve(null)
        : api.reports.list({ page, page_size: PAGE_SIZE, status: status || undefined }),
    [page, status, kind],
    { interval: 5_000 },
  )

  const health = useAsync(() => api.system.health(), [], { interval: 30_000 })
  const workflows = useAsync(() => api.processing.workflows(), [], { enabled: canRunProcessing })
  const engines = useAsync(() => api.processing.engines(), [])

  const rows = useMemo<UnifiedJob[]>(() => {
    const merged: UnifiedJob[] = [
      ...(ingestion.data?.items ?? []).map(toUnified),
      ...(processing.data?.items ?? []).map(toUnifiedProcessing),
      ...(reports.data?.items ?? []).map(toUnifiedReport),
    ]
    if (kind === 'ingestion' || kind === 'processing' || kind === 'report') {
      return merged.filter((row) => row.kind === kind)
    }
    return merged
  }, [ingestion.data, processing.data, reports.data, kind])

  const hasActive = rows.some((row) => row.statusValue === 'queued' || row.statusValue === 'running')

  useEffect(() => {
    setPage(1)
  }, [kind, status])

  const meta = useMemo(() => {
    const primary = kind === 'processing' ? processing.data : kind === 'report' ? reports.data : ingestion.data
    if (!primary) return undefined
    if (kind !== '') return { ...primary, items: [] as never[] }
    const totals = [ingestion.data, processing.data, reports.data].filter(Boolean) as { total: number; page: number; pages: number; has_next: boolean; has_previous: boolean }[]
    if (totals.length === 0) return undefined
    return {
      page: totals[0].page,
      page_size: PAGE_SIZE,
      total: totals.reduce((sum, entry) => sum + entry.total, 0),
      pages: Math.max(...totals.map((entry) => Math.max(entry.pages, 1))),
      has_next: totals.some((entry) => entry.has_next),
      has_previous: totals.some((entry) => entry.has_previous),
      items: [] as never[],
    }
  }, [kind, ingestion.data, processing.data, reports.data])

  const reloadAll = useCallback(async () => {
    setFeedback(null)
    await Promise.all([ingestion.refetch(), processing.refetch(), reports.refetch(), health.refetch()])
  }, [ingestion, processing, reports, health])

  /** Row mutation: show the server’s own wording, then re-read (never optimistic state). */
  const mutate = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true)
      setFeedback(null)
      try {
        const raw = await action()
        const note = noteFrom(raw)
        setFeedback({ tone: 'success', text: `${success}${note ? ` ${note}` : ''}` })
        return true
      } catch (cause) {
        const described = describeError(cause)
        setFeedback({
          tone: 'error',
          text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`,
        })
        return false
      } finally {
        setBusy(false)
        await Promise.all([ingestion.refetch(), processing.refetch(), reports.refetch()])
      }
    },
    [ingestion, processing, reports],
  )

  const columns = useMemo<Column<UnifiedJob>[]>(
    () => [
      {
        key: 'kind',
        header: 'Kind',
        cell: (row) => (
          <Badge tone={row.kind === 'ingestion' ? 'info' : row.kind === 'processing' ? 'success' : 'neutral'}>{KIND_LABEL[row.kind]}</Badge>
        ),
      },
      {
        key: 'job',
        header: 'Job',
        cell: (row) => (
          <div className="min-w-0">
            <button type="button" className="link truncate text-sm font-medium" onClick={() => setSelected(row)}>
              {row.label}
            </button>
            <p className="truncate text-[11px] subtle">{row.subject}</p>
          </div>
        ),
      },
      {
        key: 'dataset',
        header: 'Dataset / engine',
        cell: (row) => (
          <span className="text-xs muted">
            {row.datasetName ?? (row.engine ? humanise(row.engine) : '—')}
            {row.datasetName && row.engine ? <span className="subtle"> · {humanise(row.engine)}</span> : null}
          </span>
        ),
      },
      { key: 'status', header: 'Status', cell: (row) => <StatusBadge value={row.badgeValue} kind="job" label={humanise(row.statusValue)} /> },
      {
        key: 'started',
        header: 'Started',
        cell: (row) => (
          <span className="text-xs muted" title={row.startedAt ? formatDate(row.startedAt, true) : undefined}>
            {relativeTime(row.startedAt)}
          </span>
        ),
      },
      { key: 'duration', header: 'Duration', align: 'right', cell: (row) => <DurationCell seconds={row.durationSeconds} /> },
      {
        key: 'rows',
        header: 'Rows',
        align: 'right',
        cell: (row) => (
          <div className="text-right text-[11px] muted">
            {row.rowsIn !== null && row.rowsIn !== undefined ? <span title="records fetched / rows in">{formatNumber(row.rowsIn, 0)} in</span> : null}
            {row.rowsOut !== null && row.rowsOut !== undefined ? <span> · {formatNumber(row.rowsOut, 0)} out</span> : null}
            {row.rejected ? <span className="text-alert-high"> · {formatNumber(row.rejected, 0)} rejected</span> : null}
            {row.rowsIn == null && row.rowsOut == null ? <span className="subtle">not reported</span> : null}
          </div>
        ),
      },
      {
        key: 'retry',
        header: 'Retries',
        align: 'right',
        cell: (row) =>
          row.kind === 'ingestion' ? (
            <span className="mono text-[12px]">
              {row.retryCount ?? 0}
              {row.maxRetries ? `/${row.maxRetries}` : ''}
            </span>
          ) : (
            <span className="text-[11px] subtle">
              {row.processing?.attempt ? `${row.processing.attempt}` : '—'}
            </span>
          ),
      },
      {
        key: 'error',
        header: 'Error',
        cell: (row) =>
          row.errorMessage ? (
            <p className="max-w-[16rem] truncate text-[11px] font-medium text-alert-critical" title={row.errorMessage}>
              {displayError(row.errorMessage)}
            </p>
          ) : (
            <span className="text-[11px] subtle">—</span>
          ),
      },
      {
        key: 'actions',
        header: 'Actions',
        align: 'right',
        cell: (row) => {
          const cancellable = row.statusValue === 'queued' || row.statusValue === 'running'
          const retryable = row.kind === 'ingestion' && (row.statusValue === 'failed' || row.statusValue === 'cancelled')
          const permissionMissing =
            row.kind === 'ingestion' ? !canRunIngestion : row.kind === 'processing' ? !canRunProcessing : !canManageReports
          // The server only accepts a report cancellation while the record is still queued (409 otherwise).
          const reportCancellable = row.kind === 'report' && row.statusValue === 'queued'
          return (
            <div className="flex justify-end gap-1">
              <button
                type="button"
                className="btn btn-secondary btn-icon"
                aria-label={`Inspect ${row.label}`}
                title="Open the run detail panel (parameters, counters, logs)"
                onClick={() => setSelected(row)}
              >
                <RefreshCw className="h-3.5 w-3.5" />
              </button>
              {row.kind === 'ingestion' ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-icon"
                  aria-label={`Retry ${row.label}`}
                  disabled={!canRunIngestion || busy || !retryable}
                  title={
                    !canRunIngestion
                      ? 'Requires the ingestion:run capability'
                      : !retryable
                        ? 'The server only retries failed or cancelled jobs (409 otherwise)'
                        : 'POST /api/v1/ingestion/jobs/<id>/retry'
                  }
                  onClick={() => void mutate(() => api.ingestion.retry(row.id), `Retry requested for ${row.label}.`)}
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                </button>
              ) : null}
              <button
                type="button"
                className="btn btn-secondary btn-icon"
                aria-label={`Cancel ${row.label}`}
                disabled={
                  (row.kind === 'report' ? !reportCancellable : !cancellable) || permissionMissing || busy
                }
                title={
                  row.kind === 'report'
                    ? !canManageReports
                      ? 'Requires the platform:manage capability'
                      : !reportCancellable
                        ? 'The server refuses with 409 once generation has started or the record is terminal. Only a queued report can be cancelled'
                        : 'POST /api/v1/reports/<id>/cancel'
                    : !cancellable
                      ? 'This job already reached a terminal state'
                      : permissionMissing
                        ? row.kind === 'ingestion'
                          ? 'Requires the ingestion:run capability'
                          : 'Requires the processing:run capability'
                        : row.kind === 'ingestion'
                          ? 'Request cancellation (the worker checks cancel_requested between batches)'
                          : 'POST /api/v1/processing/<id>/cancel'
                }
                onClick={() => setConfirmCancel(row)}
              >
                <CircleSlash className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        },
      },
    ],
    [busy, canRunIngestion, canRunProcessing, canManageReports, mutate],
  )

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Pipelines & jobs"
        description="Ingestion runs, processing workflows and report generation in one queue view, with the real background executor underneath them."
        meta={
          <>
            <Badge tone={hasActive ? 'info' : 'neutral'}>{hasActive ? 'live polling every 5 s' : 'idle, polling every 5 s'}</Badge>
            <span className="subtle">{rows.length} job(s) on this page</span>
            {health.data ? (
              <Badge tone={health.data.status === 'ok' ? 'success' : 'warning'}>
                API {health.data.status} · {humanise(health.data.background.executor)}
              </Badge>
            ) : null}
          </>
        }
        actions={<RefreshButton onClick={() => void reloadAll()} busy={ingestion.refreshing || processing.refreshing || reports.refreshing} />}
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />

      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Ingestion queue" description="GET /api/v1/ingestion/jobs" dense>
          {ingestion.error ? <ErrorState error={ingestion.error} onRetry={() => void ingestion.refetch()} /> : null}
          <Breakdown counts={countByStatus(ingestion.data?.items.map((job) => job.status))} empty="No ingestion job on this page." />
          <ul className="mt-2 space-y-1 text-xs muted">
            <li className="flex justify-between"><span>Jobs on page</span><span className="mono">{(ingestion.data?.items ?? []).length}</span></li>
            <li className="flex justify-between"><span>Total reported</span><span className="mono">{ingestion.data ? formatNumber(ingestion.data.total, 0) : '—'}</span></li>
            <li className="flex justify-between"><span>Executor</span><span className="mono">{health.data?.background.executor ?? 'unknown'}</span></li>
          </ul>
        </Panel>
        <Panel title="Processing queue" description="GET /api/v1/processing/" dense>
          {processing.error ? <ErrorState error={processing.error} onRetry={() => void processing.refetch()} /> : null}
          {processing.data === null && !processing.error ? (
            <p className="text-xs subtle">{processing.loading ? 'Reading processing jobs…' : 'Not queried; the current filter excludes processing jobs.'}</p>
          ) : null}
          <Breakdown counts={countByStatus(processing.data?.items.map((job) => job.status))} empty="No processing job on this page." />
          <ul className="mt-2 space-y-1 text-xs muted">
            <li className="flex justify-between"><span>Jobs on page</span><span className="mono">{(processing.data?.items ?? []).length}</span></li>
            <li className="flex justify-between"><span>Total reported</span><span className="mono">{processing.data ? formatNumber(processing.data.total, 0) : '—'}</span></li>
            <li className="flex justify-between"><span>Active / queued</span><span className="mono">{health.data ? `${health.data.background.active_jobs} / ${health.data.background.queued_jobs}` : '—'}</span></li>
          </ul>
        </Panel>
        <Panel title="Report generation" description="GET /api/v1/reports" dense>
          {reports.error ? <ErrorState error={reports.error} onRetry={() => void reports.refetch()} /> : null}
          {reports.data === null && !reports.error ? (
            <p className="text-xs subtle">{reports.loading ? 'Reading reports…' : 'Not queried; the current filter excludes report jobs.'}</p>
          ) : null}
          <Breakdown counts={countByStatus(reports.data?.items.map((report) => report.status))} kind="generic" empty="No report record on this page." />
          <ul className="mt-2 space-y-1 text-xs muted">
            <li className="flex justify-between"><span>Reports on page</span><span className="mono">{(reports.data?.items ?? []).length}</span></li>
            <li className="flex justify-between"><span>Total reported</span><span className="mono">{reports.data ? formatNumber(reports.data.total, 0) : '—'}</span></li>
            <li className="flex justify-between"><span>Active model</span><span className="mono">{health.data?.processing_mode ?? '—'}</span></li>
          </ul>
        </Panel>
      </div>

      <FilterBar className="mt-6">
        <Field label="Kind" htmlFor="pipeline-kind">
          <Select
            id="pipeline-kind"
            ariaLabel="Filter by job kind"
            placeholder="All kinds"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'ingestion', label: 'Ingestion only' },
              { value: 'processing', label: 'Processing only' },
              { value: 'report', label: 'Reports only' },
            ]}
          />
        </Field>
        <Field label="Status" htmlFor="pipeline-status">
          <Select
            id="pipeline-status"
            ariaLabel="Filter by status"
            placeholder="Any status"
            value={status}
            onChange={setStatus}
            options={[
              { value: 'queued', label: 'Queued' },
              { value: 'running', label: 'Running' },
              { value: 'completed', label: 'Completed' },
              { value: 'completed_with_errors', label: 'Completed with errors' },
              { value: 'failed', label: 'Failed' },
              { value: 'cancelled', label: 'Cancelled' },
              { value: 'generated', label: 'Generated (reports)' },
              { value: 'expired', label: 'Expired (reports)' },
            ]}
          />
        </Field>
        <p className="ml-auto max-w-md text-[11px] leading-relaxed subtle">
          A status that one store does not use simply returns nothing; the filters are passed through to each endpoint rather than applied
          locally.
        </p>
      </FilterBar>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel title="Jobs" description="Newest first; the three stores are polled on the same 5-second cadence while anything is active." dense>
          {ingestion.loading && rows.length === 0 ? <TableSkeleton rows={6} columns={9} /> : null}
          <DataTable
            caption="All platform jobs"
            columns={columns}
            rows={rows}
            rowKey={(row) => `${row.kind}-${row.id}`}
            empty="No job has been recorded by the endpoints this page can reach."
            onRowClick={(row) => setSelected(row)}
          />
          <Pagination meta={meta ?? undefined} onPage={setPage} label="jobs" />
          <Hint>
            Cancelling sets <span className="mono">cancel_requested</span> and the worker observes it between batches, so a running job may
            take a moment to settle. Report records only accept cancellation while <span className="mono">queued</span>. Once generation has
            started, the server answers 409.
          </Hint>
        </Panel>

        <div className="min-w-0 space-y-6">
          <WorkflowLauncher
            workflows={workflows.data ?? []}
            loading={workflows.loading}
            error={workflows.error}
            disabled={!canRunProcessing}
            busy={busy}
            onReload={() => void workflows.refetch()}
            onStarted={(message) => {
              setFeedback({ tone: 'success', text: message })
              void processing.refetch()
              void health.refetch()
            }}
            onFailed={(message) => setFeedback({ tone: 'error', text: message })}
          />

          <ExecutorPanel
            health={health.data}
            error={health.error}
            onRetry={() => void health.refetch()}
            workflowIds={(workflows.data ?? []).map((entry) => entry.id)}
          />

          <EnginePanel
            engines={engines.data}
            loading={engines.loading}
            error={engines.error}
            onRetry={() => void engines.refetch()}
          />
        </div>
      </div>

      {selected ? (
        <JobDetailModal
          job={selected}
          onClose={() => setSelected(null)}
          canRunIngestion={canRunIngestion}
          busy={busy}
          onRetry={async () => {
            const done = await mutate(() => api.ingestion.retry(selected.id), `Retry requested for ${selected.label}.`)
            if (done) setSelected(null)
          }}
        />
      ) : null}

      <ConfirmDialog
        open={Boolean(confirmCancel)}
        title="Cancel this job?"
        tone="danger"
        busy={busy}
        confirmLabel="Cancel job"
        onClose={() => setConfirmCancel(null)}
        onConfirm={() => {
          const target = confirmCancel
          setConfirmCancel(null)
          if (!target) return
          const action =
            target.kind === 'ingestion'
              ? () => api.ingestion.cancel(target.id)
              : target.kind === 'processing'
                ? () => api.processing.cancel(target.id)
                : () => api.reports.cancel(target.id)
          void mutate(
            action,
            target.kind === 'report'
              ? `Cancellation requested for ${target.label}. The server only accepts this while the report is queued; the status will update once it settles.`
              : `Cancellation requested for ${target.label}. The status will change when the worker observes it.`,
          )
        }}
        message={
          confirmCancel?.kind === 'report' ? (
            <span>
              <strong>{confirmCancel.label}</strong> is still queued, so <span className="mono">POST /api/v1/reports/&lt;id&gt;/cancel</span>{' '}
              can stop it before any artefact is written. If generation already started, the server answers 409 and nothing changes. The
              run is recorded as <strong>cancelled</strong> in the audit trail.
            </span>
          ) : (
            <span>
              <strong>{confirmCancel?.label}</strong> ({confirmCancel ? KIND_LABEL[confirmCancel.kind] : ''}) will stop as soon as the worker
              checks its cancellation flag. Work already committed stays committed: ingested observations and written Parquet files are not
              rolled back. The run is recorded as <strong>cancelled</strong> in the audit trail.
            </span>
          )
        }
      />
    </div>
  )
}

/* ---------------------------------------------------------- workflow launcher */

interface ParameterField {
  name: string
  type: string
  required: boolean
  label: string
  help?: string
  min?: number
  max?: number
  choices?: string[]
  default?: unknown
}

function fieldsFrom(spec: ProcessingWorkflow['parameter_spec'] | undefined): ParameterField[] {
  return Object.entries(spec ?? {}).map(([name, rules]) => ({
    name,
    type: rules.type,
    required: Boolean(rules.required),
    label: rules.label ?? humanise(name),
    help: rules.help,
    min: rules.min,
    max: rules.max,
    choices: rules.choices,
    default: rules.default,
  }))
}

function WorkflowLauncher({
  workflows,
  loading,
  error,
  disabled,
  busy,
  onReload,
  onStarted,
  onFailed,
}: {
  workflows: ProcessingWorkflow[]
  loading: boolean
  error: unknown
  disabled: boolean
  busy: boolean
  onReload: () => void
  onStarted: (message: string) => void
  onFailed: (message: string) => void
}) {
  const [workflowId, setWorkflowId] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})
  const [confirm, setConfirm] = useState(false)

  const chosen = useMemo(() => workflows.find((entry) => entry.id === workflowId) ?? null, [workflows, workflowId])
  const fields = useMemo(() => fieldsFrom(chosen?.parameter_spec), [chosen])

  useEffect(() => {
    setValues(Object.fromEntries(fieldsFrom(chosen?.parameter_spec).map((field) => [field.name, field.default === undefined || field.default === null ? '' : String(field.default)])))
  }, [chosen])

  const options: Option[] = workflows.map((entry) => ({
    value: entry.id,
    label: `${entry.label} · ${entry.engine}${entry.available ? '' : ' (unavailable)'}`,
    hint: entry.unavailable_reason ?? entry.description,
  }))

  async function launch() {
    if (!chosen) return
    const params: Record<string, unknown> = {}
    fields.forEach((field) => {
      const raw = values[field.name] ?? ''
      if (raw === '') return
      if (field.type === 'int') params[field.name] = Number.parseInt(raw, 10)
      else if (field.type === 'float') params[field.name] = Number.parseFloat(raw)
      else if (field.type === 'bool') params[field.name] = raw === 'true'
      else params[field.name] = raw
    })
    const missing = fields.filter((field) => field.required && params[field.name] === undefined)
    if (missing.length > 0) {
      onFailed(`Missing required parameter(s): ${missing.map((field) => field.label).join(', ')}.`)
      return
    }
    try {
      const raw = await api.processing.start({ workflow: chosen.id, parameters: params })
      const job = unwrapOne<ProcessingJob>(raw)
      onStarted(
        `${chosen.label} queued on ${chosen.engine}${job?.id ? ` (job ${job.id})` : ''}. Progress follows the 5-second poll; the engine used is the one the server reports, not a guess.`,
      )
      setConfirm(false)
      setWorkflowId('')
    } catch (cause) {
      const described = describeError(cause)
      onFailed(`${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`)
      setConfirm(false)
    }
  }

  return (
    <Panel
      title="Run a processing workflow"
      description="GET /api/v1/processing/workflows → POST /api/v1/processing/. Engines and parameters are the server’s own catalogue."
      dense
      actions={<RefreshButton onClick={onReload} busy={loading} label="Reload" />}
    >
      {error ? <ErrorState error={error} /> : null}
      {loading && workflows.length === 0 ? <p className="text-xs muted">Reading the workflow catalogue…</p> : null}
      {!loading && workflows.length === 0 && !error ? (
        <p className="text-xs subtle">The workflow catalogue returned no entries, so there is nothing to run.</p>
      ) : null}

      <div className="space-y-3">
        <Field label="Workflow" htmlFor="workflow-select" hint={chosen?.description ?? 'Each entry carries the engine it needs and why it may be unavailable.'}>
          <Select id="workflow-select" options={options} value={workflowId} onChange={setWorkflowId} placeholder={loading ? 'Loading…' : 'Select a workflow'} disabled={disabled || workflows.length === 0} />
        </Field>

        {chosen && !chosen.available ? (
          <p className="rounded-xl border border-solar-300 bg-solar-50/50 p-2.5 text-xs leading-relaxed text-solar-700 dark:bg-solar-950/20 dark:text-solar-300">
            {chosen.unavailable_reason ?? 'This workflow cannot run on the current configuration.'} Required: {chosen.requires.join(', ') || '—'}
          </p>
        ) : null}

        {chosen ? (
          <div className="grid gap-3 sm:grid-cols-2">
            {fields.map((field) => (
              <Field key={field.name} label={field.label} htmlFor={`wf-${field.name}`} required={field.required} hint={field.help ?? `${field.type}${field.min !== undefined ? ` ≥ ${field.min}` : ''}${field.max !== undefined ? ` ≤ ${field.max}` : ''}`}>
                {field.type === 'bool' ? (
                  <Select
                    id={`wf-${field.name}`}
                    value={values[field.name] ?? ''}
                    onChange={(next) => setValues((current) => ({ ...current, [field.name]: next }))}
                    options={[
                      { value: 'true', label: 'Yes' },
                      { value: 'false', label: 'No' },
                    ]}
                  />
                ) : field.choices?.length ? (
                  <Select
                    id={`wf-${field.name}`}
                    value={values[field.name] ?? ''}
                    onChange={(next) => setValues((current) => ({ ...current, [field.name]: next }))}
                    options={field.choices.map((choice) => ({ value: choice, label: choice }))}
                    placeholder="Select…"
                  />
                ) : (
                  <input
                    id={`wf-${field.name}`}
                    className="input"
                    type={field.type === 'int' || field.type === 'float' ? 'number' : field.type === 'date' ? 'date' : 'text'}
                    min={field.min}
                    max={field.max}
                    step={field.type === 'float' ? '0.01' : field.type === 'int' ? '1' : undefined}
                    value={values[field.name] ?? ''}
                    onChange={(event) => setValues((current) => ({ ...current, [field.name]: event.target.value }))}
                  />
                )}
              </Field>
            ))}
            {fields.length === 0 ? <p className="text-xs subtle">This workflow declares no parameters. It runs with the defaults the engine applies.</p> : null}
            <p className="text-xs muted sm:col-span-2">
              Engine <span className="mono">{chosen.engine}</span> · requires {chosen.requires.length ? chosen.requires.join(', ') : 'nothing extra'}
            </p>
          </div>
        ) : null}

        <button
          type="button"
          className="btn btn-primary"
          disabled={disabled || !chosen || !chosen.available || busy}
          title={disabled ? 'Requires the processing:run capability' : !chosen ? 'Select a workflow first' : !chosen.available ? chosen.unavailable_reason ?? 'Unavailable' : 'Queue this workflow'}
          onClick={() => setConfirm(true)}
        >
          <Play className="h-4 w-4" /> Run workflow
        </button>
      </div>

      <ConfirmDialog
        open={confirm}
        title={`Run ${chosen?.label ?? 'workflow'}?`}
        busy={busy}
        confirmLabel="Start run"
        onClose={() => setConfirm(false)}
        onConfirm={() => void launch()}
        message={
          <span>
            This starts a background run on the <strong>{chosen?.engine}</strong> engine and consumes compute on the configured
            cluster. Parameters sent: <span className="mono">{chosen && fields.length ? fields.map((field) => `${field.name}=${values[field.name] || '—'}`).join(', ') : 'none'}</span>. The job is
            audited as <span className="mono">processing.job_created</span> and appears in the queue below.
          </span>
        }
      />
    </Panel>
  )
}

/* ------------------------------------------------------------ executor panel */

function ExecutorPanel({
  health,
  error,
  onRetry,
  workflowIds,
}: {
  health: { background: { executor: string; active_jobs: number; queued_jobs: number }; processing_mode: string; schema_version: number } | null
  error: unknown
  onRetry: () => void
  workflowIds: string[]
}) {
  const tasks = useMemo(
    () => [
      { name: 'ingestion.run_api_sync', purpose: 'fetch a live source into a dataset (NASA POWER, NOAA, live API)' },
      { name: 'ingestion.run_file_import', purpose: 'stream an uploaded CSV/JSON/XLSX/Parquet file into observations' },
      { name: 'report.generate', purpose: 'build a report artefact in the data lake' },
      ...workflowIds.map((id) => ({ name: `processing.${id}`, purpose: 'processing workflow catalogue entry (engine chosen by the server)' })),
    ],
    [workflowIds],
  )

  return (
    <Panel
      title="Background execution"
      description="GET /api/v1/health → background.executor. Only names registered in common/tasks.py REGISTRY can run."
      dense
      actions={<RefreshButton onClick={onRetry} busy={!health && Boolean(error)} label="Reload" />}
      footer={
        <Hint>
          The API never accepts a shell command or a module path from the browser: the executor resolves a registered task name, and an
          unregistered name is refused before anything is queued.
        </Hint>
      }
    >
      {error && !health ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {health ? (
        <>
          <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
            <div className="flex items-center justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
              <dt className="subtle">Executor</dt>
              <dd><Badge tone="info">{humanise(health.background.executor)}</Badge></dd>
            </div>
            <div className="flex items-center justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
              <dt className="subtle">Active jobs</dt>
              <dd className="mono">{health.background.active_jobs}</dd>
            </div>
            <div className="flex items-center justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
              <dt className="subtle">Queued jobs</dt>
              <dd className="mono">{health.background.queued_jobs}</dd>
            </div>
            <div className="flex items-center justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
              <dt className="subtle">Processing mode</dt>
              <dd className="mono">{health.processing_mode}</dd>
            </div>
          </dl>
          {health.background.executor === 'in-process-thread-pool' ? (
            <p className="mt-2 text-[11px] leading-relaxed subtle">
              Runs execute in a bounded thread pool inside the web process, so an in-flight run is lost when the process restarts and the
              pool does not stretch across multiple workers.
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-xs subtle">{error ? 'The health endpoint has not answered, so the executor is unknown.' : 'Reading health…'}</p>
      )}

      <p className="panel-title mt-4 mb-1">Runnable task names</p>
      <ul className="space-y-1 text-xs muted">
        {tasks.map((task) => (
          <li key={task.name} className="flex flex-wrap items-baseline gap-2">
            <Server className="mt-0.5 h-3 w-3 shrink-0 text-[var(--text-subtle)]" aria-hidden="true" />
            <span className="mono">{task.name}</span>
            <span className="subtle">— {task.purpose}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-[11px] leading-relaxed subtle">
        Processing registrations are discovered when <span className="mono">apps/processing</span> loads; the ids above come from the live
        catalogue, not from a hardcoded list.
      </p>
    </Panel>
  )
}

/* -------------------------------------------------------------- engine panel */

interface EngineRow {
  component: string
  configured: string
  detail: string
  endpoint?: string | null
}

function normalizeEngines(list: EngineHealth[]): EngineRow[] {
  const typed = list[0]
  if (!typed) return []
  const rows: EngineRow[] = []
  if (typed.hdfs) rows.push({ component: 'HDFS', configured: typed.hdfs.configured ? (typed.hdfs.reachable ? 'configured, reachable' : 'configured, unreachable') : 'not configured', detail: typed.hdfs.detail ?? `mode: ${typed.hdfs.mode}`, endpoint: typed.hdfs.endpoint })
  if (typed.spark) rows.push({ component: 'Spark', configured: typed.spark.installed ? (typed.spark.session_startable ? 'installed, session startable' : 'installed, session fails') : 'not installed', detail: typed.spark.error ?? `version: ${typed.spark.version ?? 'unknown'}` })
  if (typed.hadoop) rows.push({ component: 'Hadoop / MapReduce', configured: typed.hadoop.bin_configured ? (typed.hadoop.cluster_configured ? 'binaries + cluster configured' : 'binaries only') : 'not configured', detail: typed.hadoop.detail ?? `version: ${typed.hadoop.version ?? 'unknown'}` })
  if (typed.data_lake) rows.push({ component: 'Data lake', configured: `${typed.data_lake.mode} ${typed.data_lake.reachable ? 'reachable' : 'unreachable'}`, detail: `${typed.data_lake.root ?? 'root not reported'}${typeof typed.data_lake.free_bytes === 'number' ? ` · ${formatBytes(typed.data_lake.free_bytes)} free` : ' · free space not collected'}` })
  return rows
}

function EnginePanel({ engines, loading, error, onRetry }: { engines: EngineHealth[] | null; loading: boolean; error: unknown; onRetry: () => void }) {
  const rows = useMemo(() => normalizeEngines(engines ?? []), [engines])
  const note = engines?.[0]?.note

  return (
    <Panel
      title="Compute engines"
      description="GET /api/v1/processing/engines. Shows what the server probed for Hadoop, Spark and the data lake."
      dense
      actions={<RefreshButton onClick={onRetry} busy={loading} label="Probe" />}
    >
      {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
      {loading && rows.length === 0 ? <p className="text-xs muted">Probing engines…</p> : null}
      {!loading && rows.length === 0 && !error ? <p className="text-xs subtle">The engines endpoint returned no probe results.</p> : null}
      <ul className="space-y-2">
        {rows.map((row) => (
          <li key={row.component} className="rounded-xl border p-2.5 text-xs" style={{ borderColor: 'var(--border)' }}>
            <div className="flex flex-wrap items-center gap-2">
              <Boxes className="h-3.5 w-3.5 text-[var(--text-subtle)]" aria-hidden="true" />
              <span className="font-medium">{row.component}</span>
              <Badge
                tone={row.configured.startsWith('not') || row.configured === 'disabled' ? 'neutral' : row.configured.includes('unreachable') || row.configured.includes('fails') ? 'danger' : 'success'}
              >
                {humanise(row.configured)}
              </Badge>
              {row.endpoint ? <code className="kbd">{row.endpoint}</code> : null}
            </div>
            {row.detail ? <p className="mt-1 leading-relaxed muted">{row.detail}</p> : null}
          </li>
        ))}
      </ul>
      {note ? <p className="mt-2 text-[11px] leading-relaxed subtle">{note}</p> : null}
    </Panel>
  )
}

/* --------------------------------------------------------------- job detail */

function JobDetailModal({ job, onClose, canRunIngestion, busy, onRetry }: { job: UnifiedJob; onClose: () => void; canRunIngestion: boolean; busy: boolean; onRetry: () => Promise<void> }) {
  const logs = useAsync(
    () => (job.kind === 'processing' ? api.processing.logs(job.id) : Promise.resolve(null)),
    [job.id, job.kind],
    { enabled: job.kind === 'processing' },
  )

  const logLines = useMemo(() => {
    if (logs.data) return logs.data.lines.map((line) => ({ at: line.timestamp, level: line.level, message: line.message }))
    if (job.ingestion?.log_lines) return job.ingestion.log_lines
    if (job.processing?.log_lines) return job.processing.log_lines
    return []
  }, [logs.data, job.ingestion, job.processing])

  return (
    <Modal open width="max-w-2xl" title={`${KIND_LABEL[job.kind]} run: ${job.label}`} description={`Record id ${job.id}. Counters and timings are exactly what the API reported.`} onClose={onClose}>
      <div className="space-y-4 text-sm">
        <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
          <DetailLine label="Status" value={<StatusBadge value={job.badgeValue} kind="job" label={humanise(job.statusValue)} />} />
          <DetailLine label="Started" value={job.startedAt ? formatDate(job.startedAt, true) : '—'} />
          <DetailLine label="Finished" value={job.finishedAt ? formatDate(job.finishedAt, true) : 'not finished'} />
          <DetailLine label="Duration" value={formatDuration(job.durationSeconds)} />
          <DetailLine label="Requested by" value={job.requestedBy ?? 'not recorded'} />
          <DetailLine label="Executor / engine" value={`${job.executor ?? '—'} / ${job.engine ?? '—'}`} />
          {job.rowsIn !== null && job.rowsIn !== undefined ? <DetailLine label="Fetched / rows in" value={formatNumber(job.rowsIn, 0)} /> : null}
          {job.rowsOut !== null && job.rowsOut !== undefined ? <DetailLine label="Rows out / records" value={formatNumber(job.rowsOut, 0)} /> : null}
          {job.accepted !== null && job.accepted !== undefined ? <DetailLine label="Accepted" value={formatNumber(job.accepted, 0)} /> : null}
          {job.rejected !== null && job.rejected !== undefined ? <DetailLine label="Rejected" value={formatNumber(job.rejected, 0)} /> : null}
          {job.skipped !== null && job.skipped !== undefined ? <DetailLine label="Skipped" value={formatNumber(job.skipped, 0)} /> : null}
          {job.duplicateCount !== null && job.duplicateCount !== undefined ? <DetailLine label="Duplicates" value={formatNumber(job.duplicateCount, 0)} /> : null}
          {job.report ? <DetailLine label="File size" value={job.report.file_size_bytes ? formatBytes(job.report.file_size_bytes) : 'not generated'} /> : null}
          {job.report ? <DetailLine label="Expires" value={job.report.expires_at ? formatDate(job.report.expires_at, true) : 'no expiry reported'} /> : null}
        </dl>

        {job.errorMessage ? (
          <div role="alert" className="rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-xs" title={job.errorMessage}>
            <p className="panel-title mb-1 text-alert-critical">Failure reported by the server</p>
            <p className="break-words leading-relaxed">{displayError(job.errorMessage)}</p>
            <p className="mt-1 subtle">Rendered as returned; the API wraps unhandled errors in its standard envelope, so no stack trace reaches the browser.</p>
          </div>
        ) : null}

        {job.ingestion?.validation_errors && job.ingestion.validation_errors.length > 0 ? (
          <div>
            <p className="panel-title mb-1">Validation rejections</p>
            <ul className="space-y-1 text-xs muted">
              {job.ingestion.validation_errors.slice(0, 12).map((issue, index) => (
                <li key={`${issue.code}-${index}`} className="flex flex-wrap items-start gap-2">
                  <Badge tone={issue.severity === 'error' ? 'danger' : issue.severity === 'warning' ? 'warning' : 'neutral'}>{issue.severity}</Badge>
                  <span>{issue.field ? <span className="mono">{issue.field}: </span> : null}{issue.message}{issue.count ? ` ×${issue.count}` : ''}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {job.report?.insufficient_data ? (
          <p className="rounded-xl border p-3 text-xs leading-relaxed" style={{ borderColor: 'var(--border)' }}>
            The report generator reported insufficient data{job.report.reason ? `: ${job.report.reason}` : '.'} No
            numbers were produced rather than estimated.
          </p>
        ) : null}

        <div>
          <p className="panel-title mb-1">Parameters sent</p>
          <MetadataView metadata={job.parameters} />
        </div>

        <div>
          <p className="panel-title mb-1">Worker log</p>
          {logs.error ? <ErrorState error={logs.error} onRetry={() => void logs.refetch()} /> : null}
          {logLines.length === 0 ? <p className="text-xs subtle">{logs.loading ? 'Reading log lines…' : 'No log line was stored for this run.'}</p> : null}
          <ul className="max-h-64 space-y-1 overflow-auto rounded-lg border p-2.5 text-[11px]" style={{ borderColor: 'var(--border)' }}>
            {logLines.slice(-40).map((line, index) => (
              <li key={`${line.at}-${index}`} className="flex flex-wrap gap-2">
                <span className="mono subtle">{line.at ? relativeTime(line.at) : ''}</span>
                <Badge tone={line.level === 'error' ? 'danger' : line.level === 'warning' ? 'warning' : 'neutral'}>{line.level}</Badge>
                <span className="min-w-0 flex-1 break-words muted">{line.message}</span>
              </li>
            ))}
          </ul>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <CodeCell value={job.id} label="job id" />
          {job.report && job.report.status === 'generated' ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={() => {
                const report = job.report
                if (!report) return
                void api.reports.download(report.id, report.download_name ?? `${report.title || report.report_type}.pdf`)
              }}
            >
              Download artefact
            </button>
          ) : null}
          {job.kind === 'ingestion' ? (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              disabled={!canRunIngestion || busy || (job.statusValue !== 'failed' && job.statusValue !== 'cancelled')}
              title={!canRunIngestion ? 'Requires the ingestion:run capability' : job.statusValue !== 'failed' && job.statusValue !== 'cancelled' ? 'Only failed or cancelled runs can be retried' : 'Re-queue this run'}
              onClick={() => void onRetry()}
            >
              <RotateCcw className="h-3.5 w-3.5" /> Retry run
            </button>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}

function DetailLine({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="text-right text-xs muted">{value}</dd>
    </div>
  )
}
