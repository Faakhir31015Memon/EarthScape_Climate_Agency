import { useCallback, useEffect, useMemo, useState } from 'react'
import { Check, Minus, ShieldAlert, UserCog } from 'lucide-react'
import api from '../../services/api'
import { useAsync, useDebouncedValue } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  Panel,
  PageHeader,
  RefreshButton,
  SearchInput,
  Select,
  Hint,
  type Column,
  type Option,
} from '../../components/ui'
import { ActionFeedback, CodeCell, type Feedback } from './components/AdminBits'
import { ROLE_VALUES, describeError, roleLabel } from './components/adminUtils'
import { formatDate, relativeTime } from '../../lib/format'
import { ADMIN_SECTIONS, APPLICATION_SECTIONS } from '../../app/navigation'
import type { Account, Role } from '../../types/api'

interface RoleDefinition {
  role: string
  label: string
  description: string
  capabilities: string[]
  account_count?: number
  self_registerable?: boolean
}

/**
 * Colon-style capability names used by the UI guard (`can()` in
 * features/auth/AuthContext.tsx). The backend issues snake_case names, so the matrix
 * below shows both catalogues instead of pretending they are the same list.
 */
const FRONTEND_GUARD_CAPABILITIES: Record<Role, string[]> = {
  administrator: [
    'platform:manage', 'users:read', 'users:manage', 'datasets:read', 'datasets:manage', 'datasets:upload', 'ingestion:run',
    'processing:run', 'models:train', 'predictions:run', 'alerts:manage', 'reports:manage', 'support:manage',
    'feedback:manage', 'audit:read', 'system:read', 'system:manage', 'analytics:export',
  ],
  climate_analyst: [
    'datasets:read', 'datasets:manage', 'datasets:upload', 'ingestion:run', 'processing:run', 'models:train', 'predictions:run',
    'alerts:manage', 'reports:manage', 'support:read', 'feedback:create', 'analytics:export', 'system:read',
  ],
  viewer: ['datasets:read', 'datasets:upload', 'climate:read', 'reports:read', 'feedback:create', 'support:read'],
}

/**
 * Role & access management.
 *
 * Authoritative source: `GET /api/v1/users/roles` (the backend permission map) plus live
 * per-role account totals from `GET /api/v1/users?role=…&page_size=1`. Assignments are
 * written with `PATCH /api/v1/users/<id>` and are enforced server-side on every request;
 * nothing here changes a permission.
 */
