import { useCallback, useEffect, useMemo, useState } from 'react'
import { KeyRound, Pencil, Plus, ShieldCheck, Unlock, UserCheck, UserX, Users as UsersIcon } from 'lucide-react'
import api from '../../services/api'
import { ApiRequestError } from '../../services/http'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Modal,
  PageHeader,
  Panel,
  Pagination,
  RefreshButton,
  SearchInput,
  Select,
  StatusBadge,
  TableSkeleton,
  type Column,
  type Option,
} from '../../components/ui'
import { ActionFeedback, CodeCell, LockState, PasswordPolicyChecklist, type Feedback } from './components/AdminBits'
import { PAGE_SIZE, describeError, initial, noteFrom, roleLabel, unwrapOne, type AdminError } from './components/adminUtils'
import { formatDate, relativeTime } from '../../lib/format'
import type { Account } from '../../types/api'

interface RoleDefinition {
  role: string
  label: string
  description: string
  capabilities: string[]
  account_count?: number
  self_registerable?: boolean
}

type ModalKind = 'create' | 'edit' | 'password' | null

/**
 * User management.
 *
 * Reads `GET /api/v1/users` (search / role / status filters, paging) and writes through
 * `POST /users`, `PATCH /users/<id>`, `POST /users/<id>/activate`, `/deactivate`,
 * `/reset-password`, `/unlock`.
 * Server rejections — `email_taken`, `role_invalid`, `self_deactivation`,
 * `last_administrator` — are shown verbatim and the list is re-read afterwards so the
 * table always reflects the database, not an optimistic guess.
 */
