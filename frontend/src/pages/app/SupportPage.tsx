import { useEffect, useMemo, useState } from 'react'
import { MessageSquarePlus, Send, Star, Ticket as TicketIcon } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import {
  Badge,
  Column,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  Hint,
  Modal,
  Pagination,
  Panel,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
  Tabs,
  type TabItem,
} from '../../components/ui'
import { formatDate, humanise, relativeTime } from '../../lib/format'
import type { FeedbackRecord, SupportTicket } from '../../types/api'

export default function SupportPage() {
  const tabs: TabItem[] = [
    { id: 'tickets', label: 'Support tickets', content: <TicketsTab /> },
    { id: 'feedback', label: 'Feedback', content: <FeedbackTab /> },
  ]

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Help &amp; feedback</p>
        <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Support</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
          Raise tickets, follow their status, and rate the platform. Statuses come straight from the record. A ticket or piece of feedback is marked
          resolved only once it has been recorded that way.
        </p>
      </header>
      <Tabs items={tabs} initial={0} />
    </div>
  )
}

// tickets

function TicketsTab() {
  const { can, hasRole, account } = useAuth()
  const isAdmin = hasRole('administrator')
  const canManage = can('support:manage') || isAdmin

  const [page, setPage] = useState(1)
  const list = useAsync(() => api.support.list({ mine: 'true', page, page_size: 12 }), [page])
  const categories = useAsync(() => api.support.categories(), [])
  const summary = useAsync(() => api.support.summary(), [], { enabled: canManage })

  const [selectedId, setSelectedId] = useState<string | null>(null)

  const columns: Column<SupportTicket>[] = useMemo(
    () => [
      {
        key: 'subject',
        header: 'Ticket',
        cell: (ticket) => (
          <div className="min-w-0">
            <button type="button" className="link truncate text-left text-sm font-medium" onClick={() => setSelectedId(ticket.id)}>
              {ticket.subject}
            </button>
            <p className="text-xs subtle">{ticket.created_by_name ?? humanise(ticket.category)} · <Badge tone="neutral">{humanise(ticket.category)}</Badge></p>
          </div>
        ),
      },
      { key: 'priority', header: 'Priority', cell: (ticket) => <PriorityBadge priority={ticket.priority} /> },
      { key: 'status', header: 'Status', cell: (ticket) => <StatusBadge value={ticket.status} kind="generic" /> },
      { key: 'updated', header: 'Updated', cell: (ticket) => <span className="text-xs" title={ticket.updated_at ?? ticket.created_at}>{relativeTime(ticket.updated_at ?? ticket.created_at)}</span> },
      { key: 'open', header: '', align: 'right', cell: (ticket) => <button type="button" className="btn btn-ghost btn-sm" onClick={() => setSelectedId(ticket.id)}><MessageSquarePlus className="h-3.5 w-3.5" /> Open</button> },
    ],
    [],
  )

  return (
    <div className="space-y-6">
      {canManage && summary.data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Open" value={summary.data.open} tone="hydro" />
          <StatCard label="In progress" value={summary.data.in_progress} tone="solar" />
          <StatCard label="Resolved" value={summary.data.resolved} tone="forest" />
          <StatCard label="Unassigned" value={summary.data.unassigned} tone={summary.data.unassigned > 0 ? 'alert' : 'default'} hint={summary.data.median_first_response_hours != null ? `Median first response ${summary.data.median_first_response_hours.toFixed(1)}h` : undefined} />
        </div>
      ) : null}

      <NewTicketForm categories={categories.data ?? undefined} onCreated={(ticket) => { setSelectedId(ticket.id); void list.refetch() }} />

      <Panel title="Your tickets" description="Only tickets you opened are listed here. Statuses come from the server.">
        {list.loading && !list.data ? <Skeleton className="h-40 w-full" /> : null}
        {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
        {!list.loading && (list.data?.items.length ?? 0) === 0 ? (
          <EmptyState title="No tickets yet" description="Use the form above to open a support ticket. It appears here with its status and full comment thread." />
        ) : null}
        <DataTable columns={columns} rows={list.data?.items ?? []} rowKey={(ticket) => ticket.id} empty="No records" />
        <Pagination meta={list.data ?? undefined} onPage={setPage} label="tickets" />
      </Panel>

      {selectedId ? (
        <TicketDetail
          id={selectedId}
          currentUserId={account?.id ?? null}
          canManage={canManage}
          onClose={() => setSelectedId(null)}
          onChanged={() => void list.refetch()}
        />
      ) : null}

      {categories.error ? <ErrorState error={categories.error} onRetry={() => void categories.refetch()} /> : null}
    </div>
  )
}

