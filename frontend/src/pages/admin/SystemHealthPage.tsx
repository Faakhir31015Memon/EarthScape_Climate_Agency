import { useCallback, useState, type ReactNode } from 'react'
import { Database, RefreshCw } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  ErrorState,
  ModeBadge,
  Mono,
  PageHeader,
  Panel,
  RefreshButton,
} from '../../components/ui'
import { ActionFeedback, type Feedback } from './components/AdminBits'
import { describeError } from './components/adminUtils'
import { formatBytes, formatDate, formatNumber, humanise } from '../../lib/format'
import type { ComponentStatus } from '../../types/api'

const HEALTH_POLL_MS = 60_000

/** Each backend component/service status maps to a distinct tone + human label. */
const STATUS_TONE: Record<ComponentStatus['status'], 'success' | 'warning' | 'neutral' | 'danger'> = {
  available: 'success',
  degraded: 'warning',
  'not-configured': 'neutral',
  unavailable: 'danger',
}

function StatusTile({ name, status, configured, detail }: ComponentStatus) {
  const tone = STATUS_TONE[status] ?? 'neutral'
  return (
    <div className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 truncate text-sm font-medium">{name}</p>
        <Badge tone={tone}>{humanise(status)}</Badge>
      </div>
      <p className="mt-1 text-[11px] subtle">{configured ? 'configured' : 'not configured'}</p>
      <p className="mt-2 break-words text-xs leading-relaxed muted">{detail}</p>
    </div>
  )
}

/** Renders a genuinely absent metric as "not measured", never as a dash or a zero. */
function MetricValue({ value, unit, reason }: { value: number | null | undefined; unit?: string; reason: string }) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return (
      <span className="text-xs subtle" title={reason} aria-label={`not measured: ${reason}`}>
        not measured
      </span>
    )
  }
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="mono text-sm font-semibold">{formatNumber(value, 2)}</span>
      {unit ? <span className="text-[11px] subtle">{unit}</span> : null}
    </span>
  )
}

/**
 * System health.
 *
 * Composes `GET /system/health` (polled every 60s), plus `/system/status`,
 * `/system/services`, `/system/dependencies`, `/system/endpoints`, `/system/metrics`
 * and `/system/backups` — the heavier ones load once and are only re-read by the
 * manual Refresh button, so `/system/metrics` is never hammered. The metrics view
 * reports request totals / latency as null and lists them under `unavailable`, so
 * they render as "not measured" with the backend's own reason rather than a zero.
 */
