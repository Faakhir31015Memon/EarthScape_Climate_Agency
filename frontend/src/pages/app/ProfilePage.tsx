import { useMemo, useState, type ReactNode } from 'react'
import { Eye, EyeOff, KeyRound, LogOut, Save, ShieldCheck } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { useTheme } from '../../features/theme/ThemeContext'
import { ApiRequestError } from '../../services/http'
import { Badge, ErrorState, Field, Hint, Panel, Select, StatusBadge, Toggle } from '../../components/ui'
import { formatDate, humanise, relativeTime } from '../../lib/format'
import type { UserPreferences } from '../../types/api'

const UNIT_OPTIONS: Record<keyof UserPreferences['units'], { value: string; label: string }[]> = {
  temperature: [
    { value: 'celsius', label: 'Celsius (°C)' },
    { value: 'fahrenheit', label: 'Fahrenheit (°F)' },
  ],
  precipitation: [
    { value: 'millimetres', label: 'Millimetres (mm)' },
    { value: 'inches', label: 'Inches (in)' },
  ],
  wind: [
    { value: 'm_s', label: 'Metres per second (m/s)' },
    { value: 'km_h', label: 'Kilometres per hour (km/h)' },
    { value: 'mph', label: 'Miles per hour (mph)' },
    { value: 'knots', label: 'Knots' },
  ],
}

/**
 * Accounts created before preferences were structured store a partial document
 * (or none at all), so every field is read through this merge.
 */
function normalisePreferences(stored: unknown): UserPreferences {
  const value = (stored && typeof stored === 'object' ? stored : {}) as Record<string, unknown>
  const units = (value.units && typeof value.units === 'object' ? value.units : {}) as Record<string, string>
  const channels = (value.notification_channels && typeof value.notification_channels === 'object'
    ? value.notification_channels
    : {}) as Record<string, boolean>
  return {
    theme: value.theme === 'dark' ? 'dark' : 'light',
    units: {
      temperature: units.temperature === 'fahrenheit' ? 'fahrenheit' : 'celsius',
      precipitation: units.precipitation === 'inches' ? 'inches' : 'millimetres',
      wind: ['km_h', 'mph', 'knots'].includes(units.wind ?? '') ? units.wind! : 'm_s',
    },
    notification_channels: { in_app: channels.in_app !== false, email: channels.email === true },
    dashboard_region: typeof value.dashboard_region === 'string' ? value.dashboard_region : null,
  }
}

