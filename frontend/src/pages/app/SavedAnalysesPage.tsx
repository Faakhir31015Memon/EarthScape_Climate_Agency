import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Copy, ExternalLink, FolderOpen, Pencil, Trash2 } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import {
  Badge,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Field,
  Hint,
  Modal,
  Pagination,
  Skeleton,
} from '../../components/ui'
import { humanise, relativeTime } from '../../lib/format'
import type { SavedAnalysis } from '../../types/api'
import { QUERY, analyticsSearchParams, savedFiltersToParts } from './components/insight/query'

/** Derive an analysis "kind" label from what the backend actually stored (chart_config / filters). */
function analysisKind(saved: SavedAnalysis): string {
  const config = saved.chart_config as Record<string, unknown>
  const fromConfig = typeof config.chart === 'string' ? config.chart : typeof config.kind === 'string' ? config.kind : ''
  if (fromConfig) return fromConfig
  const filters = saved.filters as Record<string, unknown>
  if (typeof filters.correlation === 'boolean') return 'correlation'
  if (typeof filters.anomaly === 'boolean') return 'anomaly'
  return 'analysis'
}

function sharePath(saved: SavedAnalysis): string {
  const parts = savedFiltersToParts(saved.filters)
  const params = analyticsSearchParams({ ...parts, saved: saved.id })
  return `/app/analytics?${params.toString()}`
}

export default function SavedAnalysesPage() {
  const { account, hasRole } = useAuth()
  const navigate = useNavigate()
  const isAdmin = hasRole('administrator')

  const [page, setPage] = useState(1)
  const list = useAsync(() => api.analytics.listSaved({ page, page_size: 12 }), [page])

  const [renaming, setRenaming] = useState<SavedAnalysis | null>(null)
  const [deleting, setDeleting] = useState<SavedAnalysis | null>(null)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<ApiRequestError | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const items = useMemo(() => list.data?.items ?? [], [list.data])

  function canEdit(saved: SavedAnalysis): boolean {
    return isAdmin || Boolean(account && saved.owner_user_id === account.id)
  }

  function open(saved: SavedAnalysis) {
    navigate(sharePath(saved))
  }

  async function copyLink(saved: SavedAnalysis) {
    const url = `${window.location.origin}${sharePath(saved)}`
    try {
      await navigator.clipboard.writeText(url)
      setCopiedId(saved.id)
      window.setTimeout(() => setCopiedId((current) => (current === saved.id ? null : current)), 2500)
    } catch {
      setFormError(new ApiRequestError('Your browser blocked clipboard access. Copy the link manually from the address bar after opening the analysis.', 'clipboard_denied', 0))
    }
  }

  async function remove() {
    if (!deleting) return
    setBusy(true)
    setFormError(null)
    try {
      await api.analytics.deleteSaved(deleting.id)
      setDeleting(null)
      void list.refetch()
    } catch (error) {
      setFormError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Analytics workspace</p>
        <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Saved analyses</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
          Analyses you saved from the workbench. Opening one restores its filters on the Analytics page via the shared URL parameters
          (<span className="mono">{QUERY.metric}</span>, <span className="mono">{QUERY.start}</span>, <span className="mono">{QUERY.end}</span>,{' '}
          <span className="mono">{QUERY.regions}</span>, <span className="mono">{QUERY.locations}</span>, <span className="mono">{QUERY.frequency}</span>,{' '}
          <span className="mono">{QUERY.groupBy}</span>, <span className="mono">{QUERY.saved}</span>). Only the rows the server returns are shown.
        </p>
      </header>

      {formError ? <ErrorState error={formError} onRetry={() => setFormError(null)} /> : null}
      {list.error ? <ErrorState error={list.error} onRetry={() => void list.refetch()} /> : null}

      {list.loading && !list.data ? (
        <div className="grid-responsive">
          {[0, 1, 2, 3].map((index) => <Skeleton key={index} className="h-44 w-full" />)}
        </div>
      ) : null}

      {!list.loading && items.length === 0 ? (
        <EmptyState
          title="No saved analyses yet"
          description="Save a query from the Analytics workbench to pin its exact filters here. Saved analyses reopen with the same metric, date range, regions and locations, so you can share a precise view with teammates."
          action={<Link to="/app/analytics" className="btn btn-primary">Open the analytics workbench</Link>}
        />
      ) : null}

      {items.length > 0 ? (
        <div className="grid-responsive">
          {items.map((saved) => (
            <SavedCard
              key={saved.id}
              saved={saved}
              editable={canEdit(saved)}
              copied={copiedId === saved.id}
              onOpen={() => open(saved)}
              onRename={() => setRenaming(saved)}
              onCopy={() => void copyLink(saved)}
              onDelete={() => setDeleting(saved)}
            />
          ))}
        </div>
      ) : null}

      {items.length > 0 ? <Pagination meta={list.data ?? undefined} onPage={setPage} label="saved analyses" /> : null}

      <RenameModal saved={renaming} onClose={() => setRenaming(null)} onSaved={() => void list.refetch()} onError={setFormError} />

      <ConfirmDialog
        open={Boolean(deleting)}
        title="Delete saved analysis"
        tone="danger"
        confirmLabel="Delete"
        busy={busy}
        onClose={() => setDeleting(null)}
        onConfirm={() => void remove()}
        message={<p>Delete <strong>{deleting?.name}</strong>? The stored query is removed for everyone it was shared with. This cannot be undone.</p>}
      />

      {!isAdmin ? (
        <Hint>
          You can open, copy links for, rename and delete your own saved analyses. Administrator accounts can manage every saved analysis. The server
          enforces this independently and returns its own message if an action is not permitted.
        </Hint>
      ) : null}
    </div>
  )
}

function SavedCard({
  saved,
  editable,
  copied,
  onOpen,
  onRename,
  onCopy,
  onDelete,
}: {
  saved: SavedAnalysis
  editable: boolean
  copied: boolean
  onOpen: () => void
  onRename: () => void
  onCopy: () => void
  onDelete: () => void
}) {
  const parts = savedFiltersToParts(saved.filters)
  const chips = filterChips(parts, saved)
  return (
    <article className="card-tight flex flex-col gap-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold" title={saved.name}>{saved.name}</h2>
          <p className="mt-0.5 text-xs subtle">{saved.description || 'No description'}</p>
        </div>
        <Badge tone="info">{humanise(analysisKind(saved))}</Badge>
      </div>

      <div className="flex flex-wrap gap-1">
        {chips.length ? chips.map((chip) => <Badge key={chip} tone="neutral">{chip}</Badge>) : <span className="text-xs subtle">All data</span>}
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <div className="min-w-0">
          <dt className="subtle">Owner (account id)</dt>
          <dd className="truncate" title={saved.owner_user_id}>{saved.owner_user_id || '—'}</dd>
        </div>
        <div>
          <dt className="subtle">Created</dt>
          <dd title={saved.created_at}>{relativeTime(saved.created_at)}</dd>
        </div>
      </dl>

      <div className="mt-auto flex flex-wrap items-center gap-2">
        <button type="button" className="btn btn-primary btn-sm" onClick={onOpen}>
          <FolderOpen className="h-3.5 w-3.5" /> Open
        </button>
        <Link to={sharePath(saved)} className="btn btn-ghost btn-sm" title="Open the shareable link in a new tab" target="_blank" rel="noreferrer">
          <ExternalLink className="h-3.5 w-3.5" /> New tab
        </Link>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCopy} aria-label="Copy share link">
          <Copy className="h-3.5 w-3.5" /> {copied ? 'Copied' : 'Copy link'}
        </button>
        {editable ? (
          <button type="button" className="btn btn-ghost btn-icon ml-auto" onClick={onRename} aria-label="Rename analysis" title="Rename">
            <Pencil className="h-4 w-4" />
          </button>
        ) : null}
        {editable ? (
          <button type="button" className="btn btn-ghost btn-icon" onClick={onDelete} aria-label="Delete analysis" title="Delete">
            <Trash2 className="h-4 w-4 text-alert-high" />
          </button>
        ) : null}
      </div>
      {!saved.is_shared ? <p className="text-[11px] subtle">Private to its owner — share link only works for accounts the server allows to view it.</p> : null}
    </article>
  )
}

