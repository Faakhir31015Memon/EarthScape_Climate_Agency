/**
 * Upload — the real dataset-import flow. `POST /datasets/upload` (multipart: file + metadata)
 * answers 202 with the created dataset, its detected format, the auto column mapping and the
 * ingestion job that already started. This page then reads `GET /datasets/{id}/mapping` into an
 * editable mapping table (`POST /datasets/{id}/mapping`), re-runs the import via
 * `POST /datasets/{id}/ingest`, and polls `GET /ingestion/jobs/{id}` until the job reaches a
 * terminal status. Size/row limits come from the live `/system/status` config; every failure is
 * rendered verbatim from the error envelope `{error:{code,message,details?,request_id}}`.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertOctagon, FileUp, PlayCircle, RotateCw, Save, Sparkles, UploadCloud, X } from 'lucide-react'
import { api } from '../../services/api'
import { ApiRequestError } from '../../services/http'
import type { DatasetUploadResponse, SourceType } from '../../types/api'
import { useAsync } from '../../hooks/useAsync'
import { ALL_METRICS, formatBytes, formatNumber, humanise, metricLabel, relativeTime } from '../../lib/format'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Hint,
  Panel,
  Progress,
  RefreshButton,
  Select,
  StatusBadge,
  Toggle,
  type Column,
  type Option,
} from '../../components/ui'
import { PageHeader } from '../../components/ui/PageHeader'
import { useToast } from '../../components/ui/ToastProvider'
import { useAuth } from '../../features/auth/AuthContext'

const KNOWN_METRIC_FIELDS: readonly string[] = ALL_METRICS

const SOURCE_OPTIONS: Option[] = [
  { value: 'university_upload', label: 'University upload' },
  { value: 'nasa_power', label: 'NASA POWER' },
  { value: 'noaa', label: 'NOAA' },
  { value: 'live_api', label: 'Live API' },
  { value: 'sample', label: 'Sample generator' },
]

function asSourceType(value: string): SourceType {
  return SOURCE_OPTIONS.some((option) => option.value === value) ? (value as SourceType) : 'university_upload'
}

function toApiError(error: unknown): ApiRequestError {
  return error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0)
}

/** Turns the envelope's `details` (record / list / string) into displayable lines. */
function describeDetails(details: unknown): string[] {
  if (details === null || details === undefined) return []
  if (typeof details === 'string') return [details]
  if (Array.isArray(details)) {
    return details.map((item) => (typeof item === 'string' ? item : JSON.stringify(item)))
  }
  if (typeof details === 'object') {
    return Object.entries(details as Record<string, unknown>).map(([key, value]) => {
      const text = Array.isArray(value)
        ? value.map((item) => (typeof item === 'string' ? item : JSON.stringify(item))).join(' · ')
        : typeof value === 'string'
          ? value
          : JSON.stringify(value)
      return `${key}: ${text}`
    })
  }
  return [String(details)]
}

function asFiniteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function extensionOf(fileName: string): string {
  const dot = fileName.lastIndexOf('.')
  return dot >= 0 ? fileName.slice(dot).toLowerCase() : ''
}

function fieldLabel(field: string): string {
  return KNOWN_METRIC_FIELDS.includes(field) ? metricLabel(field) : humanise(field)
}

