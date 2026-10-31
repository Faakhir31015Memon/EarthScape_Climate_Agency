import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { FileSpreadsheet, History as HistoryIcon, RefreshCw, Trash2, UploadCloud, X } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { useToast } from '../../components/ui/ToastProvider'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  MultiSelect,
  PageHeader,
  Panel,
  Progress,
  RefreshButton,
  Select,
  StatCard,
  type Column,
  type Option,
} from '../../components/ui'
import {
  FileCategoryChart,
  FileCorrelationGrid,
  FileForecastHistoryChart,
  FileForecastMonthlyChart,
  FileHeatmapChart,
  FileHistogramChart,
  FileScatterChart,
  FileTrendChart,
  type CategoryVariant,
  type SeriesField,
  type TrendVariant,
} from '../../components/charts/FileCharts'
import { FileMapChart } from '../../components/maps/FileMapChart'
import type { FileAnalytics, FileColumnProfile } from '../../types/api'
import { formatBytes, formatDateTime, formatNumber, humanise, percent, relativeTime } from '../../lib/format'

const MAX_FILES = 10

const TREND_FIELDS: Option[] = [
  { value: 'value', label: 'Average' },
  { value: 'sum', label: 'Total' },
  { value: 'max', label: 'Highest' },
  { value: 'min', label: 'Lowest' },
]

const VARIANTS: Option[] = [
  { value: 'line', label: 'Line' },
  { value: 'area', label: 'Area' },
  { value: 'stacked', label: 'Stacked area' },
  { value: 'bar', label: 'Bars' },
  { value: 'range', label: 'Range band' },
]

const CATEGORY_VARIANTS: Option[] = [
  { value: 'bar', label: 'Bars' },
  { value: 'column', label: 'Columns' },
  { value: 'pie', label: 'Pie' },
  { value: 'donut', label: 'Donut' },
  { value: 'radar', label: 'Radar' },
]

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf('.')
  return dot >= 0 ? fileName.slice(dot).toLowerCase() : ''
}

function numericColumn(column: FileColumnProfile): boolean {
  return column.type === 'number' && (column.histogram?.length ?? 0) > 0
}

/** A numeric column worth a distribution: it has to hold more than one value. */
function spreadColumn(column: FileColumnProfile): boolean {
  return numericColumn(column) && !column.constant && column.distinct > 1
}

function Control({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-[9rem] flex-1">
      <p className="panel-title mb-1">{label}</p>
      {children}
    </div>
  )
}

