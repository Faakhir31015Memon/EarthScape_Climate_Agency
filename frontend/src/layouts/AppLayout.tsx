import { useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { Bell, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Settings, Sun, X } from 'lucide-react'
import api from '../services/api'
import { useAsync } from '../hooks/useAsync'
import { useAuth } from '../features/auth/AuthContext'
import { useTheme } from '../features/theme/ThemeContext'
import { ADMIN_SECTIONS, APPLICATION_SECTIONS, type NavSection } from '../app/navigation'
import { Brand } from '../components/Brand'
import { EnvironmentBanner } from '../components/EnvironmentBanner'
import { cn } from '../lib/cn'
import type { Role } from '../types/api'

function useVisibleSections(sections: NavSection[], role: Role | undefined): NavSection[] {
  return useMemo(
    () =>
      sections
        .map((section) => ({
          ...section,
          items: section.items.filter((item) => {
            if (item.roles && (!role || !item.roles.includes(role))) return false
            if (item.to.startsWith('/admin') && role !== 'administrator') return false
            return true
          }),
        }))
        .filter((section) => section.items.length > 0),
    [sections, role],
  )
}

function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const { account, logout } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const admin = location.pathname.startsWith('/admin')
  const sections = useVisibleSections(admin ? ADMIN_SECTIONS : APPLICATION_SECTIONS, account?.role)
  const appSections = useVisibleSections(APPLICATION_SECTIONS, account?.role)
  const badgeCounts = useBadgeCounts(admin ? 'admin' : 'app')

  return (
    <nav
      aria-label={admin ? 'Administration navigation' : 'Application navigation'}
      className={cn('flex h-full flex-col border-r transition-[width] duration-200', collapsed ? 'w-[68px]' : 'w-[248px]')}
      style={{ borderColor: 'var(--border)', backgroundColor: 'var(--surface)' }}
    >
      <div className={cn('flex items-center gap-2 border-b px-3 py-3', collapsed && 'justify-center')} style={{ borderColor: 'var(--border)' }}>
        <Brand compact={collapsed} to={admin ? '/admin' : '/app'} />
        {!collapsed ? (
          <button type="button" className="btn btn-ghost btn-icon ml-auto" onClick={onToggle} aria-label="Collapse sidebar">
            <PanelLeftClose className="h-4 w-4" />
          </button>
        ) : null}
      </div>
      {collapsed ? (
        <button type="button" className="btn btn-ghost btn-icon mx-auto my-2" onClick={onToggle} aria-label="Expand sidebar">
          <PanelLeftOpen className="h-4 w-4" />
        </button>
      ) : null}

      <div className="flex-1 overflow-y-auto px-2 py-3">
        {sections.map((section) => (
          <div key={section.id} className="mb-4">
            {!collapsed ? <p className="panel-title px-3 pb-1.5">{section.label}</p> : null}
            <ul className="space-y-0.5">
              {section.items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => cn('nav-link', isActive && 'active', collapsed && 'justify-center px-2')}
                    title={item.label}
                  >
                    <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                    {!collapsed ? <span className="min-w-0 flex-1 truncate">{item.label}</span> : null}
                    {!collapsed && item.badge && badgeCounts[item.badge] > 0 ? (
                      <span className="ml-auto rounded-full bg-alert-high px-1.5 text-[11px] font-semibold text-white">{badgeCounts[item.badge]}</span>
                    ) : null}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}

        {admin ? (
          <div className="mb-4">
            <p className="panel-title px-3 pb-1.5">Application</p>
            <ul className="space-y-0.5">
              {appSections
                .flatMap((section) => section.items)
                .slice(0, 4)
                .map((item) => (
                  <li key={item.to}>
                    <NavLink to={item.to} className={cn('nav-link', collapsed && 'justify-center px-2')} title={item.label}>
                      <item.icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                      {!collapsed ? <span className="truncate">{item.label}</span> : null}
                    </NavLink>
                  </li>
                ))}
            </ul>
          </div>
        ) : null}
      </div>

      {!collapsed ? (
        <div className="border-t px-3 py-3 text-xs" style={{ borderColor: 'var(--border)' }}>
          <p className="font-semibold">{account?.full_name}</p>
          <p className="truncate subtle">{account?.email}</p>
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="badge badge-success">{account?.role?.replaceAll('_', ' ')}</span>
            <button type="button" className="btn btn-ghost btn-sm inline-flex items-center gap-1" onClick={() => void logout().then(() => navigate('/login'))}>
              <LogOut className="h-3.5 w-3.5" /> Sign out
            </button>
          </div>
        </div>
      ) : null}
    </nav>
  )
}

function useBadgeCounts(scope: 'app' | 'admin'): { alerts: number; notifications: number; tickets: number } {
  const [counts, setCounts] = useState({ alerts: 0, notifications: 0, tickets: 0 })
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        if (scope === 'admin') {
          const summary = await api.support.summary()
          if (cancelled) return
          setCounts((current) => ({ ...current, tickets: summary.open ?? 0 }))
          return
        }
        const [alerts, unread] = await Promise.all([api.alerts.summary(), api.notifications.unread()])
        if (cancelled) return
        setCounts({ alerts: alerts.open, notifications: unread.unread, tickets: 0 })
      } catch {
        /* badges are cosmetic; never surface a failure for them */
      }
    }
    void load()
    const handle = window.setInterval(() => void load(), 120_000)
    return () => {
      cancelled = true
      window.clearInterval(handle)
    }
  }, [scope])
  return counts
}