function filterChips(parts: ReturnType<typeof savedFiltersToParts>, saved: SavedAnalysis): string[] {
  const chips: string[] = []
  const regions = parts.regions ?? []
  const locations = parts.locations ?? []
  if (parts.metric) chips.push(humanise(parts.metric))
  if (regions.length) chips.push(`Regions: ${regions.slice(0, 2).join(', ')}${regions.length > 2 ? ` +${regions.length - 2}` : ''}`)
  if (locations.length) chips.push(`Stations: ${locations.length}`)
  if (parts.start || parts.end) chips.push(`${parts.start ?? '…'} → ${parts.end ?? '…'}`)
  if (parts.frequency) chips.push(`By ${humanise(parts.frequency)}`)
  if (parts.groupBy) chips.push(`Grouped: ${humanise(parts.groupBy)}`)
  if (!chips.length && saved.chart_config && Object.keys(saved.chart_config).length) chips.push('Custom chart')
  return chips
}

function RenameModal({
  saved,
  onClose,
  onSaved,
  onError,
}: {
  saved: SavedAnalysis | null
  onClose: () => void
  onSaved: () => void
  onError: (error: ApiRequestError) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Sync local fields when a different saved analysis is opened for editing.
  if (saved && saved.id !== activeId) {
    setActiveId(saved.id)
    setName(saved.name)
    setDescription(saved.description ?? '')
  }

  async function save() {
    if (!saved) return
    setBusy(true)
    try {
      await api.analytics.updateSaved(saved.id, { name: name.trim(), description: description.trim() })
      onSaved()
      onClose()
    } catch (error) {
      onError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={Boolean(saved)}
      title="Rename saved analysis"
      description="Change the label and description only. The stored query and filters are untouched."
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={busy || !name.trim()}>Save</button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name" htmlFor="sa-name" required>
          <input id="sa-name" className="input" value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        <Field label="Description" htmlFor="sa-desc">
          <textarea id="sa-desc" className="input min-h-[72px]" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What this analysis answers" />
        </Field>
      </div>
    </Modal>
  )
}