export default function UploadPage() {
  const { can } = useAuth()
  const toast = useToast()
  const canUpload = can('datasets:upload')

  const fileInputRef = useRef<HTMLInputElement>(null)

  /* --------------------------------------------------------- live config */

  const formatsState = useAsync(() => api.datasets.formats(), [])
  const statusState = useAsync(() => api.system.status(), [], { interval: 300_000 })

  const maxUploadBytes = asFiniteNumber(statusState.data?.limits?.max_upload_bytes)
  const maxUploadRows = asFiniteNumber(statusState.data?.limits?.max_upload_rows)
  const allowedExtensions = useMemo(() => asStringList(statusState.data?.limits?.allowed_upload_extensions), [statusState.data])

  /* -------------------------------------------------------- upload state */

  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [uploadError, setUploadError] = useState<ApiRequestError | null>(null)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [sourceType, setSourceType] = useState<SourceType>('university_upload')
  const [sourceName, setSourceName] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [tags, setTags] = useState('')
  const [unitsJson, setUnitsJson] = useState('')
  const [isPublic, setIsPublic] = useState(false)
  const [defaultLocationCode, setDefaultLocationCode] = useState('')
  const [defaultRegionCode, setDefaultRegionCode] = useState('')
  const [defaultCountry, setDefaultCountry] = useState('')
  const [defaultLatitude, setDefaultLatitude] = useState('')
  const [defaultLongitude, setDefaultLongitude] = useState('')

  /* --------------------------------------------------- created dataset */

  const [uploadResult, setUploadResult] = useState<DatasetUploadResponse | null>(null)
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobFinished, setJobFinished] = useState(false)

  /* ------------------------------------------------------ mapping state */

  const mappingState = useAsync(() => api.datasets.mapping(datasetId ?? '-'), [datasetId], {
    enabled: datasetId !== null,
  })
  const [mappingDraft, setMappingDraft] = useState<Record<string, string>>({})
  const [mappingSaving, setMappingSaving] = useState(false)
  const [mappingSaveError, setMappingSaveError] = useState<ApiRequestError | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestError, setSuggestError] = useState<ApiRequestError | null>(null)
  const [ingestRunning, setIngestRunning] = useState(false)
  const [ingestError, setIngestError] = useState<ApiRequestError | null>(null)

  useEffect(() => {
    const doc = mappingState.data
    if (doc) setMappingDraft({ ...doc.current_mapping })
  }, [mappingState.data])

  /* -------------------------------------------------------- job polling */

  const jobState = useAsync(() => api.ingestion.get(jobId ?? '-'), [jobId], {
    enabled: jobId !== null,
    interval: jobFinished ? undefined : 4000,
  })
  useEffect(() => {
    const status = jobState.data?.status
    if (!status) return
    setJobFinished(status !== 'queued' && status !== 'running')
  }, [jobState.data])

  const datasetState = useAsync(() => api.datasets.get(datasetId ?? '-'), [datasetId, jobFinished], {
    enabled: datasetId !== null && jobFinished,
  })

  const job = jobState.data

  /* ------------------------------------------------------- local checks */

  const extension = file ? extensionOf(file.name) : null
  const extensionProblem =
    file && extension !== null && allowedExtensions.length > 0 && !allowedExtensions.includes(extension)
      ? `'${extension || 'no extension'}' is not in the server's allowed list (${allowedExtensions.join(', ')}). The upload would be rejected.`
      : null
  const sizeProblem =
    file && maxUploadBytes !== null && file.size > maxUploadBytes
      ? `File is ${formatBytes(file.size)} — above the configured maximum of ${formatBytes(maxUploadBytes)}. The upload would be rejected.`
      : null
  const unitsProblem = (() => {
    const text = unitsJson.trim()
    if (!text) return null
    try {
      const parsed: unknown = JSON.parse(text)
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return 'Units must be a JSON object like {"temperature_c": "C"}.'
      }
      return null
    } catch {
      return 'Units is not valid JSON — fix it or leave it empty.'
    }
  })()

  const canSubmit =
    canUpload &&
    file !== null &&
    name.trim().length > 0 &&
    !extensionProblem &&
    !sizeProblem &&
    !unitsProblem &&
    !uploading

  const formatRule = useMemo(() => {
    if (!extension || !formatsState.data) return null
    const id = extension.replace('.', '')
    return formatsState.data.formats.find((item) => item.format === id) ?? null
  }, [extension, formatsState.data])

  const acceptList = allowedExtensions.length > 0
    ? allowedExtensions.join(',')
    : '.csv,.json,.txt,.xls,.xlsx,.xlsm,.xlsb,.xltx,.xltm,.ods,.parquet'

  /* ------------------------------------------------------------ actions */

  const chooseFile = (next: File) => {
    setFile(next)
    setUploadError(null)
    setName((current) => (current.trim() ? current : next.name.replace(/\.[^.]+$/, '')))
  }

  const submit = () => {
    if (!file || !canSubmit) return
    setUploading(true)
    setUploadError(null)
    setUploadProgress(0)
    api.datasets
      .upload(
        file,
        {
          name: name.trim(),
          description: description.trim() || undefined,
          source_type: sourceType,
          source_name: sourceName.trim() || undefined,
          source_url: sourceUrl.trim() || undefined,
          tags: tags.trim() || undefined,
          units: unitsJson.trim() || undefined,
          is_public: isPublic ? 'true' : 'false',
          default_location_code: defaultLocationCode.trim() || undefined,
          default_region_code: defaultRegionCode.trim() || undefined,
          default_country: defaultCountry.trim() || undefined,
          default_latitude: defaultLatitude.trim() || undefined,
          default_longitude: defaultLongitude.trim() || undefined,
        },
        (percent) => setUploadProgress(percent),
      )
      .then((result) => {
        setUploadResult(result)
        setDatasetId(result.dataset.id)
        setJobId(result.ingestion_job_id)
        setJobFinished(false)
        toast.success('Upload accepted', `${result.dataset.name} — import started (${result.executor}).`)
      })
      .catch((error: unknown) => setUploadError(toApiError(error)))
      .finally(() => {
        setUploading(false)
        setUploadProgress(null)
      })
  }

  const cleanMapping = (): Record<string, string> =>
    Object.fromEntries(Object.entries(mappingDraft).filter(([, header]) => header.trim() !== ''))

  const saveMapping = () => {
    if (!datasetId) return
    setMappingSaving(true)
    setMappingSaveError(null)
    api.datasets
      .setMapping(datasetId, { mapping: cleanMapping() })
      .then((result) => {
        toast.success('Column mapping saved', `${Object.keys(result.mapping).length} fields mapped — re-run the import to apply it.`)
        void mappingState.refetch()
      })
      .catch((error: unknown) => setMappingSaveError(toApiError(error)))
      .finally(() => setMappingSaving(false))
  }

  const requestSuggestion = () => {
    const headers = mappingState.data?.headers ?? []
    if (!headers.length) return
    setSuggesting(true)
    setSuggestError(null)
    api.datasets
      .suggestColumns(headers)
      .then((result) => {
        setMappingDraft({ ...result.suggested_mapping })
        toast.info('Suggestion applied', `${Object.keys(result.suggested_mapping).length} of ${headers.length} columns auto-mapped; ${result.unmapped_headers.length} file columns left unmapped.`)
      })
      .catch((error: unknown) => setSuggestError(toApiError(error)))
      .finally(() => setSuggesting(false))
  }

  const resetToSuggestion = () => {
    const doc = mappingState.data
    if (!doc) return
    setMappingDraft({ ...doc.suggested_mapping })
    toast.info('Mapping reset', 'The heuristic suggestion for this file was restored in the editor.')
  }

  const runIngest = () => {
    if (!datasetId) return
    setIngestRunning(true)
    setIngestError(null)
    api.datasets
      .ingest(datasetId)
      .then((ack) => {
        setJobId(ack.ingestion_job_id)
        setJobFinished(false)
        toast.info('Import queued', `Job ${ack.ingestion_job_id} is re-running the file import (${ack.executor}).`)
      })
      .catch((error: unknown) => setIngestError(toApiError(error)))
      .finally(() => setIngestRunning(false))
  }

  const startOver = () => {
    setFile(null)
    setUploadResult(null)
    setDatasetId(null)
    setJobId(null)
    setJobFinished(false)
    setMappingDraft({})
    setUploadError(null)
    setIngestError(null)
    setMappingSaveError(null)
    setSuggestError(null)
    setName('')
    setDescription('')
    setTags('')
    setUnitsJson('')
    setIsPublic(false)
    setDefaultLocationCode('')
    setDefaultRegionCode('')
    setDefaultCountry('')
    setDefaultLatitude('')
    setDefaultLongitude('')
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  /* ------------------------------------------------------------ derived */

  const headerOptions: Option[] = useMemo(
    () => (mappingState.data?.headers ?? uploadResult?.headers ?? []).map((header) => ({ value: header, label: header })),
    [mappingState.data, uploadResult],
  )
  const mappableFields = mappingState.data?.mappable_fields ?? []
  const cleanedMapping = cleanMapping()
  const usedHeaders = new Set(Object.values(cleanedMapping))
  const unmappedHeaderCount = headerOptions.filter((option) => !usedHeaders.has(option.value)).length
  const mappingComplete = 'observation_date' in cleanedMapping
  const jobActive = !jobFinished && jobId !== null

  const mappingColumns: Column<string>[] = [
    {
      key: 'field',
      header: 'Canonical field',
      width: '240px',
      cell: (field) => (
        <span className="flex items-center gap-2">
          <span className="font-medium">{fieldLabel(field)}</span>
          <span className="mono text-[11px] subtle">{field}</span>
          {field === 'observation_date' ? <Badge tone="danger">required</Badge> : null}
        </span>
      ),
    },
    {
      key: 'header',
      header: 'File column',
      cell: (field) => (
        <Select
          ariaLabel={`Map ${field} to a file column`}
          value={mappingDraft[field] ?? ''}
          onChange={(value) =>
            setMappingDraft((prev) => {
              const next = { ...prev }
              if (value) next[field] = value
              else delete next[field]
              return next
            })
          }
          options={headerOptions}
          placeholder="— not mapped —"
          disabled={!headerOptions.length}
        />
      ),
    },
    {
      key: 'state',
      header: 'Status',
      align: 'right',
      width: '120px',
      cell: (field) => {
        const mapped = cleanedMapping[field]
        if (mapped) return <Badge tone="success">{mapped}</Badge>
        return field === 'observation_date' ? <Badge tone="danger">missing</Badge> : <Badge tone="neutral">unmapped</Badge>
      },
    },
  ]

  const limitsMeta = (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone="neutral">
        {maxUploadBytes !== null ? `≤ ${formatBytes(maxUploadBytes)} per file` : statusState.loading ? 'reading size limit…' : 'size limit unavailable — server rejects oversized files'}
      </Badge>
      <Badge tone="neutral">
        {maxUploadRows !== null ? `≤ ${formatNumber(maxUploadRows, 0)} rows imported` : statusState.loading ? 'reading row limit…' : 'row limit unavailable'}
      </Badge>
      {allowedExtensions.length ? <Badge tone="neutral">Accepts {allowedExtensions.join(' ')}</Badge> : null}
      {statusState.error ? <span className="subtle">Limits could not be read from /system/status; the backend stays authoritative.</span> : null}
    </div>
  )

  /* -------------------------------------------------------------- render */

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Data pipeline"
        title="Upload a dataset"
        description="Upload sends the file and metadata to the backend, which stores it, auto-detects the format and immediately starts the import job. You can then correct the column mapping and re-run the import."
        backTo="/app/datasets"
        meta={limitsMeta}
      />

      {!canUpload ? (
        <EmptyState
          tone="warning"
          title="Importing requires the datasets:upload capability"
          description="Your session has not been issued the datasets:upload capability. Sign out and sign in again to refresh it; every signed-in account can import its own data files."
          action={
            <Link to="/app/datasets" className="btn btn-secondary btn-sm">
              Back to the dataset library
            </Link>
          }
        />
      ) : null}

      {canUpload ? (
        <Panel
          title="1 — File & metadata"
          description="The server validates the extension, size and metadata before anything is stored. These client-side checks mirror its live configuration."
        >
          <div className="space-y-4">
            <div
              role="button"
              tabIndex={0}
              aria-label="Choose a data file or drop one here"
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  fileInputRef.current?.click()
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
                const dropped = event.dataTransfer.files.item(0)
                if (dropped) chooseFile(dropped)
              }}
              className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed p-8 text-center transition-colors"
              style={{
                borderColor: dragging ? 'var(--accent, #16a34a)' : 'var(--border-strong)',
                backgroundColor: dragging ? 'rgba(22, 163, 74, 0.06)' : 'var(--surface-muted)',
              }}
            >
              <UploadCloud className="h-6 w-6 subtle" aria-hidden="true" />
              <p className="text-sm font-medium">{dragging ? 'Drop the file to attach it' : 'Drag a file here, or click to browse'}</p>
              <p className="text-xs subtle">Accepted extensions: {acceptList}</p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              className="sr-only"
              accept={acceptList}
              onChange={(event) => {
                const picked = event.target.files?.item(0)
                if (picked) chooseFile(picked)
                event.target.value = ''
              }}
            />

            {file ? (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
                <FileUp className="h-4 w-4 subtle" aria-hidden="true" />
                <span className="font-medium">{file.name}</span>
                <span className="subtle">{formatBytes(file.size)}</span>
                <span className="mono subtle">{extension || 'no extension'}</span>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm ml-auto"
                  onClick={() => {
                    setFile(null)
                    setUploadError(null)
                  }}
                >
                  <X className="h-3.5 w-3.5" aria-hidden="true" /> Remove
                </button>
              </div>
            ) : null}

            {extensionProblem ? <p className="text-sm font-medium text-alert-critical" role="alert">{extensionProblem}</p> : null}
            {sizeProblem ? <p className="text-sm font-medium text-alert-critical" role="alert">{sizeProblem}</p> : null}
            {maxUploadRows !== null ? (
              <Hint>Files are streamed and cut at {formatNumber(maxUploadRows, 0)} rows; the import job reports exactly how many records were fetched, accepted, rejected and skipped.</Hint>
            ) : null}
            {formatRule ? (
              <div className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
                <p className="font-semibold">Format rules for “{formatRule.format}” (from /datasets/formats)</p>
                <p className="mt-1 muted">
                  Required: <span className="mono">{formatRule.required_columns.join(', ')}</span>
                  {formatRule.delimiter ? ` · delimiter ${formatRule.delimiter}` : ''}
                </p>
                <p className="mt-1 subtle">{formatRule.notes}</p>
              </div>
            ) : null}

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Dataset name" htmlFor="upload-name" required hint="Shown across the platform; up to 200 characters.">
                <input id="upload-name" className="input" value={name} maxLength={200} onChange={(event) => setName(event.target.value)} />
              </Field>
              <Field label="Source type" htmlFor="upload-source-type">
                <Select
                  id="upload-source-type"
                  value={sourceType}
                  onChange={(value) => setSourceType(asSourceType(value))}
                  options={SOURCE_OPTIONS}
                />
              </Field>
              <Field label="Description" htmlFor="upload-description" className="sm:col-span-2">
                <textarea id="upload-description" className="input min-h-20" maxLength={2000} value={description} onChange={(event) => setDescription(event.target.value)} />
              </Field>
              <Field label="Source name" htmlFor="upload-source-name" hint="Who produced this data. Defaults to the dataset name.">
                <input id="upload-source-name" className="input" maxLength={120} value={sourceName} onChange={(event) => setSourceName(event.target.value)} />
              </Field>
              <Field label="Source URL" htmlFor="upload-source-url">
                <input id="upload-source-url" className="input" maxLength={500} value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} />
              </Field>
              <Field label="Tags" htmlFor="upload-tags" hint="Comma-separated, up to 40 tags.">
                <input id="upload-tags" className="input" value={tags} onChange={(event) => setTags(event.target.value)} />
              </Field>
              <Field
                label="Units (JSON)"
                htmlFor="upload-units"
                error={unitsProblem}
                hint="Optional object of canonical field to unit, sent as a JSON string."
              >
                <textarea id="upload-units" className="input min-h-16 mono text-xs" value={unitsJson} onChange={(event) => setUnitsJson(event.target.value)} />
              </Field>
            </div>

            <fieldset className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
              <legend className="panel-title px-1">Location defaults</legend>
              <p className="mb-3 text-xs subtle">Fallbacks used when rows carry no location columns.</p>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Field label="Default location code" htmlFor="upload-location">
                  <input id="upload-location" className="input" maxLength={20} value={defaultLocationCode} onChange={(event) => setDefaultLocationCode(event.target.value)} />
                </Field>
                <Field label="Default region code" htmlFor="upload-region">
                  <input id="upload-region" className="input" maxLength={80} value={defaultRegionCode} onChange={(event) => setDefaultRegionCode(event.target.value)} />
                </Field>
                <Field label="Default country" htmlFor="upload-country">
                  <input id="upload-country" className="input" maxLength={80} value={defaultCountry} onChange={(event) => setDefaultCountry(event.target.value)} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Latitude" htmlFor="upload-lat">
                    <input id="upload-lat" className="input" inputMode="decimal" value={defaultLatitude} onChange={(event) => setDefaultLatitude(event.target.value)} />
                  </Field>
                  <Field label="Longitude" htmlFor="upload-lon">
                    <input id="upload-lon" className="input" inputMode="decimal" value={defaultLongitude} onChange={(event) => setDefaultLongitude(event.target.value)} />
                  </Field>
                </div>
              </div>
            </fieldset>

            <Toggle checked={isPublic} onChange={setIsPublic} label="Public — visible to viewers across the platform" />

            {uploadProgress !== null ? (
              <div className="space-y-1">
                <Progress value={uploadProgress} tone="hydro" label={`Upload progress ${uploadProgress}%`} />
                <p className="text-xs subtle">Uploading… {uploadProgress}% {file ? `of ${formatBytes(file.size)}` : ''}</p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!canSubmit}
                onClick={submit}
                title={
                  !file
                    ? 'Choose a file first.'
                    : !name.trim()
                      ? 'The backend requires a dataset name.'
                      : extensionProblem || sizeProblem || unitsProblem
                        ? 'Fix the highlighted problems — the backend would reject this upload.'
                        : 'Sends the multipart upload; the import starts immediately.'
                }
              >
                {uploading ? <UploadCloud className="h-4 w-4 animate-pulse" aria-hidden="true" /> : <UploadCloud className="h-4 w-4" aria-hidden="true" />}
                {uploading ? 'Uploading…' : 'Upload & start import'}
              </button>
              {uploadResult ? (
                <button type="button" className="btn btn-ghost" onClick={startOver}>
                  <RotateCw className="h-4 w-4" aria-hidden="true" /> Upload another file
                </button>
              ) : null}
            </div>

            {uploadError ? <ApiErrorPanel error={uploadError} /> : null}

            {uploadResult ? (
              <div className="space-y-2 rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="success">202 accepted</Badge>
                  <StatusBadge value={uploadResult.status} kind="dataset" />
                  <span className="mono text-xs subtle">detected format: {uploadResult.detected_format}</span>
                  <span className="mono text-xs subtle">{uploadResult.headers.length} headers read</span>
                  <span className="mono text-xs subtle">executor: {uploadResult.executor}</span>
                </div>
                <p className="muted">
                  Dataset{' '}
                  <Link to={`/app/datasets/${uploadResult.dataset.id}`} className="link font-medium">
                    {uploadResult.dataset.name}
                  </Link>{' '}
                  created; job <span className="mono">{uploadResult.ingestion_job_id}</span> is already running with the auto-mapping below. Uploading again creates a separate dataset.
                </p>
              </div>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {datasetId !== null ? (
        <Panel
          title="2 — Column mapping"
          description="Fix which file column feeds each canonical field. The server rejects a mapping without observation_date; a saved mapping only takes effect when the import is re-run."
          actions={
            <RefreshButton
              onClick={() => {
                void mappingState.refetch()
              }}
              busy={mappingState.refreshing}
              label="Reload mapping"
            />
          }
        >
          <div className="space-y-4">
            {mappingState.loading ? <p className="text-sm muted">Loading mapping from the backend…</p> : null}
            {mappingState.error ? <ErrorState error={mappingState.error} onRetry={() => void mappingState.refetch()} /> : null}

            {mappingState.data ? (
              <>
                <DataTable
                  columns={mappingColumns}
                  rows={mappableFields}
                  rowKey={(field) => field}
                  empty="The backend reported no mappable fields for this dataset."
                  caption="Canonical fields mapped to file columns"
                  compact
                />
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="muted">{headerOptions.length} file columns · {mappableFields.length} canonical fields · {unmappedHeaderCount} file column(s) unused.</span>
                  {Object.keys(mappingState.data.units).length ? (
                    <span className="subtle">
                      Stored units:{' '}
                      {Object.entries(mappingState.data.units)
                        .map(([field, unit]) => `${field}=${unit}`)
                        .join(', ')}
                    </span>
                  ) : null}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={mappingSaving || !mappingComplete}
                    onClick={saveMapping}
                    title={!mappingComplete ? 'The backend rejects mappings without an observation_date column.' : undefined}
                  >
                    <Save className="h-4 w-4" aria-hidden="true" /> {mappingSaving ? 'Saving…' : 'Save mapping'}
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={suggesting || !headerOptions.length}
                    onClick={requestSuggestion}
                    title="POST /datasets/column-suggestion re-runs the backend heuristic on the real headers."
                  >
                    <Sparkles className="h-4 w-4" aria-hidden="true" /> {suggesting ? 'Asking backend…' : 'Suggest columns'}
                  </button>
                  <button type="button" className="btn btn-ghost" onClick={resetToSuggestion}>
                    <RotateCw className="h-4 w-4" aria-hidden="true" /> Reset to suggestion
                  </button>
                  <button
                    type="button"
                    className="btn btn-secondary"
                    disabled={ingestRunning || jobActive}
                    onClick={runIngest}
                    title={jobActive ? 'An import is already running for this dataset.' : 'POST /datasets/{id}/ingest re-runs the import with the saved mapping.'}
                  >
                    <PlayCircle className="h-4 w-4" aria-hidden="true" /> {ingestRunning ? 'Queuing…' : jobActive ? 'Import running…' : 'Re-run ingest'}
                  </button>
                </div>

                {mappingSaveError ? <ApiErrorPanel error={mappingSaveError} /> : null}
                {suggestError ? <ApiErrorPanel error={suggestError} /> : null}
                {ingestError ? <ApiErrorPanel error={ingestError} /> : null}
              </>
            ) : null}
          </div>
        </Panel>
      ) : null}

      {jobId !== null ? (
        <Panel
          title="3 — Ingestion progress"
          description="Live job state, polled from GET /ingestion/jobs/{id} every few seconds until the job finishes."
          actions={
            <RefreshButton
              onClick={() => {
                void jobState.refetch()
              }}
              busy={jobState.refreshing}
              label="Poll now"
            />
          }
        >
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StatusBadge value={job?.status ?? 'queued'} kind="job" />
              <span className="mono text-xs subtle">job {jobId}</span>
              {job?.started_at ? <span className="subtle">started {relativeTime(job.started_at)}</span> : <span className="subtle">waiting for the worker to pick the job up…</span>}
              {job?.executor ? <span className="mono text-xs subtle">executor: {job.executor}</span> : null}
              {job?.processing_mode ? <span className="mono text-xs subtle">mode: {job.processing_mode}</span> : null}
            </div>

            {jobState.error ? <ErrorState error={jobState.error} onRetry={() => void jobState.refetch()} /> : null}

            {job ? (
              <>
                {job.records_fetched > 0 ? (
                  <Progress
                    value={Math.min(100, Math.round((job.records_accepted / job.records_fetched) * 100))}
                    tone={job.status === 'failed' ? 'alert' : 'forest'}
                    label="Accepted share of fetched rows"
                  />
                ) : null}
                <div className="flex flex-wrap gap-3 text-xs">
                  <span className="muted">fetched <span className="mono">{formatNumber(job.records_fetched, 0)}</span></span>
                  <span className="text-forest-600 dark:text-forest-300">accepted <span className="mono">{formatNumber(job.records_accepted, 0)}</span></span>
                  <span className={job.records_rejected > 0 ? 'font-semibold text-alert-critical' : 'muted'}>rejected <span className="mono">{formatNumber(job.records_rejected, 0)}</span></span>
                  <span className="muted">skipped <span className="mono">{formatNumber(job.records_skipped, 0)}</span></span>
                  {job.duplicate_count !== undefined ? <span className="muted">duplicates <span className="mono">{formatNumber(job.duplicate_count, 0)}</span></span> : null}
                  {job.retry_count > 0 ? <span className="subtle">retries {job.retry_count}/{job.max_retries ?? '—'}</span> : null}
                </div>

                {job.error_message ? (
                  <div className="rounded-lg border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm text-alert-critical" role="alert">
                    <span className="font-semibold">{humanise(job.status)}:</span> {job.error_message}
                  </div>
                ) : null}

                {job.validation_errors && job.validation_errors.length > 0 ? (
                  <div className="space-y-1">
                    <p className="text-xs font-semibold">
                      Validation errors ({formatNumber(job.validation_errors.length, 0)} reported
                      {job.validation_errors.length >= 50 ? ' — truncated by the backend' : ''})
                    </p>
                    <ul className="space-y-1 text-xs">
                      {job.validation_errors.slice(0, 8).map((issue, index) => (
                        <li key={`${issue.code}-${index}`} className="flex flex-wrap items-baseline gap-2">
                          <Badge tone={issue.severity === 'error' ? 'danger' : issue.severity === 'warning' ? 'warning' : 'info'}>{issue.code}</Badge>
                          <span className="muted">
                            {issue.field ? `${issue.field} · ` : ''}
                            {issue.row !== null && issue.row !== undefined ? `row ${issue.row} · ` : ''}
                            {issue.message}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {job.log_lines && job.log_lines.length > 0 ? (
                  <div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
                    <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide subtle">Latest log</p>
                    <ul className="space-y-0.5 text-[11px]">
                      {job.log_lines.slice(-4).map((line, index) => (
                        <li key={`${line.at}-${index}`} className="mono break-words muted">
                          {line.level}: {line.message}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                {jobFinished && datasetId ? (
                  <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
                    {datasetState.loading || datasetState.error ? null : (
                      <>
                        <StatusBadge value={datasetState.data?.status ?? null} kind="dataset" />
                        <span className="muted">
                          Dataset now holds <span className="mono">{formatNumber(datasetState.data?.record_count ?? 0, 0)}</span> records.
                        </span>
                      </>
                    )}
                    <Link to={`/app/datasets/${datasetId}`} className="link">
                      Open dataset detail
                    </Link>
                    <Link to="/app/ingestion" className="link">
                      All ingestion jobs
                    </Link>
                    {datasetState.error ? <span className="text-xs text-alert-critical">{datasetState.error.message}</span> : null}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        </Panel>
      ) : null}
    </div>
  )
}

/* ---------------------------------------------------------------- helpers */

function ApiErrorPanel({ error }: { error: ApiRequestError }) {
  const details = describeDetails(error.details)
  return (
    <div className="card border-alert-critical/40 bg-alert-critical/5" role="alert">
      <div className="flex items-start gap-3">
        <AlertOctagon className="mt-0.5 h-5 w-5 shrink-0 text-alert-critical" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">The backend rejected this request</h3>
          <p className="mt-1 text-sm leading-relaxed muted">{error.message}</p>
          {details.length ? (
            <ul className="mt-2 space-y-1 text-xs">
              {details.map((line) => (
                <li key={line} className="mono break-words muted">
                  {line}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] subtle">
            <span className="badge badge-neutral">{error.code}</span>
            {error.status ? <span className="badge badge-neutral">HTTP {error.status}</span> : null}
            {error.requestId ? (
              <span className="mono" title="Reference for support requests">
                ref {error.requestId}
              </span>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  )
}
