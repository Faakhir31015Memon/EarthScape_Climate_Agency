import { useCallback, useEffect, useMemo, useState } from 'react'
import { Inbox, Send, ShieldAlert } from 'lucide-react'
import api from '../../services/api'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Hint,
  KeyValue,
  Modal,
  Mono,
  PageHeader,
  Pagination,
  Panel,
  RefreshButton,
  SearchInput,
  Select,
  StatCard,
  Tabs,
  type Column,
  type Option,
  type TabItem,
} from '../../components/ui'
import { ActionFeedback, type Feedback } from './components/AdminBits'
import { PAGE_SIZE, describeError, noteFrom, unwrapOne } from './components/adminUtils'
import { formatDate, humanise, relativeTime } from '../../lib/format'
import type { Account, FeedbackRecord, SupportTicket, TicketComment } from '../../types/api'

const DEFAULT_STATUSES = ['open', 'in_progress', 'resolved', 'closed']
const DEFAULT_PRIORITIES = ['low', 'medium', 'high', 'urgent']
const DEFAULT_CATEGORIES = ['bug', 'data_request', 'access', 'feature', 'other']

const PRIORITY_TONE: Record<string, 'danger' | 'warning' | 'info' | 'neutral'> = {
  urgent: 'danger',
  high: 'danger',
  medium: 'warning',
  low: 'info',
}
const TICKET_STATUS_TONE: Record<string, 'info' | 'warning' | 'success' | 'neutral'> = {
  open: 'info',
  in_progress: 'warning',
  resolved: 'success',
  closed: 'neutral',
}
const FEEDBACK_STATUS_TONE: Record<string, 'info' | 'neutral' | 'success'> = {
  new: 'info',
  reviewed: 'neutral',
  actioned: 'success',
}

function toOptions(values: string[]): Option[] {
  return values.map((value) => ({ value, label: humanise(value) }))
}

/**
 * Support & feedback administration.
 *
 * Tickets are read through `GET /support/tickets` and triaged with
 * `PATCH /support/tickets/<id>`, `POST .../messages` and `POST .../close`. The
 * backend scopes the queue: staff see every ticket, everyone else sees only their
 * own, so the page relies on that rather than guessing scope. Feedback is read
 * with `GET /feedback` and triaged with `PATCH /feedback/<id>` (administrator
 * only). The client's typed `message()` cannot flag an internal note, so messages
 * added here are public; internal notes created elsewhere are shown (staff only)
 * and marked distinctly.
 */
export default function TicketAdminPage() {
  const { can, hasRole } = useAuth()
  const canRead = can('support:read')
  const canManage = can('support:manage')
  const isAdministrator = hasRole('administrator')
  const canManageFeedback = can('feedback:manage')

  if (!canRead) {
    return (
      <div>
        <PageHeader eyebrow="Administration" title="Support & feedback" description="Tickets and feedback submitted by users." />
        <div className="flex items-start gap-2 rounded-xl border p-4 text-sm muted" style={{ borderColor: 'var(--border)' }} role="status">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-alert-high" aria-hidden="true" />
          <span>Your account does not have the <Mono>support:read</Mono> capability, so the support queue is not shown. No ticket data was loaded.</span>
        </div>
      </div>
    )
  }

  const tabs: TabItem[] = [
    { id: 'tickets', label: 'Tickets', content: <TicketsTab canManage={canManage} isAdministrator={isAdministrator} /> },
    { id: 'feedback', label: 'Feedback', content: <FeedbackTab canManage={canManageFeedback} /> },
  ]

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Support & feedback"
        description="The support queue and user feedback. Staff see the whole queue; anyone else sees only their own tickets. The server enforces this either way."
      />
      <Tabs items={tabs} />
    </div>
  )
}

// tickets

