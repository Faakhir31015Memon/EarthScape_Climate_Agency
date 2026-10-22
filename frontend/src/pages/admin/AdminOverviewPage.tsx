import { useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Bell, Boxes, Database, HardDrive, MessageSquare, ShieldCheck } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  DataTable,
  EmptyState,
  ErrorState,
  ModeBadge,
  PageHeader,
  Panel,
  RefreshButton,
  StatCard,
  StatusBadge,
  type Column,
} from '../../components/ui'
import { ActionFeedback, Breakdown, ConfigRequirement, MaskedValue, type Feedback } from './components/AdminBits'
import { breakdownTotal, describeError, formatUptime, maskedConfigured } from './components/adminUtils'
import { formatBytes, formatDateTime, humanise } from '../../lib/format'
import type { ComponentStatus, SettingsResponse, SystemStatusResponse } from '../../types/api'

/**
 * Administrative overview: the honest, API-derived state of the platform.
 *
 * Everything here comes from `GET /api/v1/system/status` (plus the alert, notification,
 * support and settings summaries). Values the backend does not collect are labelled as
 * such — no estimated uptime, no invented counts.
 */
export default function AdminOverviewPage() {
  const { can } = useAuth()
  const canReadSystem = can('system:read')
  const status = useAsync(() => api.system.status(), [], { enabled: canReadSystem, interval: 60_000 })
  const settings = useAsync(() => api.system.settings(), [], { enabled: can('system:manage') })
  const alertsSummary = useAsync(() => api.alerts.summary(), [])
  const unread = useAsync(() => api.notifications.unread(), [], { interval: 120_000 })
  const tickets = useAsync(() => api.support.summary(), [])

  const [refreshing, setRefreshing] = useState(false)
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  const data: SystemStatusResponse | null = status.data

  const readiness = useMemo(() => buildReadiness(data, settings.data?.env_derived), [data, settings.data])

  const componentColumns = useMemo<Column<ComponentStatus>[]>(
    () => [
      { key: 'name', header: 'Component', cell: (row) => <span className="font-medium">{humanise(row.name)}</span> },
      {
        key: 'configured',
        header: 'Configured',
        cell: (row) => <Badge tone={row.configured ? 'success' : 'neutral'}>{row.configured ? 'yes' : 'no'}</Badge>,
      },
      { key: 'status', header: 'State', cell: (row) => <StatusBadge value={row.status} /> },
      {
        key: 'detail',
        header: 'Detail reported by the backend',
        cell: (row) => <span className="text-xs leading-relaxed muted">{row.detail || 'No detail supplied.'}</span>,
      },
    ],
    [],
  )

  const collectionRows = useMemo(
    () => Object.entries(data?.collection_counts ?? {}).map(([name, count]) => ({ name, count })),
    [data],
  )

  async function refreshAll() {
    setRefreshing(true)
    setFeedback(null)
    try {
      await Promise.all([status.refetch(), alertsSummary.refetch(), unread.refetch(), tickets.refetch()])
      setFeedback({ tone: 'success', text: 'Administration status re-read from the API.' })
    } catch (cause) {
      setFeedback({ tone: 'error', text: describeError(cause).message })
    } finally {
      setRefreshing(false)
    }
  }

  if (!canReadSystem) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-5 w-5" />}
        title="This account cannot read platform status"
        description="The administrative overview needs the system:read capability. Ask for it to be granted, then reload."
      />
    )
  }

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Administrative overview"
        description="Platform status, data volumes, job and model totals, storage, and what is still missing before this deployment is production-ready."
        meta={
          data ? (
            <>
              <ModeBadge mode={data.database.mode} detail="Primary application database mode" />
              <Badge tone={data.status === 'ok' ? 'success' : 'warning'}>{humanise(data.status)}</Badge>
              <span className="mono">schema v{data.schema_version}</span>
              <span title="Uptime measured by this API process">uptime {formatUptime(data.uptime_seconds)}</span>
              <span>processing: {humanise(data.processing_mode)}</span>
              <span className="subtle">generated {formatDateTime(data.generated_at)}</span>
            </>
          ) : null
        }
        actions={<RefreshButton onClick={() => void refreshAll()} busy={refreshing || status.refreshing} label="Re-read status" />}
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />

      {status.error ? <ErrorState error={status.error} onRetry={() => void status.refetch()} /> : null}

      <div className="grid-responsive mb-6">
        <StatCard
          label="Open alerts"
          value={alertsSummary.data?.open ?? (alertsSummary.loading ? '—' : 0)}
          hint={alertsSummary.data ? `${alertsSummary.data.rules_enabled} enabled rule(s)` : 'Rule totals unavailable'}
          tone="alert"
          icon={<AlertTriangle className="h-4 w-4" />}
          to="/app/alerts"
          loading={alertsSummary.loading}
        />
        <StatCard
          label="Unread notifications"
          value={unread.data?.unread ?? (unread.loading ? '—' : 0)}
          hint={unread.data ? `${unread.data.critical} critical for this account` : 'Delivery counts unavailable'}
          tone="hydro"
          icon={<Bell className="h-4 w-4" />}
          to="/app/notifications"
          loading={unread.loading}
        />
        <StatCard
          label="Open support tickets"
          value={tickets.data?.open ?? (tickets.loading ? '—' : 0)}
          hint={tickets.data ? `${tickets.data.unassigned} unassigned · ${tickets.data.in_progress} in progress` : 'Queue totals unavailable'}
          tone="solar"
          icon={<MessageSquare className="h-4 w-4" />}
          to="/admin/tickets"
          loading={tickets.loading}
        />
        <StatCard
          label="Background jobs"
          value={data ? data.background.active_jobs : '—'}
          unit="active"
          hint={data ? `${data.background.queued_jobs} queued · executor: ${data.background.executor}` : 'Executor state unknown'}
          tone="forest"
          icon={<Boxes className="h-4 w-4" />}
          to="/admin/pipelines"
          loading={status.loading}
        />
      </div>

      <div className="grid-responsive mb-6">
        <Panel title="Platform status" description="Direct read of GET /api/v1/system/status." className="min-w-0">
          {status.loading && !data ? (
            <p className="text-sm muted">Loading status…</p>
          ) : data ? (
            <dl className="space-y-3 text-sm">
              <Row label="Database">
                <span className="flex flex-wrap items-center gap-2">
                  <ModeBadge mode={data.database.mode} />
                  <span className="mono text-[12px]">{data.database.database}</span>
                  <Badge tone={data.database.ok ? 'success' : 'danger'}>{data.database.ok ? 'reachable' : 'unreachable'}</Badge>
                </span>
              </Row>
              <Row label="Schema version">
                <span className="mono">{data.schema_version}</span> <span className="subtle text-xs">(applied by common/schema.py)</span>
              </Row>
              <Row label="Uptime">
                <span className="mono">{formatUptime(data.uptime_seconds)}</span>
                <span className="ml-2 text-xs subtle">process-local; resets when the API restarts</span>
              </Row>
              <Row label="Background executor">
                <span className="flex flex-wrap items-center gap-2">
                  <Badge tone={data.background.executor === 'celery' ? 'success' : 'info'}>{humanise(data.background.executor)}</Badge>
                  <span className="text-xs muted">{data.background.active_jobs} active · {data.background.queued_jobs} queued</span>
                </span>
              </Row>
              <Row label="Processing mode">{humanise(data.processing_mode)}</Row>
              <Row label="Environment">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="mono text-[12px]">{String(data.config_summary?.environment ?? 'not reported')}</span>
                  <Badge tone={data.config_summary?.debug === true ? 'warning' : 'success'} title="DJANGO_DEBUG">
                    {data.config_summary?.debug === true ? 'debug on' : 'debug off'}
                  </Badge>
                </span>
              </Row>
              <Row label="Request id">{data.request_id ? <span className="mono text-[12px]">{data.request_id}</span> : 'not reported'}</Row>
            </dl>
          ) : (
            <p className="text-sm subtle">Status could not be read.</p>
          )}
        </Panel>

        <Panel
          title="Job totals by status"
          description="Counted by the backend over ingestion, processing and report collections."
          className="min-w-0"
        >
          {data ? (
            <div className="space-y-4">
              <JobGroup label="Ingestion" counts={data.jobs.ingestion} to="/admin/pipelines" />
              <JobGroup label="Processing" counts={data.jobs.processing} to="/admin/pipelines" />
              <JobGroup label="Reports" counts={data.jobs.reports} to="/app/reports" />
            </div>
          ) : (
            <p className="text-sm subtle">Job totals are not available while status is unreachable.</p>
          )}
        </Panel>

        <Panel title="Machine learning totals" description="Model registry and prediction counts from the same response." className="min-w-0">
          {data ? (
            <div className="space-y-4">
              <JobGroup label="Models by status" counts={data.ml.models} to="/app/models" />
              <Row label="Predictions stored">
                <span className="font-semibold">{data.ml.predictions.toLocaleString()}</span>
              </Row>
              <Row label="Active model">
                {data.ml.active_model ? (
                  <span className="mono text-[12px]" title="Identifier reported by the backend">
                    {data.ml.active_model}
                  </span>
                ) : (
                  <Badge tone="neutral">no active model</Badge>
                )}
              </Row>
              <Link to="/app/ml-lab" className="link text-xs">
                Open the machine-learning lab
              </Link>
            </div>
          ) : (
            <p className="text-sm subtle">Model totals are not available.</p>
          )}
        </Panel>
      </div>

      <div className="grid-responsive mb-6">
        <Panel
          title="Collection counts"
          description="Document counts per MongoDB collection as counted by the API."
          className="min-w-0"
          footer={
            collectionRows.length ? (
              <span>
                {collectionRows.length} collections · {collectionRows.reduce((total, row) => total + row.count, 0).toLocaleString()} documents counted
              </span>
            ) : null
          }
        >
          {data ? (
            <DataTable
              compact
              caption="Collection counts"
              rows={collectionRows}
              rowKey={(row) => row.name}
              columns={[
                { key: 'name', header: 'Collection', cell: (row) => <span className="mono text-[12px]">{row.name}</span> },
                {
                  key: 'count',
                  header: 'Documents',
                  align: 'right',
                  cell: (row) => <span className="font-medium">{row.count.toLocaleString()}</span>,
                },
              ]}
              empty="The status response contained no collection counts."
            />
          ) : (
            <p className="text-sm subtle">Counts unavailable.</p>
          )}
        </Panel>

        <Panel title="Storage" description="Where files, uploads and generated reports are kept." className="min-w-0">
          {data ? (
            <StorageFacts storage={data.storage} limits={data.limits} />
          ) : (
            <p className="text-sm subtle">Storage state unavailable.</p>
          )}
        </Panel>

        <Panel title="Secret material" description="Masked configuration summaries only — the backend never returns a secret value." className="min-w-0">
          {data ? (
            <div className="space-y-2">
              <MaskedValue label="MONGODB_URI" value={data.config_summary?.mongo_uri} />
              <MaskedValue label="REDIS_URL" value={data.config_summary?.redis_url} />
              <MaskedValue label="NOAA_API_KEY" value={data.config_summary?.noaa_api_key} />
              <p className="mt-2 text-xs leading-relaxed subtle">
                Values are produced by <span className="mono">env.mask_value()</span> on the server: length and a three-character
                prefix at most. This page has no reveal control, and copy actions elsewhere exclude secret fields.
              </p>
            </div>
          ) : (
            <p className="text-sm subtle">Configuration summary unavailable.</p>
          )}
        </Panel>
      </div>

      <Panel
        title="Service readiness for production"
        description="State derived from the live status and settings responses, with the exact environment key to set for each gap."
        className="mb-6"
        footer={
          <span>
            {readiness.missing > 0
              ? `${readiness.missing} item(s) still need configuration before a production cutover.`
              : 'Every checked item reports as configured.'}
            {settings.error ? ` Configuration endpoint: ${describeError(settings.error).message}` : null}
          </span>
        }
      >
        <ul className="grid gap-3 sm:grid-cols-2">
          {readiness.items.map((item) => (
            <ConfigRequirement key={item.envKey + item.label} {...item} />
          ))}
        </ul>
      </Panel>

      <Panel
        title="Component checks"
        description="Every entry is a check the API runs against a real service; 'not-configured' means the deployment chose not to enable it."
        actions={<Badge tone="neutral">{data?.components.length ?? 0} component(s)</Badge>}
      >
        {data ? (
          <DataTable
            caption="Health components"
            rows={data.components}
            rowKey={(row) => row.name}
            columns={componentColumns}
            empty="The status response contained no component checks."
          />
        ) : (
          <p className="text-sm subtle">Component checks are listed once the status endpoint answers.</p>
        )}
      </Panel>
    </div>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-2 last:border-b-0 last:pb-0" style={{ borderColor: 'var(--border)' }}>
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="min-w-0 text-right">{children}</dd>
    </div>
  )
}