export default function UsersPage() {
  const { account, can } = useAuth()
  const canManage = can('users:manage')

  const [term, setTerm] = useState('')
  const debouncedTerm = useDebouncedValue(term, 350)
  const [role, setRole] = useState('')
  const [status, setStatus] = useState('all')
  const [sort, setSort] = useState('created_at')
  const [page, setPage] = useState(1)

  const [modal, setModal] = useState<ModalKind>(null)
  const [selected, setSelected] = useState<Account | null>(null)
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState(false)

  const users = useAsync(
    () =>
      api.users.list({
        page,
        page_size: PAGE_SIZE,
        role: role || undefined,
        status: status || undefined,
        sort,
        // The typed client documents `query`; the Django view reads `search`. Sending both
        // keeps filtering working against the backend as implemented.
        query: debouncedTerm || undefined,
        search: debouncedTerm || undefined,
      }),
    [page, role, status, sort, debouncedTerm],
  )

  const roles = useAsync(() => api.users.roles(), [])
  const roleOptions = useMemo<Option[]>(() => {
    const catalogue = roles.data?.roles ?? []
    if (catalogue.length === 0) {
      return [
        { value: 'administrator', label: roleLabel('administrator') },
        { value: 'climate_analyst', label: roleLabel('climate_analyst') },
        { value: 'viewer', label: roleLabel('viewer') },
      ]
    }
    return catalogue.map((entry) => ({ value: entry.role, label: entry.label || roleLabel(entry.role) }))
  }, [roles.data])

  const filterOptions = useMemo<Option[]>(() => [{ value: '', label: 'All roles' }, ...roleOptions], [roleOptions])

  useEffect(() => setPage(1), [debouncedTerm, role, status, sort])

  const rows = users.data?.items ?? []

  const reload = useCallback(async () => {
    setFeedback(null)
    await users.refetch()
    await roles.refetch()
  }, [users, roles])

  /** Shared mutation handler: surface the server message, then re-read the page. */
  const mutate = useCallback(
    async (action: () => Promise<unknown>, success: string) => {
      setBusy(true)
      setFeedback(null)
      try {
        const raw = await action()
        const note = noteFrom(raw)
        setFeedback({ tone: 'success', text: note ? `${success} ${note}` : success })
        return true
      } catch (cause) {
        const described = describeError(cause)
        setFeedback({
          tone: 'error',
          text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}`,
        })
        return false
      } finally {
        setBusy(false)
        await users.refetch()
      }
    },
    [users],
  )

  const columns = useMemo<Column<Account>[]>(
    () => [
      {
        key: 'name',
        header: 'Account',
        cell: (row) => (
          <div className="flex min-w-0 items-start gap-2">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-clay-50 text-[11px] font-semibold text-clay-800 dark:bg-clay-900/40 dark:text-clay-200">
              {initial(row.full_name)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {row.full_name}
                {account?.id === row.id ? <span className="ml-1 text-xs subtle">(you)</span> : null}
              </p>
              <p className="truncate text-xs subtle">{row.email}</p>
              {row.organisation ? <p className="truncate text-[11px] subtle">{row.organisation}</p> : null}
            </div>
          </div>
        ),
      },
      { key: 'role', header: 'Role', cell: (row) => <Badge tone={row.role === 'administrator' ? 'success' : row.role === 'climate_analyst' ? 'info' : 'neutral'}>{roleLabel(row.role)}</Badge> },
      {
        key: 'active',
        header: 'Status',
        cell: (row) => <StatusBadge value={row.is_active ? 'active' : 'inactive'} label={row.is_active ? 'active' : 'deactivated'} />,
      },
      {
        key: 'verified',
        header: 'Email',
        cell: (row) => <Badge tone={row.email_verified ? 'success' : 'warning'} title={row.email_verified ? 'Address marked verified' : 'Verification email was not confirmed on this deployment'}>{row.email_verified ? 'verified' : 'unverified'}</Badge>,
      },
      { key: 'locked', header: 'Login lock', cell: (row) => <LockState isLocked={row.is_locked} lockedUntil={row.locked_until} /> },
      { key: 'last_login', header: 'Last login', cell: (row) => <span className="text-xs muted" title={row.last_login_at ? formatDate(row.last_login_at, true) : undefined}>{relativeTime(row.last_login_at)}</span> },
      { key: 'created', header: 'Created', cell: (row) => <span className="text-xs muted">{formatDate(row.created_at)}</span> },
      {
        key: 'actions',
        header: 'Actions',
        align: 'right',
        cell: (row) => (
          <div className="flex justify-end gap-1">
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Edit ${row.full_name}`}
              title={canManage ? 'Edit profile and role' : 'Requires the users:manage capability'}
              disabled={!canManage}
              onClick={() => {
                setSelected(row)
                setModal('edit')
              }}
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Set a password for ${row.full_name}`}
              title={canManage ? 'Administrator password reset' : 'Requires the users:manage capability'}
              disabled={!canManage}
              onClick={() => {
                setSelected(row)
                setModal('password')
              }}
            >
              <KeyRound className="h-3.5 w-3.5" />
            </button>
            {row.is_active ? (
              <button
                type="button"
                className="btn btn-secondary btn-icon"
                aria-label={`Deactivate ${row.full_name}`}
                title={account?.id === row.id ? 'You cannot deactivate your own account.' : canManage ? 'Deactivate and revoke sessions' : 'Requires the users:manage capability'}
                disabled={!canManage || account?.id === row.id}
                onClick={() => {
                  setSelected(row)
                  setConfirmDeactivate(true)
                }}
              >
                <UserX className="h-3.5 w-3.5" />
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-secondary btn-icon"
                aria-label={`Activate ${row.full_name}`}
                title={canManage ? 'Re-activate this account' : 'Requires the users:manage capability'}
                disabled={!canManage}
                onClick={() => void mutate(() => api.users.activate(row.id), `${row.full_name} re-activated.`)}
              >
                <UserCheck className="h-3.5 w-3.5" />
              </button>
            )}
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Unlock ${row.full_name}`}
              title={row.is_locked ? (canManage ? 'Clear the login lockout' : 'Requires the users:manage capability') : 'Not locked'}
              disabled={!canManage || !row.is_locked}
              onClick={() => void mutate(() => api.users.unlock(row.id), `Lockout cleared for ${row.full_name}.`)}
            >
              <Unlock className="h-3.5 w-3.5" />
            </button>
          </div>
        ),
      },
    ],
    [account?.id, canManage, mutate],
  )

  const [confirmDeactivate, setConfirmDeactivate] = useState(false)

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="User management"
        description="Accounts are held in the platform’s user directory. Roles, lockouts and password policy are enforced on every request."
        meta={
          users.data ? (
            <>
              <Badge tone="neutral">{users.data.total.toLocaleString()} account(s)</Badge>
              <span className="subtle">page {users.data.page} of {Math.max(users.data.pages, 1)}</span>
            </>
          ) : null
        }
        actions={
          <>
            <RefreshButton onClick={() => void reload()} busy={users.refreshing} />
            <button type="button" className="btn btn-primary" onClick={() => { setSelected(null); setModal('create') }} disabled={!canManage} title={canManage ? 'Provision a new account' : 'Requires the users:manage capability'}>
              <Plus className="h-4 w-4" /> Create user
            </button>
          </>
        }
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
      {users.error ? <div className="mb-4"><ErrorState error={users.error} onRetry={() => void users.refetch()} /></div> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <FilterBar>
            <div className="min-w-[13rem] flex-1">
              <SearchInput value={term} onChange={setTerm} label="Search accounts" placeholder="Search name, email or organisation…" />
            </div>
            <Field label="Role" htmlFor="users-role-filter">
              <Select id="users-role-filter" options={filterOptions} value={role} onChange={setRole} ariaLabel="Filter by role" />
            </Field>
            <Field label="Status" htmlFor="users-status-filter">
              <Select
                id="users-status-filter"
                options={[
                  { value: 'all', label: 'All statuses' },
                  { value: 'active', label: 'Active' },
                  { value: 'inactive', label: 'Deactivated' },
                  { value: 'locked', label: 'Locked out' },
                ]}
                value={status}
                onChange={setStatus}
                ariaLabel="Filter by status"
              />
            </Field>
            <Field label="Sort" htmlFor="users-sort">
              <Select
                id="users-sort"
                options={[
                  { value: 'created_at', label: 'Newest accounts' },
                  { value: 'name', label: 'Name' },
                  { value: 'email', label: 'Email' },
                  { value: 'last_login', label: 'Last login' },
                  { value: 'role', label: 'Role' },
                ]}
                value={sort}
                onChange={setSort}
                ariaLabel="Sort accounts"
              />
            </Field>
          </FilterBar>

          <Panel title="Accounts" description="Deactivation revokes refresh tokens server-side; accounts are not hard-deleted." dense>
            {users.loading ? (
              <TableSkeleton rows={6} columns={7} />
            ) : (
              <DataTable
                caption="User accounts"
                columns={columns}
                rows={rows}
                rowKey={(row) => row.id}
                empty="No account matches these filters."
              />
            )}
            <Pagination meta={users.data ?? undefined} onPage={setPage} label="accounts" />
          </Panel>
        </div>

        <RoleCatalogue roles={roles.data?.roles ?? []} loading={roles.loading} error={roles.error} />
      </div>

      {/* ------------------------------------------------------------- create */}
      {modal === 'create' ? (
        <UserFormModal
          mode="create"
          roleOptions={roleOptions}
          onClose={() => setModal(null)}
          onSubmitted={(message) => {
            setModal(null)
            setFeedback({ tone: 'success', text: message })
            void users.refetch()
            void roles.refetch()
          }}
          busy={busy}
          setBusy={setBusy}
        />
      ) : null}

      {/* --------------------------------------------------------------- edit */}
      {modal === 'edit' && selected ? (
        <UserFormModal
          mode="edit"
          account={selected}
          roleOptions={roleOptions}
          onClose={() => { setModal(null); setSelected(null) }}
          onSubmitted={(message) => {
            setModal(null)
            setSelected(null)
            setFeedback({ tone: 'success', text: message })
            void users.refetch()
          }}
          busy={busy}
          setBusy={setBusy}
        />
      ) : null}

      {/* ----------------------------------------------------------- password */}
      {modal === 'password' && selected ? (
        <PasswordResetModal
          account={selected}
          onClose={() => { setModal(null); setSelected(null) }}
          onDone={(message) => {
            setModal(null)
            setSelected(null)
            setFeedback({ tone: 'success', text: message })
            void users.refetch()
          }}
        />
      ) : null}

      <ConfirmDialog
        open={confirmDeactivate}
        title="Deactivate this account?"
        tone="danger"
        busy={busy}
        confirmLabel="Deactivate"
        onClose={() => { setConfirmDeactivate(false); setSelected(null) }}
        onConfirm={() => {
          const target = selected
          setConfirmDeactivate(false)
          setSelected(null)
          if (!target) return
          void mutate(() => api.users.deactivate(target.id), `${target.full_name} deactivated; their sessions were revoked.`)
        }}
        message={
          selected ? (
            <span>
              <strong>{selected.full_name}</strong> ({selected.email}, {roleLabel(selected.role)}) will not be able to sign in, every
              active session is revoked, and this is refused when it would leave no other active administrator. The account
              record and its data stay in the platform.
            </span>
          ) : null
        }
      />
    </div>
  )
}