function TicketsTab({ canManage, isAdministrator }: { canManage: boolean; isAdministrator: boolean }) {
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [priority, setPriority] = useState('')
  const [assignee, setAssignee] = useState('')
  const [term, setTerm] = useState('')
  const debouncedTerm = useDebouncedValue(term, 350)
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState<SupportTicket | null>(null)

  const enums = useAsync(() => api.support.categories(), [])
  const summary = useAsync(() => api.support.summary(), [])
  const users = useAsync(() => api.users.list({ page: 1, page_size: 200 }), [])

  const listParams = {
    page,
    page_size: PAGE_SIZE,
    status: status || undefined,
    category: category || undefined,
    priority: priority || undefined,
    // The backend honours assigned_to and search even though the typed signature
    // exposes only status/category/priority/mine; PageParams allows the extra keys.
    assigned_to: assignee || undefined,
    search: debouncedTerm.trim() || undefined,
  }
  const tickets = useAsync(() => api.support.list(listParams), [page, status, category, priority, assignee, debouncedTerm])

  useEffect(() => setPage(1), [status, category, priority, assignee, debouncedTerm])

  const staffOptions = useMemo<Option[]>(() => {
    const accounts = users.data?.items ?? []
    return accounts
      .filter((account: Account) => account.is_active && (account.role === 'administrator' || account.role === 'climate_analyst'))
      .map((account: Account) => ({ value: account.id, label: account.full_name || account.email }))
  }, [users.data])

  const statusValues = enums.data?.statuses ?? DEFAULT_STATUSES
  const categoryValues = enums.data?.categories ?? DEFAULT_CATEGORIES
  const priorityValues = enums.data?.priorities ?? DEFAULT_PRIORITIES

  const refresh = useCallback(async () => {
    await Promise.all([tickets.refetch(), summary.refetch()])
  }, [tickets, summary])

  const columns = useMemo<Column<SupportTicket>[]>(
    () => [
      {
        key: 'subject',
        header: 'Ticket',
        cell: (row) => (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{row.subject}</p>
            <p className="truncate text-[11px] subtle">{row.created_by_name || row.created_by} · {formatDate(row.created_at)}</p>
          </div>
        ),
      },
      { key: 'category', header: 'Category', width: '8rem', cell: (row) => <Badge tone="neutral">{humanise(row.category)}</Badge> },
      { key: 'priority', header: 'Priority', width: '7rem', cell: (row) => <Badge tone={PRIORITY_TONE[row.priority] ?? 'neutral'}>{humanise(row.priority)}</Badge> },
      { key: 'status', header: 'Status', width: '8rem', cell: (row) => <Badge tone={TICKET_STATUS_TONE[row.status] ?? 'neutral'}>{humanise(row.status)}</Badge> },
      {
        key: 'assignee',
        header: 'Assignee',
        width: '9rem',
        cell: (row) => (row.assigned_to_name ? <span className="text-sm">{row.assigned_to_name}</span> : <span className="text-xs subtle">unassigned</span>),
      },
      { key: 'updated', header: 'Last activity', width: '9rem', cell: (row) => <span className="text-xs muted" title={formatDate(row.updated_at ?? row.created_at, true)}>{relativeTime(row.updated_at ?? row.created_at)}</span> },
      { key: 'comments', header: 'Replies', width: '5rem', align: 'right', cell: (row) => <span className="mono text-[12px]">{row.comments.length}</span> },
    ],
    [],
  )

  const summaryData = summary.data
  const summaryAny = summaryData as unknown as { unassigned?: number | null } | null

  return (
    <div>
      {/* stat cards — only counts the summary endpoint returned */}
      <div className="grid-responsive mb-4">
        <StatCard label="Open" value={summaryData ? summaryData.open.toLocaleString() : '—'} icon={<Inbox className="h-4 w-4" />} loading={summary.loading} hint="Status: open" />
        <StatCard label="In progress" value={summaryData ? summaryData.in_progress.toLocaleString() : '—'} loading={summary.loading} hint="Being worked" />
        <StatCard label="Resolved" value={summaryData ? summaryData.resolved.toLocaleString() : '—'} tone="forest" loading={summary.loading} />
        <StatCard label="Closed" value={summaryData ? summaryData.closed.toLocaleString() : '—'} loading={summary.loading} />
        <StatCard
          label="Unassigned"
          value={summaryAny && typeof summaryAny.unassigned === 'number' ? summaryAny.unassigned.toLocaleString() : <span className="text-lg">queue only</span>}
          loading={summary.loading}
          hint={summaryAny && summaryAny.unassigned === null ? 'Shown for staff; your scope hides the queue-wide count.' : 'Open tickets with no assignee'}
        />
        <StatCard
          label="Median first response"
          value={summaryData && summaryData.median_first_response_hours !== null ? `${summaryData.median_first_response_hours.toLocaleString()} h` : <span className="text-lg">—</span>}
          loading={summary.loading}
          hint={summaryData && summaryData.median_first_response_hours === null ? 'No ticket has been answered yet in this scope.' : 'From stored reply timestamps'}
        />
      </div>

      {summary.error ? <div className="mb-4"><ErrorState error={summary.error} onRetry={() => void summary.refetch()} /></div> : null}

      {summaryData && Object.keys(summaryData.by_priority ?? {}).length > 0 ? (
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs">
          <span className="subtle">Open by priority:</span>
          {Object.entries(summaryData.by_priority).filter(([, count]) => count > 0).map(([key, count]) => (
            <span key={key} className="inline-flex items-center gap-1">
              <Badge tone={PRIORITY_TONE[key] ?? 'neutral'}>{humanise(key)}</Badge>
              <span className="mono">{count.toLocaleString()}</span>
            </span>
          ))}
        </div>
      ) : null}

      <FilterBar>
        <div className="min-w-[13rem] flex-1">
          <SearchInput value={term} onChange={setTerm} label="Search tickets" placeholder="Search subject or description…" />
        </div>
        <Field label="Status" htmlFor="tk-status"><Select id="tk-status" options={[{ value: '', label: 'Any status' }, ...toOptions(statusValues)]} value={status} onChange={setStatus} ariaLabel="Filter by status" /></Field>
        <Field label="Category" htmlFor="tk-category"><Select id="tk-category" options={[{ value: '', label: 'Any category' }, ...toOptions(categoryValues)]} value={category} onChange={setCategory} ariaLabel="Filter by category" /></Field>
        <Field label="Priority" htmlFor="tk-priority"><Select id="tk-priority" options={[{ value: '', label: 'Any priority' }, ...toOptions(priorityValues)]} value={priority} onChange={setPriority} ariaLabel="Filter by priority" /></Field>
        <Field label="Assignee" htmlFor="tk-assignee">
          <Select id="tk-assignee" options={[{ value: '', label: 'Any assignee' }, { value: 'none', label: 'Unassigned' }, ...staffOptions]} value={assignee} onChange={setAssignee} ariaLabel="Filter by assignee" />
        </Field>
      </FilterBar>

      <Panel
        title="Queue"
        description="Click a row to open, reply, triage, assign or close. Staff triage controls are hidden when your account lacks the capability."
        dense
        actions={<RefreshButton onClick={() => void refresh()} busy={tickets.refreshing} />}
      >
        <DataTable
          caption="Support tickets"
          columns={columns}
          rows={tickets.data?.items ?? []}
          rowKey={(row) => row.id}
          loading={tickets.loading}
          onRowClick={(row) => setSelected(row)}
          empty={tickets.loading ? 'Loading tickets…' : 'No tickets match these filters.'}
        />
        <Pagination meta={tickets.data ?? undefined} onPage={setPage} label="tickets" />
      </Panel>

      {selected ? (
        <TicketDetailModal
          ticket={selected}
          canManage={canManage}
          isAdministrator={isAdministrator}
          staffOptions={staffOptions}
          statusValues={statusValues}
          categoryValues={categoryValues}
          priorityValues={priorityValues}
          onClose={() => setSelected(null)}
          onRefreshed={refresh}
        />
      ) : null}
    </div>
  )
}

