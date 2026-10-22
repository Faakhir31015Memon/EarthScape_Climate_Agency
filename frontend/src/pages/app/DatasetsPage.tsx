/**
 * Datasets — the library of registered data sources. Search and filters sync to
 * the URL so a scoped view is shareable; the connector strip reports each
 * provider's real configuration state rather than assuming it works.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Plus, Upload } from 'lucide-react'
import { api } from '../../services/api'
import type { ConnectorStatus, Dataset, IngestionJob, SourceType } from '../../types/api'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import { formatDate, formatNumber, humanise } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Panel,
  Pagination,
  RefreshButton,
  SearchInput,
  Select,
  StatusBadge,
  type Column,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { useAuth } from '../../features/auth/AuthContext'
import { asList } from './components/data/normalise'

const PAGE_SIZE = 25

const STATUS_OPTIONS = [
  { value: 'ready', label: 'Ready' },
  { value: 'ingesting', label: 'Ingesting' },
  { value: 'validated', label: 'Validated' },
  { value: 'validating', label: 'Validating' },
  { value: 'draft', label: 'Draft' },
  { value: 'failed', label: 'Failed' },
  { value: 'archived', label: 'Archived' },
]

const SOURCE_OPTIONS: { value: SourceType; label: string }[] = [
  { value: 'nasa_power', label: 'NASA POWER (API)' },
  { value: 'noaa', label: 'NOAA (API)' },
  { value: 'live_api', label: 'Live API' },
  { value: 'university_upload', label: 'University upload' },
  { value: 'sample', label: 'Sample' },
]

export default function DatasetsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { can } = useAuth()

  const [term, setTerm] = useState(searchParams.get('query') ?? '')
  const debouncedTerm = useDebouncedValue(term, 350)
  const status = searchParams.get('status') ?? undefined
  const sourceType = searchParams.get('source_type') ?? undefined
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

  // Keep the query in the URL so a filtered library view is shareable.
  useEffect(() => {
    if ((searchParams.get('query') ?? '') !== debouncedTerm) update({ query: debouncedTerm || undefined }, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedTerm])

  const state = useAsync(
    () =>
      api.datasets.list({
        page,
        page_size: PAGE_SIZE,
        status,
        source_type: sourceType,
        search: debouncedTerm || undefined,
      }),
    [page, status, sourceType, debouncedTerm],
  )

  // Connectors are an ingestion-app resource: GET /ingestion/connectors.
  const connectorsState = useAsync(() => api.ingestion.connectors(), [])
  const connectors = useMemo(
    () => asList<ConnectorStatus>(connectorsState.data, 'connectors'),
    [connectorsState.data],
  )

  // Connector objects carry no sync timestamp, so the latest job per source type is
  // read from the real job list instead of being implied.
  const jobsState = useAsync(() => api.ingestion.list({ page: 1, page_size: 100 }), [])
  const lastJobBySource = useMemo(() => {
    const map = new Map<SourceType, IngestionJob>()
    ;(jobsState.data?.items ?? []).forEach((job) => {
      if (!job.source_type || !job.started_at) return
      const previous = map.get(job.source_type)
      if (!previous || new Date(job.started_at).getTime() > new Date(previous.started_at).getTime()) {
        map.set(job.source_type, job)
      }
    })
    return map
  }, [jobsState.data])

  const canUpload = can('datasets:upload')

  const columns = useMemo<Column<Dataset>[]>(
    () => [
      {
        key: 'name',
        header: 'Dataset',
        sortable: true,
        cell: (row) => (
          <div className="min-w-0">
            <span className="block truncate font-medium">{row.name}</span>
            <span className="mono block text-xs subtle">{row.id}</span>
          </div>
        ),
      },
      {
        key: 'source',
        header: 'Source',
        cell: (row) => (
          <div className="text-xs">
            <span className="block truncate">{row.source_name}</span>
            <span className="subtle">{row.source_type?.replace(/_/g, ' ')}</span>
          </div>
        ),
      },
      { key: 'format', header: 'Format', cell: (row) => <Badge tone="neutral">{row.format}</Badge> },
      {
        key: 'record_count',
        header: 'Records',
        align: 'right',
        sortable: true,
        sortKey: 'record_count',
        cell: (row) => <span className="mono">{formatNumber(row.record_count, 0)}</span>,
      },
      {
        key: 'status',
        header: 'Status',
        cell: (row) => <StatusBadge value={row.status} kind="dataset" />,
      },
      {
        key: 'temporal',
        header: 'Coverage',
        cell: (row) => {
          const t = row.temporal_coverage
          if (!t?.start && !t?.end) return <span className="text-xs subtle">—</span>
          return (
            <span className="mono text-xs">
              {t.start ?? '…'} → {t.end ?? '…'}
            </span>
          )
        },
      },
      {
        key: 'updated',
        header: 'Updated',
        align: 'right',
        cell: (row) => <span className="text-xs">{row.updated_at ? new Date(row.updated_at).toLocaleDateString() : '—'}</span>,
      },
    ],
    [],
  )

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title="Dataset library"
        description="Every registered source — uploaded files, API providers and sample sets — with its real ingestion status."
        actions={
          <div className="flex items-center gap-2">
            <RefreshButton onClick={() => void state.refetch()} busy={state.refreshing} />
            {canUpload ? (
              <button type="button" className="btn btn-primary btn-sm" onClick={() => navigate('/app/uploads')}>
                <Plus className="h-4 w-4" aria-hidden="true" /> Import data
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                disabled
                title="This session has not been issued the datasets:upload capability — sign out and sign in again to refresh it."
              >
                <Upload className="h-4 w-4" aria-hidden="true" /> Import data
              </button>
            )}
          </div>
        }
      />

      <Panel dense bodyClassName="p-0">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <SearchInput label="Search" value={term} onChange={setTerm} placeholder="Name, source or description…" className="w-64" />
          <Field label="Status" htmlFor="ds-status" className="w-40">
            <Select id="ds-status" value={status ?? ''} onChange={(value) => update({ status: value || undefined })} options={STATUS_OPTIONS} placeholder="All statuses" />
          </Field>
          <Field label="Source type" htmlFor="ds-source" className="w-48">
            <Select id="ds-source" value={sourceType ?? ''} onChange={(value) => update({ source_type: value || undefined })} options={SOURCE_OPTIONS} placeholder="All sources" />
          </Field>
        </div>
      </Panel>

      <Panel>
        <DataTable
          columns={columns}
          rows={state.data?.items ?? []}
          rowKey={(row) => row.id}
          loading={state.loading}
          onRowClick={(row) => navigate(`/app/datasets/${row.id}`)}
          empty={
            state.error ? (
              <ErrorState error={state.error} onRetry={() => void state.refetch()} />
            ) : (
              <EmptyState
                title={debouncedTerm || status || sourceType ? 'No datasets match these filters' : 'No datasets registered yet'}
                description={
                  canUpload
                    ? 'Upload a CSV/JSON/XLSX file or connect an API source to begin. Nothing is shown here until data is actually registered.'
                    : 'No datasets match the current view. Ask an analyst or administrator to register data.'
                }
                action={
                  canUpload ? (
                    <button type="button" className="btn btn-primary btn-sm" onClick={() => navigate('/app/uploads')}>
                      Upload a dataset
                    </button>
                  ) : undefined
                }
              />
            )
          }
        />
        {state.data ? (
          <Pagination meta={state.data} onPage={(next) => update({ page: String(next) })} disabled={state.refreshing} label={`${state.data.total.toLocaleString()} datasets`} />
        ) : null}
      </Panel>

      <Panel
        title="Source connectors"
        description="Configuration state reported by GET /ingestion/connectors — a connector only shows as configured when the backend says it is."
      >
        {connectorsState.loading ? <p className="text-xs subtle">Checking connectors…</p> : null}
        {connectorsState.error ? <ErrorState error={connectorsState.error} onRetry={() => void connectorsState.refetch()} /> : null}
        {!connectorsState.loading && connectors.length === 0 && !connectorsState.error ? (
          <EmptyState
            title="No connectors reported"
            description="The backend did not return any source connectors, so their configuration state is unknown rather than assumed."
          />
        ) : null}
        {connectorsState.data?.note ? <p className="mb-3 text-xs subtle">{connectorsState.data.note}</p> : null}
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {connectors.map((connector) => {
            const lastJob = lastJobBySource.get(connector.source_type)
            return (
              <li key={connector.connector} className="card-tight p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{humanise(connector.connector)}</span>
                  <ConnectorBadge connector={connector} />
                </div>
                <p className="mt-1 text-xs muted">{connector.setup_hint || `Source type: ${connector.source_type}`}</p>
                {connector.secret_summary ? (
                  <p className="mt-1 text-xs subtle">
                    <span className="muted">Key:</span> {connector.secret_summary}
                  </p>
                ) : null}
                {connector.polling_model ? <p className="mt-1 text-xs subtle">Polling: {connector.polling_model}</p> : null}
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs subtle">
                  <span>{connector.requires_key ? 'API key required' : 'No key needed'}</span>
                  {jobsState.error ? (
                    <span>job history unavailable</span>
                  ) : lastJob ? (
                    <span className="mono">
                      last job {formatDate(lastJob.started_at, true)} · {lastJob.status}
                    </span>
                  ) : (
                    <span>{jobsState.loading ? 'checking jobs…' : 'no job recorded for this source'}</span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </Panel>
    </div>
  )
}

function ConnectorBadge({ connector }: { connector: ConnectorStatus }) {
  const tone = connector.configured ? 'success' : connector.state === 'needs_configuration' ? 'warning' : 'neutral'
  const label = connector.configured ? 'configured' : connector.state === 'needs_configuration' ? 'needs config' : 'state unknown'
  return <Badge tone={tone}>{label}</Badge>
}
