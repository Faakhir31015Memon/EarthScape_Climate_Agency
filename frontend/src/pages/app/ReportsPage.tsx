import { useEffect, useMemo, useState } from 'react'
import { Download, FileDown, RefreshCw, Trash2, X } from 'lucide-react'
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
  Hint,
  MultiSelect,
  Pagination,
  Panel,
  Select,
  Skeleton,
  StatusBadge,
} from '../../components/ui'
import { ALL_METRICS, formatBytes, formatNumber, humanise, metricLabel, relativeTime } from '../../lib/format'
import type { Metric, ReportRecord, ReportTypeCatalogue } from '../../types/api'

/** The formats the typed client accepts, intersected with what a catalogue entry publishes. */
const CLIENT_FORMATS = ['csv', 'json', 'pdf'] as const
type ReportFormat = (typeof CLIENT_FORMATS)[number]

function offeredFormats(formats: string[]): ReportFormat[] {
  return CLIENT_FORMATS.filter((option) => formats.includes(option))
}

function isReportFormat(value: string): value is ReportFormat {
  return (CLIENT_FORMATS as readonly string[]).includes(value)
}

export default function ReportsPage() {
  const { can } = useAuth()
  const canManage = can('reports:manage')

  const types = useAsync(() => api.reports.types(), [])
  const regions = useAsync(() => api.climate.regions(), [])
  const datasets = useAsync(() => api.datasets.list({ page_size: 100 }), [])

  const [page, setPage] = useState(1)
  const [hasGenerating, setHasGenerating] = useState(false)
  const list = useAsync(() => api.reports.list({ page, page_size: 12 }), [page], { interval: hasGenerating ? 5000 : undefined })

  useEffect(() => {
    setHasGenerating(list.data?.items.some((report) => report.status === 'queued' || report.status === 'running') ?? false)
  }, [list.data])

  const [reportType, setReportType] = useState('')
  const [format, setFormat] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [selectedRegions, setSelectedRegions] = useState<string[]>([])
  const [selectedLocations, setSelectedLocations] = useState<string[]>([])
  const [metric, setMetric] = useState<Metric | ''>('')
  const [datasetId, setDatasetId] = useState('')
  const [creating, setCreating] = useState(false)
  const [formError, setFormError] = useState<ApiRequestError | null>(null)
  const [created, setCreated] = useState<ReportRecord | null>(null)

  const [confirm, setConfirm] = useState<{ action: 'delete' | 'cancel'; report: ReportRecord } | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)

  const catalog = types.data
  const selectedType = catalog?.types.find((item) => item.report_type === reportType) ?? null

  const formatOptions = useMemo(() => (selectedType ? offeredFormats(selectedType.formats) : []), [selectedType])
  const chosenFormat: ReportFormat | '' = isReportFormat(format) && formatOptions.includes(format) ? format : formatOptions[0] ?? ''

  const regionOptions = useMemo(() => (regions.data?.regions ?? []).map((region) => ({ value: region.code, label: region.name, hint: region.country ?? undefined })), [regions.data])
  const locationOptions = useMemo(() => (regions.data?.regions ?? []).flatMap((region) => region.locations.map((loc) => ({ value: loc.code, label: loc.name, hint: region.code }))), [regions.data])
  const datasetOptions = useMemo(() => (datasets.data?.items ?? []).map((dataset) => ({ value: dataset.id, label: dataset.name, hint: humanise(dataset.source_type) })), [datasets.data])

  async function create() {
    if (!reportType) return
    setCreating(true)
    setFormError(null)
    setCreated(null)
    try {
      // Filter keys are exactly the vocabulary documented by GET /reports/types (filter_help).
      const filters: Record<string, unknown> = {}
      if (start) filters.start_date = start
      if (end) filters.end_date = end
      if (selectedRegions.length) filters.regions = selectedRegions
      if (selectedLocations.length) filters.location_codes = selectedLocations
      if (metric) filters.metric = metric
      if (datasetId) filters.datasets = [datasetId]
      const record = await api.reports.create({
        report_type: reportType,
        format: chosenFormat || undefined,
        filters,
      })
      setCreated(record)
      void list.refetch()
    } catch (error) {
      setFormError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setCreating(false)
    }
  }

  async function runConfirm() {
    if (!confirm) return
    setConfirmBusy(true)
    try {
      if (confirm.action === 'delete') await api.reports.remove(confirm.report.id)
      else await api.reports.cancel(confirm.report.id)
      setConfirm(null)
      void list.refetch()
    } catch (error) {
      setFormError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
      setConfirm(null)
    } finally {
      setConfirmBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Alerts &amp; reporting</p>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Reports and exports</h1>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
              Generate reports from the catalogue and download them once the file exists. Statuses are the server's own. When a report can't be built, it
              says why: insufficient data, or an export too large.
            </p>
          </div>
        </div>
      </header>

      {types.error ? <ErrorState error={types.error} onRetry={() => void types.refetch()} /> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Panel title="Report types" description="Only report types that can be computed from the current record are selectable; the others show what is blocking them.">
          {types.loading && !catalog ? <Skeleton className="h-40 w-full" /> : null}
          {catalog ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {catalog.types.map((item) => (
                <TypeCard key={item.report_type} item={item} selected={reportType === item.report_type} disabled={!canManage || !item.computable_now} onSelect={() => setReportType(item.report_type)} />
              ))}
            </div>
          ) : null}
          {!canManage ? <Hint>Report generation requires the reports:manage capability (analyst or administrator). Your role can still download completed reports.</Hint> : null}
        </Panel>

        <div className="space-y-6">
          <Panel title="Generate report" description="Filters are passed verbatim to the report generator.">
            <div className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Start date" htmlFor="rp-start">
                  <input id="rp-start" type="date" className="input" value={start} onChange={(event) => setStart(event.target.value)} disabled={!canManage || !selectedType} />
                </Field>
                <Field label="End date" htmlFor="rp-end">
                  <input id="rp-end" type="date" className="input" value={end} onChange={(event) => setEnd(event.target.value)} disabled={!canManage || !selectedType} />
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Regions">
                  <MultiSelect options={regionOptions} value={selectedRegions} onChange={setSelectedRegions} placeholder="All regions" label="Regions" disabled={!canManage || !selectedType} />
                </Field>
                <Field label="Locations">
                  <MultiSelect options={locationOptions} value={selectedLocations} onChange={setSelectedLocations} placeholder="All stations" label="Locations" disabled={!canManage || !selectedType} />
                </Field>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Metric" htmlFor="rp-metric">
                  <Select id="rp-metric" options={[{ value: '', label: 'All metrics' }, ...ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) }))]} value={metric} onChange={(value) => setMetric(value as Metric | '')} disabled={!canManage || !selectedType} />
                </Field>
                <Field label="Dataset (optional)" htmlFor="rp-dataset">
                  <Select id="rp-dataset" options={[{ value: '', label: 'Any dataset' }, ...datasetOptions]} value={datasetId} onChange={setDatasetId} disabled={!canManage || !selectedType} />
                </Field>
              </div>
              <Field label="Format" htmlFor="rp-format" hint={selectedType ? `Formats offered by this report type: ${formatOptions.map((option) => option.toUpperCase()).join(', ') || 'none'}` : 'Choose a report type first.'}>
                <Select
                  id="rp-format"
                  options={formatOptions.map((option) => ({ value: option, label: option.toUpperCase() }))}
                  value={chosenFormat}
                  onChange={setFormat}
                  disabled={!canManage || !selectedType || formatOptions.length === 0}
                />
              </Field>

              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void create()}
                disabled={!canManage || !selectedType || creating}
                title={!canManage ? 'Requires reports:manage role.' : !selectedType ? 'Choose a computable report type first.' : undefined}
              >
                <FileDown className="h-4 w-4" /> {creating ? 'Queuing…' : 'Generate report'}
              </button>

              {created ? (
                <p className="mt-2 text-xs muted" role="status">Report "{created.label}" queued with status <StatusBadge value={created.status} kind="job" />. Watch the table below.</p>
              ) : null}
            </div>
          </Panel>

          <InstantExport
            canExport={canManage}
            metricOptions={ALL_METRICS.map((field) => ({ value: field, label: metricLabel(field) }))}
            regionOptions={regionOptions}
          />
        </div>
      </div>

      {formError ? <ErrorState error={formError} /> : null}

      <Panel
        title="Report history"
        description={hasGenerating ? 'Polling every 5s while a report is generating.' : undefined}
        actions={
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void list.refetch()} disabled={list.refreshing}>
            <RefreshCw className={`h-3.5 w-3.5 ${list.refreshing ? 'animate-spin' : ''}`} /> Refresh
          </button>
        }
      >
        {list.loading ? <Skeleton className="h-40 w-full" /> : null}
        {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
        {!list.loading && (list.data?.items.length ?? 0) === 0 ? (
          <EmptyState title="No reports yet" description="Generate a report above. It appears here with its status." />
        ) : null}
        <DataTable columns={reportColumns(canManage, setConfirm)} rows={list.data?.items ?? []} rowKey={(report) => report.id} empty="No records" />
        <Pagination meta={list.data ?? undefined} onPage={setPage} label="reports" />
      </Panel>

      <ConfirmDialog
        open={Boolean(confirm)}
        title={confirm?.action === 'delete' ? 'Delete report' : 'Cancel report'}
        tone={confirm?.action === 'delete' ? 'danger' : 'primary'}
        confirmLabel={confirm ? humanise(confirm.action) : 'Confirm'}
        busy={confirmBusy}
        onClose={() => setConfirm(null)}
        onConfirm={() => void runConfirm()}
        message={
          confirm?.action === 'delete' ? (
            <p>Delete report <strong>{confirm.report.title || confirm.report.label}</strong> and its generated file? This cannot be undone.</p>
          ) : (
            <p>Cancel generation of <strong>{confirm?.report.title || confirm?.report.label}</strong>? Only a queued report can be withdrawn; the server rejects a cancel request for any other status. Already-generated output is kept.</p>
          )
        }
      />
    </div>
  )
}