function TicketDetailModal({
  ticket,
  canManage,
  isAdministrator,
  staffOptions,
  statusValues,
  categoryValues,
  priorityValues,
  onClose,
  onRefreshed,
}: {
  ticket: SupportTicket
  canManage: boolean
  isAdministrator: boolean
  staffOptions: Option[]
  statusValues: string[]
  categoryValues: string[]
  priorityValues: string[]
  onClose: () => void
  onRefreshed: () => Promise<void>
}) {
  const [current, setCurrent] = useState<SupportTicket>(ticket)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reply, setReply] = useState('')
  const [closing, setClosing] = useState(false)
  const [resolution, setResolution] = useState('')

  useEffect(() => setCurrent(ticket), [ticket])

  const apply = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true)
      setError(null)
      setFeedback(null)
      try {
        const raw = await action()
        const note = noteFrom(raw)
        setFeedback({ tone: 'success', text: note ? `${success} ${note}` : success })
        return true
      } catch (cause) {
        const described = describeError(cause)
        // Reflect the refusal verbatim and leave the ticket unchanged.
        setError(`${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`)
        return false
      } finally {
        setBusy(false)
      }
    },
    [],
  )

  const reloadCurrent = useCallback(async () => {
    try {
      const fresh = unwrapOne<SupportTicket>(await api.support.get(current.id))
      setCurrent(fresh)
    } catch {
      /* the list refresh below still surfaces the true state */
    }
    await onRefreshed()
  }, [current.id, onRefreshed])

  const changeField = useCallback(
    async (changes: Record<string, unknown>, label: string) => {
      const ok = await apply(() => api.support.update(current.id, changes), label)
      if (ok) await reloadCurrent()
    },
    [apply, current.id, reloadCurrent],
  )

  const sendReply = useCallback(async () => {
    const body = reply.trim()
    if (!body) {
      setError('Type a message before sending.')
      return
    }
    const ok = await apply(() => api.support.message(current.id, body), 'Message added.')
    if (ok) {
      setReply('')
      await reloadCurrent()
    }
  }, [apply, current.id, reply, reloadCurrent])

  const closeTicket = useCallback(async () => {
    const note = resolution.trim()
    if (!note) {
      setError('A resolution reason is required to close this ticket.')
      return
    }
    const ok = await apply(() => api.support.close(current.id, note), 'Ticket closed.')
    if (ok) {
      setClosing(false)
      setResolution('')
      await reloadCurrent()
    }
  }, [apply, current.id, reloadCurrent, resolution])

  const extra = current as unknown as { comment_count_total?: number; can_reply?: boolean; closed_at?: string | null }
  const hiddenInternal = typeof extra.comment_count_total === 'number' && extra.comment_count_total > current.comments.length

  return (
    <Modal open onClose={onClose} title={current.subject} width="max-w-3xl" description={<span>#{current.id} · opened by {current.created_by_name || current.created_by} · {formatDate(current.created_at, true)}</span>}>
      <div className="space-y-5">
        <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
        {error ? (
          <div role="alert" className="rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm text-alert-critical">
            {error}
          </div>
        ) : null}

        <KeyValue
          columns={2}
          items={[
            { label: 'Status', value: <Badge tone={TICKET_STATUS_TONE[current.status] ?? 'neutral'}>{humanise(current.status)}</Badge> },
            { label: 'Priority', value: <Badge tone={PRIORITY_TONE[current.priority] ?? 'neutral'}>{humanise(current.priority)}</Badge> },
            { label: 'Category', value: humanise(current.category) },
            { label: 'Assignee', value: current.assigned_to_name ?? 'unassigned' },
            { label: 'First response', value: current.first_response_at ? formatDate(current.first_response_at, true) : 'not answered yet' },
            { label: 'Resolution', value: current.resolution_note ?? '—' },
          ]}
        />

        <Field label="Description" htmlFor={`desc-${current.id}`}>
          <div id={`desc-${current.id}`} className="whitespace-pre-wrap rounded-lg border p-3 text-sm leading-relaxed muted" style={{ borderColor: 'var(--border)' }}>
            {current.description}
          </div>
        </Field>

        {/* triage controls */}
        {canManage ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Change status" htmlFor="tk-set-status" hint="Staff only. Sending a staff reply also moves Open → In progress.">
              <Select id="tk-set-status" options={toOptions(statusValues)} value={current.status} disabled={busy} onChange={(next) => void changeField({ status: next }, 'Status updated.')} ariaLabel="Change status" />
            </Field>
            <Field label="Change priority" htmlFor="tk-set-priority">
              <Select id="tk-set-priority" options={toOptions(priorityValues)} value={current.priority} disabled={busy} onChange={(next) => void changeField({ priority: next }, 'Priority updated.')} ariaLabel="Change priority" />
            </Field>
            <Field label="Category" htmlFor="tk-set-category">
              <Select id="tk-set-category" options={toOptions(categoryValues)} value={current.category} disabled={busy} onChange={(next) => void changeField({ category: next }, 'Category updated.')} ariaLabel="Change category" />
            </Field>
            <Field label="Assign to" htmlFor="tk-set-assignee" hint={isAdministrator ? undefined : 'Only an administrator can assign a ticket.'} className="sm:col-span-2">
              <Select
                id="tk-set-assignee"
                options={[{ value: '', label: 'Unassigned' }, ...staffOptions]}
                value={current.assigned_to ?? ''}
                disabled={busy || !isAdministrator}
                onChange={(next) => void changeField({ assigned_to: next || null }, next ? 'Ticket assigned.' : 'Assignment cleared.')}
                ariaLabel="Assign ticket"
              />
            </Field>
            <div className="flex items-end">
              <button type="button" className="btn btn-secondary btn-sm w-full sm:w-auto" disabled={busy} onClick={() => setClosing(true)}>
                Close with reason
              </button>
            </div>
          </div>
        ) : null}

        {/* thread */}
        <div>
          <p className="panel-title mb-2">Conversation</p>
          {current.comments.length === 0 ? <p className="text-xs subtle">No messages yet.</p> : null}
          <ol className="space-y-2">
            {current.comments.map((comment, index) => (
              <CommentRow key={`${comment.author_user_id}-${index}-${comment.created_at}`} comment={comment} />
            ))}
          </ol>
          {hiddenInternal ? <p className="mt-2 text-[11px] subtle">Some internal staff notes are not visible to your role and are omitted here.</p> : null}
        </div>

        {/* add message */}
        <div>
          <Field label="Add a message" htmlFor="tk-new-message" hint="Sent as a public reply: the typed client exposes no internal-note flag, so notes you add here are visible to the author.">
            <textarea id="tk-new-message" className="textarea" rows={3} value={reply} onChange={(event) => setReply(event.target.value)} placeholder="Write a reply…" />
          </Field>
          <div className="mt-2 flex justify-end">
            <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => void sendReply()}>
              <Send className="h-3.5 w-3.5" aria-hidden="true" /> Send message
            </button>
          </div>
        </div>

        {closing ? (
          <div className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
            <Field label="Resolution reason" htmlFor="tk-resolution" required hint="Required before the ticket can be closed.">
              <textarea id="tk-resolution" className="textarea" rows={2} value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="How was this resolved?" />
            </Field>
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => { setClosing(false); setResolution('') }}>Cancel</button>
              <button type="button" className="btn btn-danger btn-sm" disabled={busy} onClick={() => void closeTicket()}>Confirm close</button>
            </div>
          </div>
        ) : null}
      </div>
    </Modal>
  )
}