// role catalogue

function RoleCatalogue({ roles, loading, error }: { roles: RoleDefinition[]; loading: boolean; error: ApiRequestError | null }) {
  return (
    <Panel title="Roles and capabilities" description="The authoritative server-side permission map." dense>
      {error ? <ErrorState error={error} /> : null}
      {loading && roles.length === 0 ? <p className="text-sm muted">Loading roles…</p> : null}
      <ul className="space-y-4">
        {roles.map((entry) => (
          <li key={entry.role} className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold">{entry.label || roleLabel(entry.role)}</p>
              {typeof entry.account_count === 'number' ? <Badge tone="neutral">{entry.account_count} account(s)</Badge> : null}
            </div>
            <p className="mt-1 text-xs leading-relaxed muted">{entry.description}</p>
            <ul className="mt-2 flex flex-wrap gap-1">
              {entry.capabilities.map((capability) => (
                <li key={capability}>
                  <code className="kbd">{capability}</code>
                </li>
              ))}
            </ul>
            {entry.self_registerable === false ? (
              <p className="mt-2 text-[11px] subtle">Not self-registerable on this deployment.</p>
            ) : null}
          </li>
        ))}
      </ul>
      <p className="mt-4 flex items-start gap-2 text-xs leading-relaxed subtle">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>Capability names are issued by the server (<span className="mono">manage_users</span>, <span className="mono">view_audit_logs</span>…). The interface uses its own colon-style names, so both lists are shown wherever a decision depends on them.</span>
      </p>
    </Panel>
  )
}

