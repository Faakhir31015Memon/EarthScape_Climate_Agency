import type { ReactNode } from 'react'
import { Link, Outlet } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Brand } from '../components/Brand'
import { useAuth } from '../features/auth/AuthContext'
import { Badge } from '../components/ui/StatusBadge'

export function AuthLayout({ title, subtitle, aside }: { title: string; subtitle: string; aside?: ReactNode }) {
  const { configuration } = useAuth()
  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-[1040px]">
        <div className="mb-6 flex items-center justify-between">
          <Brand to="/" />
          <Link to="/" className="link inline-flex items-center gap-1.5 text-sm">
            <ArrowLeft className="h-4 w-4" /> Back to site
          </Link>
        </div>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <section className="card p-6 sm:p-8">
            <h1 className="font-display text-2xl font-semibold">{title}</h1>
            <p className="mt-1.5 text-sm muted">{subtitle}</p>
            <div className="mt-6">
              <Outlet />
            </div>
          </section>
          <aside className="space-y-4">
            {aside ?? (
              <div className="card-tight">
                <p className="panel-title mb-2">Deployment status</p>
                <ul className="space-y-2.5 text-sm">
                  <li className="flex items-center justify-between gap-2">
                    <span className="muted">Self-registration</span>
                    <Badge tone={configuration?.self_registration_enabled ? 'success' : 'neutral'}>
                      {configuration?.self_registration_enabled ? 'open' : 'disabled'}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between gap-2">
                    <span className="muted">Password recovery</span>
                    <Badge tone={configuration?.password_reset_enabled ? 'success' : 'neutral'}>
                      {configuration?.password_reset_enabled ? 'available' : 'email required'}
                    </Badge>
                  </li>
                  <li className="flex items-center justify-between gap-2">
                    <span className="muted">Access token lifetime</span>
                    <span className="mono text-xs">{configuration?.access_token_minutes ?? '—'} min</span>
                  </li>
                </ul>
                <p className="mt-3 text-xs leading-relaxed subtle">
                  Accounts are usually created by an administrator. Where registration is open, a new account starts with read access, and an
                  administrator can widen it later.
                </p>
              </div>
            )}
          </aside>
        </div>
      </div>
    </div>
  )
}