function CommentRow({ comment }: { comment: TicketComment }) {
  return (
    <li
      className={`rounded-lg border p-3 text-sm ${comment.internal ? 'border-dashed bg-[var(--surface-muted)]' : ''}`}
      style={{ borderColor: 'var(--border)' }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs subtle">
        <span>
          <span className="font-medium">{comment.author_name || comment.author_user_id}</span> · {humanise(comment.author_role)}
        </span>
        <span className="flex items-center gap-2">
          {comment.internal ? <Badge tone="warning" title="Internal note, visible to staff only">internal</Badge> : null}
          <span>{formatDate(comment.created_at, true)}</span>
        </span>
      </div>
      <p className="mt-1 whitespace-pre-wrap break-words muted">{comment.body}</p>
    </li>
  )
}

// feedback

const FEEDBACK_STATUSES = ['new', 'reviewed', 'actioned']

function FeedbackTab({ canManage }: { canManage: boolean }) {
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [ratingTerm, setRatingTerm] = useState('')
  const [selected, setSelected] = useState<FeedbackRecord | null>(null)

  const summary = useAsync(() => api.feedback.summary(), [])
  const list = useAsync(
    () => api.feedback.list({ page, page_size: PAGE_SIZE, status: status || undefined }),
    [page, status],
  )
  useEffect(() => setPage(1), [status])

  const rows = useMemo(() => {
    const all = list.data?.items ?? []
    const min = Number(ratingTerm)
    if (!Number.isFinite(min) || min < 1) return all
    return all.filter((record) => record.rating >= min)
  }, [list.data, ratingTerm])

  const columns = useMemo<Column<FeedbackRecord>[]>(
    () => [
      { key: 'rating', header: 'Rating', width: '6rem', cell: (row) => <span className="mono">{row.rating}<span className="subtle">/5</span></span> },
      { key: 'message', header: 'Message', cell: (row) => <span className="line-clamp-2 text-sm">{row.message}</span> },
      { key: 'category', header: 'Area', width: '8rem', cell: (row) => <Badge tone="neutral">{humanise(row.category)}</Badge> },
      { key: 'status', header: 'Triage', width: '7rem', cell: (row) => <Badge tone={FEEDBACK_STATUS_TONE[row.status] ?? 'neutral'}>{humanise(row.status)}</Badge> },
      { key: 'author', header: 'From', width: '10rem', cell: (row) => <span className="text-xs muted">{row.created_by_name || row.created_by}</span> },
      { key: 'created', header: 'When', width: '9rem', cell: (row) => <span className="text-xs muted">{formatDate(row.created_at)}</span> },
    ],
    [],
  )

  return (
    <div>
      <div className="grid-responsive mb-4">
        <StatCard label="Feedback count" value={summary.data ? summary.data.count.toLocaleString() : '—'} loading={summary.loading} />
        <StatCard label="Average rating" value={summary.data && summary.data.average_rating !== null ? `${summary.data.average_rating.toLocaleString()} / 5` : <span className="text-lg">no ratings</span>} loading={summary.loading} hint={summary.data && summary.data.average_rating === null ? 'No feedback with a rating yet.' : undefined} />
      </div>
      {summary.error ? <div className="mb-4"><ErrorState error={summary.error} onRetry={() => void summary.refetch()} /></div> : null}

      <FilterBar>
        <Field label="Triage status" htmlFor="fb-status"><Select id="fb-status" options={[{ value: '', label: 'Any status' }, ...toOptions(FEEDBACK_STATUSES)]} value={status} onChange={setStatus} ariaLabel="Filter by status" /></Field>
        <div className="min-w-[10rem]">
          <Field label="Minimum rating (this page)" htmlFor="fb-rating" hint="The API has no rating filter, so this narrows the loaded page only.">
            <input id="fb-rating" className="input" type="number" min={1} max={5} step={1} value={ratingTerm} onChange={(event) => setRatingTerm(event.target.value)} />
          </Field>
        </div>
      </FilterBar>

      <Panel title="Feedback" description="Ratings and notes left by users. The list is scoped by the server: staff see all records, others see their own." dense actions={<RefreshButton onClick={() => void list.refetch()} busy={list.refreshing} />}>
        <DataTable
          caption="User feedback"
          columns={columns}
          rows={rows}
          rowKey={(row) => row.id}
          loading={list.loading}
          onRowClick={(row) => setSelected(row)}
          empty={list.loading ? 'Loading feedback…' : 'No feedback matches this filter.'}
        />
        <Pagination meta={list.data ?? undefined} onPage={setPage} label="records" />
      </Panel>

      {selected ? (
        <FeedbackDetailModal
          record={selected}
          canManage={canManage}
          onClose={() => setSelected(null)}
          onRefreshed={() => list.refetch()}
        />
      ) : null}
    </div>
  )
}

function FeedbackDetailModal({
  record,
  canManage,
  onClose,
  onRefreshed,
}: {
  record: FeedbackRecord
  canManage: boolean
  onClose: () => void
  onRefreshed: () => Promise<void>
}) {
  const [current, setCurrent] = useState<FeedbackRecord>(record)
  const [status, setStatus] = useState<FeedbackRecord['status']>(record.status)
  const [reply, setReply] = useState(record.administrator_reply ?? '')
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setCurrent(record)
    setStatus(record.status)
    setReply(record.administrator_reply ?? '')
    setError(null)
    setFeedback(null)
  }, [record])

  const save = useCallback(async () => {
    setBusy(true)
    setError(null)
    setFeedback(null)
    try {
      const updated = unwrapOne<FeedbackRecord>(await api.feedback.update(current.id, { status, reply: reply.trim() }))
      setCurrent(updated)
      setFeedback({ tone: 'success', text: 'Triage saved. Re-read from the server.' })
      await onRefreshed()
    } catch (cause) {
      const described = describeError(cause)
      setError(`${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`)
    } finally {
      setBusy(false)
    }
  }, [current.id, onRefreshed, reply, status])

  const dirty = status !== current.status || reply.trim() !== (current.administrator_reply ?? '').trim()

  return (
    <Modal open onClose={onClose} title="Feedback detail" width="max-w-2xl" description={<span>#{current.id} · {formatDate(current.created_at, true)}</span>}>
      <div className="space-y-5">
        <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
        {error ? <div role="alert" className="rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm text-alert-critical">{error}</div> : null}

        <KeyValue
          columns={2}
          items={[
            { label: 'Rating', value: <span className="mono">{current.rating}/5</span> },
            { label: 'Area', value: humanise(current.category) },
            { label: 'From', value: current.created_by_name || current.created_by },
            { label: 'Page context', value: current.page_context || '—' },
            { label: 'Status', value: <Badge tone={FEEDBACK_STATUS_TONE[current.status] ?? 'neutral'}>{humanise(current.status)}</Badge> },
            { label: 'Replied', value: current.replied_at ? `${formatDate(current.replied_at, true)}${current.replied_by ? ` by ${current.replied_by}` : ''}` : 'not yet' },
          ]}
        />

        <Field label="Message" htmlFor={`fb-${current.id}`}>
          <div id={`fb-${current.id}`} className="whitespace-pre-wrap rounded-lg border p-3 text-sm leading-relaxed muted" style={{ borderColor: 'var(--border)' }}>
            {current.message}
          </div>
        </Field>

        {canManage ? (
          <div className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
            <p className="panel-title mb-2">Triage (administrator)</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Status" htmlFor="fb-set-status" hint="The API stores new / reviewed / actioned.">
                <Select id="fb-set-status" options={toOptions(FEEDBACK_STATUSES)} value={status} onChange={(next) => setStatus(next as FeedbackRecord['status'])} ariaLabel="Set feedback status" disabled={busy} />
              </Field>
              <div className="flex items-end text-xs subtle">
                <span>“Planned” and “addressed” are not supported by this endpoint; choose the closest of the three available statuses.</span>
              </div>
            </div>
            <div className="mt-3">
              <Field label="Response to the user" htmlFor="fb-set-reply" required hint="A short response is stored and shown to the user.">
                <textarea id="fb-set-reply" className="textarea" rows={3} value={reply} onChange={(event) => setReply(event.target.value)} disabled={busy} placeholder="Optional reply text" />
              </Field>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button type="button" className="btn btn-secondary btn-sm" disabled={busy || !dirty} onClick={() => { setStatus(current.status); setReply(current.administrator_reply ?? ''); setError(null) }}>Reset</button>
              <button type="button" className="btn btn-primary btn-sm" disabled={busy || !dirty || reply.trim().length === 0} onClick={() => void save()}>Save triage</button>
            </div>
          </div>
        ) : (
          <Hint>Feedback triage requires the <Mono>feedback:manage</Mono> capability (administrator). It is read-only for your account.</Hint>
        )}

        <div className="text-xs subtle">Record id <Mono>{current.id}</Mono></div>
      </div>
    </Modal>
  )
}