function Topbar({ onOpenMobile }: { onOpenMobile: () => void }) {
  const { theme, toggle } = useTheme()
  const { account } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [menu, setMenu] = useState(false)
  const notifications = useAsync(() => api.notifications.list({ page_size: 6, unread: 'true' }), [], { interval: 90_000 })

  const title = useMemo(() => {
    const sections = location.pathname.startsWith('/admin') ? ADMIN_SECTIONS : APPLICATION_SECTIONS
    const item = sections.flatMap((section) => section.items).find((candidate) => (candidate.end ? location.pathname === candidate.to : location.pathname.startsWith(candidate.to)))
    return item?.label ?? 'EarthScape'
  }, [location.pathname])

  return (
    <header
      className="sticky top-0 z-30 flex items-center gap-3 border-b px-4 py-2.5 backdrop-blur"
      style={{ borderColor: 'var(--border)', backgroundColor: 'color-mix(in srgb, var(--surface) 88%, transparent)' }}
    >
      <button type="button" className="btn btn-ghost btn-icon lg:hidden" onClick={onOpenMobile} aria-label="Open navigation">
        <Menu className="h-4.5 w-4.5" />
      </button>
      <h1 className="truncate font-display text-[15px] font-semibold">{title}</h1>
      <div className="ml-auto flex items-center gap-1.5">
        <div className="relative">
          <button
            type="button"
            className="btn btn-ghost btn-icon"
            onClick={() => setMenu((current) => !current)}
            aria-expanded={menu}
            aria-label={`Notifications (${notifications.data?.items.length ?? 0} unread shown)`}
          >
            <Bell className="h-4 w-4" />
            {(notifications.data?.items.length ?? 0) > 0 ? (
              <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-alert-high" aria-hidden="true" />
            ) : null}
          </button>
          {menu ? (
            <div className="card-tight absolute right-0 z-40 mt-2 w-[min(22rem,90vw)] p-3 shadow-elevated">
              <div className="mb-2 flex items-center justify-between">
                <p className="panel-title">Unread notifications</p>
                <Link to="/app/notifications" className="link text-xs" onClick={() => setMenu(false)}>
                  View all
                </Link>
              </div>
              {notifications.loading ? <p className="py-4 text-xs subtle">Loading…</p> : null}
              {notifications.data && notifications.data.items.length === 0 ? (
                <p className="py-4 text-xs subtle">Nothing unread. Alerts appear here as soon as a rule matches.</p>
              ) : null}
              <ul className="max-h-72 space-y-2 overflow-auto">
                {notifications.data?.items.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className="w-full rounded-lg border p-2 text-left hover:bg-[var(--surface-muted)]"
                      style={{ borderColor: 'var(--border)' }}
                      onClick={() => {
                        setMenu(false)
                        navigate(item.related_resource?.type === 'alert' ? '/app/alerts' : '/app/notifications')
                      }}
                    >
                      <p className="text-sm font-medium leading-snug">{item.title}</p>
                      <p className="mt-0.5 line-clamp-2 text-xs muted">{item.body}</p>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
        <button type="button" className="btn btn-ghost btn-icon" onClick={toggle} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
        {account?.role === 'administrator' ? (
          <Link to="/admin" className="btn btn-ghost btn-sm hidden sm:inline-flex" title="Administration">
            <Settings className="h-3.5 w-3.5" /> Admin
          </Link>
        ) : null}
      </div>
    </header>
  )
}

export function AppLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const [mobile, setMobile] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setMobile(false)
  }, [location.pathname])

  return (
    <div className="flex min-h-screen">
      <a href="#main" className="skip-link">
        Skip to content
      </a>
      <aside className="hidden lg:block">
        <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((current) => !current)} />
      </aside>

      {mobile ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="absolute inset-0 bg-black/45" onClick={() => setMobile(false)} aria-hidden="true" />
          <div className="absolute inset-y-0 left-0 w-[264px] animate-fade-in">
            <Sidebar collapsed={false} onToggle={() => setMobile(false)} />
            <button type="button" className="btn btn-ghost btn-icon absolute -right-11 top-3 text-white" onClick={() => setMobile(false)} aria-label="Close navigation">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="sticky top-0 z-40">
          <EnvironmentBanner />
          <Topbar onOpenMobile={() => setMobile(true)} />
        </div>
        <main id="main" className="min-w-0 flex-1 px-4 py-6 sm:px-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1500px]">
            <Outlet />
          </div>
        </main>
        <footer className="border-t px-6 py-4 text-xs subtle" style={{ borderColor: 'var(--border)' }}>
          <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-2">
            <span>EarthScape Climate Agency — Big Data Climate Intelligence Platform</span>
            <span className="flex items-center gap-3">
              <Link to="/privacy" className="link">Privacy</Link>
              <Link to="/terms" className="link">Terms</Link>
              <Link to="/app/support" className="link">Support</Link>
            </span>
          </div>
        </footer>
      </div>
    </div>
  )
}
