import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Bell, Check, PlayCircle, SearchX, ShieldCheck, X } from 'lucide-react'
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
  Modal,
  Pagination,
  Panel,
  RefreshButton,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
  Tabs,
} from '../../components/ui'
import { SeverityBar } from '../../components/charts/MiniCharts'
import { JobMonitor } from './components/insight/JobMonitor'
import { QUERY } from './components/insight/query'
import { formatMetric, formatNumber, humanise, metricLabel, relativeTime, severityTone } from '../../lib/format'
import type { Alert, Notification } from '../../types/api'

const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical']
const ALERT_STATUSES = ['open', 'acknowledged', 'resolved', 'dismissed']

export default function AlertsPage() {
  const unread = useAsync(() => api.notifications.unread(), [], { interval: 60_000 })
  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Alerts &amp; reporting</p>
        <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Alerts and notification centre</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
          Fired alerts with their real thresholds and deviations, plus delivery records. Every value below is what the rules engine stored — a rule that fired
          without enough data says so rather than inventing a reading.
        </p>
      </header>
      <Tabs
        items={[
          { id: 'alerts', label: 'Alerts', content: <AlertsTab /> },
          {
            id: 'notifications',
            label: 'Notifications',
            badge: (unread.data?.unread ?? 0) > 0 ? <span className="badge badge-warning">{unread.data?.unread}</span> : undefined,
            content: <NotificationsTab />,
          },
        ]}
        initial={0}
      />
    </div>
  )
}

/* ------------------------------------------------------------------- alerts */