function PriorityBadge({ priority }: { priority: SupportTicket['priority'] }) {
  const tone = priority === 'urgent' ? 'danger' : priority === 'high' ? 'warning' : priority === 'medium' ? 'info' : 'neutral'
  return <Badge tone={tone}>{humanise(priority)}</Badge>
}

function NewTicketForm({ categories, onCreated }: { categories: { categories: string[]; priorities: string[]; statuses: string[] } | undefined; onCreated: (ticket: SupportTicket) => void }) {
  const [subject, setSubject] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState('')
  const [priority, setPriority] = useState('medium')
  const [pageContext, setPageContext] = useState(typeof window !== 'undefined' ? window.location.pathname : '/app/support')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [done, setDone] = useState<string | null>(null)

  useEffect(() => {
    if (!category && categories?.categories?.length) setCategory(categories.categories[0])
    if (priority && categories?.priorities?.length && !categories.priorities.includes(priority)) setPriority(categories.priorities.includes('medium') ? 'medium' : categories.priorities[0])
  }, [categories, category, priority])

  const categoryOptions = useMemo(() => (categories?.categories ?? []).map((value) => ({ value, label: humanise(value) })), [categories])
  const priorityOptions = useMemo(() => (categories?.priorities ?? []).map((value) => ({ value, label: humanise(value) })), [categories])

  async function submit() {
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const ticket = await api.support.create({ subject: subject.trim(), description: description.trim(), category, priority, page_context: pageContext.trim() || undefined })
      onCreated(ticket)
      setSubject('')
      setDescription('')
      setDone(`Ticket "${ticket.subject}" opened with status ${humanise(ticket.status)}.`)
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Open a support ticket" description="Provide a clear subject and description. File attachments aren’t offered here; a ticket carries text only.">
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Category" htmlFor="tk-category">
            <Select id="tk-category" options={categoryOptions} value={category} onChange={setCategory} placeholder="Loading categories…" />
          </Field>
          <Field label="Priority" htmlFor="tk-priority">
            <Select id="tk-priority" options={priorityOptions} value={priority} onChange={setPriority} />
          </Field>
        </div>
        <Field label="Subject" htmlFor="tk-subject" required>
          <input id="tk-subject" className="input" value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="Short summary of the issue" />
        </Field>
        <Field label="Description" htmlFor="tk-desc" required>
          <textarea id="tk-desc" className="input min-h-[96px]" rows={4} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What happened, what you expected, and steps to reproduce" />
        </Field>
        <Field label="Page context" htmlFor="tk-page" hint="The screen where the issue occurred, sent with the ticket so support can locate it.">
          <input id="tk-page" className="input mono text-xs" value={pageContext} onChange={(event) => setPageContext(event.target.value)} />
        </Field>
        <div className="flex items-center gap-3">
          <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy || !subject.trim() || !description.trim() || !category}>
            <TicketIcon className="h-4 w-4" /> {busy ? 'Opening…' : 'Submit ticket'}
          </button>
          {done ? <p className="text-xs text-forest-600 dark:text-forest-300" role="status">{done}</p> : null}
        </div>
        {error ? <ErrorState error={error} onRetry={() => void submit()} /> : null}
      </div>
    </Panel>
  )
}

