import { Link, NavLink, Outlet } from 'react-router-dom'
import { ExternalLink, Menu, Moon, Sun } from 'lucide-react'
import { useState } from 'react'
import { Brand } from '../components/Brand'
import { ScrollRail } from '../components/ScrollRail'
import { PUBLIC_LINKS } from '../app/navigation'
import { useAuth } from '../features/auth/AuthContext'
import { useTheme } from '../features/theme/ThemeContext'
import { cn } from '../lib/cn'

export function PublicLayout() {
  const { status, account } = useAuth()
  const { theme, toggle } = useTheme()
  const [open, setOpen] = useState(false)

  return (
    <div className="flex min-h-screen flex-col">
      <ScrollRail />
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b backdrop-blur" style={{ borderColor: 'var(--border)', backgroundColor: 'color-mix(in srgb, var(--surface) 85%, transparent)' }}>
        <div className="mx-auto flex max-w-[1200px] items-center gap-4 px-5 py-3">
          <Brand to="/" />
          <nav className="ml-6 hidden items-center gap-1 md:flex" aria-label="Primary">
            {PUBLIC_LINKS.map((link) => (
              <NavLink
                key={link.to}
                to={link.to}
                end={link.to === '/'}
                className={({ isActive }) =>
                  cn('rounded-lg px-3 py-1.5 text-sm font-medium transition-colors', isActive ? 'text-[var(--brand)]' : 'muted hover:text-[var(--text)]')
                }
              >
                {link.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button type="button" className="btn btn-ghost btn-icon" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
              {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            {status === 'authenticated' && account ? (
              <Link to={account.role === 'administrator' ? '/admin' : '/app'} className="btn btn-primary btn-sm">
                Open dashboard
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn btn-secondary btn-sm hidden sm:inline-flex">
                  Sign in
                </Link>
                <Link to="/register" className="btn btn-primary btn-sm">
                  Create account
                </Link>
              </>
            )}
            <button type="button" className="btn btn-ghost btn-icon md:hidden" onClick={() => setOpen((current) => !current)} aria-label="Toggle menu">
              <Menu className="h-4 w-4" />
            </button>
          </div>
        </div>
        {open ? (
          <nav className="border-t px-5 py-2 md:hidden" style={{ borderColor: 'var(--border)' }} aria-label="Mobile">
            {PUBLIC_LINKS.map((link) => (
              <Link key={link.to} to={link.to} className="nav-link" onClick={() => setOpen(false)}>
                {link.label}
              </Link>
            ))}
          </nav>
        ) : null}
      </header>

      <main id="main" className="flex-1">
        <Outlet />
      </main>

      <footer className="border-t" style={{ borderColor: 'var(--border)', backgroundColor: 'var(--surface)' }}>
        <div className="mx-auto grid max-w-[1200px] gap-8 px-5 py-10 sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <Brand to="/" />
            <p className="mt-3 max-w-xs text-sm leading-relaxed muted">
              We collect daily observations from named providers and from partner uploads, run the same checks over all of them, and turn the result
              into trends, anomalies, forecasts and alerts.
            </p>
          </div>
          <div>
            <p className="panel-title mb-2">Help</p>
            <ul className="space-y-1.5 text-sm muted">
              <li>
                <Link to="/help" className="hover:text-[var(--text)]">
                  Help &amp; support
                </Link>
              </li>
            </ul>
          </div>
          <div>
            <p className="panel-title mb-2">Account</p>
            <ul className="space-y-1.5 text-sm muted">
              <li>
                <Link to="/register" className="hover:text-[var(--text)]">
                  Create an account
                </Link>
              </li>
              <li>
                <Link to="/login" className="hover:text-[var(--text)]">
                  Sign in
                </Link>
              </li>
            </ul>
            <a href="https://power.larc.nasa.gov/" target="_blank" rel="noreferrer" className="link mt-3 inline-flex items-center gap-1 text-sm">
              NASA POWER <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </div>
        </div>
        <div className="border-t px-5 py-4" style={{ borderColor: 'var(--border)' }}>
          <p className="mx-auto max-w-[1200px] text-xs subtle">
            © {new Date().getFullYear()} EarthScape Climate Agency. Measurements are credited to the provider that recorded them and shown as
            recorded.
          </p>
        </div>
      </footer>
    </div>
  )
}
