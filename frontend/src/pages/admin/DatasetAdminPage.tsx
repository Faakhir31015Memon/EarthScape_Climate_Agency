import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Archive, Database, Plug, RefreshCw, ShieldCheck, UploadCloud } from 'lucide-react'
import api from '../../services/api'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Hint,
  PageHeader,
  Panel,
  Pagination,
  RefreshButton,
  SearchInput,
  Select,
  StatusBadge,
  TableSkeleton,
  type Column,
  type Option,
} from '../../components/ui'
import { ActionFeedback, CodeCell, ConfigRequirement, MetadataView, type Feedback } from './components/AdminBits'
import { PAGE_SIZE, describeError, noteFrom, redactIfNeeded, unwrapOne } from './components/adminUtils'
import { formatBytes, formatDate, formatNumber, humanise, relativeTime } from '../../lib/format'
import type { Dataset, DatasetStatus, FormatDocumentation, IngestionJob, Paginated, SourceType, ValidationReport } from '../../types/api'

const STATUS_OPTIONS: DatasetStatus[] = ['draft', 'validating', 'validated', 'ingesting', 'ready', 'failed', 'archived']
const SOURCE_OPTIONS: SourceType[] = ['nasa_power', 'noaa', 'live_api', 'university_upload', 'sample']
const FORMAT_OPTIONS = ['csv', 'json', 'xlsx', 'parquet', 'api']

/** Registry names accepted by `POST /api/v1/ingestion/jobs` (`source` must be one of these). */
const CONNECTOR_BY_SOURCE: Partial<Record<SourceType, string>> = {
  nasa_power: 'nasa_power',
  noaa: 'noaa_cdo',
  live_api: 'open_meteo',
}

/**
 * The report stored on a dataset record is declared as a free-form object; the backend’s
 * `build_validation_report()` (apps/datasets/importer.py) always emits `headers` and
 * `counts.fetched`, so both are checked at runtime before the declared fields are read.
 * An empty `{}` (seeded or API-created datasets) fails this guard and is shown as
 * "no report stored" instead of guessed values.
 */
function isValidationReport(value: unknown): value is ValidationReport {
  if (!value || typeof value !== 'object') return false
  const candidate = value as { headers?: unknown; counts?: unknown }
  if (!Array.isArray(candidate.headers)) return false
  if (!candidate.counts || typeof candidate.counts !== 'object') return false
  return typeof (candidate.counts as { fetched?: unknown }).fetched === 'number'
}

function reportErrorCount(report: ValidationReport): number {
  return report.error_count ?? report.validation_errors?.length ?? 0
}

/** Provenance is the account id the server records; names are not returned by this endpoint. */
function uploaderLabel(dataset: Dataset): string {
  return dataset.uploaded_by ? `account ${dataset.uploaded_by.slice(-6)}` : 'account not recorded'
}

/** Only a genuine boolean from the probe record is rendered as reachable/unreachable. */
function reachabilityFlag(record: Record<string, unknown> | null | undefined): boolean | null {
  if (!record) return null
  const value = record.reachable
  return typeof value === 'boolean' ? value : null
}

/**
 * Fallback shown only when `GET /api/v1/ingestion/connectors` fails to answer:
 * the three sources the backend registry can actually build. Names and hints are copied
 * from `apps/ingestion/connectors/**`; nothing here claims a live probe happened.
 */
const EXPECTED_CONNECTORS: { name: string; sourceType: string; envKeys: string; requiresKey: boolean; hint: string }[] = [
  {
    name: 'nasa_power',
    sourceType: 'nasa_power',
    envKeys: 'NASA_POWER_BASE_URL',
    requiresKey: false,
    hint: 'NASA POWER needs no API key — only outbound network access.',
  },
  {
    name: 'noaa_cdo',
    sourceType: 'noaa',
    envKeys: 'NOAA_API_KEY',
    requiresKey: true,
    hint: 'Set NOAA_API_KEY (register a token at https://www.ncdc.noaa.gov/cdo-web/token) to enable NOAA CDO ingestion.',
  },
  {
    name: 'open_meteo',
    sourceType: 'live_api',
    envKeys: 'LIVE_CLIMATE_PROVIDER, LIVE_CLIMATE_BASE_URL, LIVE_CLIMATE_API_KEY',
    requiresKey: false,
    hint: 'Key-free by default; LIVE_CLIMATE_PROVIDER must name a registered provider. Polling of daily values, not a sensor stream.',
  },
]

/**
 * Dataset administration.
 *
 * Every dataset on the platform, including other users' uploads
 * (`GET /api/v1/datasets` — administrators see all records, `storage_uri` included).
 * Writes go through `PATCH /datasets/<id>`, `DELETE /datasets/<id>` (which is the
 * backend's soft-archive), and `POST /ingestion/jobs` for a re-sync. Validation and
 * connector status are reported exactly as the API answers; unrouted endpoints are
 * named instead of guessed.
 */