function TicketDetail({ id, currentUserId, canManage, onClose, onChanged }: { id: string; currentUserId: string | null; canManage: boolean; onClose: () => void; onChanged: () => void }) {
  const detail = useAsync(() => api.support.get(id), [id])
  const [body, setBody] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<ApiRequestError | null>(null)
  const [resolving, setResolving] = useState(false)
  const [resolution, setResolution] = useState('')

  const ticket = detail.data

  async function addComment() {
    if (!body.trim()) return
    setBusy(true)
    setActionError(null)
    try {
      await api.support.message(id, body.trim())
      setBody('')
      void detail.refetch()
      onChanged()
    } catch (cause) {
      setActionError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  async function close() {
    setBusy(true)
    setActionError(null)
    try {
      await api.support.close(id, resolution.trim())
      setResolving(false)
      setResolution('')
      void detail.refetch()
      onChanged()
    } catch (cause) {
      setActionError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  async function reopen() {
    setBusy(true)
    setActionError(null)
    try {
      await api.support.update(id, { status: 'open' })
      void detail.refetch()
      onChanged()
    } catch (cause) {
      setActionError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  const isOwner = Boolean(currentUserId && ticket?.created_by === currentUserId)
  const canAct = canManage || isOwner
  const isOpenish = ticket ? ticket.status === 'open' || ticket.status === 'in_progress' : false
  const isClosedish = ticket ? ticket.status === 'resolved' || ticket.status === 'closed' : false

  return (
    <Modal open onClose={onClose} title={ticket?.subject ?? 'Ticket'} width="max-w-2xl" description={ticket ? `Category ${humanise(ticket.category)} · opened ${relativeTime(ticket.created_at)}` : undefined}>
      {detail.loading && !ticket ? <Skeleton className="h-40 w-full" /> : null}
      {detail.error ? <ErrorState error={detail.error} onRetry={() => void detail.refetch()} /> : null}
      {ticket ? (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge value={ticket.status} kind="generic" />
            <PriorityBadge priority={ticket.priority} />
            {ticket.assigned_to_name ? <Badge tone="neutral">Assigned: {ticket.assigned_to_name}</Badge> : <Badge tone="neutral">Unassigned</Badge>}
          </div>

          <div>
            <p className="panel-title mb-1">Description</p>
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{ticket.description}</p>
          </div>

          <Timeline ticket={ticket} />

          {ticket.resolution_note ? (
            <div className="rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
              <p className="panel-title mb-1">Resolution</p>
              <p className="whitespace-pre-wrap muted">{ticket.resolution_note}</p>
            </div>
          ) : null}

          <div>
            <p className="panel-title mb-2">Conversation</p>
            <div className="space-y-3">
              {ticket.comments.length === 0 ? <p className="text-sm subtle">No messages yet.</p> : null}
              {ticket.comments.map((comment, index) => (
                <div key={`${comment.created_at}-${index}`} className="rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
                  <div className="mb-1 flex items-center justify-between gap-2 text-xs subtle">
                    <span className="font-medium text-[var(--text)]">{comment.author_name ?? 'Participant'} <Badge tone={comment.author_role === 'administrator' ? 'warning' : 'neutral'}>{humanise(comment.author_role)}</Badge></span>
                    <span title={comment.created_at}>{relativeTime(comment.created_at)}</span>
                  </div>
                  <p className="whitespace-pre-wrap muted">{comment.body}</p>
                </div>
              ))}
            </div>
          </div>

          {isOpenish && canAct ? (
            <div className="space-y-3">
              <Field label="Add a message" htmlFor="tk-msg">
                <textarea id="tk-msg" className="input min-h-[72px]" rows={3} value={body} onChange={(event) => setBody(event.target.value)} placeholder="Reply to the support team" />
              </Field>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="btn btn-primary btn-sm" onClick={() => void addComment()} disabled={busy || !body.trim()}>
                  <Send className="h-3.5 w-3.5" /> Send message
                </button>
                {ticket.status !== 'closed' ? (
                  <button type="button" className="btn btn-secondary btn-sm" onClick={() => setResolving(true)} disabled={busy}>Resolve / close</button>
                ) : null}
              </div>
            </div>
          ) : null}

          {isClosedish && canAct ? (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => void reopen()} disabled={busy}>
              Reopen ticket
            </button>
          ) : null}

          {!canAct ? <Hint>Only the ticket owner and support staff can message, resolve or reopen this ticket.</Hint> : null}
          {actionError ? <ErrorState error={actionError} /> : null}

          <Modal open={resolving} title="Resolve ticket" onClose={() => setResolving(false)} description="Record how the issue was resolved. The ticket status only changes to resolved once saved here.">
            <div className="space-y-4">
              <Field label="Resolution note" htmlFor="tk-res" required>
                <textarea id="tk-res" className="input min-h-[80px]" rows={3} value={resolution} onChange={(event) => setResolution(event.target.value)} placeholder="What was done to resolve the issue" />
              </Field>
              <div className="flex justify-end gap-2">
                <button type="button" className="btn btn-secondary" onClick={() => setResolving(false)} disabled={busy}>Cancel</button>
                <button type="button" className="btn btn-primary" onClick={() => void close()} disabled={busy || !resolution.trim()}>Confirm resolution</button>
              </div>
            </div>
          </Modal>
        </div>
      ) : null}
    </Modal>
  )
}

function Timeline({ ticket }: { ticket: SupportTicket }) {
  const events = [
    { at: ticket.created_at, label: 'Ticket created' },
    ticket.first_response_at ? { at: ticket.first_response_at, label: 'First response' } : null,
    ...ticket.comments.map((comment) => ({ at: comment.created_at, label: `Message from ${comment.author_name ?? 'participant'}` })),
    ticket.resolved_at ? { at: ticket.resolved_at, label: 'Resolved' } : null,
  ].filter(Boolean) as { at: string; label: string }[]
  const sorted = [...events].sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())

  return (
    <div>
      <p className="panel-title mb-2">Status timeline</p>
      <ol className="space-y-2 border-l pl-4 text-xs" style={{ borderColor: 'var(--border)' }}>
        {sorted.map((event, index) => (
          <li key={`${event.at}-${index}`} className="relative">
            <span className="absolute -left-[1.28rem] top-1 h-2 w-2 rounded-full bg-clay-500" aria-hidden="true" />
            <p className="font-medium">{event.label}</p>
            <p className="subtle" title={event.at}>{formatDate(event.at, true)} · {relativeTime(event.at)}</p>
          </li>
        ))}
        <li className="relative">
          <span className="absolute -left-[1.28rem] top-1 h-2 w-2 rounded-full bg-[var(--border-strong)]" aria-hidden="true" />
          <p className="muted">Current status: {humanise(ticket.status)}</p>
        </li>
      </ol>
    </div>
  )
}

// feedback

function FeedbackTab() {
  const { can, hasRole } = useAuth()
  const isAdmin = hasRole('administrator')
  const canSeeSummary = can('feedback:manage') || isAdmin

  const [page, setPage] = useState(1)
  const list = useAsync(() => api.feedback.list({ page, page_size: 10 }), [page])
  const summary = useAsync(() => api.feedback.summary(), [], { enabled: canSeeSummary })
  const categories = useAsync(() => api.support.categories(), [])

  const [rating, setRating] = useState(0)
  const [category, setCategory] = useState('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const [done, setDone] = useState<string | null>(null)

  useEffect(() => {
    if (!category && categories.data?.categories.length) setCategory(categories.data.categories[0])
  }, [categories.data, category])

  const categoryOptions = useMemo(
    () => (categories.data?.categories ?? []).map((value) => ({ value, label: categories.data?.category_labels[value] ?? humanise(value) })),
    [categories.data],
  )

  async function submit() {
    if (rating < 1 || !comment.trim() || !category) return
    setBusy(true)
    setError(null)
    setDone(null)
    try {
      const record = await api.feedback.create({ rating, message: comment.trim(), category })
      setComment('')
      setRating(0)
      void list.refetch()
      setDone(`Feedback submitted (status ${humanise(record.status)}). The team reviews it.`)
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      {canSeeSummary && summary.data ? (
        <Panel title="Aggregate feedback" description="Visible to permitted roles only. Figures are the server's totals.">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Average rating" value={summary.data.average_rating != null ? summary.data.average_rating.toFixed(1) : '—'} unit="of 5" tone="solar" />
            <StatCard label="Submissions" value={summary.data.count} tone="hydro" />
            <StatCard label="New" value={summary.data.by_status.new ?? 0} tone="default" />
            <StatCard label="Actioned" value={summary.data.by_status.actioned ?? 0} tone="forest" />
          </div>
          {Object.keys(summary.data.by_category).length ? (
            <div className="mt-4 flex flex-wrap gap-1.5">
              {Object.entries(summary.data.by_category).map(([key, value]) => <Badge key={key} tone="neutral">{humanise(key)}: {value}</Badge>)}
            </div>
          ) : null}
        </Panel>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Rate the platform" description="Share a rating (1–5), a category, and a comment. Your submission keeps its status until an administrator acts on it.">
          <div className="space-y-4">
            <div>
              <span className="label">Rating</span>
              <div className="flex items-center gap-1">
                {[1, 2, 3, 4, 5].map((value) => (
                  <button key={value} type="button" onClick={() => setRating(value)} aria-label={`${value} star${value > 1 ? 's' : ''}`} aria-pressed={rating === value} className="btn btn-ghost btn-icon">
                    <Star className={`h-5 w-5 ${value <= rating ? 'fill-solar-400 text-solar-500' : 'text-[var(--border-strong)]'}`} aria-hidden="true" />
                  </button>
                ))}
              </div>
            </div>
            <Field label="Category" htmlFor="fb-category">
              <Select id="fb-category" options={categoryOptions} value={category} onChange={setCategory} placeholder="Loading categories…" />
            </Field>
            <Field label="Comment" htmlFor="fb-comment" required>
              <textarea id="fb-comment" className="input min-h-[96px]" rows={4} value={comment} onChange={(event) => setComment(event.target.value)} placeholder="What is working well or what should change?" />
            </Field>
            <div className="flex items-center gap-3">
              <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy || rating < 1 || !category || !comment.trim()}>
                <Send className="h-4 w-4" /> {busy ? 'Sending…' : 'Submit feedback'}
              </button>
              {done ? <p className="text-xs text-forest-600 dark:text-forest-300" role="status">{done}</p> : null}
            </div>
            {error ? <ErrorState error={error} onRetry={() => void submit()} /> : null}
          </div>
        </Panel>

        <Panel title="Your feedback" description="Submissions visible to your account. Statuses are the server's own.">
          {!canSeeSummary ? <Hint>Aggregate statistics are only shown to permitted roles. You can still see and rate your own feedback below.</Hint> : null}
          {list.loading && !list.data ? <Skeleton className="h-40 w-full" /> : null}
          {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}
          {!list.loading && (list.data?.items.length ?? 0) === 0 ? (
            <EmptyState title="No feedback yet" description="Submit a rating above; it will appear here with the status the team records." />
          ) : null}
          <div className="space-y-3">
            {(list.data?.items ?? []).map((record) => <FeedbackRow key={record.id} record={record} />)}
          </div>
          <Pagination meta={list.data ?? undefined} onPage={setPage} label="feedback entries" />
        </Panel>
      </div>
    </div>
  )
}

function FeedbackRow({ record }: { record: FeedbackRecord }) {
  return (
    <div className="card-tight">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-1" aria-label={`Rating ${record.rating} out of 5`}>
            {[1, 2, 3, 4, 5].map((value) => (
              <Star key={value} className={`h-3.5 w-3.5 ${value <= record.rating ? 'fill-solar-400 text-solar-500' : 'text-[var(--border-strong)]'}`} aria-hidden="true" />
            ))}
            <span className="ml-1 text-xs subtle">{record.rating}/5</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-sm">{record.message}</p>
        </div>
        <StatusBadge value={record.status} kind="generic" />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs subtle">
        <Badge tone="neutral">{humanise(record.category)}</Badge>
        <span title={record.created_at}>{relativeTime(record.created_at)}</span>
        {record.administrator_reply ? <span className="mt-1 w-full">Reply: {record.administrator_reply}</span> : null}
      </div>
    </div>
  )
}