function asText(value: unknown): string | null {
  return typeof value === 'string' && value ? value : null
}

function asCount(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * Storage facts from `GET /api/v1/system/status`. The `storage` object is an open
 * record of whatever the backend chose to probe, so every cell narrows its own key
 * and shows "not reported" / "not collected" instead of implying a measurement.
 */
function StorageFacts({ storage, limits }: { storage: Record<string, unknown>; limits: Record<string, unknown> }) {
  const mode = asText(storage.mode)
  const localRoot = asText(storage.local_root)
  const hdfsEndpoint = asText(storage.hdfs_endpoint)
  const freeBytes = asCount(storage.free_bytes)
  const uploads = asCount(storage.uploads)
  const reports = asCount(storage.reports)
  return (
    <div className="space-y-3 text-sm">
      <Row label="Mode">
        <span className="flex flex-wrap items-center gap-2">
          {mode ? <Badge tone={mode === 'hdfs' ? 'success' : 'info'}>{humanise(mode)}</Badge> : <Badge tone="neutral">not reported</Badge>}
          <span className="text-xs subtle">local fallback is expected when HDFS_ENDPOINT is unset</span>
        </span>
      </Row>
      <Row label="Local root">
        <span className="mono break-all text-[12px]">{localRoot ?? 'not reported'}</span>
      </Row>
      <Row label="HDFS endpoint">
        {hdfsEndpoint ? <span className="mono text-[12px]">{hdfsEndpoint}</span> : <Badge tone="neutral">not configured</Badge>}
      </Row>
      <Row label="Free bytes">
        {freeBytes === null ? <Badge tone="neutral">not collected</Badge> : <span className="font-semibold">{formatBytes(freeBytes)}</span>}
      </Row>
      <Row label="Stored artefacts">
        <span className="flex flex-wrap items-center gap-3 text-xs">
          <span className="inline-flex items-center gap-1">
            <Database className="h-3.5 w-3.5" /> {uploads === null ? 'upload count not reported' : `${uploads.toLocaleString()} upload(s)`}
          </span>
          <span className="inline-flex items-center gap-1">
            <HardDrive className="h-3.5 w-3.5" /> {reports === null ? 'report file count not reported' : `${reports.toLocaleString()} report file(s)`}
          </span>
        </span>
      </Row>
      <Row label="Limits reported by the API">
        <span className="text-xs muted">
          {Object.keys(limits).length > 0
            ? Object.entries(limits)
                .map(([key, value]) => `${humanise(key)}: ${typeof value === 'number' ? formatBytes(value) : String(value)}`)
                .join(' · ')
            : 'not reported'}
        </span>
      </Row>
    </div>
  )
}

function JobGroup({ label, counts, to }: { label: string; counts: Record<string, number>; to: string }) {
  const total = breakdownTotal(counts)
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide subtle">{label}</p>
        <span className="text-xs muted">{total.toLocaleString()} job(s)</span>
      </div>
      <Breakdown counts={counts} kind="job" empty="No jobs recorded." />
      <Link to={to} className="link mt-1 inline-block text-xs">
        Inspect {label.toLowerCase()} jobs
      </Link>
    </div>
  )
}