function TypeCard({ item, selected, disabled, onSelect }: { item: ReportTypeCatalogue; selected: boolean; disabled: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`card-tight text-left transition-colors ${selected ? 'ring-2 ring-clay-500/60' : ''} ${disabled ? 'opacity-60' : ''}`}
      title={!item.computable_now ? item.reason ?? 'Not computable with current data' : undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-semibold">{item.label}</p>
        <Badge tone={item.computable_now ? 'success' : 'warning'}>{item.computable_now ? 'ready' : 'blocked'}</Badge>
      </div>
      <p className="mt-1 line-clamp-2 text-xs muted">{item.description}</p>
      <div className="mt-2 flex flex-wrap gap-1">
        {item.formats.map((format) => <Badge key={format} tone="neutral">{format.toUpperCase()}</Badge>)}
      </div>
      {item.required_data ? <p className="mt-1 text-[11px] subtle">Needs: {item.required_data}</p> : null}
      {!item.computable_now && item.reason ? <p className="mt-1 text-[11px] text-alert-high">{item.reason}</p> : null}
    </button>
  )
}

function InstantExport({
  canExport,
  metricOptions,
  regionOptions,
}: {
  canExport: boolean
  metricOptions: { value: string; label: string }[]
  regionOptions: { value: string; label: string; hint?: string }[]
}) {
  const [format, setFormat] = useState('csv')
  const [metric, setMetric] = useState<Metric | ''>('')
  const [region, setRegion] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [busy, setBusy] = useState(false)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      const params: Record<string, string> = { format }
      if (metric) params.metric = metric
      if (region) params.region = region
      if (start) params.start = start
      if (end) params.end = end
      await api.reports.instantExport(params)
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Instant export" description="Download a raw csv/json extract of the current selection without a stored report.">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Format" htmlFor="ix-format">
          <Select id="ix-format" options={[{ value: 'csv', label: 'CSV' }, { value: 'json', label: 'JSON' }]} value={format} onChange={setFormat} disabled={!canExport} />
        </Field>
        <Field label="Metric" htmlFor="ix-metric">
          <Select id="ix-metric" options={[{ value: '', label: 'All metrics' }, ...metricOptions]} value={metric} onChange={(value) => setMetric(value as Metric | '')} disabled={!canExport} />
        </Field>
        <Field label="Region" htmlFor="ix-region">
          <Select id="ix-region" options={[{ value: '', label: 'All regions' }, ...regionOptions]} value={region} onChange={setRegion} disabled={!canExport} />
        </Field>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Start" htmlFor="ix-start"><input id="ix-start" type="date" className="input" value={start} onChange={(event) => setStart(event.target.value)} disabled={!canExport} /></Field>
          <Field label="End" htmlFor="ix-end"><input id="ix-end" type="date" className="input" value={end} onChange={(event) => setEnd(event.target.value)} disabled={!canExport} /></Field>
        </div>
      </div>
      <div className="mt-3">
        <button type="button" className="btn btn-secondary" onClick={() => void run()} disabled={!canExport || busy} title={!canExport ? 'Requires export capability.' : undefined}>
          <Download className="h-4 w-4" /> {busy ? 'Preparing…' : 'Download'}
        </button>
        {!canExport ? <Hint>Export requires the climate analyst or administrator role.</Hint> : null}
      </div>
      {error ? <div className="mt-3"><ErrorState error={error} /></div> : null}
    </Panel>
  )
}