export default function DatasetAdminPage() {
  const { can } = useAuth()
  const canManageDatasets = can('datasets:manage')
  const canRunIngestion = can('ingestion:run')

  const [term, setTerm] = useState('')
  const debouncedTerm = useDebouncedValue(term, 350)
  const [status, setStatus] = useState('')
  const [source, setSource] = useState('')
  const [format, setFormat] = useState('')
  const [sort, setSort] = useState('created_at')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<Dataset | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmArchive, setConfirmArchive] = useState(false)

  const list = useAsync(
    () =>
      api.datasets.list({
        page,
        page_size: PAGE_SIZE,
        status: status || undefined,
        source_type: source || undefined,
        // `query` is what the typed client documents; the Django view reads `search`.
        query: debouncedTerm || undefined,
        search: debouncedTerm || undefined,
        format: format || undefined,
        sort,
      }),
    [page, status, source, format, debouncedTerm, sort],
  )

  const connectors = useAsync(() => api.ingestion.connectors(), [])
  const formats = useAsync(() => api.datasets.formats(), [])
  const settings = useAsync(() => api.system.settings(), [])

  const detail = useAsync(
    () => (selected ? api.datasets.get(selected.id).then((raw) => unwrapOne<Dataset>(raw)) : Promise.resolve(null)),
    [selected?.id],
    { enabled: Boolean(selected) },
  )

  const history = useAsync(
    () =>
      selected
        ? api.ingestion.list({ dataset_id: selected.id, page: 1, page_size: 10 })
        : Promise.resolve(null as Paginated<IngestionJob> | null),
    [selected?.id],
    { enabled: Boolean(selected), interval: 15_000 },
  )

  const rows = list.data?.items ?? []

  // A new search term always returns to the first page of the filtered result set.
  useEffect(() => {
    setPage(1)
  }, [debouncedTerm])

  const reload = useCallback(async () => {
    setFeedback(null)
    await list.refetch()
    if (selected) {
      await detail.refetch()
      await history.refetch()
    }
  }, [list, detail, history, selected])

  /** `POST /ingestion/jobs` — the routed re-sync path (see the payload note below). */
  const resync = useCallback(
    async (dataset: Dataset) => {
      const connector = CONNECTOR_BY_SOURCE[dataset.source_type]
      if (!connector) {
        setFeedback({
          tone: 'error',
          text: `${dataset.name} is not backed by a live connector (${dataset.source_type}); re-run its import from the upload screen instead.`,
        })
        return
      }
      setBusy(true)
      setFeedback(null)
      try {
        // The typed client sends `source_type`; the view requires the registry name in
        // `source`, so both keys travel together.
        const payload: Record<string, unknown> = {
          source: connector,
          source_type: dataset.source_type,
          dataset_id: dataset.id,
          name: `admin re-sync — ${dataset.name}`,
          triggered_by: 'manual',
        }
        const raw = await api.ingestion.start(payload as Parameters<typeof api.ingestion.start>[0])
        const note = noteFrom(raw)
        setFeedback({
          tone: 'success',
          text: `Re-sync queued for ${dataset.name} against ${connector}.${note ? ` ${note}` : ''} The job is polled below.`,
        })
        await Promise.all([list.refetch(), history.refetch(), detail.refetch()])
      } catch (cause) {
        const described = describeError(cause)
        setFeedback({
          tone: 'error',
          text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`,
        })
      } finally {
        setBusy(false)
      }
    },
    [detail, history, list],
  )

  const archive = useCallback(async () => {    const target = selected
    if (!target) return
    setBusy(true)
    setFeedback(null)
    try {
      const raw = await api.datasets.archive(target.id)
      const note = noteFrom(raw)
      setFeedback({
        tone: 'success',
        text: `${target.name} archived (status → archived).${note ? ` ${note}` : ' The stored file and its observations are retained; there is no hard-delete route.'}`,
      })
      setSelected(null)
      await list.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFeedback({
        tone: 'error',
        text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`,
      })
      await list.refetch()
    } finally {
      setBusy(false)
    }
  }, [list, selected])

  /** `POST /datasets/<id>/validate` — routed on the server and exposed by the typed client. */
  const revalidate = useCallback(
    async (target: Dataset) => {
      setBusy(true)
      setFeedback(null)
      try {
        const report = await api.datasets.validate(target.id)
        const errors = reportErrorCount(report)
        setFeedback({
          tone: 'success',
          text: `Validation re-run for ${target.name}: ${report.counts.fetched.toLocaleString()} row(s) fetched, ${report.counts.accepted.toLocaleString()} accepted, ${errors} error(s) reported.`,
        })
        await Promise.all([detail.refetch(), list.refetch()])
      } catch (cause) {
        const described = describeError(cause)
        setFeedback({
          tone: 'error',
          text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`,
        })
      } finally {
        setBusy(false)
      }
    },
    [detail, list],
  )

  const columns = useMemo<Column<Dataset>[]>(
    () => [
      {
        key: 'dataset',
        header: 'Dataset',
        cell: (row) => (
          <div className="min-w-0">
            <button type="button" className="link truncate text-sm font-medium" onClick={() => setSelected(row)}>
              {row.name}
            </button>
            {row.description ? <p className="truncate text-xs subtle" title={row.description}>{row.description}</p> : null}
            <p className="truncate text-[11px] subtle">
              {uploaderLabel(row)}
            </p>
          </div>
        ),
      },
      {
        key: 'source',
        header: 'Source',
        cell: (row) => (
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={row.source_type === 'university_upload' || row.source_type === 'sample' ? 'neutral' : 'info'}>
              {humanise(row.source_type)}
            </Badge>
            <span className="text-xs muted">{row.source_name}</span>
            <Badge tone="neutral">{row.format}</Badge>
          </div>
        ),
      },
      {
        key: 'status',
        header: 'Status',
        cell: (row) => (
          <div className="flex flex-wrap items-center gap-1.5">
            <StatusBadge value={row.status} kind="dataset" />
            {isValidationReport(row.validation_report) ? (
              <Badge tone={reportErrorCount(row.validation_report) > 0 ? 'danger' : 'success'} title="Error count taken from the stored validation report">
                {reportErrorCount(row.validation_report) > 0 ? `${reportErrorCount(row.validation_report)} error(s)` : 'no errors'}
              </Badge>
            ) : (
              <Badge tone="neutral" title="The stored record has no parseable validation report (seeded and API-created datasets carry an empty object)">
                no report
              </Badge>
            )}
          </div>
        ),
      },
      {
        key: 'records',
        header: 'Records',
        align: 'right',
        cell: (row) => (
          <div className="text-right">
            <p className="mono text-[12px] font-semibold">{formatNumber(row.record_count, 0)}</p>
            <p className="text-[11px] subtle">schema v{row.schema_version}</p>
          </div>
        ),
      },
      {
        key: 'coverage',
        header: 'Coverage',
        cell: (row) => (
          <div className="text-xs muted">
            <p>
              {row.temporal_coverage?.start ?? '—'} → {row.temporal_coverage?.end ?? '—'}
              {row.temporal_coverage?.frequency ? <span className="subtle"> · {row.temporal_coverage.frequency}</span> : null}
            </p>
            <p className="text-[11px] subtle">
              {row.geographic_coverage?.regions?.length ?? 0} region(s) · {row.geographic_coverage?.location_codes?.length ?? 0} location(s)
            </p>
          </div>
        ),
      },
      {
        key: 'storage',
        header: 'Storage',
        cell: (row) => {
          const safe = redactIfNeeded('storage_uri', row.storage_uri ?? null)
          if (!safe.value || safe.value === null) return <span className="text-xs subtle">not reported</span>
          return (
            <div className="min-w-0">
              <p className="mono max-w-[16rem] truncate text-[11px]" title={String(safe.value)}>
                {String(safe.value)}
              </p>
              {safe.redacted ? <Badge tone="warning">credential redacted</Badge> : null}
            </div>
          )
        },
      },
      {
        key: 'updated',
        header: 'Updated',
        cell: (row) => (
          <span className="text-xs muted" title={formatDate(row.updated_at ?? row.created_at, true)}>
            {relativeTime(row.updated_at ?? row.created_at)}
          </span>
        ),
      },
      {
        key: 'actions',
        header: 'Actions',
        align: 'right',
        cell: (row) => (
          <div className="flex justify-end gap-1">
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Re-read ${row.name}`}
              title="Re-read this dataset row and its ingestion history from the API"
              onClick={() => {
                setSelected(row)
                void Promise.all([detail.refetch(), history.refetch()])
              }}
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Re-run sync for ${row.name}`}
              disabled={!canRunIngestion || busy || !CONNECTOR_BY_SOURCE[row.source_type]}
              title={
                !canRunIngestion
                  ? 'Requires the ingestion:run capability'
                  : !CONNECTOR_BY_SOURCE[row.source_type]
                    ? 'Only live sources (NASA POWER, NOAA, live API) can be re-synced; file imports are re-run from the upload screen.'
                    : 'Queue a fresh fetch with POST /api/v1/ingestion/jobs'
              }
              onClick={() => void resync(row)}
            >
              <UploadCloud className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Archive ${row.name}`}
              disabled={!canManageDatasets || row.status === 'archived' || busy}
              title={
                !canManageDatasets
                  ? 'Requires the datasets:manage capability'
                  : row.status === 'archived'
                    ? 'Already archived'
                    : 'Soft-archive: DELETE /api/v1/datasets/<id> (administrator only on the server)'
              }
              onClick={() => {
                setSelected(row)
                setConfirmArchive(true)
              }}
            >
              <Archive className="h-3.5 w-3.5" />
            </button>
          </div>
        ),
      },
    ],
    [busy, canManageDatasets, canRunIngestion, detail, history, resync],
  )

  const formatDocs = useMemo<FormatDocumentation[]>(() => formats.data?.formats ?? [], [formats.data])

  const derived = settings.data?.env_derived
  const uploadLimits = useMemo<Option[]>(() => {
    if (!derived) return []
    return [
      { value: 'bytes', label: `Upload size limit: ${formatBytes(derived.max_upload_bytes)}` },
      { value: 'rows', label: `Upload row limit: ${derived.max_upload_rows.toLocaleString()} rows` },
    ]
  }, [derived])

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Dataset administration"
        description="Every dataset on the platform, including other users’ uploads, with storage provenance, validation state and the ingestion runs behind each one."
        meta={
          list.data ? (
            <>
              <Badge tone="neutral">{list.data.total.toLocaleString()} dataset(s)</Badge>
              <span className="subtle">page {list.data.page} of {Math.max(list.data.pages, 1)}</span>
              {derived ? <Badge tone="info">{humanise(derived.database_mode)} store</Badge> : null}
            </>
          ) : null
        }
        actions={<RefreshButton onClick={() => void reload()} busy={list.refreshing || detail.refreshing} label="Re-read" />}
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
      {list.error ? <div className="mb-4"><ErrorState error={list.error} onRetry={() => void list.refetch()} /></div> : null}

      <FilterBar>
        <div className="min-w-[13rem] flex-1">
          <SearchInput value={term} onChange={setTerm} label="Search datasets" placeholder="Search name, description or tags…" />
        </div>
        <Field label="Status" htmlFor="dataset-status-filter">
          <Select
            id="dataset-status-filter"
            ariaLabel="Filter by status"
            placeholder="All statuses"
            value={status}
            onChange={(next) => { setStatus(next); setPage(1) }}
            options={STATUS_OPTIONS.map((value) => ({ value, label: humanise(value) }))}
          />
        </Field>
        <Field label="Source" htmlFor="dataset-source-filter">
          <Select
            id="dataset-source-filter"
            ariaLabel="Filter by source type"
            placeholder="All sources"
            value={source}
            onChange={(next) => { setSource(next); setPage(1) }}
            options={SOURCE_OPTIONS.map((value) => ({ value, label: humanise(value) }))}
          />
        </Field>
        <Field label="Format" htmlFor="dataset-format-filter">
          <Select
            id="dataset-format-filter"
            ariaLabel="Filter by storage format"
            placeholder="All formats"
            value={format}
            onChange={(next) => { setFormat(next); setPage(1) }}
            options={FORMAT_OPTIONS.map((value) => ({ value, label: value }))}
          />
        </Field>
        <Field label="Sort" htmlFor="dataset-sort">
          <Select
            id="dataset-sort"
            ariaLabel="Sort datasets"
            value={sort}
            onChange={(next) => { setSort(next); setPage(1) }}
            options={[
              { value: 'created_at', label: 'Newest' },
              { value: 'updated_at', label: 'Recently updated' },
              { value: 'record_count', label: 'Most records' },
              { value: 'name', label: 'Name' },
              { value: 'status', label: 'Status' },
            ]}
          />
        </Field>
      </FilterBar>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <Panel title="Datasets" description="GET /api/v1/datasets — administrators see all records; the server hides storage_uri from anyone without a right to it." dense>
          {list.loading ? <TableSkeleton rows={6} columns={8} /> : (
            <DataTable
              caption="All datasets"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              empty="No dataset matches these filters."
              onRowClick={(row) => setSelected(row)}
            />
          )}
          <Pagination meta={list.data ?? undefined} onPage={setPage} label="datasets" />
          <Hint>
            Archiving is soft: the backend flips <span className="mono">status</span> to <span className="mono">archived</span>, keeps the
            file and its observations, and refuses the call with HTTP 403 for anyone but an administrator. There is no hard-delete route,
            so this page never offers one.
          </Hint>
        </Panel>

        <div className="min-w-0 space-y-6">
          <DatasetDetail
            dataset={detail.data ?? selected}
            loading={detail.loading}
            error={detail.error}
            history={history.data}
            historyLoading={history.loading}
            historyError={history.error}
            canRunIngestion={canRunIngestion}
            busy={busy}
            onClose={() => setSelected(null)}
            onRetryJob={async (job) => {
              setBusy(true)
              setFeedback(null)
              try {
                const raw = await api.ingestion.retry(job.id)
                const note = noteFrom(raw)
                setFeedback({ tone: 'success', text: `Retry queued for ${job.source_name}.${note ? ` ${note}` : ''}` })
                await Promise.all([history.refetch(), detail.refetch()])
              } catch (cause) {
                const described = describeError(cause)
                setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
                await history.refetch()
              } finally {
                setBusy(false)
              }
            }}
            onArchive={() => setConfirmArchive(true)}
            canArchive={canManageDatasets}
            onRevalidate={() => {
              const target = detail.data ?? selected
              if (target) void revalidate(target)
            }}
          />

          <Panel
            title="Source connectors"
            description="GET /api/v1/ingestion/connectors — configuration state only; secret values stay masked by env.mask_value()."
            dense
            actions={<RefreshButton onClick={() => void connectors.refetch()} busy={connectors.refreshing} label="Probe" />}
          >
            {connectors.error ? (
              <>
                <ErrorState error={connectors.error} onRetry={() => void connectors.refetch()} />
                <p className="mt-3 text-xs leading-relaxed muted">
                  The connector endpoint has not answered, so the entries below are the static registry names only — their configuration
                  state is unknown until an endpoint answers. Each is listed with the environment key it needs.
                </p>
                <ul className="mt-3 space-y-3">
                  {EXPECTED_CONNECTORS.map((entry) => (
                    <ConfigRequirement
                      key={entry.name}
                      label={`${entry.name} (${humanise(entry.sourceType)})`}
                      envKey={entry.envKeys}
                      configured={null}
                      detail={entry.hint}
                      impact={entry.requiresKey ? 'Requires an API key; the value is never returned to the browser.' : 'No key required.'}
                    />
                  ))}
                </ul>
              </>
            ) : (connectors.data?.connectors ?? []).length === 0 ? (
              <p className="text-xs subtle">{connectors.loading ? 'Reading connector state…' : 'No connector was reported by the endpoint.'}</p>
            ) : (
              <>
                <ul className="space-y-3">
                  {(connectors.data?.connectors ?? []).map((entry) => {
                    const reachable = reachabilityFlag(entry.reachability)
                    return (
                      <li key={entry.connector} className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
                        <div className="flex flex-wrap items-center gap-2">
                          <Plug className="h-3.5 w-3.5 text-[var(--text-subtle)]" aria-hidden="true" />
                          <span className="text-sm font-medium">{entry.connector}</span>
                          <Badge tone="neutral">{humanise(entry.source_type)}</Badge>
                          <StatusBadge value={entry.state} label={entry.state.split('_').join(' ')} />
                          {reachable === null ? null : <Badge tone={reachable ? 'success' : 'danger'}>{reachable ? 'reachable' : 'unreachable'}</Badge>}
                          {entry.requires_key ? <Badge tone={entry.configured ? 'success' : 'warning'}>{entry.configured ? 'key configured' : 'key missing'}</Badge> : null}
                        </div>
                        <p className="mt-1 text-xs leading-relaxed muted">{entry.setup_hint}</p>
                        {entry.secret_summary ? (
                          <p className="mt-1 text-[11px] subtle">
                            key state: <span className="mono">{entry.secret_summary}</span>
                          </p>
                        ) : null}
                        {entry.provider ? <p className="mt-1 text-[11px] subtle">provider: <span className="mono">{entry.provider}</span></p> : null}
                        {entry.polling_model ? <p className="mt-1 text-[11px] subtle">polling: <span className="mono">{entry.polling_model}</span></p> : null}
                        {entry.reachability ? <MetadataView metadata={entry.reachability} /> : null}
                      </li>
                    )
                  })}
                </ul>
                {connectors.data?.note ? <p className="mt-3 text-[11px] leading-relaxed subtle">{connectors.data.note}</p> : null}
              </>
            )}
          </Panel>

          <Panel
            title="Upload limits & accepted formats"
            description="Ceilings come from GET /api/v1/system/settings (env-derived); format rules from GET /api/v1/datasets/formats."
            dense
          >
            {settings.error ? <ErrorState error={settings.error} onRetry={() => void settings.refetch()} /> : null}
            {uploadLimits.length > 0 ? (
              <ul className="mb-3 space-y-1">
                {uploadLimits.map((entry) => (
                  <li key={entry.value} className="text-sm muted">{entry.label}</li>
                ))}
                <li className="text-sm muted">Allowed extensions: the server rejects executables and markup (FORBIDDEN_EXTENSIONS in apps/datasets/views.py).</li>
              </ul>
            ) : (
              <p className="text-xs subtle">{settings.loading ? 'Reading limits…' : 'The settings endpoint has not answered, so the ceilings are unknown — they are not guessed here.'}</p>
            )}
            {formats.error ? <ErrorState error={formats.error} onRetry={() => void formats.refetch()} /> : null}
            {formatDocs.length === 0 && !formats.loading && !formats.error ? (
              <p className="text-xs subtle">The formats endpoint returned no documentation entries.</p>
            ) : null}
            <ul className="space-y-3">
              {formatDocs.map((entry) => (
                <li key={entry.format} className="rounded-xl border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
                  <p className="flex flex-wrap items-center gap-2">
                    <Badge tone="info">{entry.format}</Badge>
                    {entry.delimiter ? <span className="subtle">{entry.delimiter}</span> : null}
                    {entry.shapes?.length ? <span className="subtle">{entry.shapes.join(' · ')}</span> : null}
                  </p>
                  {entry.notes ? <p className="mt-1 leading-relaxed muted">{entry.notes}</p> : null}
                  {entry.required_columns?.length ? (
                    <p className="mt-1 leading-relaxed">
                      <span className="subtle">Required columns: </span>
                      {entry.required_columns.map((column) => <code key={column} className="kbd mr-1">{column}</code>)}
                    </p>
                  ) : null}
                  {entry.optional_columns?.length ? (
                    <p className="mt-1 leading-relaxed subtle">
                      Mappable: {entry.optional_columns.slice(0, 10).join(', ')}
                      {entry.optional_columns.length > 10 ? ` +${entry.optional_columns.length - 10} more` : ''}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
            <Hint>
              Re-validation (<span className="mono">POST /api/v1/datasets/&lt;id&gt;/validate</span>) is triggered from the detail panel
              above; re-running a live fetch uses <span className="mono">POST /api/v1/ingestion/jobs</span>. Both are routed and exposed
              by the typed client.
            </Hint>
          </Panel>
        </div>
      </div>

      <ConfirmDialog
        open={confirmArchive}
        title="Archive this dataset?"
        tone="danger"
        busy={busy}
        confirmLabel="Archive dataset"
        onClose={() => setConfirmArchive(false)}
        onConfirm={() => {
          setConfirmArchive(false)
          void archive()
        }}
        message={
          <span>
            <strong>{selected?.name}</strong> will move to <strong>archived</strong> and disappear from the analyst library. The stored
            file, its observations and its ingestion history are retained — this is a soft archive, and the backend rejects the request
            with HTTP 403 unless the caller is an administrator. The action is written to the audit trail as{' '}
            <span className="mono">dataset.archived</span>.
          </span>
        }
      />
    </div>
  )
}

/* ------------------------------------------------------------- detail panel */

function DatasetDetail({
  dataset,
  loading,
  error,
  history,
  historyLoading,
  historyError,
  canRunIngestion,
  busy,
  onClose,
  onRetryJob,
  onArchive,
  canArchive,
  onRevalidate,
}: {
  dataset: Dataset | null
  loading: boolean
  error: unknown
  history: Paginated<IngestionJob> | null
  historyLoading: boolean
  historyError: unknown
  canRunIngestion: boolean
  busy: boolean
  onClose: () => void
  onRetryJob: (job: IngestionJob) => Promise<void>
  onArchive: () => void
  canArchive: boolean
  onRevalidate: () => void
}) {
  if (!dataset) {
    return (
      <Panel title="Dataset detail" description="Select a dataset row to inspect its provenance, validation report and ingestion runs." dense>
        <p className="flex items-start gap-2 text-xs leading-relaxed muted">
          <Database className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>Nothing selected. The table on the left is the same catalogue analysts browse, unfiltered by owner.</span>
        </p>
      </Panel>
    )
  }

  const report = isValidationReport(dataset.validation_report) ? dataset.validation_report : null
  const reportIssues = report?.validation_errors ?? []
  const jobs = history?.items ?? []
  /** Re-validation re-reads the stored file; the server answers 400 without a `storage_uri`. */
  const hasStoredFile = Boolean(dataset.storage_uri)

  return (
    <Panel
      title={dataset.name}
      description={dataset.description ?? 'No description recorded.'}
      dense
      actions={
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Clear
        </button>
      }
      footer={
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            disabled={!canArchive || busy || !hasStoredFile}
            title={
              !canArchive
                ? 'Requires the datasets:manage capability'
                : !hasStoredFile
                  ? 'This dataset has no stored file (storage_uri is null), so POST /api/v1/datasets/<id>/validate would be rejected by the server'
                  : 'POST /api/v1/datasets/<id>/validate rebuilds the report from the stored file on the server'
            }
            onClick={onRevalidate}
          >
            <RefreshCw className={busy ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} /> Re-validate
          </button>
          <button
            type="button"
            className="btn btn-danger btn-sm"
            disabled={!canArchive || dataset.status === 'archived' || busy}
            title={!canArchive ? 'Requires the datasets:manage capability' : dataset.status === 'archived' ? 'Already archived' : 'Soft-archive via DELETE /api/v1/datasets/<id>'}
            onClick={onArchive}
          >
            <Archive className="h-3.5 w-3.5" /> Archive
          </button>
          <span className="ml-auto text-[11px] subtle">
            id <span className="mono">{dataset.id}</span>
          </span>
        </div>
      }
    >
      {loading ? <p className="text-xs muted">Reading detail…</p> : null}
      {error ? <ErrorState error={error} /> : null}

      <dl className="grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
        <DetailRow label="Status" value={<StatusBadge value={dataset.status} kind="dataset" />} />
        <DetailRow label="Source" value={`${dataset.source_name} · ${humanise(dataset.source_type)} · ${dataset.format}`} />
        <DetailRow label="Records" value={formatNumber(dataset.record_count, 0)} />
        <DetailRow label="Schema version" value={`v${dataset.schema_version}`} />
        <DetailRow label="Temporal" value={`${dataset.temporal_coverage?.start ?? '—'} → ${dataset.temporal_coverage?.end ?? '—'}`} />
        <DetailRow label="Regions" value={(dataset.geographic_coverage?.regions ?? []).join(', ') || 'not recorded'} />
        <DetailRow label="Uploaded by" value={uploaderLabel(dataset)} />
        <DetailRow label="Created" value={formatDate(dataset.created_at, true)} />
        <DetailRow label="Public" value={dataset.is_public ? 'visible to every account' : 'restricted'} />
        <DetailRow label="Ingestion runs" value={`${dataset.ingestion_job_ids?.length ?? 0} referenced job(s)`} />
      </dl>

      <div className="mt-3">
        <p className="panel-title mb-1">Storage provenance</p>
        <StorageLine storageUri={dataset.storage_uri ?? null} sourceUrl={dataset.source_url ?? null} fileName={dataset.file_name ?? null} />
        {dataset.source_metadata && Object.keys(dataset.source_metadata).length > 0 ? <MetadataView metadata={dataset.source_metadata} /> : null}
      </div>

      {report ? (
        <div className="mt-3 rounded-xl border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
          <p className="panel-title mb-1">Validation report</p>
          <p className="flex flex-wrap items-center gap-2">
            <Badge tone={reportErrorCount(report) > 0 ? 'danger' : 'success'}>
              {reportErrorCount(report) > 0 ? `${reportErrorCount(report)} error(s)` : 'no errors'}
            </Badge>
            <span className="muted">{formatNumber(report.counts.fetched, 0)} row(s) fetched</span>
            <span className="muted">{formatNumber(report.counts.accepted, 0)} accepted</span>
            <span className="subtle">{report.generated_at ? `checked ${relativeTime(report.generated_at)}` : 'timestamp not recorded'}</span>
            {typeof report.counts.rejected === 'number' ? <span className="subtle">{formatNumber(report.counts.rejected, 0)} rejected</span> : null}
            {typeof report.counts.duplicates_against_database === 'number' ? (
              <span className="subtle">{report.counts.duplicates_against_database} duplicate key(s) against the database</span>
            ) : null}
            {report.aborted_reason ? <span className="font-medium text-alert-high">aborted: {report.aborted_reason}</span> : null}
            {report.error ? <span className="font-medium text-alert-critical">import error: {report.error}</span> : null}
          </p>
          <ul className="mt-2 space-y-1">
            {reportIssues.slice(0, 6).map((issue, index) => (
              <li key={`${issue.code}-${index}`} className="flex flex-wrap items-start gap-2">
                <Badge tone={issue.severity === 'error' ? 'danger' : issue.severity === 'warning' ? 'warning' : 'neutral'}>{issue.severity}</Badge>
                <span className="muted">
                  {issue.field ? <span className="mono">{issue.field}: </span> : null}
                  {issue.message}
                  {issue.row !== null && issue.row !== undefined ? <span className="subtle"> (row {issue.row})</span> : null}
                  {issue.count ? <span className="subtle"> ×{issue.count}</span> : null}
                </span>
              </li>
            ))}
            {reportIssues.length > 6 ? <li className="subtle">+{reportIssues.length - 6} further issue(s) in the stored report.</li> : null}
            {reportIssues.length === 0 ? <li className="subtle">No issue recorded by the last validation pass.</li> : null}
          </ul>
        </div>
      ) : dataset.validation_report ? (
        <p className="mt-3 text-xs subtle">
          The stored validation object has no report content this screen can read (keys{' '}
          <span className="mono">{Object.keys(dataset.validation_report).join(', ') || 'none'}</span>) — nothing is inferred from it.
          {hasStoredFile ? ' Use Re-validate to rebuild it from the stored file.' : ' This dataset records no stored file, so no re-validation can run.'}
        </p>
      ) : (
        <p className="mt-3 text-xs subtle">No validation report has been stored for this dataset yet.</p>
      )}

      <div className="mt-4">
        <p className="panel-title mb-1">Ingestion history</p>
        {historyError ? <ErrorState error={historyError} /> : null}
        {historyLoading && jobs.length === 0 ? <TableSkeleton rows={3} columns={4} /> : null}
        {!historyLoading && jobs.length === 0 ? <p className="text-xs subtle">No ingestion job references this dataset.</p> : null}
        <ul className="space-y-2">
          {jobs.map((job) => (
            <li key={job.id} className="rounded-xl border p-2.5 text-xs" style={{ borderColor: 'var(--border)' }}>
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge value={job.status} kind="job" />
                <span className="font-medium">{job.source_name}</span>
                <Badge tone="neutral">{humanise(job.job_type)}</Badge>
                <span className="subtle">{relativeTime(job.started_at)}</span>
                {job.triggered_by ? <Badge tone={job.triggered_by === 'schedule' ? 'info' : 'neutral'}>{job.triggered_by}</Badge> : null}
                <button
                  type="button"
                  className="btn btn-secondary btn-sm ml-auto"
                  disabled={!canRunIngestion || busy || (job.status !== 'failed' && job.status !== 'cancelled')}
                  title={
                    !canRunIngestion
                      ? 'Requires the ingestion:run capability'
                      : job.status !== 'failed' && job.status !== 'cancelled'
                        ? 'Only failed or cancelled jobs can be retried (the server answers 409 otherwise)'
                        : `Retry count ${job.retry_count}${job.max_retries ? ` of ${job.max_retries}` : ''}`
                  }
                  onClick={() => void onRetryJob(job)}
                >
                  Retry
                </button>
              </div>
              <p className="mt-1 muted">
                fetched {formatNumber(job.records_fetched, 0)} · accepted {formatNumber(job.records_accepted, 0)} · rejected{' '}
                {formatNumber(job.records_rejected, 0)} · skipped {formatNumber(job.records_skipped, 0)}
                {job.duplicate_count ? ` · ${formatNumber(job.duplicate_count, 0)} duplicate(s)` : ''}
              </p>
              {job.data_period?.start || job.data_period?.end ? (
                <p className="subtle">
                  data period {job.data_period.start ?? '—'} → {job.data_period.end ?? '—'}
                </p>
              ) : null}
              {job.error_message ? (
                <p className="mt-1 break-words font-medium text-alert-critical">{job.error_message}</p>
              ) : null}
              {job.validation_errors && job.validation_errors.length > 0 ? (
                <p className="mt-1 subtle">
                  {job.validation_errors.length} validation rejection(s) —{' '}
                  {job.validation_errors.slice(0, 3).map((issue) => `${issue.code}${issue.count ? ` ×${issue.count}` : ''}`).join(', ')}
                </p>
              ) : null}
              {job.executor ? <p className="subtle">executor: {job.executor}</p> : null}
            </li>
          ))}
        </ul>
        <Hint>
          The list is polled every 15 seconds while a dataset is open; the retry button calls{' '}
          <span className="mono">POST /api/v1/ingestion/jobs/&lt;id&gt;/retry</span> and re-reads, so a{' '}
          <span className="mono">max_retries</span> or <span className="mono">not_retryable</span> rejection appears exactly as the server
          phrased it.
        </Hint>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <CodeCell value={dataset.id} label="dataset id" />
      </div>
    </Panel>
  )
}

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2 border-b py-1 last:border-b-0" style={{ borderColor: 'var(--border)' }}>
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="text-right text-xs muted">{value}</dd>
    </div>
  )
}

function StorageLine({ storageUri, sourceUrl, fileName }: { storageUri: string | null; sourceUrl: string | null; fileName: string | null }) {
  const safe = redactIfNeeded('storage_uri', storageUri)
  return (
    <ul className="space-y-1 text-xs muted">
      <li className="flex flex-wrap items-center gap-2">
        <span className="subtle">storage_uri</span>
        {safe.value ? (
          <span className="mono max-w-[15rem] break-all" title={String(safe.value)}>
            {String(safe.value)}
          </span>
        ) : (
          <Badge tone="neutral">not reported</Badge>
        )}
        {safe.redacted ? <Badge tone="warning">credential redacted</Badge> : null}
      </li>
      <li className="flex flex-wrap items-center gap-2">
        <span className="subtle">source_url</span>
        <span className="mono max-w-[15rem] break-all">{sourceUrl || '—'}</span>
      </li>
      <li className="flex flex-wrap items-center gap-2">
        <span className="subtle">file_name</span>
        <span className="mono">{fileName || '—'}</span>
      </li>
      <li className="flex items-start gap-2 subtle">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          Connection strings are never surfaced: a value that looks like a credential is replaced with the redaction the audit layer uses
          (<span className="mono">[redacted]</span>).
        </span>
      </li>
    </ul>
  )
}