export default function FileAnalyticsPage() {
  const { can } = useAuth()
  const toast = useToast()
  const canAnalyse = can('datasets:upload')

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [uploadError, setUploadError] = useState<ApiRequestError | null>(null)
  const [targetYear, setTargetYear] = useState('')

  const [analysis, setAnalysis] = useState<FileAnalytics | null>(null)
  const [openingId, setOpeningId] = useState<string | null>(null)
  const [pendingDelete, setPendingDelete] = useState<FileAnalytics | null>(null)
  const [deleting, setDeleting] = useState(false)

  const statusState = useAsync(() => api.system.status(), [], { interval: 300_000 })
  const historyState = useAsync(() => api.fileAnalytics.list({ page_size: 20 }), [analysis?.id])

  const allowedExtensions = useMemo(() => asStringList(statusState.data?.limits?.allowed_upload_extensions), [statusState.data])
  const maxBytes = statusState.data?.limits?.max_upload_bytes ?? null
  const acceptList = allowedExtensions.length > 0 ? allowedExtensions.join(',') : undefined

  const payload = analysis?.payload ?? null

  // controls

  const [trendColumns, setTrendColumns] = useState<string[]>([])
  const [trendField, setTrendField] = useState<SeriesField>('value')
  const [trendVariant, setTrendVariant] = useState<TrendVariant>('line')
  const [histogramColumn, setHistogramColumn] = useState('')
  const [categoryColumn, setCategoryColumn] = useState('')
  const [categoryMetric, setCategoryMetric] = useState('count')
  const [categoryVariant, setCategoryVariant] = useState<CategoryVariant>('bar')
  const [scatterKey, setScatterKey] = useState('')
  const [forecastColumn, setForecastColumn] = useState('')

  useEffect(() => {
    if (!payload) return
    setTrendColumns(payload.series.slice(0, 2).map((entry) => entry.column))
    const spreadColumns = payload.columns.filter(spreadColumn)
    setHistogramColumn((spreadColumns.find((column) => column.distinct >= 5) ?? spreadColumns[0])?.column ?? '')
    setCategoryColumn(payload.category_totals.find((group) => group.items.length > 1)?.column ?? payload.category_totals[0]?.column ?? '')
    setCategoryMetric('count')
    setScatterKey(payload.scatter[0] ? `${payload.scatter[0].x}|${payload.scatter[0].y}` : '')
    setForecastColumn(payload.forecast.columns?.[0]?.column ?? '')
  }, [payload])

  const trendOptions: Option[] = useMemo(
    () => payload?.series.map((entry) => ({ value: entry.column, label: humanise(entry.column), hint: `${entry.points.length} points` })) ?? [],
    [payload],
  )
  const histogramOptions: Option[] = useMemo(
    () =>
      payload?.columns
        .filter(numericColumn)
        .map((column) => ({ value: column.column, label: humanise(column.column), hint: `${column.values ?? 0} values` })) ?? [],
    [payload],
  )
  const categoryOptions: Option[] = useMemo(() => payload?.category_totals.map((group) => ({ value: group.column, label: humanise(group.column) })) ?? [], [payload])

  const categoryGroup = useMemo(
    () => payload?.category_totals.find((group) => group.column === categoryColumn) ?? payload?.category_totals[0] ?? null,
    [payload, categoryColumn],
  )

  const categoryMetricOptions: Option[] = useMemo(() => {
    if (!categoryGroup) return []
    const options: Option[] = [{ value: 'count', label: 'Record count' }]
    categoryGroup.measures.forEach((measure) => {
      const key = `mean_${measure}`
      if (categoryGroup.items.some((item) => typeof item[key] === 'number')) {
        options.push({ value: key, label: `Average ${humanise(measure)}` })
      }
    })
    return options
  }, [categoryGroup])

  const scatterOptions: Option[] = useMemo(
    () => payload?.scatter.map((pair) => ({ value: `${pair.x}|${pair.y}`, label: `${humanise(pair.x)} vs ${humanise(pair.y)}` })) ?? [],
    [payload],
  )

  const trendSeries = useMemo(
    () => (payload ? payload.series.filter((entry) => trendColumns.includes(entry.column)) : []),
    [payload, trendColumns],
  )
  const histogramProfile = useMemo(
    () => payload?.columns.find((column) => column.column === histogramColumn) ?? null,
    [payload, histogramColumn],
  )
  const scatterPair = useMemo(
    () => payload?.scatter.find((pair) => `${pair.x}|${pair.y}` === scatterKey) ?? null,
    [payload, scatterKey],
  )
  const forecastColumns = useMemo(() => payload?.forecast.columns ?? [], [payload])
  const forecastRecord = useMemo(
    () => forecastColumns.find((entry) => entry.column === forecastColumn) ?? forecastColumns[0] ?? null,
    [forecastColumns, forecastColumn],
  )
  const forecastOptions: Option[] = useMemo(
    () =>
      forecastColumns.map((entry) => ({
        value: entry.column,
        label: humanise(entry.column),
        hint: `${entry.target_year} · average ${formatNumber(entry.annual, 1)}`,
      })),
    [forecastColumns],
  )

  // upload

  const oversized = files.filter((entry) => maxBytes !== null && entry.size > Number(maxBytes))
  const wrongExtension = files.filter((entry) => allowedExtensions.length > 0 && !allowedExtensions.includes(extensionOf(entry.name)))
  const totalBytes = files.reduce((sum, entry) => sum + entry.size, 0)

  function addFiles(picked: File[]) {
    if (picked.length === 0) return
    const merged = [...files]
    for (const entry of picked) {
      if (merged.length >= MAX_FILES) {
        toast.warning('Only ten files at a time', `The extra ${picked.length} file(s) were not added.`)
        break
      }
      if (merged.some((have) => have.name === entry.name && have.size === entry.size)) continue
      merged.push(entry)
    }
    setFiles(merged)
    setUploadError(null)
  }

  async function analyse() {
    if (files.length === 0) return
    setBusy(true)
    setUploadError(null)
    setUploadProgress(0)
    try {
      const year = Number.parseInt(targetYear, 10)
      const result = await api.fileAnalytics.upload(files, Number.isFinite(year) ? year : undefined, (value) => setUploadProgress(value))
      setAnalysis(result)
      setFiles([])
      toast.success(
        result.file_count && result.file_count > 1 ? 'Files analysed' : 'File analysed',
        `${result.file_name}: ${formatNumber(result.rows_analyzed, 0)} rows across ${result.column_count} columns.`,
      )
      void historyState.refetch()
    } catch (error) {
      const requestError = error instanceof ApiRequestError ? error : new ApiRequestError('Could not analyse these files.', 'upload_failed', 500)
      setUploadError(requestError)
      toast.danger('Analysis failed', requestError.message)
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }

  async function reopen(record: FileAnalytics) {
    setOpeningId(record.id)
    try {
      const full = await api.fileAnalytics.get(record.id)
      setAnalysis(full)
      setUploadError(null)
    } catch (error) {
      toast.danger('Could not open this analysis', error instanceof Error ? error.message : 'Unknown error.')
    } finally {
      setOpeningId(null)
    }
  }

  async function confirmDelete() {
    const record = pendingDelete
    if (!record) return
    setDeleting(true)
    try {
      await api.fileAnalytics.remove(record.id)
      if (analysis?.id === record.id) setAnalysis(null)
      setPendingDelete(null)
      toast.info('Analysis removed', `${record.file_name} is no longer in your history.`)
      void historyState.refetch()
    } catch (error) {
      toast.danger('Could not remove it', error instanceof Error ? error.message : 'Unknown error.')
    } finally {
      setDeleting(false)
    }
  }

  // table

  const columnRows: Column<FileColumnProfile>[] = [
    { key: 'column', header: 'Column', cell: (row) => <span className="font-medium">{humanise(row.column)}</span> },
    { key: 'type', header: 'Detected type', cell: (row) => <Badge tone={typeTone(row.type)}>{row.type}</Badge> },
    {
      key: 'values',
      header: 'Values',
      align: 'right',
      cell: (row) => <span className="mono">{formatNumber(row.values ?? 0, 0)}</span>,
    },
    {
      key: 'distinct',
      header: 'Distinct',
      align: 'right',
      cell: (row) => (
        <span className="mono" title={row.distinct_capped ? 'Over 480 different values — counting stopped there.' : undefined}>
          {formatNumber(row.distinct, 0)}
          {row.distinct_capped ? '+' : ''}
        </span>
      ),
    },
    {
      key: 'missing',
      header: 'Missing',
      align: 'right',
      cell: (row) => (row.missing_rate > 0 ? <span className="mono">{percent(row.missing_rate)}</span> : <span className="subtle">—</span>),
    },
    {
      key: 'summary',
      header: 'What it holds',
      cell: (row) => <span className="text-xs muted">{summarise(row)}</span>,
    },
  ]

  const previewColumns: Column<Record<string, string | number | boolean | null>>[] = useMemo(() => {
    if (!payload) return []
    return payload.preview.headers.map((header) => ({
      key: header,
      header: <span className="whitespace-nowrap">{humanise(header)}</span>,
      cell: (row) => {
        const value = row[header]
        if (value === null || value === undefined || value === '') return <span className="subtle">—</span>
        if (typeof value === 'number') return <span className="mono">{formatNumber(value, 2)}</span>
        return <span className="text-xs">{String(value)}</span>
      },
    }))
  }, [payload])

  const historyColumns: Column<FileAnalytics>[] = [
    {
      key: 'file_name',
      header: 'File',
      cell: (row) => (
        <div className="min-w-0">
          <p className="truncate font-medium">{row.file_name}</p>
          <p className="truncate text-xs subtle">
            {row.format.toUpperCase()} · {formatBytes(row.size_bytes)}
            {row.file_count && row.file_count > 1 ? ` · ${row.file_count} files` : ''}
          </p>
        </div>
      ),
    },
    { key: 'rows', header: 'Rows', align: 'right', cell: (row) => <span className="mono">{formatNumber(row.rows_analyzed, 0)}</span> },
    { key: 'columns', header: 'Columns', align: 'right', cell: (row) => <span className="mono">{row.column_count}</span> },
    { key: 'time', header: 'Time axis', cell: (row) => (row.summary.time_column ? <span className="text-xs">{humanise(row.summary.time_column)}</span> : <span className="subtle">none</span>) },
    { key: 'created', header: 'Analysed', cell: (row) => <span title={formatDateTime(row.created_at)}>{relativeTime(row.created_at)}</span> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (row) => (
        <div className="flex items-center justify-end gap-1">
          <button type="button" className="btn-ghost btn-sm inline-flex items-center gap-1.5" onClick={() => void reopen(row)} disabled={openingId === row.id}>
            <RefreshCw className={`h-3.5 w-3.5 ${openingId === row.id ? 'animate-spin' : ''}`} aria-hidden="true" />
            {analysis?.id === row.id ? 'Open' : 'Reopen'}
          </button>
          <button type="button" className="btn-ghost btn-sm" aria-label={`Delete analysis of ${row.file_name}`} onClick={() => setPendingDelete(row)}>
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      ),
    },
  ]

  // render

  if (!canAnalyse) {
    return (
      <>
        <PageHeader eyebrow="Data & pipelines" title="File analytics" />
        <EmptyState
          tone="warning"
          icon={<FileSpreadsheet className="h-5 w-5" />}
          title="Analysing a file needs the upload capability"
          description="Your session has not been issued it. Sign out and sign in again to refresh the session."
        />
      </>
    )
  }

  return (
    <>
      <PageHeader
        eyebrow="Data & pipelines"
        title="File analytics"
        description="Upload one table or a batch of them and read them as charts. Every column is profiled on its own — numeric columns get distributions and trends, categories get breakdowns, dates become a time axis, coordinates become a map, and a year-ahead projection is fitted when the file spans enough years."
        meta={analysis ? (
          <>
            <Badge tone="success">{analysis.file_count && analysis.file_count > 1 ? `${analysis.file_count} files` : analysis.file_name}</Badge>
            <span>{analysis.format.toUpperCase()}</span>
            <span>{formatBytes(analysis.size_bytes)}</span>
            <span>{formatNumber(analysis.rows_analyzed, 0)} rows</span>
            {analysis.truncated ? <span className="text-solar-700 dark:text-solar-300">partial profile</span> : null}
          </>
        ) : null}
        actions={<RefreshButton onClick={() => void historyState.refetch()} busy={historyState.refreshing} label="Refresh history" />}
      />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel title="Analyse files" description="Files are read once, combined, summarised and then discarded — only the summary is kept.">
          <div className="space-y-4">
            <div
              role="button"
              tabIndex={0}
              aria-label="Choose files to analyse, or drop them here"
              onClick={() => !busy && fileInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  if (!busy) fileInputRef.current?.click()
                }
              }}
              onDragOver={(event) => {
                event.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault()
                setDragging(false)
                addFiles(Array.from(event.dataTransfer.files))
              }}
              className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center transition-colors"
              style={{
                borderColor: dragging ? 'var(--accent, #16a34a)' : 'var(--border-strong)',
                backgroundColor: dragging ? 'rgba(22, 163, 74, 0.06)' : 'var(--surface-muted)',
              }}
            >
              <UploadCloud className="h-6 w-6 subtle" aria-hidden="true" />
              <p className="text-sm font-medium">{dragging ? 'Drop the files to add them' : 'Drag files here, or click to browse'}</p>
              <p className="text-xs subtle">
                {allowedExtensions.length > 0 ? allowedExtensions.join(' · ') : 'CSV, JSON, Excel, ODS, Parquet'}
                {maxBytes !== null ? ` · up to ${formatBytes(Number(maxBytes))} each` : ''} · up to {MAX_FILES} at a time
              </p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="sr-only"
              accept={acceptList}
              onChange={(event) => {
                addFiles(Array.from(event.target.files ?? []))
                event.target.value = ''
              }}
            />

            {files.length > 0 ? (
              <ul className="divide-y rounded-lg border" style={{ borderColor: 'var(--border)' }}>
                {files.map((entry, index) => (
                  <li key={`${entry.name}-${entry.size}-${index}`} className="flex items-center gap-3 p-2.5 text-sm">
                    <FileSpreadsheet className="h-4 w-4 shrink-0 subtle" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                    <span className="mono text-xs subtle">{formatBytes(entry.size)}</span>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      aria-label={`Remove ${entry.name} from the list`}
                      onClick={() => setFiles((current) => current.filter((_item, position) => position !== index))}
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            ) : null}
            {oversized.length > 0 ? (
              <p className="text-xs text-alert-high">{`${oversized.length} file(s) are above the ${formatBytes(Number(maxBytes))} limit and will be refused.`}</p>
            ) : null}
            {wrongExtension.length > 0 ? (
              <p className="text-xs text-alert-high">{`${wrongExtension.map((entry) => extensionOf(entry.name) || 'a file without an extension').join(', ')} cannot be analysed.`}</p>
            ) : null}

            <div className="flex flex-wrap items-end gap-3">
              <Control label="Project which year?">
                <input
                  type="number"
                  inputMode="numeric"
                  min={1900}
                  max={2200}
                  value={targetYear}
                  onChange={(event) => setTargetYear(event.target.value)}
                  placeholder="next year after the file"
                  className="input w-56"
                  aria-label="Target year for the projection"
                />
              </Control>
              <button type="button" className="btn btn-primary" onClick={() => void analyse()} disabled={busy || files.length === 0}>
                <UploadCloud className="h-4 w-4" aria-hidden="true" />
                {busy ? 'Analysing…' : files.length > 1 ? `Analyse ${files.length} files` : 'Analyse file'}
              </button>
              {files.length > 0 && !busy ? (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setFiles([])}>
                  Clear list
                </button>
              ) : null}
            </div>
            <p className="text-xs subtle">
              {files.length > 0 ? `${formatBytes(totalBytes)} staged · columns that line up across the files are merged into one table.` : 'Leave the year blank to project the year straight after the last one in the file.'}
            </p>

            {uploadProgress !== null ? (
              <div>
                <p className="panel-title mb-1.5">Analysing · {uploadProgress}%</p>
                <Progress value={uploadProgress} tone="hydro" label="Upload progress" />
              </div>
            ) : null}
            {uploadError ? <ErrorState error={uploadError} /> : null}
            {busy && uploadProgress === null ? <p className="text-xs subtle">Reading the files…</p> : null}
          </div>
        </Panel>

        <Panel title="Saved analyses" description="Every analysis you keep can be reopened or removed." actions={<RefreshButton onClick={() => void historyState.refetch()} busy={historyState.refreshing} />}>
          {historyState.error ? (
            <ErrorState error={historyState.error} onRetry={() => void historyState.refetch()} />
          ) : (
            <DataTable
              columns={historyColumns}
              rows={historyState.data?.items ?? []}
              rowKey={(row) => row.id}
              loading={historyState.loading}
              compact
              empty={
                <p className="py-6 text-center text-sm muted">
                  <HistoryIcon className="mr-1.5 inline h-4 w-4" aria-hidden="true" />
                  Nothing analysed yet.
                </p>
              }
            />
          )}
        </Panel>
      </div>

      {analysis && payload ? (
        <div className="mt-5 space-y-5">
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard label="Rows profiled" value={formatNumber(payload.rows_analyzed, 0)} hint={payload.truncated ? 'first block of the file' : 'whole file'} />
            <StatCard label="Columns" value={payload.column_count} hint={`${payload.measures.length} numeric · ${payload.dimensions.length} category`} />
            <StatCard label="Completeness" value={percent(payload.quality.completeness)} tone="hydro" hint={`${formatNumber(payload.quality.duplicate_rows, 0)} duplicate rows`} />
            <StatCard label="Time axis" value={payload.time_column ? humanise(payload.time_column) : '—'} size="text" hint={payload.time_column ? `${payload.series.length} trends available` : 'no date column found'} />
            <StatCard
              label="Spread"
              value={payload.columns.filter((column) => column.type === 'number' && !column.constant).length}
              hint={`${payload.quality.constant_columns.length} constant column${payload.quality.constant_columns.length === 1 ? '' : 's'}`}
            />
          </div>

          <Panel
            title="Trend over time"
            description="One line per numeric column, averaged into the same time buckets."
          >
            {payload.series.length === 0 ? (
              <EmptyState
                title="No date column was found"
                description="Trends need one column that holds dates. Distribution, category and relationship charts below still work."
              />
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-4">
                  <Control label="Columns">
                    <MultiSelect
                      options={trendOptions}
                      value={trendColumns}
                      onChange={(next) => setTrendColumns(next.slice(0, 6))}
                      placeholder="Pick a column"
                      maxChips={2}
                    />
                  </Control>
                  <Control label="Statistic">
                    <Select options={TREND_FIELDS} value={trendField} onChange={(next) => setTrendField(next as SeriesField)} />
                  </Control>
                  <Control label="Chart">
                    <Select options={VARIANTS} value={trendVariant} onChange={(next) => setTrendVariant(next as TrendVariant)} />
                  </Control>
                </div>
                {trendSeries.length === 0 ? (
                  <p className="py-6 text-center text-sm muted">Pick at least one column to plot.</p>
                ) : (
                  <FileTrendChart series={trendSeries} field={trendField} variant={trendVariant} />
                )}
              </div>
            )}
          </Panel>

          <div className="grid gap-5 xl:grid-cols-2">
            <Panel title="Distribution" description="How often each range of a numeric column occurs.">
              {histogramOptions.length === 0 ? (
                <p className="py-6 text-center text-sm muted">No numeric column holds enough values to chart.</p>
              ) : (
                <div className="space-y-4">
                  <Control label="Column">
                    <Select options={histogramOptions} value={histogramColumn} onChange={setHistogramColumn} className="max-w-xs" />
                  </Control>
                  {histogramProfile ? (
                    <>
                      <FileHistogramChart bins={histogramProfile.histogram ?? []} label={histogramProfile.column} />
                      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
                        {[
                          ['Min', histogramProfile.min],
                          ['Q1', histogramProfile.q1],
                          ['Median', histogramProfile.median],
                          ['Q3', histogramProfile.q3],
                          ['Max', histogramProfile.max],
                          ['Mean', histogramProfile.mean],
                          ['Std dev', histogramProfile.std],
                          ['Outliers', histogramProfile.outliers],
                        ].map(([label, value]) => (
                          <div key={String(label)} className="flex items-baseline justify-between gap-2 border-b pb-1" style={{ borderColor: 'var(--border)' }}>
                            <span className="subtle">{String(label)}</span>
                            <span className="mono">{typeof value === 'number' ? formatNumber(value, value === histogramProfile.outliers ? 0 : 2) : '—'}</span>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <p className="py-6 text-center text-sm muted">Pick a column to see how its values are spread.</p>
                  )}
                </div>
              )}
            </Panel>

            <Panel title="By category" description="Counts per category, or the average of a numeric column inside each one.">
              {categoryOptions.length === 0 ? (
                <p className="py-6 text-center text-sm muted">No categorical column was detected.</p>
              ) : (
                <div className="space-y-4">
                  <div className="flex flex-wrap gap-4">
                    <Control label="Column">
                      <Select options={categoryOptions} value={categoryGroup?.column ?? ''} onChange={setCategoryColumn} />
                    </Control>
                    <Control label="Measure">
                      <Select options={categoryMetricOptions} value={categoryMetric} onChange={setCategoryMetric} />
                    </Control>
                    <Control label="Chart">
                      <Select options={CATEGORY_VARIANTS} value={categoryVariant} onChange={(next) => setCategoryVariant(next as CategoryVariant)} className="max-w-[8rem]" />
                    </Control>
                  </div>
                  {categoryGroup ? (
                    categoryGroup.items.length > 1 ? (
                      <FileCategoryChart items={categoryGroup.items} metricKey={categoryMetric} variant={categoryVariant} />
                    ) : (
                      <p className="py-6 text-center text-sm muted">
                        Every row holds the same value in {humanise(categoryGroup.column)}, so there is nothing to compare across categories.
                      </p>
                    )
                  ) : null}
                </div>
              )}
            </Panel>
          </div>

          {payload.geo ? (
            <Panel
              title="Where the readings came from"
              description={`Grouped by ${humanise(payload.geo.latitude_column)} and ${humanise(payload.geo.longitude_column)} — a bigger circle means more readings at that spot, a darker one a higher ${payload.geo.measure ? humanise(payload.geo.measure) : 'value'}.`}
            >
              <FileMapChart geo={payload.geo} />
            </Panel>
          ) : null}

          {payload.heatmap ? (
            <Panel
              title="Season by season"
              description="Every month of every year in the file, shaded against the overall average so a repeating season stands out."
            >
              <FileHeatmapChart heatmap={payload.heatmap} />
            </Panel>
          ) : null}

          <Panel
            title={`${payload.forecast.target_year ?? 'Next'} year projection`}
            description="Fitted from the file's own yearly averages: a straight trend through the past years plus the seasonal shape of each month."
          >
            {payload.forecast.available && forecastRecord ? (
              <div className="space-y-4">
                {forecastColumns.length > 1 ? (
                  <Control label="Projected column">
                    <Select options={forecastOptions} value={forecastRecord.column} onChange={setForecastColumn} className="max-w-xs" />
                  </Control>
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <StatCard
                    label={`${forecastRecord.target_year} average`}
                    value={formatNumber(forecastRecord.annual, 1)}
                    hint={`${formatNumber(forecastRecord.annual_low, 1)} to ${formatNumber(forecastRecord.annual_high, 1)} likely range`}
                  />
                  <StatCard
                    label="Yearly trend"
                    value={`${(forecastRecord.trend_per_year ?? 0) >= 0 ? '+' : ''}${formatNumber(forecastRecord.trend_per_year, 3)}`}
                    tone="solar"
                    hint={`change per year across ${forecastRecord.observed.length} year(s)`}
                  />
                  <StatCard
                    label="Missed by"
                    value={formatNumber(forecastRecord.metrics.mae, 2)}
                    tone="hydro"
                    hint={`average error when a year was held back · ${forecastRecord.metrics.years_tested} year(s) tested`}
                  />
                  <StatCard
                    label="Better than the average?"
                    value={
                      forecastRecord.metrics.skill_vs_average === null
                        ? '—'
                        : `${forecastRecord.metrics.skill_vs_average >= 0 ? '+' : ''}${percent(forecastRecord.metrics.skill_vs_average)}`
                    }
                    tone={forecastRecord.metrics.skill_vs_average !== null && forecastRecord.metrics.skill_vs_average >= 0 ? 'forest' : 'solar'}
                    hint={
                      forecastRecord.metrics.skill_vs_average === null
                        ? 'not enough years to compare'
                        : forecastRecord.metrics.skill_vs_average >= 0
                          ? 'the trend beats just using the past average'
                          : 'the past average alone would forecast this file better'
                    }
                  />
                </div>
                <FileForecastMonthlyChart record={forecastRecord} />
                <FileForecastHistoryChart record={forecastRecord} />
                <p className="text-xs subtle">
                  {`Built on ${humanise(forecastRecord.column)} from ${forecastRecord.basis_years.first} to ${forecastRecord.basis_years.last}${forecastRecord.extrapolated ? `, projected beyond those years` : ''}. `}
                  {payload.forecast.skipped && payload.forecast.skipped.length > 0 ? `Other columns could not be projected: ${payload.forecast.skipped.join(' ')}` : ''}
                </p>
              </div>
            ) : (
              <EmptyState
                title="No projection from this file"
                description={payload.forecast.reason ?? 'The file needs at least three years of dated numbers before a future year can be projected honestly.'}
              />
            )}
          </Panel>

          <div className="grid gap-5 xl:grid-cols-2">
            <Panel title="Relationships" description="How each numeric column moves with every other one.">
              {payload.correlation.columns.length < 2 ? (
                <p className="py-6 text-center text-sm muted">Two or more numeric columns are needed before anything can be compared.</p>
              ) : (
                <FileCorrelationGrid columns={payload.correlation.columns} cells={payload.correlation.cells} />
              )}
            </Panel>
            <Panel title="Scatter" description="The strongest pairs, drawn point by point.">
              {scatterOptions.length === 0 ? (
                <p className="py-6 text-center text-sm muted">At least two numeric columns are needed to plot a pair.</p>
              ) : (
                <div className="space-y-4">
                  <Control label="Pair">
                    <Select options={scatterOptions} value={scatterKey} onChange={setScatterKey} className="max-w-sm" />
                  </Control>
                  {scatterPair ? <FileScatterChart pair={scatterPair} /> : null}
                </div>
              )}
            </Panel>
          </div>

          <Panel title="Column summary" description="What the reader found in each column, including the ones not charted.">
            <DataTable columns={columnRows} rows={payload.columns} rowKey={(row) => row.column} compact empty={<p className="py-6 text-center text-sm muted">No columns were read.</p>} />
          </Panel>

          <Panel title="First rows" description={`${payload.preview.rows.length} of ${formatNumber(payload.rows_analyzed, 0)} rows, exactly as the reader saw them.`}>
            <DataTable
              columns={previewColumns}
              rows={payload.preview.rows}
              rowKey={(_row, index) => String(index)}
              compact
              empty={<p className="py-6 text-center text-sm muted">No rows in this file.</p>}
            />
          </Panel>
        </div>
      ) : (
        <div className="mt-5">
          <EmptyState
            icon={<FileSpreadsheet className="h-5 w-5" />}
            title="Nothing to chart yet"
            description="Pick a file above, or reopen one of your saved analyses. A single CSV, JSON, Excel, ODS or Parquet table is enough."
          />
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Remove this analysis?"
        message={pendingDelete ? `${pendingDelete.file_name} will disappear from your saved analyses. The summary cannot be recovered.` : ''}
        confirmLabel="Remove"
        tone="danger"
        busy={deleting}
        onConfirm={() => void confirmDelete()}
        onClose={() => setPendingDelete(null)}
      />
    </>
  )
}

function typeTone(type: FileColumnProfile['type']): 'success' | 'info' | 'warning' | 'neutral' {
  if (type === 'number') return 'success'
  if (type === 'datetime') return 'info'
  if (type === 'boolean') return 'warning'
  return 'neutral'
}

function summarise(column: FileColumnProfile): string {
  if (column.type === 'number') {
    if (column.constant) return `one repeated value: ${formatNumber(column.mean, 2)}`
    return `${formatNumber(column.min, 2)} to ${formatNumber(column.max, 2)} · mean ${formatNumber(column.mean, 2)} · ${column.outliers ?? 0} outside the usual range`
  }
  if (column.type === 'datetime') {
    const first = column.first ? formatDateTime(column.first) : '—'
    const last = column.last ? formatDateTime(column.last) : '—'
    return `${first} → ${last} · ${column.distinct_days ?? 0} distinct days`
  }
  if (column.type === 'boolean') {
    const items = column.items ?? []
    return items.map((item) => `${item.label} ${percent(item.share, 0)}`).join(' · ') || 'true / false'
  }
  if (column.type === 'empty') return 'no values in the analysed rows'
  const top = (column.top ?? []).slice(0, 3)
  return top.length > 0
    ? `${top.map((item) => `${item.label} (${percent(item.share, 0)})`).join(' · ')}${column.high_cardinality ? ' · many distinct values' : ''}`
    : 'text values'
}
