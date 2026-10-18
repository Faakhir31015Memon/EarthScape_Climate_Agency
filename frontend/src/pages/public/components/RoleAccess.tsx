import { useMemo } from 'react'
import { ShieldCheck } from 'lucide-react'
import { Badge, Skeleton } from '../../../components/ui'
import { useAsync } from '../../../hooks/useAsync'
import api from '../../../services/api'
import { useAuth } from '../../../features/auth/AuthContext'
import { PUBLIC_ROLE_SUMMARIES } from './publicData'

interface ServerRole {
  role: string
  label: string
  description: string
  capabilities: string[]
}

/**
 * Role and permission explainer. The descriptions mirror the capability map the
 * client uses for optimistic UI (``ROLE_CAPABILITIES`` in ``features/auth/AuthContext.tsx``);
 * when an administrator is signed in the authoritative server catalogue from
 * ``GET /api/v1/users/roles`` is shown alongside it.
 */
export function RoleAccess({ compact = false }: { compact?: boolean }) {
  const { account } = useAuth()
  const isAdmin = account?.role === 'administrator'
  const rolesState = useAsync(
    () => api.users.roles(),
    [],
    { enabled: isAdmin },
  )

  const serverRoles = useMemo<ServerRole[]>(() => {
    const items = rolesState.data?.roles
    return Array.isArray(items) ? items : []
  }, [rolesState.data])

  return (
    <div>
      <div className="grid-responsive">
        {PUBLIC_ROLE_SUMMARIES.map((entry) => {
          const fromServer = serverRoles.find((role) => role.role === entry.role)
          return (
            <article key={entry.role} className="card flex min-w-0 flex-col">
              <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="font-display text-base font-semibold">{fromServer?.label ?? entry.title}</h3>
                  <p className="mt-1 text-xs subtle">{entry.audience}</p>
                </div>
                <Badge tone={entry.role === 'administrator' ? 'danger' : entry.role === 'climate_analyst' ? 'info' : 'success'}>
                  <ShieldCheck className="h-3 w-3" aria-hidden="true" />
                  {entry.role}
                </Badge>
              </header>

              <p className="text-sm leading-relaxed muted">{fromServer?.description ?? entry.summary}</p>

              <p className="panel-title mt-4 mb-2">Can do</p>
              <ul className="space-y-1.5 text-sm muted">
                {(fromServer?.capabilities.length ? fromServer.capabilities : entry.capabilities).map((capability) => (
                  <li key={capability} className="flex items-start gap-2">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-forest-600 dark:bg-forest-400" aria-hidden="true" />
                    <span className="min-w-0">{capability}</span>
                  </li>
                ))}
              </ul>

              {!compact ? (
                <p className="mt-4 border-t pt-3 text-xs leading-relaxed subtle" style={{ borderColor: 'var(--border)' }}>
                  {entry.cannot}
                </p>
              ) : null}
            </article>
          )
        })}
      </div>

      {isAdmin ? (
        <div className="mt-4 text-xs leading-relaxed subtle">
          {rolesState.loading ? (
            <>
              <Skeleton className="h-3 w-64" />
              <span className="ml-2">Loading the authoritative role catalogue…</span>
            </>
          ) : rolesState.error ? (
            <span>The role catalogue could not be read from the API ({rolesState.error.code}); the summary above mirrors the client capability map.</span>
          ) : (
            <span>
              Shown from <span className="mono">GET /api/v1/users/roles</span>. Capability checks in the browser only hide controls — the backend
              re-checks every request.
            </span>
          )}
        </div>
      ) : (
        <p className="mt-4 text-xs leading-relaxed subtle">
          Role labels above match the deployment catalogue at <span className="mono">/api/v1/users/roles</span>, which an administrator can read after
          signing in. Capability checks in the browser only hide controls — the backend re-checks every request.
        </p>
      )}
    </div>
  )
}

export default RoleAccess