export default function RolesPage() {
  const { configuration, can } = useAuth()
  const canManage = can('users:manage')

  const roles = useAsync(() => api.users.roles(), [])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [countError, setCountError] = useState<string | null>(null)
  const [countsLoading, setCountsLoading] = useState(true)

  const loadCounts = useCallback(async () => {
    setCountsLoading(true)
    setCountError(null)
    try {
      const results = await Promise.all(
        ROLE_VALUES.map(async (role) => {
          const page = await api.users.list({ role, page_size: 1, page: 1 })
          return [role, page.total] as const
        }),
      )
      setCounts(Object.fromEntries(results))
    } catch (cause) {
      setCountError(describeError(cause).message)
    } finally {
      setCountsLoading(false)
    }
  }, [])

  // Per-role account totals come from the real paginated user endpoint, once per mount
  // and whenever the operator re-reads the catalogue.
  useEffect(() => {
    void loadCounts()
  }, [loadCounts])

  const definitions = useMemo<RoleDefinition[]>(() => roles.data?.roles ?? [], [roles.data])

  const backendCapabilities = useMemo(() => {
    const set = new Set<string>()
    definitions.forEach((entry) => entry.capabilities.forEach((capability) => set.add(capability)))
    return Array.from(set).sort()
  }, [definitions])

  const guardCapabilities = useMemo(() => {
    const set = new Set<string>()
    ROLE_VALUES.forEach((role) => FRONTEND_GUARD_CAPABILITIES[role].forEach((capability) => set.add(capability)))
    return Array.from(set).sort()
  }, [])

  const routeRows = useMemo(
    () =>
      [...APPLICATION_SECTIONS, ...ADMIN_SECTIONS]
        .flatMap((section) => section.items)
        .map((item) => ({
          path: item.to,
          label: item.label,
          section: item.to.startsWith('/admin') ? 'Administration' : 'Application',
          roles: item.to.startsWith('/admin') ? ['administrator'] : item.roles ?? [...ROLE_VALUES],
        })),
    [],
  )

  const matrixColumns = useMemo<Column<{ capability: string; style: 'backend' | 'guard' }>[]>(
    () => [
      {
        key: 'capability',
        header: 'Capability',
        cell: (row) => (
          <span className="flex flex-wrap items-center gap-2">
            <code className="kbd">{row.capability}</code>
            <Badge tone={row.style === 'backend' ? 'info' : 'neutral'} title={row.style === 'backend' ? 'Issued by the backend in /users/roles and by /auth/me' : 'Name used by the frontend route/control guard'}>
              {row.style === 'backend' ? 'server' : 'ui guard'}
            </Badge>
          </span>
        ),
      },
      ...ROLE_VALUES.map<Column<{ capability: string; style: 'backend' | 'guard' }>>((role) => ({
        key: role,
        header: roleLabel(role),
        align: 'center',
        cell: (row) => {
          const granted =
            row.style === 'backend'
              ? (definitions.find((entry) => entry.role === role)?.capabilities ?? []).includes(row.capability)
              : FRONTEND_GUARD_CAPABILITIES[role].includes(row.capability)
          return granted ? (
            <Check className="mx-auto h-4 w-4 text-forest-600 dark:text-forest-300" aria-label={`${roleLabel(role)} granted`} />
          ) : (
            <Minus className="mx-auto h-4 w-4 text-[var(--text-subtle)]" aria-label={`${roleLabel(role)} not granted`} />
          )
        },
      })),
    ],
    [definitions],
  )

  const matrixRows = useMemo(
    () => [
      ...backendCapabilities.map((capability) => ({ capability, style: 'backend' as const })),
      ...guardCapabilities.map((capability) => ({ capability, style: 'guard' as const })),
    ],
    [backendCapabilities, guardCapabilities],
  )

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Roles & access management"
        description="Three platform roles, their server-issued capabilities, the routes each role reaches, and the token/session policy in force on this deployment."
        meta={<Badge tone="neutral">{definitions.length || 3} role(s) defined in common/api.py</Badge>}
        actions={<RefreshButton onClick={() => { void roles.refetch(); void loadCounts() }} busy={roles.refreshing || countsLoading} />}
      />

      {roles.error ? <div className="mb-4"><ErrorState error={roles.error} onRetry={() => void roles.refetch()} /></div> : null}

      <div className="grid-responsive mb-6">
        {definitions.length === 0
          ? ROLE_VALUES.map((role) => (
              <Panel key={role} title={roleLabel(role)} description="The catalogue endpoint has not answered yet." dense>
                <p className="text-sm subtle">Loading…</p>
              </Panel>
            ))
          : definitions.map((entry) => (
              <Panel
                key={entry.role}
                title={entry.label || roleLabel(entry.role)}
                description={entry.description}
                dense
                footer={
                  <span className="flex flex-wrap items-center gap-2">
                    <Badge tone="success">{countsLoading ? 'counting…' : (counts[entry.role] ?? entry.account_count ?? 0).toLocaleString()} account(s)</Badge>
                    {entry.self_registerable === undefined ? null : (
                      <Badge tone={entry.self_registerable ? 'warning' : 'neutral'}>
                        {entry.self_registerable ? 'self-registration allowed' : 'administrator-provisioned only'}
                      </Badge>
                    )}
                  </span>
                }
              >
                <ul className="mt-1 flex flex-wrap gap-1">
                  {entry.capabilities.map((capability) => (
                    <li key={capability}>
                      <code className="kbd">{capability}</code>
                    </li>
                  ))}
                </ul>
              </Panel>
            ))}
      </div>

      {countError ? <p className="mb-4 text-xs text-alert-critical">Per-role totals could not be read: {countError}</p> : null}

      <Panel
        title="Capability matrix"
        description="The left column is the authoritative server map; rows tagged “ui guard” are the colon-style names the interface checks locally."
        className="mb-6"
        footer={
          <Hint>
            The two catalogues are kept apart on purpose. A mismatch (for example the server’s <span className="mono">manage_users</span>{' '}
            versus the interface’s <span className="mono">users:manage</span>) is reported to the platform owners; the server map is what
            controls access.
          </Hint>
        }
      >
        <DataTable
          caption="Capability matrix by role"
          columns={matrixColumns}
          rows={matrixRows}
          rowKey={(row) => `${row.style}-${row.capability}`}
          empty="No capability names were returned."
          compact
        />
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel title="Page-level access map" description="Mirrors the RequireRole guards in src/app/router.tsx and the sidebar visibility rules.">
          <DataTable
            caption="Route access by role"
            rows={routeRows}
            rowKey={(row) => row.path}
            compact
            columns={[
              {
                key: 'route',
                header: 'Route',
                cell: (row) => (
                  <div className="min-w-0">
                    <p className="text-sm font-medium">{row.label}</p>
                    <code className="mono text-[12px] subtle">{row.path}</code>
                  </div>
                ),
              },
              { key: 'section', header: 'Area', cell: (row) => <Badge tone={row.section === 'Administration' ? 'success' : 'neutral'}>{row.section}</Badge> },
              ...ROLE_VALUES.map<Column<(typeof routeRows)[number]>>((role) => ({
                key: role,
                header: roleLabel(role),
                align: 'center',
                cell: (row) =>
                  row.roles.includes(role) ? (
                    <Check className="mx-auto h-4 w-4 text-forest-600 dark:text-forest-300" aria-label="reachable" />
                  ) : (
                    <Minus className="mx-auto h-4 w-4 text-[var(--text-subtle)]" aria-label="guarded" />
                  ),
              })),
            ]}
          />
          <Hint>
            Routes outside a role’s set are not merely hidden: <span className="mono">RequireRole</span> renders a refusal and every API
            call re-checks the role from the database (<span className="mono">IsAdministrator</span>,{' '}
            <span className="mono">AdministratorOrAnalyst</span>).
          </Hint>
        </Panel>

        <div className="space-y-6 min-w-0">
          <Panel title="Token & session policy" description="Live values from GET /api/v1/auth/configuration." dense>
            <dl className="space-y-2 text-sm">
              <PolicyRow label="Access token lifetime" value={configuration ? `${configuration.access_token_minutes} minutes` : 'not loaded'} hint="JWT_ACCESS_MINUTES" />
              <PolicyRow label="Refresh token lifetime" value={configuration ? `${configuration.refresh_token_days} days` : 'not loaded'} hint="JWT_REFRESH_DAYS" />
              <PolicyRow label="Refresh rotation" value="single-use; reuse revokes the token family" hint="apps/authentication/services.py · rotate_refresh_token()" />
              <PolicyRow
                label="Login lockout"
                value="not reported by /auth/configuration"
                hint={`Backend defaults: 8 failed attempts, 15 minutes (LOGIN_MAX_FAILED_ATTEMPTS, LOGIN_LOCKOUT_MINUTES)`}
              />
              <PolicyRow label="Password policy" value={configuration ? `min ${configuration.password_policy.min_length}, upper + lower + digit + symbol` : 'not loaded'} hint="PASSWORD_MIN_LENGTH" />
              <PolicyRow label="Self-registration" value={configuration?.self_registration_enabled ? `enabled → ${configuration.registration_roles.join(', ')}` : 'disabled'} hint="ENABLE_SELF_REGISTRATION, ALLOW_ANALYST_SELF_REGISTRATION" />
              <PolicyRow label="Password reset" value={configuration?.password_reset_enabled ? (configuration.email_delivery ? 'enabled, email delivery ready' : 'enabled, but SMTP not configured') : 'disabled'} hint="ENABLE_PASSWORD_RESET, SMTP_HOST" />
            </dl>
          </Panel>

          <Panel title="What each role can do" dense>
            <ul className="space-y-3 text-sm">
              {definitions.map((entry) => (
                <li key={entry.role}>
                  <p className="font-semibold">{entry.label || roleLabel(entry.role)}</p>
                  <p className="text-xs leading-relaxed muted">{entry.description}</p>
                </li>
              ))}
              {definitions.length === 0 ? <li className="text-sm subtle">Loading…</li> : null}
            </ul>
            <Hint>
              Role assignments live on the MongoDB user document (<span className="mono">users.role</span>) and are read on every request;
              changing one immediately invalidates that account’s issued sessions.
            </Hint>
          </Panel>

          <ChangeRolePanel disabled={!canManage} roleOptions={definitions.map((entry) => ({ value: entry.role, label: entry.label || roleLabel(entry.role) }))} />
        </div>
      </div>
    </div>
  )
}