function reportColumns(canManage: boolean, onConfirm: (value: { action: 'delete' | 'cancel'; report: ReportRecord }) => void): Column<ReportRecord>[] {
  return [
    {
      key: 'name',
      header: 'Report',
      cell: (report) => (
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{report.title || report.label}</p>
          <p className="text-xs subtle">{humanise(report.report_type)} · {report.format ? report.format.toUpperCase() : 'not recorded'}</p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      cell: (report) => (
        <div className="text-xs">
          <StatusBadge value={report.status} kind="job" />
          {report.status === 'failed' && report.error_message ? <p className="mt-0.5 text-alert-high">{report.error_message}</p> : null}
          {report.insufficient_data ? <p className="mt-0.5 text-solar-700">{report.reason ?? 'Insufficient data for this report.'}</p> : null}
        </div>
      ),
    },
    {
      key: 'requested',
      header: 'Requested by',
      cell: (report) => <span className="text-xs">{report.requested_by_role ? humanise(report.requested_by_role) : report.requested_by ? 'account' : '—'}</span>,
    },
    {
      key: 'filters',
      header: 'Filters',
      cell: (report) => <FilterChips filters={report.filters} />,
    },
    {
      key: 'output',
      header: 'Output',
      align: 'right',
      cell: (report) => <span className="mono text-xs">{report.file_size_bytes != null ? formatBytes(report.file_size_bytes) : report.row_count != null ? `${formatNumber(report.row_count, 0)} rows` : '—'}</span>,
    },
    {
      key: 'created',
      header: 'Created',
      cell: (report) => <span className="text-xs" title={report.created_at}>{relativeTime(report.created_at)}</span>,
    },
    {
      key: 'actions',
      header: 'Actions',
      align: 'right',
      cell: (report) => {
        const downloadable = report.status === 'generated' && Boolean(report.download_name)
        return (
          <div className="flex justify-end gap-2">
            <a
              className={`btn btn-secondary btn-sm ${downloadable ? '' : 'pointer-events-none opacity-55'}`}
              aria-disabled={!downloadable}
              title={downloadable ? 'Download the generated file' : report.status === 'generated' ? 'No file is available for download' : `Cannot download while status is ${report.status}`}
              onClick={(event) => {
                if (!downloadable) {
                  event.preventDefault()
                  return
                }
                event.preventDefault()
                void api.reports.download(report.id, report.download_name ?? `${report.report_type}.${report.format}`)
              }}
              href="#"
            >
              <Download className="h-3.5 w-3.5" /> Download
            </a>
            {canManage ? (
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => onConfirm({ action: 'cancel', report })} aria-label="Cancel report" title={report.status === 'queued' ? 'Cancel queued report' : 'Only a queued report can be cancelled'} disabled={report.status !== 'queued'}>
                <X className="h-4 w-4" />
              </button>
            ) : null}
            {canManage ? (
              <button type="button" className="btn btn-ghost btn-icon" onClick={() => onConfirm({ action: 'delete', report })} aria-label="Delete report" title="Delete" disabled={report.status === 'running'}>
                <Trash2 className="h-4 w-4 text-alert-high" />
              </button>
            ) : null}
          </div>
        )
      },
    },
  ]
}

function FilterChips({ filters }: { filters: Record<string, unknown> }) {
  const entries = Object.entries(filters).filter(([, value]) => value !== undefined && value !== null && (Array.isArray(value) ? value.length > 0 : true))
  if (!entries.length) return <span className="text-xs subtle">All data</span>
  return (
    <div className="flex flex-wrap gap-1">
      {entries.slice(0, 4).map(([key, value]) => (
        <Badge key={key} tone="neutral" title={String(Array.isArray(value) ? value.join(', ') : value)}>
          {humanise(key)}: {Array.isArray(value) ? `${value.length}` : typeof value === 'object' ? 'set' : String(value).slice(0, 18)}
        </Badge>
      ))}
      {entries.length > 4 ? <Badge tone="neutral">+{entries.length - 4}</Badge> : null}
    </div>
  )
}
