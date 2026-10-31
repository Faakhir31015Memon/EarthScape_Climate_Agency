/**
 * Dataset detail — the full audit trail for one source: metadata, its validation
 * report, the ingestion jobs it spawned (polled while any is active), and a
 * sample of stored observations. Mutations are capability-gated and destructive
 * ones confirmed.
 */
import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Archive, RefreshCw } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import type { ConnectorStatus, Dataset, IngestionJob, Observation } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { formatDate, formatMetric, formatNumber, metricLabel } from '../../lib/format'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  KeyValue,
  Panel,
  Progress,
  RefreshButton,
  StatusBadge,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { CardSkeleton } from '../../components/ui/Skeleton'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import { asList, numberOrNull } from './components/data/normalise'

const ACTIVE = new Set(['queued', 'running'])

export default function DatasetDetailPage() {
  const { datasetId = '' } = useParams()
  const toast = useToast()
  const { can, hasRole } = useAuth()

  const [confirmArchive, setConfirmArchive] = useState(false)
  const [busy, setBusy] = useState(false)

  // `GET /datasets/{id}` returns `{dataset}` — the client unwraps it, so data is the dataset.
  const state = useAsync(() => api.datasets.get(datasetId), [datasetId])
  const dataset = state.data

  const jobsState = useAsync(() => api.ingestion.list({ dataset_id: datasetId, page: 1, page_size: 20 }), [datasetId])
  const jobs = useMemo(() => jobsState.data?.items ?? [], [jobsState.data])
  const hasActiveJob = jobs.some((job) => ACTIVE.has(job.status))

  const jobsPoll = useAsync(
    () => api.ingestion.list({ dataset_id: datasetId, page: 1, page_size: 20 }),
    [datasetId],
    { enabled: hasActiveJob, interval: 5000 },
  )
  const liveJobs = hasActiveJob ? jobsPoll.data?.items ?? jobs : jobs

  // `GET /climate/observations` filters datasets through `?datasets=`.
  const observationsState = useAsync(
    () => api.climate.observations({ datasets: datasetId, page: 1, page_size: 10 }),
    [datasetId],
  )

  // A sync can only be offered for a source the backend actually exposes as a connector.
  const connectorsState = useAsync(() => api.ingestion.connectors(), [])
  const connector = useMemo<ConnectorStatus | null>(() => {
    if (!dataset?.source_type) return null
    return (
      asList<ConnectorStatus>(connectorsState.data, 'connectors').find(
        (item) => item.source_type === dataset.source_type,
      ) ?? null
    )
  }, [connectorsState.data, dataset?.source_type])

  const notFound = state.error?.code === 'not_found'

  if (notFound) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="Data pipeline" title="Dataset not found" backTo="/app/datasets" />
        <Panel>
          <EmptyState
            title="This dataset does not exist"
            description={`No dataset was found for "${datasetId}". It may have been deleted, or the link is out of date.`}
            tone="warning"
            action={
              <Link to="/app/datasets" className="btn btn-primary btn-sm">
                Back to the library
              </Link>
            }
          />
        </Panel>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title={dataset?.name ?? 'Dataset'}
        backTo="/app/datasets"
        description={dataset?.description || undefined}
        meta={
          dataset ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatusBadge value={dataset.status} kind="dataset" />
              <Badge tone="neutral">{dataset.source_type?.replace(/_/g, ' ')}</Badge>
              <span className="mono subtle">{dataset.id}</span>
            </div>
          ) : null
        }
        actions={
          <div className="flex items-center gap-2">
            <RefreshButton
              onClick={() => {
                void state.refetch()
                void jobsState.refetch()
                void observationsState.refetch()
              }}
              busy={state.refreshing}
            />
            {dataset ? (
              <SyncButton
                enabled={can('ingestion:run')}
                connector={connector}
                connectorPending={connectorsState.loading && !connectorsState.data}
                datasetId={datasetId}
                onDone={() => {
                  void jobsState.refetch()
                  void jobsPoll.refetch()
                }}
              />
            ) : null}
            {can('datasets:manage') && hasRole('administrator') ? (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setConfirmArchive(true)}
                disabled={dataset?.status === 'archived'}
              >
                <Archive className="h-4 w-4" aria-hidden="true" /> Archive
              </button>
            ) : null}
          </div>
        }
      />

      {state.loading && !dataset ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <CardSkeleton lines={6} />
          <CardSkeleton lines={6} />
        </div>
      ) : null}
      {state.error && !notFound ? (
        <Panel>
          <ErrorState error={state.error} onRetry={() => void state.refetch()} />
        </Panel>
      ) : null}

      {dataset ? (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="Metadata">
              <KeyValue
                columns={2}
                items={[
                  { label: 'Source', value: `${dataset.source_name} (${dataset.source_type?.replace(/_/g, ' ')})` },
                  { label: 'Format', value: dataset.format },
                  { label: 'Records', value: <span className="mono">{formatNumber(dataset.record_count, 0)}</span> },
                  { label: 'Schema version', value: <span className="mono">v{dataset.schema_version}</span> },
                  {
                    label: 'Temporal coverage',
                    value: dataset.temporal_coverage?.start || dataset.temporal_coverage?.end ? (
                      <span className="mono">
                        {dataset.temporal_coverage?.start ?? '…'} → {dataset.temporal_coverage?.end ?? '…'}
                      </span>
                    ) : (
                      '—'
                    ),
                  },
                  {
                    label: 'Geographic coverage',
                    value: dataset.geographic_coverage?.regions?.length
                      ? dataset.geographic_coverage.regions.join(', ')
                      : dataset.geographic_coverage?.location_codes?.length
                        ? `${dataset.geographic_coverage.location_codes.length} locations`
                        : 'Global / unspecified',
                  },
                  { label: 'Created', value: formatDate(dataset.created_at, true) },
                  {
                    // The API returns only the uploader's user id; no display name is resolvable here.
                    label: 'Uploaded by (user id)',
                    value: dataset.uploaded_by ? <span className="mono break-all">{dataset.uploaded_by}</span> : '—',
                  },
                  { label: 'Stored file', value: dataset.file_name ? <span className="mono break-all">{dataset.file_name}</span> : 'No file stored for this source' },
                  dataset.source_url ? { label: 'Source URL', value: <span className="mono break-all">{dataset.source_url}</span> } : null,
                  dataset.storage_uri ? { label: 'Storage URI', value: <span className="mono break-all">{dataset.storage_uri}</span> } : null,
                ].flatMap((item) => (item ? [item] : []))}
              />
              <div className="mt-4 flex flex-wrap gap-2">
                <Link
                  to={`/app/analytics?metric=${encodeURIComponent('temperature_c')}`}
                  className="btn btn-secondary btn-sm"
                >
                  Analyse in Analytics
                </Link>
                <Link
                  to={`/app/history?${dataset.geographic_coverage?.location_codes?.length ? `location=${dataset.geographic_coverage.location_codes[0]}&` : ''}metric=temperature_c`}
                  className="btn btn-ghost btn-sm"
                >
                  View raw observations
                </Link>
                {dataset.tags?.length ? (
                  <div className="flex flex-wrap gap-1">
                    {dataset.tags.map((tag) => (
                      <Badge key={tag} tone="info">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                ) : null}
              </div>
            </Panel>

            <ValidationPanel dataset={dataset} />
          </div>

          <Panel
            title="Ingestion jobs"
            description={hasActiveJob ? 'A job is running. This table refreshes every 5 seconds.' : 'Jobs recorded for this dataset.'}
            actions={hasActiveJob ? <Badge tone="info">live · 5s</Badge> : undefined}
          >
            <DataTable
              columns={jobColumns}
              rows={liveJobs}
              rowKey={(row) => row.id}
              loading={jobsState.loading}
              compact
              empty={
                jobsState.error ? (
                  <ErrorState error={jobsState.error} onRetry={() => void jobsState.refetch()} />
                ) : (
                  <EmptyState
                    title="No ingestion jobs yet"
                    description="This dataset has not been ingested. Records will appear once a sync or import runs."
                    action={
                      can('ingestion:run') ? (
                        <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                          Start a job
                        </Link>
                      ) : undefined
                    }
                  />
                )
              }
            />
          </Panel>

          <Panel title="Sample observations" description="The first stored records for this dataset.">
            <DataTable
              columns={observationColumns}
              rows={observationsState.data?.items ?? []}
              rowKey={(row, index) => row.id ?? `${row.observation_date}-${row.location_code}-${index}`}
              loading={observationsState.loading}
              compact
              empty={
                observationsState.error ? (
                  <ErrorState error={observationsState.error} onRetry={() => void observationsState.refetch()} />
                ) : (
                  <EmptyState
                    title="No stored observations"
                    description="No records are in the store for this dataset yet. Ingestion must complete before data appears here."
                    tone="warning"
                    action={
                      <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                        Go to ingestion
                      </Link>
                    }
                  />
                )
              }
            />
          </Panel>
        </>
      ) : null}

      <ConfirmDialog
        open={confirmArchive}
        tone="danger"
        title="Archive this dataset?"
        message="Archiving is the only way to remove a dataset here. It's administrator-only and soft: the record, its stored file and any observations it produced stay in storage and remain referenced by past jobs."
        confirmLabel="Archive"
        busy={busy}
        onClose={() => setConfirmArchive(false)}
        onConfirm={() => {
          setBusy(true)
          api.datasets
            .archive(datasetId)
            .then(() => {
              toast.success('Dataset archived', dataset?.name)
              void state.refetch()
              setConfirmArchive(false)
            })
            .catch((error: unknown) => toast.danger('Could not archive', error instanceof ApiRequestError ? error.message : String(error)))
            .finally(() => setBusy(false))
        }}
      />
    </div>
  )
}

// validation report reading

/**
 * `dataset.validation_report` is a free-form `Record<string, unknown>`: the backend
 * stores whatever the importer's `build_validation_report` produced. Every field is
 * therefore read defensively, and anything the report does not carry is stated as
 * absent rather than defaulted.
 */
function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function readRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function readRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return []
  return value.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === 'object' && !Array.isArray(row))
}

