import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, ShieldCheck } from 'lucide-react'
import api from '../../services/api'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import {
  Badge,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Modal,
  Mono,
  PageHeader,
  Pagination,
  Panel,
  KeyValue,
  RefreshButton,
  SearchInput,
  Select,
  type Column,
  type Option,
} from '../../components/ui'
import { PAGE_SIZE } from './components/adminUtils'
import { formatDate } from '../../lib/format'
import type { AuditLogEntry } from '../../types/api'

const OUTCOME_OPTIONS: Option[] = [
  { value: '', label: 'Any outcome' },
  { value: 'success', label: 'Success' },
  { value: 'failure', label: 'Failure' },
  { value: 'denied', label: 'Denied' },
]

const OUTCOME_TONE: Record<AuditLogEntry['outcome'], 'success' | 'warning' | 'danger'> = {
  success: 'success',
  failure: 'warning',
  denied: 'danger',
}

/**
 * Audit log.
 *
 * Reads `GET /api/v1/system/audit` (paginated). The backend filters only on
 * `action`, `outcome` and `actor_role`; the typed client additionally exposes
 * `user_id` / `resource_type`, which the view ignores. To avoid offering a
 * server filter that silently does nothing, this page sends just `action` and
 * `outcome` upstream and applies actor / resource / date-range narrowing to the
 * loaded page, labelled as such. There is no audit write endpoint, so every
 * record is shown read-only.
 */
export default function AuditLogPage() {
  const [action, setAction] = useState('')
  const debouncedAction = useDebouncedValue(action, 350)
  const [outcome, setOutcome] = useState('')
  const [page, setPage] = useState(1)

  const [actorTerm, setActorTerm] = useState('')
  const [resourceType, setResourceType] = useState('')
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')

  const [selected, setSelected] = useState<AuditLogEntry | null>(null)

  const logs = useAsync(
    () =>
      api.system.audit({
        page,
        page_size: PAGE_SIZE,
        action: debouncedAction.trim() || undefined,
        outcome: outcome || undefined,
      }),
    [page, debouncedAction, outcome],
  )

  useEffect(() => setPage(1), [debouncedAction, outcome])

  const rows = logs.data?.items ?? []

  // Page-local narrowing: the server does not support these params, so they are
  // applied to whatever page is loaded and the count reflects that honestly.
  const filtered = useMemo(() => {
    const needle = actorTerm.trim().toLowerCase()
    const resource = resourceType.trim().toLowerCase()
    const from = fromDate ? new Date(`${fromDate}T00:00:00`).getTime() : null
    const to = toDate ? new Date(`${toDate}T23:59:59`).getTime() : null
    return rows.filter((entry) => {
      if (needle) {
        const haystack = `${entry.actor_name ?? ''} ${entry.actor_user_id ?? ''} ${entry.actor_role}`.toLowerCase()
        if (!haystack.includes(needle)) return false
      }
      if (resource && !entry.resource_type.toLowerCase().includes(resource)) return false
      const at = entry.created_at ? new Date(entry.created_at).getTime() : NaN
      if (from !== null && !Number.isNaN(at) && at < from) return false
      if (to !== null && !Number.isNaN(at) && at > to) return false
      return true
    })
  }, [rows, actorTerm, resourceType, fromDate, toDate])

  const localFilterActive = Boolean(actorTerm.trim() || resourceType.trim() || fromDate || toDate)

  const columns = useMemo<Column<AuditLogEntry>[]>(
    () => [
      {
        key: 'created_at',
        header: 'When',
        width: '13rem',
        cell: (row) => <span className="text-xs muted">{formatDate(row.created_at, true)}</span>,
      },
      {
        key: 'actor',
        header: 'Actor',
        cell: (row) => (
          <div className="min-w-0">
            <p className="truncate text-sm">{row.actor_name || row.actor_user_id || 'System'}</p>
            <p className="truncate text-[11px] subtle">
              <Badge tone={row.actor_role === 'administrator' ? 'success' : 'neutral'}>{row.actor_role}</Badge>
            </p>
          </div>
        ),
      },
      {
        key: 'action',
        header: 'Action',
        cell: (row) => <Mono>{row.action}</Mono>,
      },
      {
        key: 'resource',
        header: 'Resource',
        cell: (row) => (
          <div className="min-w-0">
            <p className="truncate text-sm">{row.resource_type || '—'}</p>
            {row.resource_id ? <p className="truncate text-[11px] subtle mono">{row.resource_id}</p> : null}
          </div>
        ),
      },
      {
        key: 'outcome',
        header: 'Outcome',
        width: '7rem',
        cell: (row) => <Badge tone={OUTCOME_TONE[row.outcome]}>{row.outcome}</Badge>,
      },
      {
        key: 'request',
        header: 'Request',
        cell: (row) => (row.request_id ? <span className="mono text-[11px]">{row.request_id}</span> : <span className="text-xs subtle">none</span>),
      },
      {
        key: 'detail',
        header: '',
        align: 'right',
        width: '3rem',
        cell: () => (
          <span className="inline-flex items-center gap-1 text-xs subtle">
            Detail <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          </span>
        ),
      },
    ],
    [],
  )

  const openDetail = useCallback((row: AuditLogEntry) => setSelected(row), [])

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Audit log"
        description="Security-relevant actions recorded by the backend. The log is append-only — the API exposes no update or delete route, so nothing here can be edited from the UI."
        meta={
          logs.data ? (
            <>
              <Badge tone="neutral">{logs.data.total.toLocaleString()} entr{logs.data.total === 1 ? 'y' : 'ies'}</Badge>
              <span className="subtle">page {logs.data.page} of {Math.max(logs.data.pages, 1)}</span>
              <span className="subtle">read-only</span>
            </>
          ) : null
        }
        actions={<RefreshButton onClick={() => void logs.refetch()} busy={logs.refreshing} />}
      />

      <div className="mb-4 flex items-start gap-2 rounded-xl border px-3 py-2 text-xs leading-relaxed muted" style={{ borderColor: 'var(--border)' }}>
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
        <p>
          Denied outcomes are highlighted in red. Metadata is shown exactly as the audit layer stored it —
          values the server already redacted (for example <span className="mono">[redacted]</span>) stay
          redacted here rather than being reconstructed.
        </p>
      </div>

      {logs.error ? (
        <div className="mb-4">
          <ErrorState error={logs.error} onRetry={() => void logs.refetch()} />
        </div>
      ) : null}

      <FilterBar>
        <div className="min-w-[13rem] flex-1">
          <Field label="Action (server, exact match)" htmlFor="audit-action">
            <input
              id="audit-action"
              className="input"
              type="search"
              value={action}
              onChange={(event) => setAction(event.target.value)}
              placeholder="e.g. support_ticket.updated"
            />
          </Field>
        </div>
        <Field label="Outcome (server)" htmlFor="audit-outcome">
          <Select id="audit-outcome" options={OUTCOME_OPTIONS} value={outcome} onChange={setOutcome} ariaLabel="Filter by outcome" />
        </Field>
        <div className="min-w-[12rem] flex-1">
          <SearchInput value={actorTerm} onChange={setActorTerm} label="Filter actor (this page)" placeholder="Actor on this page…" />
        </div>
        <Field label="Resource type (this page)" htmlFor="audit-resource">
          <input id="audit-resource" className="input" type="search" value={resourceType} onChange={(event) => setResourceType(event.target.value)} placeholder="e.g. dataset" />
        </Field>
        <Field label="From (this page)" htmlFor="audit-from">
          <input id="audit-from" className="input" type="date" value={fromDate} onChange={(event) => setFromDate(event.target.value)} />
        </Field>
        <Field label="To (this page)" htmlFor="audit-to">
          <input id="audit-to" className="input" type="date" value={toDate} onChange={(event) => setToDate(event.target.value)} />
        </Field>
      </FilterBar>

      <Panel
        title="Recorded events"
        description={
          localFilterActive
            ? 'Actor, resource and date filters run on the loaded page only; the API has no matching query parameter, so the total count still reflects the server-side action/outcome filters.'
            : 'Newest first, straight from the audit collection.'
        }
        dense
      >
        <DataTable
          caption="Audit log entries"
          columns={columns}
          rows={filtered}
          rowKey={(row) => row.id}
          loading={logs.loading}
          onRowClick={openDetail}
          empty={logs.loading ? 'Loading…' : 'No entries match the server filters (and any page-local filters).'}
        />
        <Pagination meta={logs.data ?? undefined} onPage={setPage} label="entries" />
      </Panel>

      {selected ? <AuditDetailModal entry={selected} onClose={() => setSelected(null)} /> : null}
    </div>
  )
}