function AlertsTab() {
  const { can, hasRole } = useAuth()
  const isStaff = can('alerts:manage') || hasRole('administrator', 'climate_analyst')
  const isAdmin = hasRole('administrator')

  const [severity, setSeverity] = useState('')
  const [status, setStatus] = useState('')
  const [ruleId, setRuleId] = useState('')
  const [page, setPage] = useState(1)
  const [hasOpen, setHasOpen] = useState(false)

  const summary = useAsync(() => api.alerts.summary(), [], { interval: hasOpen ? 5000 : undefined })
  const rules = useAsync(() => api.alerts.rules({ page_size: 100 }), [])
  const list = useAsync(
    () => api.alerts.list({ page, page_size: 15, severity: severity || undefined, status: status || undefined, rule_id: ruleId || undefined }),
    [page, severity, status, ruleId],
    { interval: hasOpen ? 5000 : undefined },
  )

  const [resolving, setResolving] = useState<Alert | null>(null)
  const [note, setNote] = useState('')
  const [confirmAck, setConfirmAck] = useState<Alert | null>(null)
  const [working, setWorking] = useState(false)
  const [actionError, setActionError] = useState<ApiRequestError | null>(null)
  const [scanJobId, setScanJobId] = useState<string | null>(null)
  const [scanResult, setScanResult] = useState<string | null>(null)
  const [scanning, setScanning] = useState(false)

  useEffect(() => {
    setHasOpen(list.data?.items.some((alert) => alert.status === 'open') ?? false)
  }, [list.data])

  const ruleOptions = useMemo(
    () => (rules.data?.items ?? []).map((rule) => ({ value: rule.id, label: rule.name, hint: humanise(rule.severity) })),
    [rules.data],
  )

  async function acknowledge(alert: Alert) {
    setWorking(true)
    setActionError(null)
    try {
      await api.alerts.acknowledge(alert.id)
      setConfirmAck(null)
      void list.refetch()
      void summary.refetch()
    } catch (error) {
      setActionError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setWorking(false)
    }
  }

  async function resolve() {
    if (!resolving) return
    setWorking(true)
    setActionError(null)
    try {
      await api.alerts.resolve(resolving.id, note.trim())
      setResolving(null)
      setNote('')
      void list.refetch()
      void summary.refetch()
    } catch (error) {
      setActionError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setWorking(false)
    }
  }

  async function runScan() {
    if (!isStaff) return
    setScanning(true)
    setScanResult(null)
    setActionError(null)
    try {
      const response = await api.alerts.scan()
      if (response.job?.id) {
        setScanJobId(response.job.id)
      } else if (typeof response.matched === 'number') {
        setScanResult(`${response.matched} alert${response.matched === 1 ? '' : 's'} matched and recorded by this evaluation pass.`)
      } else {
        setScanResult(response.detail ?? 'Evaluation completed with no alerts to report.')
      }
      void list.refetch()
      void summary.refetch()
    } catch (error) {
      setActionError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setScanning(false)
    }
  }

  const rows = list.data?.items ?? []

  return (
    <div className="space-y-6">
      {summary.error ? <ErrorState error={summary.error} onRetry={() => void summary.refetch()} /> : null}

      {summary.data ? (
        <>
          <div className="grid-responsive">
            <StatCard label="Open" value={formatNumber(summary.data.open, 0)} tone="alert" hint="Awaiting acknowledgement" />
            <StatCard label="Acknowledged" value={formatNumber(summary.data.acknowledged, 0)} tone="solar" />
            <StatCard label="Resolved" value={formatNumber(summary.data.resolved, 0)} tone="forest" />
            <StatCard label="Rules enabled" value={formatNumber(summary.data.rules_enabled, 0)} tone="hydro" hint={summary.data.last_evaluation ? `Last evaluation ${relativeTime(summary.data.last_evaluation)}` : 'No evaluation recorded'} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Panel title="By severity" dense>
              <SeverityBar counts={summary.data.by_severity} />
            </Panel>
            <Panel title="By region" dense>
              {summary.data.by_region?.length ? (
                <ul className="space-y-1.5 text-xs">
                  {summary.data.by_region.map((entry) => (
                    <li key={entry.region_code} className="flex items-center justify-between gap-2 border-b pb-1" style={{ borderColor: 'var(--border)' }}>
                      <span className="mono">{entry.region_code}</span>
                      <span className="font-semibold">{entry.count}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm muted">No regional breakdown available.</p>
              )}
            </Panel>
          </div>
        </>
      ) : summary.loading ? (
        <div className="grid-responsive">
          {Array.from({ length: 4 }).map((_, index) => (
            <Skeleton key={index} className="h-24 w-full" />
          ))}
        </div>
      ) : null}

      <Panel
        title="Run rule evaluation now"
        description="Re-evaluates every enabled rule against the latest observations. Reports real match counts or an insufficient-data note."
        actions={
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void runScan()} disabled={!isStaff || scanning} title={!isStaff ? 'Requires climate analyst or administrator role.' : undefined}>
            <PlayCircle className="h-3.5 w-3.5" /> {scanning ? 'Evaluating…' : 'Evaluate rules'}
          </button>
        }
      >
        {!isStaff ? <Hint>You can view alerts, but running evaluations requires the climate analyst or administrator role.</Hint> : null}
        {scanResult ? <p className="mt-2 text-sm muted" role="status">{scanResult}</p> : null}
      </Panel>

      {scanJobId ? <JobMonitor jobId={scanJobId} label="Rule evaluation job" onFinished={() => { void list.refetch(); void summary.refetch() }} /> : null}
      {actionError ? <ErrorState error={actionError} /> : null}

      <Panel
        title="Alerts"
        description="Newest first. Rows stay open while an alert is unacknowledged."
        actions={<RefreshButton onClick={() => void list.refetch()} busy={list.refreshing} />}
      >
        <div className="mb-4 flex flex-wrap items-end gap-3">
          <Field label="Severity" className="w-36">
            <Select options={[{ value: '', label: 'All severities' }, ...SEVERITIES.map((value) => ({ value, label: humanise(value) }))]} value={severity} onChange={(value) => { setSeverity(value); setPage(1) }} ariaLabel="Filter by severity" />
          </Field>
          <Field label="Status" className="w-36">
            <Select options={[{ value: '', label: 'All statuses' }, ...ALERT_STATUSES.map((value) => ({ value, label: humanise(value) }))]} value={status} onChange={(value) => { setStatus(value); setPage(1) }} ariaLabel="Filter by status" />
          </Field>
          <Field label="Rule" className="w-48">
            <Select options={[{ value: '', label: 'All rules' }, ...ruleOptions]} value={ruleId} onChange={(value) => { setRuleId(value); setPage(1) }} ariaLabel="Filter by rule" />
          </Field>
        </div>

        {list.loading ? <Skeleton className="h-40 w-full" /> : null}
        {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
        {!list.loading && rows.length === 0 ? (
          <EmptyState icon={<SearchX className="h-5 w-5" />} title="No alerts match these filters" description="Alerts appear as soon as an enabled rule matches a stored observation." action={<Link to="/admin/alert-rules" className="link">Manage alert rules</Link>} />
        ) : null}

        <DataTable
          columns={alertColumns(isStaff, isAdmin, setConfirmAck, setResolving)}
          rows={rows}
          rowKey={(alert) => alert.id}
          empty="No records"
        />
        <Pagination meta={list.data ?? undefined} onPage={setPage} label="alerts" />
      </Panel>

      <ConfirmDialog
        open={Boolean(confirmAck)}
        title="Acknowledge alert"
        confirmLabel="Acknowledge"
        busy={working}
        onClose={() => setConfirmAck(null)}
        onConfirm={() => confirmAck && void acknowledge(confirmAck)}
        message={<>Acknowledge <strong>{confirmAck?.name ?? humanise(confirmAck?.severity ?? '')}</strong>? This records who picked it up; the alert stays visible until resolved.</>}
      />

      <Modal
        open={Boolean(resolving)}
        title="Resolve alert"
        description="Add a short resolution note describing what was done."
        onClose={() => { setResolving(null); setNote('') }}
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={() => { setResolving(null); setNote('') }} disabled={working}>Cancel</button>
            <button type="button" className="btn btn-danger" onClick={() => void resolve()} disabled={working || !note.trim()}>
              <X className="h-3.5 w-3.5" /> {working ? 'Working…' : 'Resolve'}
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-sm muted">{resolving?.explanation}</p>
          <Field label="Resolution note" htmlFor="resolve-note" required>
            <textarea id="resolve-note" className="input" rows={3} value={note} onChange={(event) => setNote(event.target.value)} placeholder="e.g. Verified with station ops; sensor recalibrated." />
          </Field>
          <Hint>The resolution note is sent to the server with the resolve request; the alert is only marked resolved once the backend records it that way.</Hint>
        </div>
      </Modal>
    </div>
  )
}

function alertColumns(
  isStaff: boolean,
  isAdmin: boolean,
  onAcknowledge: (alert: Alert) => void,
  onResolve: (alert: Alert) => void,
): Column<Alert>[] {
  return [
    {
      key: 'severity',
      header: 'Severity',
      cell: (alert) => <Badge tone={severityTone(alert.severity)}>{humanise(alert.severity)}</Badge>,
    },
    {
      key: 'when',
      header: 'Triggered',
      cell: (alert) => (
        <div className="min-w-0">
          <span className="block text-xs font-medium" title={alert.triggered_at}>{relativeTime(alert.triggered_at)}</span>
          {alert.name ? <span className="block max-w-[12rem] truncate text-xs subtle">{alert.name}</span> : null}
        </div>
      ),
    },
    {
      key: 'scope',
      header: 'Scope',
      cell: (alert) => (
        <div className="text-xs">
          <span className="mono">{alert.location_code ?? alert.region_code ?? 'global'}</span>
          {alert.location_name ? <span className="block subtle">{alert.location_name}</span> : null}
        </div>
      ),
    },
    {
      key: 'reading',
      header: 'Value vs threshold',
      cell: (alert) => {
        const field = alert.metric ?? alert.target_variable
        return (
          <div className="text-xs">
            <span className="block font-medium">{metricLabel(field)}</span>
            <span className="mono">
              {alert.value != null && field ? formatMetric(alert.value, field) : '—'}
              {alert.threshold_or_baseline != null ? <span className="subtle"> / {formatNumber(alert.threshold_or_baseline, 1)}{alert.unit ? ` ${alert.unit}` : ''}</span> : null}
            </span>
            {alert.deviation_score != null ? <span className="block subtle">deviation {formatNumber(alert.deviation_score, 2)}σ</span> : null}
          </div>
        )
      },
    },
    {
      key: 'status',
      header: 'Status',
      cell: (alert) => <StatusBadge value={alert.status} />,
    },
    {
      key: 'explanation',
      header: 'Explanation',
      cell: (alert) => (
        <div className="min-w-0 max-w-[22rem]">
          <p className="text-xs leading-relaxed muted">{alert.explanation}</p>
          {alert.method ? <p className="mt-0.5 text-[11px] subtle">via {humanise(alert.method)}{alert.source ? ` · ${humanise(alert.source)}` : ''}</p> : null}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Link
              to={`/app/anomalies?${new URLSearchParams(compact({ [QUERY.metric]: alert.target_variable, [QUERY.date]: alert.observation_date, [QUERY.region]: alert.region_code, [QUERY.location]: alert.location_code })).toString()}`}
              className="link text-[11px]"
            >
              Anomaly context
            </Link>
            <Link
              to={`/app/history?${new URLSearchParams(compact({ [QUERY.metric]: alert.target_variable, [QUERY.region]: alert.region_code, [QUERY.location]: alert.location_code })).toString()}`}
              className="link text-[11px]"
            >
              History
            </Link>
            {isAdmin && alert.rule_id ? (
              <Link to="/admin/alert-rules" className="link inline-flex items-center gap-1 text-[11px]">
                <ShieldCheck className="h-3 w-3" /> Rule
              </Link>
            ) : null}
          </div>
        </div>
      ),
    },
    {
      key: 'actions',
      header: 'Actions',
      align: 'right',
      cell: (alert) => {
        const actionable = isStaff && alert.status !== 'resolved' && alert.status !== 'dismissed'
        return (
          <div className="flex justify-end gap-2">
            <button type="button" className="btn btn-secondary btn-sm" disabled={!actionable || alert.status !== 'open'} onClick={() => onAcknowledge(alert)} title={!isStaff ? 'Analyst/admin role required' : alert.status !== 'open' ? 'Already acknowledged' : 'Acknowledge'}>
              <Check className="h-3.5 w-3.5" /> Ack
            </button>
            <button type="button" className="btn btn-danger btn-sm" disabled={!actionable} onClick={() => onResolve(alert)} title={!isStaff ? 'Analyst/admin role required' : 'Resolve with a note'}>
              Resolve
            </button>
          </div>
        )
      },
    },
  ]
}

function compact(input: Record<string, string | undefined | null>): Record<string, string> {
  const output: Record<string, string> = {}
  for (const [key, value] of Object.entries(input)) {
    if (value) output[key] = String(value)
  }
  return output
}

/* ----------------------------------------------------------- notifications */

function NotificationsTab() {
  const [channel, setChannel] = useState('')
  const [delivery, setDelivery] = useState('')
  const [onlyUnread, setOnlyUnread] = useState(false)
  const [page, setPage] = useState(1)

  const list = useAsync(
    () => api.notifications.list({ page, page_size: 12, channel: channel || undefined, status: delivery || undefined, unread: onlyUnread ? 'true' : undefined }),
    [page, channel, delivery, onlyUnread],
  )

  const [marking, setMarking] = useState<string | null>(null)

  async function markRead(notification: Notification) {
    setMarking(notification.id)
    try {
      await api.notifications.markRead(notification.id)
      void list.refetch()
    } finally {
      setMarking(null)
    }
  }

  async function markAll() {
    setMarking('all')
    try {
      await api.notifications.markAllRead()
      void list.refetch()
    } finally {
      setMarking(null)
    }
  }

  const rows = list.data?.items ?? []

  return (
    <Panel
      title="Notification delivery"
      description="Records scoped to your account. Suppressed email is not shown as delivered."
      actions={
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => void markAll()} disabled={marking !== null}>
          <Bell className="h-3.5 w-3.5" /> {marking === 'all' ? 'Working…' : 'Mark all read'}
        </button>
      }
    >
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Channel" className="w-36">
          <Select options={[{ value: '', label: 'All channels' }, { value: 'in_app', label: 'In-app' }, { value: 'email', label: 'Email' }]} value={channel} onChange={(value) => { setChannel(value); setPage(1) }} ariaLabel="Filter by channel" />
        </Field>
        <Field label="Delivery" className="w-40">
          <Select options={[{ value: '', label: 'All statuses' }, ...['pending', 'sent', 'failed', 'suppressed'].map((value) => ({ value, label: humanise(value) }))]} value={delivery} onChange={(value) => { setDelivery(value); setPage(1) }} ariaLabel="Filter by delivery status" />
        </Field>
        <label className="inline-flex items-center gap-2 self-center text-sm muted">
          <input type="checkbox" checked={onlyUnread} onChange={(event) => { setOnlyUnread(event.target.checked); setPage(1) }} />
          Unread only
        </label>
      </div>

      {list.loading ? <Skeleton className="h-40 w-full" /> : null}
      {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
      {!list.loading && rows.length === 0 ? (
        <EmptyState icon={<AlertTriangle className="h-5 w-5" />} title="No notifications" description="In-app and email delivery records for your account will appear here." />
      ) : null}

      <DataTable columns={notificationColumns(markRead, marking)} rows={rows} rowKey={(notification) => notification.id} empty="No records" />
      <Pagination meta={list.data ?? undefined} onPage={setPage} label="notifications" />
    </Panel>
  )
}

function notificationColumns(onRead: (notification: Notification) => void, marking: string | null): Column<Notification>[] {
  return [
    {
      key: 'read',
      header: '',
      width: '2.5rem',
      cell: (notification) =>
        notification.read_at ? <Check className="h-4 w-4 text-forest-600" aria-label="Read" /> : <span className="inline-block h-2 w-2 rounded-full bg-hydro-500" aria-label="Unread" />,
    },
    {
      key: 'title',
      header: 'Notification',
      cell: (notification) => (
        <div className="min-w-0 max-w-[24rem]">
          <p className="truncate text-sm font-medium">{notification.title}</p>
          <p className="line-clamp-2 text-xs muted">{notification.body}</p>
        </div>
      ),
    },
    { key: 'channel', header: 'Channel', cell: (notification) => <Badge tone="neutral">{humanise(notification.channel)}</Badge> },
    {
      key: 'delivery',
      header: 'Delivery',
      cell: (notification) => (
        <div className="text-xs">
          <StatusBadge value={notification.delivery_status} />
          {notification.delivery_status === 'failed' && notification.failure_reason ? <p className="mt-0.5 text-alert-high">{notification.failure_reason}</p> : null}
          {notification.delivery_status === 'suppressed' ? (
            <p className="mt-0.5 text-[11px] subtle" title="Email is recorded as suppressed until SMTP is configured; nothing was delivered.">
              Suppressed — email not configured; no delivery attempted
            </p>
          ) : notification.delivery_status === 'sent' && notification.sent_at ? (
            <p className="mt-0.5 subtle">Sent {relativeTime(notification.sent_at)}</p>
          ) : notification.delivery_status === 'pending' && notification.next_retry_at ? (
            <p className="mt-0.5 subtle">Retries {relativeTime(notification.next_retry_at)}</p>
          ) : null}
          {notification.attempt_count > 1 ? <p className="mt-0.5 subtle">{notification.attempt_count} attempts</p> : null}
        </div>
      ),
    },
    {
      key: 'related',
      header: 'Related',
      cell: (notification) =>
        notification.related_resource?.type === 'alert' ? (
          <Link to="/app/alerts" className="link text-xs">Open alert</Link>
        ) : notification.related_resource ? (
          <span className="mono text-xs">{notification.related_resource.type}</span>
        ) : (
          <span className="text-xs subtle">—</span>
        ),
    },
    { key: 'created', header: 'Created', cell: (notification) => <span className="text-xs">{relativeTime(notification.created_at)}</span> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (notification) =>
        notification.read_at ? (
          <span className="text-xs subtle">Read {relativeTime(notification.read_at)}</span>
        ) : (
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => onRead(notification)} disabled={marking === notification.id}>
            Mark read
          </button>
        ),
    },
  ]
}
