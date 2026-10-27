import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Bell, BellOff, Check, Inbox, Loader2, MailOpen } from 'lucide-react'
import api from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAsync } from '../../hooks/useAsync'
import {
  Badge,
  Column,
  DataTable,
  EmptyState,
  ErrorState,
  Hint,
  PageHeader,
  Pagination,
  Panel,
  RefreshButton,
  Select,
  Skeleton,
  StatusBadge,
  Toggle,
} from '../../components/ui'
import { useToast } from '../../components/ui/ToastProvider'
import { humanise, relativeTime } from '../../lib/format'
import type { Notification } from '../../types/api'

const PAGE_SIZE = 15
const KINDS = ['alert', 'report', 'system', 'ticket']

const KIND_TONE: Record<string, 'neutral' | 'info' | 'success' | 'warning' | 'danger'> = {
  alert: 'danger',
  report: 'info',
  system: 'neutral',
  ticket: 'success',
}

/**
 * The notification contract carries `related_resource` and `type` but no explicit
 * `link` field, so the in-app destination is derived from those.
 */
function targetFor(notification: Notification): string | null {
  const resource = notification.related_resource?.type
  const kind = resource ?? notification.type
  switch (kind) {
    case 'alert':
      return '/app/alerts'
    case 'report':
      return '/app/reports'
    case 'ticket':
      return '/app/support'
    case 'system':
      return '/app/notifications'
    default:
      return null
  }
}

export default function NotificationsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const [page, setPage] = useState(1)
  const [kind, setKind] = useState('')
  const [onlyUnread, setOnlyUnread] = useState(false)
  const [marking, setMarking] = useState<string | null>(null)
  const [actionError, setActionError] = useState<ApiRequestError | null>(null)

  const list = useAsync(
    () => api.notifications.list({ page, page_size: PAGE_SIZE, type: kind || undefined, unread: onlyUnread ? 'true' : undefined }),
    [page, kind, onlyUnread],
    { interval: 45_000 },
  )
  const counts = useAsync(() => api.notifications.unread(), [], { interval: 45_000 })

  async function markRead(notification: Notification): Promise<boolean> {
    try {
      await api.notifications.markRead(notification.id)
      void list.refetch()
      void counts.refetch()
      return true
    } catch (cause) {
      const error = cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0)
      setActionError(error)
      return false
    }
  }

  async function openNotification(notification: Notification) {
    setActionError(null)
    const target = targetFor(notification)
    if (!notification.read_at) {
      setMarking(notification.id)
      const ok = await markRead(notification)
      setMarking(null)
      if (!ok) return
    }
    if (target && target !== '/app/notifications') navigate(target)
  }

  async function markAll() {
    setMarking('all')
    setActionError(null)
    try {
      const result = await api.notifications.markAllRead()
      toast.success('Marked all as read', `${result.updated} notification${result.updated === 1 ? '' : 's'} updated.`)
      void list.refetch()
      void counts.refetch()
    } catch (cause) {
      setActionError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setMarking(null)
    }
  }

  // Newest first from the API; unread lifted above read while keeping that order within each group.
  const items = [...(list.data?.items ?? [])].sort((a, b) => Number(Boolean(a.read_at)) - Number(Boolean(b.read_at)))
  const unreadTotal = counts.data?.unread ?? 0
  const hasFilters = Boolean(kind) || onlyUnread

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Activity"
        title="Notifications"
        description="Delivery records scoped to your account. Suppressed email is shown as suppressed, with the reason it was recorded."
        meta={
          list.loading ? (
            <span className="inline-flex items-center gap-1.5">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> Reading notifications…
            </span>
          ) : (
            <span>
              <Badge tone={unreadTotal > 0 ? 'warning' : 'neutral'}>{unreadTotal} unread</Badge>
              {counts.data && counts.data.critical > 0 ? <Badge tone="danger" title="Critical items needing attention">{counts.data.critical} critical</Badge> : null}
            </span>
          )
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <RefreshButton onClick={() => { void list.refetch(); void counts.refetch() }} busy={list.refreshing} label="Refresh" />
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void markAll()} disabled={marking !== null || unreadTotal === 0}>
              <MailOpen className="h-3.5 w-3.5" aria-hidden="true" /> {marking === 'all' ? 'Working…' : 'Mark all read'}
            </button>
          </div>
        }
      />

      {actionError ? <ErrorState error={actionError} /> : null}

      <Panel
        title="Your notifications"
        description="Selecting a row marks it as read and opens its destination where one exists."
        actions={
          <div className="flex flex-wrap items-end justify-end gap-3">
            <Select
              className="w-40"
              ariaLabel="Filter by kind"
              value={kind}
              onChange={(value) => {
                setKind(value)
                setPage(1)
              }}
              options={[{ value: '', label: 'All kinds' }, ...KINDS.map((value) => ({ value, label: humanise(value) }))]}
            />
            <Toggle checked={onlyUnread} onChange={(next) => { setOnlyUnread(next); setPage(1) }} label="Unread only" />
          </div>
        }
      >
        {list.loading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-16 w-full" />
            ))}
          </div>
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={hasFilters ? <BellOff className="h-5 w-5" /> : <Inbox className="h-5 w-5" />}
            title={hasFilters ? 'No notifications match these filters' : 'No notifications yet'}
            description={
              hasFilters
                ? 'Adjust the kind or unread filter to see the rest of your records.'
                : 'In-app and email records for your account appear here. They are raised when an enabled alert rule matches a stored observation, when a report is generated, or when a ticket you follow changes.'
            }
            action={
              hasFilters ? (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    setKind('')
                    setOnlyUnread(false)
                    setPage(1)
                  }}
                >
                  Clear filters
                </button>
              ) : (
                <Link to="/app/alerts" className="btn btn-secondary btn-sm">
                  View alerts
                </Link>
              )
            }
          />
        ) : (
          <DataTable columns={columns(markRead, openNotification, marking)} rows={items} rowKey={(item) => item.id} empty="No records" />
        )}

        <Pagination meta={list.data ?? undefined} onPage={setPage} label="notifications" disabled={list.loading} />

        <Hint>
          Records refresh automatically while this page is open. A notification stays scoped to you: the recipient filter is applied by the server, so one
          account never lists another&rsquo;s notifications.
        </Hint>
      </Panel>
    </div>
  )
}