// user forms

interface FormState {
  full_name: string
  email: string
  password: string
  role: string
  organisation: string
}

function UserFormModal({
  mode,
  account: target,
  roleOptions,
  onClose,
  onSubmitted,
  busy,
  setBusy,
}: {
  mode: 'create' | 'edit'
  account?: Account
  roleOptions: Option[]
  onClose: () => void
  onSubmitted: (message: string) => void
  busy: boolean
  setBusy: (next: boolean) => void
}) {
  const { configuration } = useAuth()
  const [form, setForm] = useState<FormState>({
    full_name: target?.full_name ?? '',
    email: target?.email ?? '',
    password: '',
    role: target?.role ?? 'viewer',
    organisation: target?.organisation ?? '',
  })
  const [error, setError] = useState<AdminError | null>(null)
  const [confirmRole, setConfirmRole] = useState(false)

  const detail = useAsync(() => (mode === 'edit' && target ? api.users.get(target.id).then((value) => unwrapOne<Account>(value)) : Promise.resolve(null)), [mode, target], {
    enabled: mode === 'edit' && Boolean(target),
  })

  const set = (key: keyof FormState, value: string) => setForm((current) => ({ ...current, [key]: value }))

  async function submit() {
    setError(null)
    setBusy(true)
    try {
      if (mode === 'create') {
        await api.users.create({
          full_name: form.full_name.trim(),
          email: form.email.trim(),
          password: form.password,
          role: form.role,
          organisation: form.organisation.trim() || undefined,
        })
        setForm({ full_name: '', email: '', password: '', role: 'viewer', organisation: '' })
        onSubmitted(`Account created for ${form.email.trim()} with the ${roleLabel(form.role)} role. Share the password out of band; it is not stored by this page.`)
        return
      }
      const changes: Record<string, unknown> = {
        full_name: form.full_name.trim(),
        email: form.email.trim(),
        organisation: form.organisation.trim() || null,
      }
      if (form.role !== (target?.role ?? '')) changes.role = form.role
      await api.users.update(target?.id ?? '', changes)
      onSubmitted(`${form.full_name.trim() || 'Account'} updated.`)
    } catch (cause) {
      setError(describeError(cause))
    } finally {
      setBusy(false)
    }
  }

  const roleChanged = mode === 'edit' && form.role !== (target?.role ?? '')

  return (
    <>
      <Modal
        open
        width="max-w-xl"
        title={mode === 'create' ? 'Create a user account' : `Edit ${target?.full_name ?? 'account'}`}
        description={
          mode === 'create'
            ? 'The email, the password policy and the role are all checked; a duplicate address is rejected.'
            : 'Changing the role revokes this account’s existing sessions on the server.'
        }
        onClose={onClose}
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={
                busy ||
                form.full_name.trim().length < 2 ||
                !form.email.trim() ||
                (mode === 'create' && form.password.length === 0)
              }
              onClick={() => {
                if (roleChanged) setConfirmRole(true)
                else void submit()
              }}
            >
              {busy ? 'Working…' : mode === 'create' ? 'Create account' : 'Save changes'}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          {error ? (
            <div role="alert" className="rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm">
              <p className="font-medium text-alert-critical">{error.message}</p>
              {Object.entries(error.fields).length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-xs muted">
                  {Object.entries(error.fields).map(([field, message]) => (
                    <li key={field}>
                      <span className="mono">{field}</span>: {message}
                    </li>
                  ))}
                </ul>
              ) : null}
              {error.requestId ? <p className="mt-1 text-[11px] subtle">request id {error.requestId}</p> : null}
            </div>
          ) : null}

          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Full name" htmlFor={`name-${mode}`} required error={error?.fields.full_name}>
              <input id={`name-${mode}`} className="input" value={form.full_name} onChange={(event) => set('full_name', event.target.value)} autoComplete="name" />
            </Field>
            <Field label="Email" htmlFor={`email-${mode}`} required error={error?.fields.email} hint="Normalised to lowercase server-side; uniqueness is enforced.">
              <input id={`email-${mode}`} type="email" className="input" value={form.email} onChange={(event) => set('email', event.target.value)} autoComplete="off" />
            </Field>
            <Field label="Role" htmlFor={`role-${mode}`} required error={error?.fields.role}>
              <Select id={`role-${mode}`} options={roleOptions} value={form.role} onChange={(next) => set('role', next)} ariaLabel="Account role" />
            </Field>
            <Field label="Organisation" htmlFor={`org-${mode}`} error={error?.fields.organisation}>
              <input id={`org-${mode}`} className="input" value={form.organisation} onChange={(event) => set('organisation', event.target.value)} placeholder="Institution or agency" />
            </Field>
          </div>

          {mode === 'create' ? (
            <Field label="Initial password" htmlFor="new-account-password" required hint="Sent once over HTTPS. It is not logged, cached or shown again by this page.">
              <input
                id="new-account-password"
                type="password"
                className="input"
                autoComplete="new-password"
                value={form.password}
                onChange={(event) => set('password', event.target.value)}
              />
            </Field>
          ) : null}

          {mode === 'create' ? <PasswordPolicyChecklist value={form.password} configuration={configuration ?? null} /> : null}

          {mode === 'edit' ? (
            <div className="rounded-xl border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
              <p className="panel-title mb-2">Server-side account state</p>
              {detail.loading ? <p className="muted">Reading detail…</p> : null}
              {detail.error ? <p className="text-alert-critical">{describeError(detail.error).message}</p> : null}
              {detail.data ? (
                <ul className="space-y-1 muted">
                  <li className="flex justify-between gap-3"><span>Active sessions</span><span className="mono">{detail.data.sessions_active ?? 0}</span></li>
                  <li className="flex justify-between gap-3"><span>Last login</span><span>{relativeTime(detail.data.last_login_at)}</span></li>
                  <li className="flex justify-between gap-3"><span>Password changed</span><span>{formatDate(detail.data.password_changed_at)}</span></li>
                  <li className="flex justify-between gap-3"><span>Login lock</span><span>{detail.data.is_locked ? 'locked' : 'not locked'}</span></li>
                  <li className="flex justify-between gap-3"><span>Created</span><span>{formatDate(detail.data.created_at)}</span></li>
                </ul>
              ) : null}
              <p className="mt-3 flex items-start gap-2 subtle">
                <UsersIcon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Signing an account out of every session at once is not offered on this screen yet. Changing a role or deactivating an
                  account still revokes its sessions automatically.
                </span>
              </p>
            </div>
          ) : null}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmRole}
        title="Change this account’s role?"
        busy={busy}
        confirmLabel="Change role"
        onClose={() => setConfirmRole(false)}
        onConfirm={() => {
          setConfirmRole(false)
          void submit()
        }}
        message={
          <span>
            {target?.full_name} moves from <strong>{roleLabel(target?.role)}</strong> to <strong>{roleLabel(form.role)}</strong>. Their
            existing sessions are revoked, and the change is refused if it would leave no other active
            administrator.
          </span>
        }
      />
    </>
  )
}