interface IssueRow {
  severity: string
  message: string
  field: string | null
  code: string
  count: number | null
  row: number | null
}

function readIssues(value: unknown): IssueRow[] {
  return readRows(value)
    .map((issue) => ({
      severity: readString(issue.severity) ?? 'info',
      message: readString(issue.message) ?? '',
      field: readString(issue.field),
      code: readString(issue.code) ?? '',
      count: numberOrNull(issue.count),
      row: numberOrNull(issue.row),
    }))
    .filter((issue) => issue.message.length > 0)
}

function issueTone(severity: string): 'danger' | 'warning' | 'info' {
  return severity === 'error' ? 'danger' : severity === 'warning' ? 'warning' : 'info'
}

function formatOrDash(value: number | null): string {
  return value === null ? '—' : formatNumber(value, 0)
}

function ValidationPanel({ dataset }: { dataset: Dataset }) {
  const report = readRecord(dataset.validation_report)
  if (!report) {
    return (
      <Panel title="Validation report">
        <EmptyState
          title="No validation report"
          description="This source was registered without a stored validation report, typically an API or pre-validated dataset. Quality is reported by the ingestion job."
        />
      </Panel>
    )
  }

  const counts = readRecord(report.counts)
  const fetched = numberOrNull(counts?.fetched)
  const accepted = numberOrNull(counts?.accepted)
  const rejected = numberOrNull(counts?.rejected)
  const skipped = numberOrNull(counts?.skipped_in_batch_duplicates)
  const deduped = numberOrNull(counts?.duplicates_against_database)
  const newRecords = numberOrNull(counts?.new_records)
  const errorCount = numberOrNull(report.error_count)
  const issues = readIssues(report.validation_errors)
  const fileError = readString(report.error)
  const abortedReason = readString(report.aborted_reason)
  const generatedAt = readString(report.generated_at)
  const formatDetected = readString(report.format_detected)
  const reconciliation = readString(report.reconciliation)
  const truncated = report.truncated === true
  const sampleAccepted = readRows(report.sample_accepted)
  const sampleRejected = readRows(report.sample_rejected)
  const headerCount = Array.isArray(report.headers) ? report.headers.length : null

  const unreadable = Boolean(fileError)
  const verdictTone: 'success' | 'warning' | 'danger' = unreadable
    ? 'danger'
    : rejected !== null && rejected > 0
      ? 'warning'
      : accepted !== null && accepted > 0
        ? 'success'
        : 'warning'
  const verdictLabel = unreadable
    ? 'file unreadable'
    : accepted === null
      ? 'no counts recorded'
      : accepted > 0
        ? rejected && rejected > 0
          ? 'accepted with rejects'
          : 'accepted'
        : 'nothing accepted'

  const severityCounts = issues.reduce<Record<string, number>>((acc, issue) => {
    acc[issue.severity] = (acc[issue.severity] ?? 0) + 1
    return acc
  }, {})

  return (
    <Panel
      title="Validation report"
      footer={
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <Badge tone={verdictTone}>{verdictLabel}</Badge>
          <span className="mono">
            fetched {formatOrDash(fetched)} · accepted {formatOrDash(accepted)} · rejected {formatOrDash(rejected)}
          </span>
          {errorCount !== null ? <span className="mono">{formatNumber(errorCount, 0)} distinct issues</span> : null}
          <span className="subtle">{generatedAt ? `recorded ${formatDate(generatedAt, true)}` : 'no generation timestamp stored'}</span>
        </div>
      }
    >
      {fileError ? (
        <p className="rounded-lg border border-alert-high/40 bg-alert-high/10 px-3 py-2 text-sm text-alert-high">
          <span className="font-semibold">The stored file could not be read:</span> {fileError}
        </p>
      ) : null}
      {abortedReason ? (
        <p className="mt-3 rounded-lg border border-solar-300 bg-solar-50/60 px-3 py-2 text-sm dark:border-solar-700 dark:bg-solar-950/20">
          <span className="font-semibold">Run aborted:</span> {abortedReason}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {formatDetected ? <Badge tone="neutral">format: {formatDetected}</Badge> : null}
        {truncated ? <Badge tone="warning">report truncated</Badge> : null}
        {reconciliation ? <span className="subtle">{reconciliation}</span> : null}
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        {issues.length ? (
          Object.entries(severityCounts).map(([severity, count]) => (
            <Badge key={severity} tone={issueTone(severity)}>
              {count} {severity}
            </Badge>
          ))
        ) : (
          <Badge tone={unreadable ? 'danger' : 'success'}>{unreadable ? 'no issues parsed' : 'no issues recorded'}</Badge>
        )}
      </div>

      {issues.length ? (
        <ul className="mt-3 space-y-1 text-xs">
          {issues.slice(0, 12).map((issue, index) => (
            <li key={`${issue.code}-${issue.field ?? 'scope'}-${index}`} className="flex flex-wrap items-start gap-2">
              <Badge tone={issueTone(issue.severity)}>{issue.severity}</Badge>
              <span className="min-w-0 flex-1">
                {issue.code ? <span className="mono subtle">{issue.code}</span> : null}{' '}
                {issue.field ? <span className="mono subtle">{issue.field}</span> : null} {issue.message}
                {issue.count !== null ? <span className="subtle"> ({formatNumber(issue.count, 0)} rows)</span> : null}
                {issue.row !== null ? <span className="subtle"> (row {formatNumber(issue.row, 0)})</span> : null}
              </span>
            </li>
          ))}
          {issues.length > 12 ? <li className="subtle">… {formatNumber(issues.length - 12, 0)} more issues in the report.</li> : null}
        </ul>
      ) : null}

      {newRecords !== null || deduped !== null || skipped !== null ? (
        <p className="mt-3 text-xs muted">
          new records {formatOrDash(newRecords)} · updated against the database {formatOrDash(deduped)} · skipped as in-batch
          duplicates {formatOrDash(skipped)}
        </p>
      ) : null}

      {headerCount !== null ? (
        <p className="mt-2 text-xs subtle">
          <span className="font-semibold">{formatNumber(headerCount, 0)}</span> headers were profiled by the importer.
        </p>
      ) : null}

      {sampleAccepted.length || sampleRejected.length ? (
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <PreviewTable title="Accepted rows (sample stored with the report)" rows={sampleAccepted} empty="No accepted-row sample is stored." />
          <PreviewTable title="Rejected rows (sample stored with the report)" rows={sampleRejected} empty="No rejected-row sample is stored." />
        </div>
      ) : (
        <p className="mt-3 text-xs subtle">
          This report stores counts and issue summaries, no row samples, so there's nothing to preview.
        </p>
      )}
    </Panel>
  )
}

function PreviewTable({ title, rows, empty }: { title: string; rows: Record<string, unknown>[]; empty: string }) {
  const headers = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys(row)))).slice(0, 5), [rows])
  return (
    <div>
      <p className="panel-title mb-1">{title}</p>
      {!rows.length ? (
        <p className="text-xs subtle">{empty}</p>
      ) : (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                {headers.map((header) => (
                  <th key={header}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 5).map((row, index) => (
                <tr key={index}>
                  {headers.map((header) => (
                    <td key={header} className="mono text-xs">
                      {String(row[header] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// sync button

/**
 * `POST /ingestion/jobs` takes a connector name, not a dataset source type, and
 * answers with a queue acknowledgement (`job_id`), never a job document.
 */
function SyncButton({
  enabled,
  connector,
  connectorPending,
  datasetId,
  onDone,
}: {
  enabled: boolean
  connector: ConnectorStatus | null
  connectorPending: boolean
  datasetId: string
  onDone: () => void
}) {
  const toast = useToast()
  const [pending, setPending] = useState(false)

  const unavailable = connectorPending
    ? 'Checking the connector catalogue…'
    : !connector
      ? 'This dataset’s source type has no connector configured here, so a sync cannot be requested.'
      : !connector.configured
        ? connector.setup_hint || 'This source is not configured on the server, so a sync would fail.'
        : null
  const canStart = enabled && !unavailable
  const title = !enabled
    ? 'Running ingestion requires the ingestion:run capability (analyst or administrator).'
    : (unavailable ?? `Queue a fresh ${connector?.connector} sync job against this dataset.`)

  return (
    <button
      type="button"
      className="btn btn-primary btn-sm"
      disabled={!canStart || pending}
      title={title}
      onClick={() => {
        if (!canStart || !connector) return
        setPending(true)
        api.ingestion
          .start({ source: connector.connector, dataset_id: datasetId, triggered_by: 'manual' })
          .then((ack) => {
            toast.success('Sync queued', `Job ${ack.job_id} was accepted with status ${ack.status}.`)
            onDone()
          })
          .catch((error: unknown) => toast.danger('Sync failed to start', error instanceof ApiRequestError ? error.message : String(error)))
          .finally(() => setPending(false))
      }}
    >
      <RefreshCw className="h-4 w-4" aria-hidden="true" /> {pending ? 'Starting…' : 'Re-run sync'}
    </button>
  )
}

// table columns

function jobProgress(job: IngestionJob): number | null {
  if (job.status === 'completed') return 100
  const fetched = numberOrNull(job.records_fetched)
  if (!fetched) return null
  const processed = (numberOrNull(job.records_accepted) ?? 0) + (numberOrNull(job.records_rejected) ?? 0) + (numberOrNull(job.records_skipped) ?? 0)
  if (processed <= 0) return 0
  return Math.min(100, Math.round((processed / fetched) * 100))
}

const jobColumns: Column<IngestionJob>[] = [
  { key: 'job_type', header: 'Type', cell: (row) => <span className="text-xs">{row.job_type?.replace(/_/g, ' ') ?? '—'}</span> },
  { key: 'status', header: 'Status', cell: (row) => <StatusBadge value={row.status} kind="job" /> },
  {
    key: 'progress',
    header: 'Progress',
    cell: (row) => {
      const pct = jobProgress(row)
      return pct === null ? <span className="text-xs subtle">—</span> : <Progress value={pct} tone={row.status === 'failed' ? 'alert' : 'forest'} />
    },
  },
  {
    key: 'records',
    header: 'Records (a/r/f)',
    align: 'right',
    cell: (row) => (
      <span className="mono text-xs">
        {formatNumber(row.records_accepted, 0)}/{formatNumber(row.records_rejected, 0)}/{formatNumber(row.records_fetched, 0)}
      </span>
    ),
  },
  { key: 'processing_mode', header: 'Mode', cell: (row) => (row.processing_mode ? <Badge tone="neutral">{row.processing_mode}</Badge> : <span className="text-xs subtle">—</span>) },
  { key: 'started', header: 'Started', align: 'right', cell: (row) => <span className="text-xs">{row.started_at ? formatDate(row.started_at, true) : '—'}</span> },
]

const observationColumns: Column<Observation>[] = [
  { key: 'date', header: 'Date', cell: (row) => <span className="mono text-xs">{row.observation_date}</span> },
  { key: 'location', header: 'Location', cell: (row) => <span className="text-xs">{row.location_name ?? row.location_code}</span> },
  {
    key: 'temp',
    header: metricLabel('temperature_c'),
    align: 'right',
    cell: (row) => <span className="mono text-xs">{row.temperature_c === null || row.temperature_c === undefined ? '—' : formatMetric(row.temperature_c, 'temperature_c')}</span>,
  },
  {
    key: 'precip',
    header: metricLabel('precipitation_mm'),
    align: 'right',
    cell: (row) =>
      <span className="mono text-xs">{row.precipitation_mm === null || row.precipitation_mm === undefined ? '—' : formatMetric(row.precipitation_mm, 'precipitation_mm')}</span>,
  },
  {
    key: 'flags',
    header: 'Flags',
    cell: (row) => (row.quality_flags?.length ? <Badge tone="warning">{row.quality_flags.length}</Badge> : <Badge tone="success">clean</Badge>),
  },
]