function PolicyRow({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="border-b pb-2 last:border-b-0 last:pb-0" style={{ borderColor: 'var(--border)' }}>
      <dt className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</span>
        <span className="text-right text-sm">{value}</span>
      </dt>
      <dd className="mt-0.5 text-[11px] subtle">{hint}</dd>
    </div>
  )
}

// change-role action

function ChangeRolePanel({ disabled, roleOptions }: { disabled: boolean; roleOptions: Option[] }) {
  const [term, setTerm] = useState('')
  const debounced = useDebouncedValue(term, 350)
  const [target, setTarget] = useState<Account | null>(null)
  const [nextRole, setNextRole] = useState('viewer')
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)

  const search = useAsync(
    () => api.users.list({ page: 1, page_size: 20, query: debounced || undefined, search: debounced || undefined }),
    [debounced],
  )

  const options = useMemo<Option[]>(() => (search.data?.items ?? []).map((item) => ({ value: item.id, label: `${item.full_name} (${item.email})`, hint: roleLabel(item.role) })), [search.data])

  const chosen = useMemo(() => (search.data?.items ?? []).find((item) => item.id === target?.id) ?? target, [search.data, target])

  async function apply() {
    if (!chosen) return
    setBusy(true)
    setFeedback(null)
    try {
      await api.users.update(chosen.id, { role: nextRole })
      setFeedback({ tone: 'success', text: `${chosen.full_name} is now ${roleLabel(nextRole)}. Their sessions were revoked server-side.` })
      setTarget(null)
      await search.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
      // Re-read so the table reflects the database after a rejection such as last_administrator.
      await search.refetch()
    } finally {
      setBusy(false)
    }
  }

  const demoting = chosen?.role === 'administrator' && nextRole !== 'administrator'

  return (
    <Panel title="Change a role" description="PATCH /api/v1/users/<id> with the last-administrator guard." dense>
      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
      <div className="space-y-3">
        <SearchInput value={term} onChange={setTerm} label="Search accounts" placeholder="Find an account by name or email…" />
        <Field label="Account" htmlFor="role-user">
          <Select
            id="role-user"
            options={options}
            value={chosen?.id ?? ''}
            onChange={(id) => setTarget((search.data?.items ?? []).find((item) => item.id === id) ?? null)}
            placeholder={search.loading ? 'Loading accounts…' : 'Select an account'}
            disabled={disabled}
          />
        </Field>
        <Field label="New role" htmlFor="role-next">
          <Select id="role-next" options={roleOptions.length ? roleOptions : [{ value: 'viewer', label: 'Viewer' }]} value={nextRole} onChange={setNextRole} disabled={disabled || !chosen} />
        </Field>

        {chosen ? (
          <div className="rounded-xl border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
            <p className="flex flex-wrap items-center gap-2">
              <Badge tone="neutral">{roleLabel(chosen.role)} now</Badge>
              <Badge tone={chosen.is_active ? 'success' : 'neutral'}>{chosen.is_active ? 'active' : 'deactivated'}</Badge>
              <span className="subtle">joined {formatDate(chosen.created_at)}</span>
              <span className="subtle">last login {relativeTime(chosen.last_login_at)}</span>
            </p>
            <p className="mt-2 leading-relaxed muted">
              {demoting
                ? 'Demoting an administrator is refused with HTTP 409 when it would leave no other active administrator account.'
                : 'The account keeps its data; only the role field and the resulting permissions change.'}
            </p>
            <CodeCell value={chosen.id} label="account id" />
          </div>
        ) : null}

        <button
          type="button"
          className="btn btn-primary"
          disabled={disabled || !chosen || !nextRole || chosen?.role === nextRole || busy}
          title={disabled ? 'Requires the users:manage capability' : !chosen ? 'Select an account first' : chosen && chosen.role === nextRole ? 'Already assigned' : 'Change this account’s role'}
          onClick={() => setConfirm(true)}
        >
          <UserCog className="h-4 w-4" /> Change role
        </button>
        {!disabled && ROLE_VALUES.length > 0 ? (
          <p className="flex items-start gap-2 text-[11px] leading-relaxed subtle">
            <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>Every role change is audited as <span className="mono">user.updated</span> and is enforced by DRF permissions, not by this page.</span>
          </p>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirm}
        title={`Assign ${roleLabel(nextRole)} to ${chosen?.full_name ?? 'this account'}?`}
        busy={busy}
        confirmLabel="Change role"
        tone={demoting ? 'danger' : 'primary'}
        onClose={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false)
          void apply()
        }}
        message={
          <span>
            {chosen?.full_name} ({chosen?.email}) moves from <strong>{roleLabel(chosen?.role)}</strong> to <strong>{roleLabel(nextRole)}</strong>.
            All of their active sessions are revoked, the change is written to the audit trail,
            {demoting ? ' and the backend refuses it if this is the only active administrator account.' : ' and takes effect on their next sign-in.'}
          </span>
        }
      />
    </Panel>
  )
}