// password reset

function PasswordResetModal({
  account: target,
  onClose,
  onDone,
}: {
  account: Account
  onClose: () => void
  onDone: (message: string) => void
}) {
  const { configuration } = useAuth()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<AdminError | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirm, setConfirm] = useState(false)

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      const raw = await api.users.resetPassword(target.id, password)
      setPassword('')
      const note = noteFrom(raw)
      onDone(`Password set for ${target.full_name}.${note ? ` ${note}` : ' Advise the account holder to change it at first sign-in.'}`)
    } catch (cause) {
      setError(describeError(cause))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Modal
        open
        title={`Administrator password reset for ${target.full_name}`}
        description="The value is sent once over HTTPS, not shown back, and this page keeps no copy of it."
        onClose={onClose}
        footer={
          <>
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" disabled={busy || password.length === 0} onClick={() => setConfirm(true)}>
              {busy ? 'Working…' : 'Set password'}
            </button>
          </>
        }
      >
        <div className="space-y-4">
          {error ? (
            <div role="alert" className="rounded-xl border border-alert-critical/40 bg-alert-critical/5 p-3 text-sm">
              <p className="font-medium text-alert-critical">{error.message}</p>
              {Object.entries(error.fields).length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-xs muted">
                  {Object.entries(error.fields).map(([field, message]) => (
                    <li key={field}>
                      <span className="mono">{field}</span>: {message}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}

          <Field label="New password" htmlFor="admin-reset-password" required hint="Use a temporary value and tell the holder to change it from Profile → Password after signing in.">
            <input
              id="admin-reset-password"
              type="password"
              className="input"
              autoComplete="new-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </Field>

          <PasswordPolicyChecklist value={password} configuration={configuration ?? null} />

          <div className="rounded-xl border p-3 text-xs leading-relaxed muted" style={{ borderColor: 'var(--border)' }}>
            <p className="panel-title mb-1">What happens when you save</p>
            <p>
              The new value is stored only as a bcrypt hash, the failed-login counter and any lockout are reset, every refresh
              token for this account is revoked and an audit record is written. No email is sent, so this works even when email
              delivery is not configured.
            </p>
          </div>

          <CodeCell value={target.email} label="account email" />
        </div>
      </Modal>

      <ConfirmDialog
        open={confirm}
        title="Overwrite this account’s password?"
        busy={busy}
        confirmLabel="Set password"
        onClose={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false)
          void submit()
        }}
        message={
          <span>
            {target.full_name} ({target.email}) will be signed out of all sessions immediately and must use the new password on the
            next sign-in. This action is audited.
          </span>
        }
      />
    </>
  )
}