interface ReadinessItem {
  label: string
  envKey: string
  configured: boolean | null
  detail?: string
  impact?: string
}

/**
 * Builds the production-readiness checklist from real API payloads only:
 * `SystemStatusResponse.config_summary` (masked env summary) and `SettingsResponse.env_derived`.
 */
function buildReadiness(
  status: SystemStatusResponse | null,
  derived?: SettingsResponse['env_derived'],
): { items: ReadinessItem[]; missing: number } {
  const environment = status?.config_summary ?? {}
  const items: ReadinessItem[] = []

  const atlasRequired = derived?.force_atlas_connection ?? maskedConfigured(environment.atlas_required) ?? Boolean(environment.atlas_required)
  items.push({
    label: 'MongoDB Atlas as primary store',
    envKey: 'MONGODB_URI + FORCE_ATLAS_CONNECTION',
    configured: atlasRequired && status?.database.mode === 'atlas',
    detail: `Current database mode: ${humanise(status?.database.mode ?? 'unknown')}. FORCE_ATLAS_CONNECTION=true makes startup fail instead of falling back to the local document store.`,
    impact: 'Local document-store mode is fine for development, but it is not a production datastore.',
  })

  const emailReady = derived?.email_delivery_ready ?? maskedConfigured(environment.email_notifications) ?? Boolean(environment.email_notifications)
  items.push({
    label: 'Email delivery (SMTP)',
    envKey: 'SMTP_HOST + SMTP_USER + ENABLE_EMAIL_NOTIFICATIONS',
    configured: emailReady === true,
    detail: `Email notifications ${emailReady ? 'enabled' : 'disabled'}; password-reset email requires SMTP_HOST and ENABLE_EMAIL_NOTIFICATIONS=true.`,
    impact: 'Without SMTP the self-service password reset returns “configuration required”; administrators must set passwords from User management.',
  })

  items.push({
    label: 'Self-service password reset',
    envKey: 'ENABLE_PASSWORD_RESET',
    configured: derived?.password_reset_enabled ?? null,
    detail: 'State comes from GET /api/v1/system/settings env_derived.password_reset_enabled.',
    impact: 'Disabled when the settings endpoint has not been reached.',
  })

  const noaa = maskedConfigured(environment.noaa_api_key)
  items.push({
    label: 'NOAA Climate Data Online connector',
    envKey: 'NOAA_API_KEY',
    configured: noaa,
    detail: `Key state reported as “${String(environment.noaa_api_key ?? 'not configured')}”.`,
    impact: 'The NOAA source stays in needs_configuration until a token is set; NASA POWER needs network access only.',
  })

  const redis = maskedConfigured(environment.redis_url) ?? Boolean(derived?.redis_configured)
  items.push({
    label: 'Celery broker for background work',
    envKey: 'REDIS_URL (plus CELERY_BROKER_URL)',
    configured: redis === true,
    detail: `Executor in use: ${humanise(status?.background.executor ?? 'unknown')}.`,
    impact: 'Without a reachable broker the in-process thread pool runs the same registered tasks — single-process only.',
  })

  const hdfs = maskedConfigured(environment.hdfs_endpoint) ?? Boolean(derived?.hdfs_configured)
  items.push({
    label: 'HDFS data lake',
    envKey: 'HDFS_ENDPOINT',
    configured: hdfs === true,
    detail: `Reported endpoint: ${String(environment.hdfs_endpoint ?? 'not configured')}.`,
    impact: 'Processing writes to the local data-lake directory when HDFS is unset.',
  })

  const spark = String(environment.spark_master ?? '')
  items.push({
    label: 'Spark master',
    envKey: 'SPARK_MASTER + SPARK_ENABLED',
    configured: spark ? !/not configured/i.test(spark) : null,
    detail: `Reported master: ${spark || 'not configured'}.`,
    impact: 'local[*] runs single-node; a cluster master is required for distributed PySpark jobs.',
  })

  const kafka = maskedConfigured(environment.kafka_brokers) ?? Boolean(derived?.kafka_enabled)
  items.push({
    label: 'Kafka streaming ingestion',
    envKey: 'KAFKA_ENABLED + KAFKA_BROKERS',
    configured: derived?.kafka_enabled === undefined ? kafka : derived.kafka_enabled,
    detail: `Brokers reported: ${String(environment.kafka_brokers ?? 'not configured')}.`,
    impact: 'Structured-streaming workflows stay unavailable while disabled.',
  })

  items.push({
    label: 'Airflow scheduled DAGs',
    envKey: 'AIRFLOW_ENABLED + AIRFLOW_BASE_URL',
    configured: derived?.airflow_enabled ?? null,
    detail: 'When disabled, scheduled syncs rely on the in-process scheduler and manual runs.',
    impact: 'Set AIRFLOW_ENABLED=true and deploy earthscape_daily_sync for operator-grade scheduling.',
  })

  items.push({
    label: 'Closed self-registration',
    envKey: 'ENABLE_SELF_REGISTRATION (+ ALLOW_ANALYST_SELF_REGISTRATION)',
    configured: derived ? derived.self_registration_enabled === false : maskedConfigured(environment.registration_enabled) === false,
    detail: `Self-registration ${derived?.self_registration_enabled === undefined ? 'state unknown' : derived.self_registration_enabled ? 'enabled' : 'disabled'}.`,
    impact: 'Production provisioning should run through User management or scripts/bootstrap_platform.py.',
  })

  items.push({
    label: 'Debug off + explicit allowed hosts',
    envKey: 'DJANGO_DEBUG=false, DJANGO_ALLOWED_HOSTS, FRONTEND_ORIGIN',
    configured: status ? status.config_summary?.debug === false : null,
    detail: `CORS allow-list: ${(status?.config_summary?.allowed_hosts as string[] | undefined)?.join(', ') ?? 'not reported'}.`,
    impact: 'HSTS and secure cookies are only emitted when DJANGO_DEBUG is false.',
  })

  const missing = items.filter((item) => item.configured !== true).length
  return { items, missing }
}