export default function ProfilePage() {
  const { account, capabilities, logout, setAccount } = useAuth()
  const { theme, setTheme, density, setDensity } = useTheme()
  const configuration = useAsync(() => api.auth.configuration(), [])
  const regions = useAsync(() => api.climate.regions(), [])

  const [fullName, setFullName] = useState(account?.full_name ?? '')
  const [organisation, setOrganisation] = useState(account?.organisation ?? '')
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileMsg, setProfileMsg] = useState<string | null>(null)
  const [profileError, setProfileError] = useState<ApiRequestError | null>(null)

  const [prefs, setPrefs] = useState<UserPreferences | null>(() => normalisePreferences(account?.preferences))
  const [prefsBusy, setPrefsBusy] = useState(false)
  const [prefsMsg, setPrefsMsg] = useState<string | null>(null)
  const [prefsError, setPrefsError] = useState<ApiRequestError | null>(null)

  const regionOptions = useMemo(
    () => [{ value: '', label: 'No default region' }, ...(regions.data?.regions ?? []).map((region) => ({ value: region.code, label: region.name }))],
    [regions.data],
  )

  if (!account) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-semibold">Profile</h1>
        <ErrorState error={new ApiRequestError('Your session could not be loaded. Sign in again to manage your profile.', 'no_account', 401)} />
      </div>
    )
  }

  const emailDelivery = configuration.data?.email_delivery ?? false

  async function saveProfile() {
    setProfileBusy(true)
    setProfileMsg(null)
    setProfileError(null)
    try {
      const updated = await api.auth.updateMe({ full_name: fullName.trim(), organisation: organisation.trim() || null })
      setAccount(updated)
      setProfileMsg(`Saved: full name${organisation.trim() ? ' and organisation' : ''}.`)
    } catch (error) {
      setProfileError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setProfileBusy(false)
    }
  }

  async function persistPreferences(next: UserPreferences, summary: string) {
    setPrefsBusy(true)
    setPrefsMsg(null)
    setPrefsError(null)
    try {
      const updated = await api.auth.updateMe({ preferences: next })
      setAccount(updated)
      setPrefs(updated.preferences)
      setPrefsMsg(`Saved: ${summary}.`)
    } catch (error) {
      setPrefsError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setPrefsBusy(false)
    }
  }

  async function applyTheme(next: 'light' | 'dark') {
    setTheme(next)
    if (prefs) await persistPreferences({ ...prefs, theme: next }, `theme set to ${next}`)
  }

  async function applyDensity(next: 'comfortable' | 'compact') {
    setDensity(next)
    if (prefs) await persistPreferences({ ...prefs, density: next }, `density set to ${next}`)
  }

  function savePreferences() {
    if (!prefs || !account) return
    const base = normalisePreferences(account.preferences)
    const changed: string[] = []
    if (prefs.units.temperature !== base.units.temperature) changed.push('temperature unit')
    if (prefs.units.precipitation !== base.units.precipitation) changed.push('precipitation unit')
    if (prefs.units.wind !== base.units.wind) changed.push('wind unit')
    if (prefs.notification_channels.in_app !== base.notification_channels.in_app) changed.push('in-app notifications')
    if (prefs.notification_channels.email !== base.notification_channels.email) changed.push('email notifications')
    if ((prefs.dashboard_region ?? '') !== (base.dashboard_region ?? '')) changed.push('default dashboard region')
    if (!changed.length) {
      setPrefsMsg('No unit, notification, or region changes to save.')
      return
    }
    void persistPreferences({ ...prefs, theme, density }, changed.join(', '))
  }

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Account</p>
        <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Your profile</h1>
        <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
          Account details, interface preferences and password management for <span className="font-medium">{account.email}</span>. Preferences are stored on
          your account so they follow you across devices; each save below reports which field changed.
        </p>
      </header>

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="Account summary" description="Read from your signed-in session.">
          <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
            <Summary label="Name" value={account.full_name} />
            <Summary label="Email" value={<span className="break-all">{account.email}</span>} />
            <Summary label="Role" value={<Badge tone="info">{humanise(account.role)}</Badge>} />
            <Summary label="Status" value={<StatusBadge value={account.is_active ? 'active' : 'inactive'} kind="generic" />} />
            <Summary label="Email verified" value={<StatusBadge value={account.email_verified ? 'verified' : 'unverified'} kind="generic" label={account.email_verified ? 'Verified' : 'Not verified'} />} />
            <Summary label="Active sessions" value={account.sessions_active != null ? String(account.sessions_active) : '—'} />
            <Summary label="Created" value={<span title={account.created_at}>{formatDate(account.created_at)}</span>} />
            <Summary label="Last login" value={<span title={account.last_login_at ?? ''}>{account.last_login_at ? relativeTime(account.last_login_at) : 'never'}</span>} />
            <Summary label="Password changed" value={<span title={account.password_changed_at ?? ''}>{account.password_changed_at ? relativeTime(account.password_changed_at) : 'unknown'}</span>} />
          </dl>

          {!account.email_verified ? (
            <div className="mt-4">
              {emailDelivery ? (
                <Hint>Your email address isn't verified yet. Verification runs through the platform's email service, so this page won't report a sent message unless the server sent one.</Hint>
              ) : (
                <Hint>Email delivery is not configured on this deployment, so a verification email cannot be sent. An administrator can confirm this status directly.</Hint>
              )}
            </div>
          ) : null}
        </Panel>

        <Panel title="Capabilities" description="Issued by the server for your role. Actions elsewhere are still checked independently on every request.">
          <div className="flex flex-wrap gap-1.5">
            {capabilities.length ? (
              capabilities.map((capability) => (
                <Badge key={capability} tone={capability.includes('manage') ? 'warning' : capability.includes('run') || capability.includes('train') ? 'success' : 'neutral'} title={capability}>
                  {capability}
                </Badge>
              ))
            ) : (
              <span className="text-sm muted">No capabilities were returned for this session.</span>
            )}
          </div>
          <div className="mt-5 flex items-center gap-2">
            <button type="button" className="btn btn-secondary" onClick={() => void logout()}>
              <LogOut className="h-4 w-4" /> Sign out
            </button>
          </div>
          <Hint>Signing out revokes your refresh token and returns you to the sign-in page.</Hint>
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-2">
        <Panel title="Editable profile" description="Changes are saved to your account record.">
          <div className="space-y-4">
            <Field label="Full name" htmlFor="pf-name" required>
              <input id="pf-name" className="input" value={fullName} onChange={(event) => setFullName(event.target.value)} />
            </Field>
            <Field label="Organisation" htmlFor="pf-org" hint="Optional. Shown on reports you generate.">
              <input id="pf-org" className="input" value={organisation} onChange={(event) => setOrganisation(event.target.value)} placeholder="e.g. Regional Climate Office" />
            </Field>
            <button type="button" className="btn btn-primary" onClick={() => void saveProfile()} disabled={profileBusy || !fullName.trim()}>
              <Save className="h-4 w-4" /> {profileBusy ? 'Saving…' : 'Save profile'}
            </button>
            {profileMsg ? <p className="mt-1 text-xs text-forest-600 dark:text-forest-300" role="status">{profileMsg}</p> : null}
            {profileError ? <ErrorState error={profileError} onRetry={() => void saveProfile()} /> : null}
          </div>
        </Panel>

        <PasswordPanel policy={configuration.data?.password_policy ?? null} policyError={configuration.error} emailDelivery={emailDelivery} passwordChangedAt={account.password_changed_at} />
      </div>

      <Panel title="Preferences" description="Theme and density apply instantly and are persisted. Unit, notification and region choices are saved together.">
        <div className="space-y-5">
          <div className="flex flex-wrap gap-6">
            <div>
              <span className="label">Theme</span>
              <div className="flex gap-2">
                <button type="button" className={`btn btn-sm ${theme === 'light' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => void applyTheme('light')} disabled={prefsBusy}>Light</button>
                <button type="button" className={`btn btn-sm ${theme === 'dark' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => void applyTheme('dark')} disabled={prefsBusy}>Dark</button>
              </div>
            </div>
            <div>
              <span className="label">Density</span>
              <div className="flex gap-2">
                <button type="button" className={`btn btn-sm ${density === 'comfortable' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => void applyDensity('comfortable')} disabled={prefsBusy}>Comfortable</button>
                <button type="button" className={`btn btn-sm ${density === 'compact' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => void applyDensity('compact')} disabled={prefsBusy}>Compact</button>
              </div>
            </div>
          </div>

          {!prefs ? (
            <Hint>Preferences are still loading from your account.</Hint>
          ) : (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Temperature unit" htmlFor="u-temp">
                  <Select id="u-temp" options={UNIT_OPTIONS.temperature} value={prefs.units.temperature} onChange={(value) => setPrefs({ ...prefs, units: { ...prefs.units, temperature: value } })} />
                </Field>
                <Field label="Precipitation unit" htmlFor="u-precip">
                  <Select id="u-precip" options={UNIT_OPTIONS.precipitation} value={prefs.units.precipitation} onChange={(value) => setPrefs({ ...prefs, units: { ...prefs.units, precipitation: value } })} />
                </Field>
                <Field label="Wind unit" htmlFor="u-wind">
                  <Select id="u-wind" options={UNIT_OPTIONS.wind} value={prefs.units.wind} onChange={(value) => setPrefs({ ...prefs, units: { ...prefs.units, wind: value } })} />
                </Field>
              </div>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Default dashboard region" htmlFor="p-region" hint="Used to preselect a region on the dashboard and map.">
                  <Select id="p-region" options={regionOptions} value={prefs.dashboard_region ?? ''} onChange={(value) => setPrefs({ ...prefs, dashboard_region: value || null })} />
                </Field>
              </div>

              <div className="flex flex-wrap gap-6">
                <Toggle checked={prefs.notification_channels.in_app} onChange={(next) => setPrefs({ ...prefs, notification_channels: { ...prefs.notification_channels, in_app: next } })} label="In-app notifications" />
                <Toggle checked={prefs.notification_channels.email} onChange={(next) => setPrefs({ ...prefs, notification_channels: { ...prefs.notification_channels, email: next } })} label="Email notifications" />
                {!emailDelivery ? <span className="text-xs subtle">Email delivery isn't configured on this deployment, so the email toggle is saved but no email is sent.</span> : null}
              </div>

              <div className="flex items-center gap-2">
                <button type="button" className="btn btn-primary" onClick={savePreferences} disabled={prefsBusy}>
                  <Save className="h-4 w-4" /> {prefsBusy ? 'Saving…' : 'Save preferences'}
                </button>
                {prefsMsg ? <p className="text-xs text-forest-600 dark:text-forest-300" role="status">{prefsMsg}</p> : null}
              </div>
              {prefsError ? <ErrorState error={prefsError} onRetry={savePreferences} /> : null}
            </>
          )}
        </div>
      </Panel>
    </div>
  )
}

function Summary({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="mt-0.5">{value}</dd>
    </div>
  )
}

function PasswordField({
  id,
  label,
  autoComplete,
  value,
  onChange,
  visible,
  onToggle,
  error,
}: {
  id: string
  label: string
  autoComplete: string
  value: string
  onChange: (value: string) => void
  visible: boolean
  onToggle: () => void
  error?: string | null
}) {
  return (
    <Field label={label} htmlFor={id} required error={error}>
      <div className="relative">
        <input
          id={id}
          className="input pr-10"
          type={visible ? 'text' : 'password'}
          autoComplete={autoComplete}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          className="btn btn-ghost btn-icon absolute right-0 top-1/2 -translate-y-1/2"
          onClick={onToggle}
          aria-label={visible ? `Hide ${label.toLowerCase()}` : `Show ${label.toLowerCase()}`}
          aria-pressed={visible}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
    </Field>
  )
}

function PasswordPanel({
  policy,
  policyError,
  emailDelivery,
  passwordChangedAt,
}: {
  policy: { min_length: number; requires_uppercase: boolean; requires_lowercase: boolean; requires_number: boolean; requires_symbol?: boolean } | null
  policyError: ApiRequestError | null
  emailDelivery: boolean
  passwordChangedAt?: string
}) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [visible, setVisible] = useState({ current: false, next: false, confirm: false })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<ApiRequestError | null>(null)

  const checks = useMemo(() => {
    const minLength = policy?.min_length ?? 12
    return [
      { label: `At least ${minLength} characters`, ok: next.length >= minLength, hidden: false },
      { label: 'An uppercase letter', ok: !policy?.requires_uppercase || /[A-Z]/.test(next), hidden: policy?.requires_uppercase === false },
      { label: 'A lowercase letter', ok: !policy?.requires_lowercase || /[a-z]/.test(next), hidden: policy?.requires_lowercase === false },
      { label: 'A number', ok: !policy?.requires_number || /[0-9]/.test(next), hidden: policy?.requires_number === false },
      {
        label: 'A symbol (@ . $ % & * _ = + - ! ? # /)',
        ok: (policy?.requires_symbol ?? true) === false || /[^A-Za-z0-9]/.test(next),
        hidden: policy?.requires_symbol === false,
      },
    ].filter((check) => !check.hidden)
  }, [next, policy])

  const allOk = next.length > 0 && checks.every((check) => check.ok) && next === confirm

  async function submit() {
    setBusy(true)
    setMessage(null)
    setError(null)
    try {
      await api.auth.changePassword(current, next)
      setCurrent('')
      setNext('')
      setConfirm('')
      setMessage('Password updated.')
    } catch (cause) {
      setError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel title="Change password" description={passwordChangedAt ? `Last changed ${relativeTime(passwordChangedAt)}.` : 'Rotate your credentials here.'}>
      {policyError ? <Hint>Account policy could not be loaded ({policyError.message}); the checks below use the conservative defaults until it becomes available.</Hint> : null}
      <div className="space-y-4">
        <PasswordField
          id="pw-current"
          label="Current password"
          autoComplete="current-password"
          value={current}
          onChange={setCurrent}
          visible={visible.current}
          onToggle={() => setVisible((state) => ({ ...state, current: !state.current }))}
        />
        <PasswordField
          id="pw-new"
          label="New password"
          autoComplete="new-password"
          value={next}
          onChange={setNext}
          visible={visible.next}
          onToggle={() => setVisible((state) => ({ ...state, next: !state.next }))}
        />
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide subtle">Password requirements</p>
          <ul className="space-y-1 text-xs">
            {checks.map((check) => (
              <li key={check.label} className={`flex items-center gap-2 ${check.ok ? 'text-forest-600 dark:text-forest-300' : 'muted'}`}>
                <ShieldCheck className={`h-3.5 w-3.5 ${check.ok ? '' : 'opacity-40'}`} aria-hidden="true" /> {check.label}
              </li>
            ))}
            <li className={`flex items-center gap-2 ${next && next === confirm ? 'text-forest-600 dark:text-forest-300' : 'muted'}`}>
              <ShieldCheck className={`h-3.5 w-3.5 ${next && next === confirm ? '' : 'opacity-40'}`} aria-hidden="true" /> Passwords match
            </li>
          </ul>
        </div>
        <PasswordField
          id="pw-confirm"
          label="Confirm new password"
          autoComplete="new-password"
          value={confirm}
          onChange={setConfirm}
          visible={visible.confirm}
          onToggle={() => setVisible((state) => ({ ...state, confirm: !state.confirm }))}
          error={next && confirm && next !== confirm ? 'The two passwords do not match.' : null}
        />
        <button type="button" className="btn btn-primary" onClick={() => void submit()} disabled={busy || !current || !allOk}>
          <KeyRound className="h-4 w-4" /> {busy ? 'Updating…' : 'Update password'}
        </button>
        {message ? <p className="mt-1 text-xs text-forest-600 dark:text-forest-300" role="status">{message}</p> : null}
        {!emailDelivery ? <Hint>Email delivery isn't configured, so a reset link can't be emailed from here. Set a new password directly using your current one.</Hint> : null}
        {error ? <ErrorState error={error} onRetry={() => void submit()} /> : null}
      </div>
    </Panel>
  )
}