/** Renders a value exactly as the audit layer stored it — never re-derived. */
function formatStoredValue(value: unknown): string {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

function AuditDetailModal({ entry, onClose }: { entry: AuditLogEntry; onClose: () => void }) {
  const metadataEntries = Object.entries(entry.metadata ?? {})
  const items = [
    { label: 'Action', value: <Mono>{entry.action}</Mono> },
    { label: 'Outcome', value: <Badge tone={OUTCOME_TONE[entry.outcome]}>{entry.outcome}</Badge> },
    { label: 'Actor', value: `${entry.actor_name || entry.actor_user_id || 'System'} · ${entry.actor_role}` },
    { label: 'Resource', value: entry.resource_id ? <span>{entry.resource_type} <Mono>{entry.resource_id}</Mono></span> : entry.resource_type || '—' },
    { label: 'Timestamp', value: formatDate(entry.created_at, true) },
    { label: 'Request id', value: entry.request_id ? <Mono>{entry.request_id}</Mono> : 'not recorded' },
    { label: 'Source IP', value: entry.source_ip ? <Mono>{entry.source_ip}</Mono> : 'not recorded' },
  ]

  return (
    <Modal open title="Audit entry" description="Append-only record; values shown as stored." onClose={onClose} width="max-w-2xl">
      <div className="space-y-5">
        <KeyValue items={items} columns={2} />

        <div>
          <p className="panel-title mb-2">Metadata</p>
          {metadataEntries.length === 0 ? (
            <p className="text-xs subtle">No metadata recorded for this event.</p>
          ) : (
            <KeyValue
              items={metadataEntries.map(([key, value]) => ({
                label: key,
                value: <span className="break-all mono text-[12px]">{formatStoredValue(value)}</span>,
              }))}
              columns={1}
            />
          )}
          {metadataEntries.some(([, value]) => typeof value === 'string' && value.startsWith('[redacted')) ? (
            <p className="mt-2 text-xs subtle">
              Some values were redacted by the audit layer before storage. They are displayed as stored; the
              original content is not available to this page and is not guessed.
            </p>
          ) : null}
        </div>
      </div>
    </Modal>
  )
}