function columns(
  onMark: (notification: Notification) => Promise<boolean>,
  onOpen: (notification: Notification) => void,
  marking: string | null,
): Column<Notification>[] {
  return [
    {
      key: 'read',
      header: '',
      width: '2.5rem',
      cell: (n) =>
        n.read_at ? <Check className="h-4 w-4 text-forest-600 dark:text-forest-300" aria-label="Read" /> : <span className="inline-block h-2 w-2 rounded-full bg-hydro-500" aria-label="Unread" />,
    },
    {
      key: 'title',
      header: 'Notification',
      cell: (n) => (
        <div className="min-w-0 max-w-[26rem]">
          <p className="truncate text-sm font-medium">{n.title}</p>
          <p className="line-clamp-2 text-xs muted">{n.body}</p>
        </div>
      ),
    },
    {
      key: 'kind',
      header: 'Kind',
      cell: (n) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={KIND_TONE[n.type] ?? 'neutral'}>{humanise(n.type)}</Badge>
          <Badge tone="neutral">{humanise(n.channel)}</Badge>
        </div>
      ),
    },
    {
      key: 'delivery',
      header: 'Delivery',
      cell: (n) => (
        <div className="min-w-0 text-xs">
          <StatusBadge value={n.delivery_status} />
          {n.delivery_status === 'suppressed' ? (
            <p className="mt-1 max-w-[20rem] text-[11px] leading-relaxed subtle">
              {n.failure_reason ?? 'Email delivery is not configured on this deployment, so no message was sent.'}
            </p>
          ) : n.delivery_status === 'failed' && n.failure_reason ? (
            <p className="mt-1 text-alert-high">{n.failure_reason}</p>
          ) : n.delivery_status === 'sent' && n.sent_at ? (
            <p className="mt-1 subtle">Sent {relativeTime(n.sent_at)}</p>
          ) : n.delivery_status === 'pending' && n.next_retry_at ? (
            <p className="mt-1 subtle">Retries {relativeTime(n.next_retry_at)}</p>
          ) : null}
          {n.attempt_count > 1 ? <p className="mt-1 subtle">{n.attempt_count} attempts</p> : null}
        </div>
      ),
    },
    { key: 'created', header: 'Received', cell: (n) => <span className="whitespace-nowrap text-xs" title={n.created_at}>{relativeTime(n.created_at)}</span> },
    {
      key: 'actions',
      header: '',
      align: 'right',
      cell: (n) => {
        const target = targetFor(n)
        const busy = marking === n.id
        return (
          <div className="flex justify-end gap-2">
            {target && target !== '/app/notifications' ? (
              <button type="button" className="btn btn-secondary btn-sm" onClick={() => onOpen(n)} disabled={busy}>
                Open
              </button>
            ) : null}
            {!n.read_at ? (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => void onMark(n)} disabled={busy}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Bell className="h-3.5 w-3.5" aria-hidden="true" />} Mark read
              </button>
            ) : null}
          </div>
        )
      },
    },
  ]
}
