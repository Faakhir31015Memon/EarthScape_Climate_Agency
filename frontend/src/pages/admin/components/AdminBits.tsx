import { useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Check, Copy, Lock, X } from 'lucide-react'
import { Badge, Mono, StatusBadge } from '../../../components/ui'
import { formatDuration, isMaskedValue, lockCountdown, looksSecret, redactIfNeeded, statusBreakdown } from './adminUtils'
import type { AuthConfiguration } from '../../../types/api'

/* ------------------------------------------------------------------ feedback */

export interface Feedback {
  tone: 'success' | 'error' | 'info'
  text: string
}

/** Announces destructive / privileged action results to assistive tech. */
export function ActionFeedback({ feedback, onDismiss }: { feedback: Feedback | null; onDismiss?: () => void }) {
  if (!feedback) return null
  const classes =
    feedback.tone === 'error'
      ? 'border-alert-critical/40 bg-alert-critical/5 text-alert-critical'
      : feedback.tone === 'success'
        ? 'border-forest-300 bg-forest-50/60 text-forest-800 dark:bg-forest-900/20 dark:text-forest-200'
        : 'border-hydro-200 bg-hydro-50/60 text-hydro-800 dark:bg-hydro-900/20 dark:text-hydro-200'
  return (
    <div role="status" aria-live="polite" className={`mb-4 flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${classes}`}>
      {feedback.tone === 'error' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <Check className="mt-0.5 h-4 w-4 shrink-0" />}
      <span className="min-w-0 flex-1 break-words">{feedback.text}</span>
      {onDismiss ? (
        <button type="button" className="btn btn-ghost btn-icon shrink-0" onClick={onDismiss} aria-label="Dismiss message">
          <X className="h-3.5 w-3.5" />
        </button>
      ) : null}
    </div>
  )
}

/* --------------------------------------------------------------- secret-safe */

/** Renders a backend-masked value; never offers a way to reveal it. */
export function MaskedValue({ label, value }: { label: string; value: unknown }) {
  const text = typeof value === 'string' ? value : value === undefined || value === null ? 'not configured' : String(value)
  const configured = isMaskedValue(text) || (/key|token|secret|uri/i.test(label) ? false : Boolean(text) && text !== 'not configured')
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs subtle">{label}</span>
      <span className="mono text-[12px]">{text}</span>
      <Badge tone={configured ? 'success' : 'neutral'} title={configured ? 'A value is set in the environment' : 'No value set'}>
        {configured ? 'configured' : 'not set'}
      </Badge>
    </div>
  )
}

/** Copy-to-clipboard that refuses secret-looking content. */
export function CopyButton({ value, label, secret = false }: { value: string; label?: string; secret?: boolean }) {
  const [state, setState] = useState<Feedback | null>(null)

  useEffect(() => {
    if (!state) return
    const handle = window.setTimeout(() => setState(null), 2500)
    return () => window.clearTimeout(handle)
  }, [state])

  if (secret || looksSecret(label ?? '')) {
    return (
      <span title="Secret values are never copied or displayed">
        <Badge tone="neutral">not copyable</Badge>
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-2">
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        aria-label={`Copy ${label ?? 'value'}`}
        onClick={() => {
          if (!navigator.clipboard) {
            setState({ tone: 'error', text: 'Clipboard access is unavailable in this browser.' })
            return
          }
          void navigator.clipboard
            .writeText(value)
            .then(() => setState({ tone: 'success', text: `Copied ${label ?? 'value'}.` }))
            .catch(() => setState({ tone: 'error', text: 'The browser blocked clipboard access.' }))
        }}
      >
        <Copy className="h-3.5 w-3.5" /> Copy
      </button>
      {state ? (
        <span aria-live="polite" className={`text-xs ${state.tone === 'error' ? 'text-alert-critical' : 'text-forest-600 dark:text-forest-300'}`}>
          {state.text}
        </span>
      ) : null}
    </span>
  )
}

/* -------------------------------------------------------------------- status */

export function LockState({ isLocked, lockedUntil }: { isLocked?: boolean; lockedUntil?: string | null }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!lockedUntil) return
    const handle = window.setInterval(() => setNow(Date.now()), 10_000)
    return () => window.clearInterval(handle)
  }, [lockedUntil])

  const remaining = lockCountdown(lockedUntil, now)
  if (!isLocked && !remaining) return <Badge tone="neutral">not locked</Badge>
  return (
    <Badge tone="danger" title={remaining ? `Login throttled by the backend until ${new Date(lockedUntil as string).toLocaleString()}` : 'Lockout expired'}>
      <Lock className="h-3 w-3" /> {remaining ?? 'locked'}
    </Badge>
  )
}

export function Breakdown({ counts, kind = 'generic', empty = 'No records in this breakdown.' }: { counts?: Record<string, number>; kind?: 'job' | 'generic'; empty?: string }) {
  const entries = statusBreakdown(counts)
  if (entries.length === 0) return <p className="text-xs subtle">{empty}</p>
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {entries.map((entry) => (
        <span key={entry.key} className="inline-flex items-center gap-1">
          <StatusBadge value={entry.key} kind={kind} />
          <span className="mono text-[12px] font-semibold">{entry.value.toLocaleString()}</span>
        </span>
      ))}
    </div>
  )
}