export default function SystemHealthPage() {
  const { can, hasRole } = useAuth()
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const health = useAsync(() => api.system.health(), [], { interval: HEALTH_POLL_MS })
  const status = useAsync(() => api.system.status(), [])
  const services = useAsync(() => api.system.services(), [])
  const dependencies = useAsync(() => api.system.dependencies(), [])
  const endpointCatalog = useAsync(() => api.system.endpoints(), [])
  const metrics = useAsync(() => api.system.metrics(), [])
  const backups = useAsync(() => api.system.backups(), [])

  const refreshAll = useCallback(() => {
    setFeedback(null)
    void Promise.all([
      health.refetch(),
      status.refetch(),
      services.refetch(),
      dependencies.refetch(),
      endpointCatalog.refetch(),
      metrics.refetch(),
      backups.refetch(),
    ])
  }, [health, status, services, dependencies, endpointCatalog, metrics, backups])

  const canBackup = can('system:manage') && hasRole('administrator')

  const runBackup = useCallback(async () => {
    setBusy(true)
    setFeedback(null)
    try {
      const result = await api.system.runBackup()
      const record = result.record ?? result.job
      const parts = [
        record.mode ? `mode ${record.mode}` : null,
        typeof record.document_count === 'number' ? `${record.document_count.toLocaleString()} documents` : null,
        typeof record.size_bytes === 'number' ? formatBytes(record.size_bytes) : null,
        typeof record.sha256 === 'string' && record.sha256 ? `sha256 ${record.sha256.slice(0, 12)}…` : null,
      ].filter(Boolean)
      const note = typeof record.note === 'string' ? record.note : null
      setFeedback({ tone: 'success', text: `Backup created${parts.length ? ` (${parts.join(', ')})` : ''}.${note ? ` ${note}` : ''}` })
      await backups.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
    } finally {
      setBusy(false)
    }
  }, [backups])

  const db = health.data?.database
  const isAtlas = db?.mode === 'atlas'

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="System health"
        description="Live component, service, dependency, throughput and backup status reported directly by the backend. Nothing here is inferred — unavailable capabilities say so."
        meta={
          health.data ? (
            <>
              <Badge tone={health.data.status === 'ok' ? 'success' : 'warning'}>{humanise(health.data.status)}</Badge>
              <span className="subtle">version {health.data.version}</span>
              <span className="subtle">schema {health.data.schema_version}</span>
              <span className="subtle">refreshed {formatDate(health.data.generated_at, true)}</span>
            </>
          ) : null
        }
        actions={<RefreshButton onClick={refreshAll} busy={health.refreshing} label="Refresh all" />}
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />

      {health.error ? (
        <div className="mb-4">
          <ErrorState error={health.error} onRetry={() => void health.refetch()} />
        </div>
      ) : null}

      {/* ------------------------------------------------------- datastore mode */}
      <div
        className={`mb-6 rounded-xl border p-4 ${isAtlas ? 'border-forest-300 bg-forest-50/50 dark:bg-forest-900/20' : 'border-solar-400 bg-solar-50/60 dark:bg-solar-900/20'}`}
        role="status"
      >
        <div className="flex flex-wrap items-center gap-3">
          <Database className={`h-5 w-5 shrink-0 ${isAtlas ? 'text-forest-700 dark:text-forest-300' : 'text-solar-700 dark:text-solar-300'}`} aria-hidden="true" />
          <h2 className="text-sm font-semibold">Datastore in use</h2>
          <ModeBadge mode={db?.mode} detail={db ? `database=${db.database}, ok=${db.ok}` : undefined} />
          {db ? <Badge tone={db.ok ? 'success' : 'danger'} title={db.ok ? 'A store answered the health probe' : 'The store did not answer the health probe'}>{db.ok ? 'store ok' : 'store down'}</Badge> : null}
        </div>
        {isAtlas ? (
          <p className="mt-2 text-sm leading-relaxed muted">
            This instance is connected to <strong>MongoDB Atlas</strong> (<Mono>{db?.database}</Mono>). Backups use{' '}
            <Mono>mongodump</Mono> when it is available on the host.
          </p>
        ) : (
          <div className="mt-2 text-sm leading-relaxed muted">
            <p className="font-medium text-solar-800 dark:text-solar-200">
              This instance is <strong>not</strong> using MongoDB Atlas. It is running the mode{' '}
              <Mono>{db?.mode ?? 'unknown'}</Mono>
              {db?.mode === 'local-development'
                ? ' — an in-process document store used for local development only.'
                : ' — the document store has not finished initialising.'}
            </p>
            <p className="mt-2">
              Data lives only in this process and is <strong>not durable</strong> across restarts; counts and backups
              reflect local JSON snapshots, not a real cluster. To force a real Atlas connection, set{' '}
              <code className="kbd">MONGODB_URI</code> to the cluster connection string and{' '}
              <code className="kbd">FORCE_ATLAS_CONNECTION</code> to <Mono>true</Mono> in the backend environment, then
              restart. Until then, treat this as a non-production datastore.
            </p>
          </div>
        )}
      </div>

      {/* ------------------------------------------------------- components grid */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel
          title="Health components"
          description={`From GET /system/health · background executor ${health.data?.background.executor ?? '—'} · ${health.data?.background.active_jobs ?? 0} active / ${health.data?.background.queued_jobs ?? 0} queued jobs. Polls every ${HEALTH_POLL_MS / 1000}s.`}
          dense
        >
          <div className="grid gap-3 sm:grid-cols-2">
            {(health.data?.components ?? []).map((component) => (
              <StatusTile key={component.name} {...component} />
            ))}
            {health.loading && !health.data ? <p className="text-sm muted">Loading components…</p> : null}
          </div>
        </Panel>

        <Panel title="Live service probes" description="GET /system/services — reachability checked at request time." dense>
          {services.error ? <ErrorState error={services.error} onRetry={() => void services.refetch()} /> : null}
          <div className="grid gap-3 sm:grid-cols-2">
            {(services.data?.items ?? []).map((service) => {
              const tone = STATUS_TONE[service.status as ComponentStatus['status']] ?? 'neutral'
              return (
                <div key={service.name} className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
                  <div className="flex items-center justify-between gap-2">
                    <p className="min-w-0 truncate text-sm font-medium">{service.name}</p>
                    <Badge tone={tone}>{humanise(service.status)}</Badge>
                  </div>
                  <p className="mt-2 break-words text-xs leading-relaxed muted">{service.detail ?? 'no detail'}</p>
                </div>
              )
            })}
            {services.loading && !services.data ? <p className="text-sm muted">Probing services…</p> : null}
          </div>
        </Panel>

        {/* --------------------------------------------------------- metrics */}
        <Panel
          title="Latency & throughput"
          description={`GET /system/metrics (window: ${metrics.data?.window ?? '—'}). Read once on load and on Refresh — never polled. Null capabilities are shown as "not measured".`}
          dense
          className="lg:col-span-2"
        >
          {metrics.error ? <ErrorState error={metrics.error} onRetry={() => void metrics.refetch()} /> : null}
          {metrics.data ? (
            <MetricsPanel raw={metrics.data} />
          ) : !metrics.loading ? (
            <p className="text-sm muted">Metrics unavailable.</p>
          ) : (
            <p className="text-sm muted">Loading metrics…</p>
          )}
        </Panel>

        {/* ----------------------------------------------------- dependencies */}
        <Panel title="Dependencies" description="GET /system/dependencies — installed versions and CLI presence probed on the host." dense>
          {dependencies.error ? <ErrorState error={dependencies.error} onRetry={() => void dependencies.refetch()} /> : null}
          <div className="table-wrap">
            <table className="data text-[13px]">
              <caption className="sr-only">Installed dependencies</caption>
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Role</th>
                  <th scope="col">Version</th>
                  <th scope="col">Required</th>
                </tr>
              </thead>
              <tbody>
                {(dependencies.data?.items ?? []).map((dependency) => (
                  <tr key={dependency.name}>
                    <td className="align-top">
                      <span className="mono text-[12px]">{dependency.name}</span>
                      {!dependency.installed ? <Badge tone="danger" title="Not installed / not found on PATH">missing</Badge> : null}
                    </td>
                    <td className="align-top text-xs muted">{dependency.role}</td>
                    <td className="align-top">{dependency.version ? <span className="mono text-[12px]">{dependency.version}</span> : <span className="text-xs subtle">not installed</span>}</td>
                    <td className="align-top">{dependency.required ? <span className="mono text-[12px]">{dependency.required}</span> : <span className="text-xs subtle">—</span>}</td>
                  </tr>
                ))}
                {dependencies.loading && !dependencies.data ? (
                  <tr><td colSpan={4} className="py-6 text-center text-sm muted">Reading dependencies…</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </Panel>

        {/* ------------------------------------------------------ endpoint map */}
        <Panel title="API surface" description="GET /system/endpoints — the groups the backend advertises (read-only summary)." dense>
          {endpointCatalog.error ? <ErrorState error={endpointCatalog.error} onRetry={() => void endpointCatalog.refetch()} /> : null}
          <ul className="space-y-2">
            {(endpointCatalog.data?.groups ?? []).map((group) => (
              <li key={group.group} className="flex flex-wrap items-center justify-between gap-2 border-l pl-3" style={{ borderColor: 'var(--border)' }}>
                <span className="min-w-0">
                  <span className="text-sm font-medium">{group.group}</span>
                  <span className="ml-2 text-xs subtle">{group.description}</span>
                </span>
                <Badge tone="neutral">{group.endpoints.length} endpoint{group.endpoints.length === 1 ? '' : 's'}</Badge>
              </li>
            ))}
          </ul>
        </Panel>

        {/* ---------------------------------------------------- status extras */}
        <Panel title="Runtime context" description="GET /system/status — a few environment facts." dense className="lg:col-span-2">
          {status.error ? <ErrorState error={status.error} onRetry={() => void status.refetch()} /> : null}
          {status.data ? (
            <div className="grid gap-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
              <ContextFact label="Environment" value={<Mono>{String(status.data.environment)}</Mono>} />
              <ContextFact label="Processing mode" value={<Mono>{status.data.processing_mode}</Mono>} />
              <ContextFact label="Upload limit (bytes)" value={<span className="mono">{Number(status.data.limits.max_upload_bytes).toLocaleString()}</span>} />
              <ContextFact label="Upload limit (rows)" value={<span className="mono">{Number(status.data.limits.max_upload_rows).toLocaleString()}</span>} />
            </div>
          ) : !status.loading ? (
            <p className="text-sm muted">Status unavailable.</p>
          ) : (
            <p className="text-sm muted">Loading status…</p>
          )}
        </Panel>
      </div>

      {/* ----------------------------------------------------------- backups */}
      <Panel
        title="Backups"
        description="GET /system/backups — snapshots under the backup root. Only an administrator can trigger one."
        className="mt-6"
        actions={
          <button
            type="button"
            className="btn btn-primary btn-sm"
            disabled={!canBackup || busy}
            title={canBackup ? 'Create a snapshot now' : 'Requires an administrator with system:manage'}
            onClick={() => setConfirming(true)}
          >
            <RefreshCw className={busy ? 'h-3.5 w-3.5 animate-spin' : 'h-3.5 w-3.5'} aria-hidden="true" /> Run backup now
          </button>
        }
        dense
      >
        {backups.error ? <ErrorState error={backups.error} onRetry={() => void backups.refetch()} /> : null}
        {backups.data ? (
          <div className="mb-3 flex flex-wrap gap-2 text-xs subtle">
            <span>policy mode: <Mono>{String(backups.data.policy.mode ?? 'unknown')}</Mono></span>
            <span>retained: <span className="mono">{String(backups.data.policy.retained ?? '—')}</span></span>
            <span>database mode: <Mono>{String(backups.data.policy.database_mode ?? '—')}</Mono></span>
          </div>
        ) : null}
        <div className="table-wrap">
          <table className="data text-[13px]">
            <caption className="sr-only">Backup records</caption>
            <thead>
              <tr>
                <th scope="col">Created</th>
                <th scope="col">Mode</th>
                <th scope="col">Collections</th>
                <th scope="col">Documents</th>
                <th scope="col">Size</th>
                <th scope="col">sha256</th>
                <th scope="col">Restore verified</th>
              </tr>
            </thead>
            <tbody>
              {(backups.data?.items ?? []).map((backup) => (
                <tr key={backup.id}>
                  <td className="whitespace-nowrap">{formatDate(backup.created_at, true)}</td>
                  <td><Mono>{backup.mode}</Mono></td>
                  <td className="text-xs">{describeCollections(backup.collections)}</td>
                  <td><span className="mono">{backup.document_count.toLocaleString()}</span></td>
                  <td><span className="mono">{formatBytes(backup.size_bytes)}</span></td>
                  <td>{backup.sha256 ? <span className="mono text-[12px]" title={backup.sha256}>{`${backup.sha256.slice(0, 12)}…`}</span> : <span className="text-xs subtle">not recorded</span>}</td>
                  <td>
                    {backup.restore_verified ? <Badge tone="success" title={backup.restored_at ? `Restore round-trip completed ${formatDate(backup.restored_at, true)}` : undefined}>verified</Badge> : <Badge tone="neutral" title={backup.note ?? 'No restore round-trip has been performed by the backend'}>not verified</Badge>}
                  </td>
                </tr>
              ))}
              {backups.loading && !backups.data ? (
                <tr><td colSpan={7} className="py-6 text-center text-sm muted">Reading backups…</td></tr>
              ) : null}
              {!backups.loading && (backups.data?.items?.length ?? 0) === 0 ? (
                <tr><td colSpan={7} className="py-6 text-center text-sm muted">No backups recorded yet. Run a backup to create the first snapshot.</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
        <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
          <span aria-hidden="true">ⓘ</span>
          <span>
            Size, document count and sha256 come from the manifest the backend wrote when the snapshot was taken.
            “Restore verified” is only ever set by the backend after a real restore round-trip — this page never marks
            a snapshot as verified itself.
          </span>
        </p>
      </Panel>

      <ConfirmDialog
        open={confirming}
        title="Run a backup now?"
        busy={busy}
        confirmLabel="Run backup"
        onClose={() => setConfirming(false)}
        onConfirm={() => {
          setConfirming(false)
          void runBackup()
        }}
        message={
          <span>
            The backend writes a real snapshot to the backup root and computes its size, document count and sha256
            from actual file reads. It uses <Mono>mongodump</Mono> only when connected to Atlas and the tool is on{' '}
            PATH, otherwise it falls back to a local JSON copy. This action is audited and requires an administrator.
          </span>
        }
      />
    </div>
  )
}

function ContextFact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border p-2.5" style={{ borderColor: 'var(--border)' }}>
      <p className="text-[11px] uppercase tracking-wide subtle">{label}</p>
      <div className="mt-1 break-words">{value}</div>
    </div>
  )
}

function describeCollections(collections: unknown): string {
  if (Array.isArray(collections)) return collections.length ? collections.join(', ') : 'none listed'
  if (typeof collections === 'number') return `${collections.toLocaleString()} file(s)`
  if (collections === null || collections === undefined) return 'not recorded'
  return String(collections)
}

/**
 * Reads the metrics payload through a loose shape: `requests.total` and
 * `requests.by_status_class` are typed non-null but the view returns them as
 * `null` and lists them under `unavailable`; `jobs` uses `*_last_window` keys
 * rather than the `*_last_hour` names in the type. Both are handled here.
 */
function MetricsPanel({ raw }: { raw: import('../../types/api').SystemMetricsResponse }) {
  const data = raw as unknown as {
    requests?: { total?: number | null; by_status_class?: Record<string, number> | null; error_rate_pct?: number | null }
    latency?: { p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; max_ms: number | null; mean_ms: number | null }
    by_endpoint_group?: { group: string; count: number; mean_ms: number | null; errors: number }[]
    jobs?: Record<string, unknown>
    resources?: { cpu_count?: number | null; disk_free_bytes?: number | null; process_memory_mb?: number | null }
    unavailable?: string[]
    note?: string
  }
  const unavailable = new Set(data.unavailable ?? [])
  const reason = data.note ?? 'the backend reports this capability as unavailable'
  const notMeasured = (name: string) => (unavailable.has(name) ? reason : `${name} was not reported`)

  const jobs = data.jobs ?? {}
  const jobsNum = (keys: string[]): number | null => {
    for (const key of keys) {
      const value = jobs[key]
      if (typeof value === 'number') return value
    }
    return null
  }

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <MetricTile label="Requests (total)" value={<MetricValue value={data.requests?.total ?? null} reason={notMeasured('requests')} />} />
        <MetricTile label="Error rate" value={<MetricValue value={data.requests?.error_rate_pct ?? null} unit="%" reason={notMeasured('requests')} />} />
        <MetricTile label="Active jobs" value={<MetricValue value={jobsNum(['active'])} reason={notMeasured('jobs')} />} />
        <MetricTile label="Queued jobs" value={<MetricValue value={jobsNum(['queued'])} reason={notMeasured('jobs')} />} />

        <MetricTile label="Latency p50" value={<MetricValue value={data.latency?.p50_ms ?? null} unit="ms" reason={notMeasured('latency')} />} />
        <MetricTile label="Latency p95" value={<MetricValue value={data.latency?.p95_ms ?? null} unit="ms" reason={notMeasured('latency')} />} />
        <MetricTile label="Latency p99" value={<MetricValue value={data.latency?.p99_ms ?? null} unit="ms" reason={notMeasured('latency')} />} />
        <MetricTile label="Latency mean" value={<MetricValue value={data.latency?.mean_ms ?? null} unit="ms" reason={notMeasured('latency')} />} />

        <MetricTile label="Completed (last window)" value={<MetricValue value={jobsNum(['completed_last_window', 'completed_last_hour'])} reason={notMeasured('jobs')} />} />
        <MetricTile label="Failed (last window)" value={<MetricValue value={jobsNum(['failed_last_window', 'failed_last_hour'])} reason={notMeasured('jobs')} />} />
        <MetricTile label="Mean job duration" value={<MetricValue value={typeof jobs.mean_duration_seconds === 'number' ? jobs.mean_duration_seconds : null} unit="s" reason={notMeasured('jobs')} />} />
        <MetricTile label="Host CPU cores" value={<MetricValue value={data.resources?.cpu_count ?? null} reason={notMeasured('resources')} />} />

        <MetricTile label="Process memory" value={<MetricValue value={data.resources?.process_memory_mb ?? null} unit="MB" reason="not available on this host" />} />
        <MetricTile label="Disk free" value={<span className="text-sm">{data.resources?.disk_free_bytes === null || data.resources?.disk_free_bytes === undefined ? <span className="text-xs subtle" title="host disk stats could not be read">unavailable</span> : <span className="mono">{formatBytes(data.resources.disk_free_bytes)}</span>}</span>} />
      </div>

      {(data.by_endpoint_group ?? []).length > 0 ? (
        <div className="table-wrap">
          <table className="data text-[13px]">
            <caption className="sr-only">Latency by endpoint group</caption>
            <thead>
              <tr>
                <th scope="col">Group</th>
                <th scope="col" className="text-right">Requests</th>
                <th scope="col" className="text-right">Mean ms</th>
                <th scope="col" className="text-right">Errors</th>
              </tr>
            </thead>
            <tbody>
              {(data.by_endpoint_group ?? []).map((group) => (
                <tr key={group.group}>
                  <td>{group.group}</td>
                  <td className="text-right mono">{group.count.toLocaleString()}</td>
                  <td className="text-right"><MetricValue value={group.mean_ms} unit="ms" reason={notMeasured('latency')} /></td>
                  <td className="text-right mono">{group.errors.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs subtle" role="status">
          Per-endpoint breakdown: <strong>not measured</strong> — {notMeasured('by_endpoint_group')}.
        </p>
      )}
    </div>
  )
}

function MetricTile({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
      <p className="text-[11px] uppercase tracking-wide subtle">{label}</p>
      <div className="mt-1">{value}</div>
    </div>
  )
}