/** One row of the "what is missing for production" checklists. */
export function ConfigRequirement({
  label,
  envKey,
  configured,
  detail,
  impact,
}: {
  label: string
  envKey: string
  configured: boolean | null
  detail?: ReactNode
  impact?: ReactNode
}) {
  const tone = configured === true ? 'success' : configured === false ? 'warning' : 'neutral'
  const state = configured === true ? 'ready' : configured === false ? 'missing' : 'unknown'
  return (
    <li className="flex flex-col gap-1 border-l pl-3" style={{ borderColor: 'var(--border)' }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{label}</span>
        <Badge tone={tone}>{state}</Badge>
        <code className="kbd">{envKey}</code>
      </div>
      {detail ? <p className="text-xs leading-relaxed muted">{detail}</p> : null}
      {impact ? <p className="text-xs leading-relaxed subtle">{impact}</p> : null}
    </li>
  )
}

/* ------------------------------------------------------------------- display */

/** Readable key/value expansion that redacts anything secret-looking. */
export function MetadataView({ metadata }: { metadata?: Record<string, unknown> | null }) {
  const [open, setOpen] = useState(false)
  const entries = Object.entries(metadata ?? {})
  if (entries.length === 0) {
    return <p className="text-xs subtle">No metadata recorded for this event.</p>
  }
  return (
    <div className="min-w-0">
      <button type="button" className="link text-xs" onClick={() => setOpen((current) => !current)} aria-expanded={open}>
        {open ? 'Hide' : 'Show'} {entries.length} metadata {entries.length === 1 ? 'field' : 'fields'}
      </button>
      {open ? (
        <ul className="mt-2 space-y-1.5 rounded-lg border p-2.5" style={{ borderColor: 'var(--border)' }}>
          {entries.map(([key, value]) => {
            const safe = redactIfNeeded(key, value)
            return (
              <li key={key} className="flex flex-wrap items-start gap-2 text-xs">
                <span className="mono font-medium">{safe.redacted ? `${key} (redacted by the audit layer)` : key}</span>
                <span className="muted break-all">
                  {safe.redacted
                    ? '[redacted]'
                    : typeof value === 'object'
                      ? JSON.stringify(value)
                      : String(value)}
                </span>
              </li>
            )
          })}
        </ul>
      ) : null}
    </div>
  )
}

/* -------------------------------------------------------------------- policy */

type PolicyCheck = { label: string; passed: boolean }

/** Live password policy checklist — evaluates booleans only, never echoes the value. */
export function PasswordPolicyChecklist({ value, configuration }: { value: string; configuration: AuthConfiguration | null }) {
  const policy = configuration?.password_policy
  const checks: PolicyCheck[] = [
    { label: `At least ${policy?.min_length ?? 10} characters`, passed: value.length >= (policy?.min_length ?? 10) },
    { label: 'One uppercase letter', passed: (policy?.requires_uppercase ?? true) === false || /[A-Z]/.test(value) },
    { label: 'One lowercase letter', passed: (policy?.requires_lowercase ?? true) === false || /[a-z]/.test(value) },
    { label: 'One number', passed: (policy?.requires_number ?? true) === false || /[0-9]/.test(value) },
    { label: 'One symbol (@ . $ % & * _ = + - ! ? # /)', passed: (policy?.requires_symbol ?? true) === false || /[^A-Za-z0-9]/.test(value) },
    { label: 'Only supported characters', passed: !/[^A-Za-z0-9@.$%&*_=+ !?#/\-]/.test(value) },
  ]
  const satisfied = checks.every((check) => check.passed) && value.length > 0
  return (
    <div>
      <ul className="space-y-1">
        {checks.map((check) => (
          <li key={check.label} className="flex items-center gap-2 text-xs">
            {check.passed ? (
              <Check className="h-3.5 w-3.5 text-forest-600 dark:text-forest-300" aria-hidden="true" />
            ) : (
              <X className="h-3.5 w-3.5 text-alert-high" aria-hidden="true" />
            )}
            <span className={check.passed ? 'muted' : 'font-medium text-alert-high'}>{check.label}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs subtle" aria-live="polite">
        {satisfied
          ? 'Meets the server-side policy. The password is sent once and is never stored or shown by this page.'
          : 'The backend re-checks this policy; a password that fails is rejected with its own message.'}
      </p>
    </div>
  )
}

export function DurationCell({ seconds }: { seconds?: number | null }) {
  return <span className="mono text-[12px]">{seconds === null || seconds === undefined ? '—' : formatDuration(seconds)}</span>
}

export function CodeCell({ value, label }: { value?: string | null; label?: string }) {
  if (!value) return <span className="text-xs subtle">—</span>
  return (
    <span className="inline-flex items-center gap-1.5">
      <Mono>{value}</Mono>
      <CopyButton value={value} label={label ?? 'identifier'} />
    </span>
  )
}
