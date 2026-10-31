/**
 * Pure helpers for the /admin slice.
 *
 * Two jobs: (1) tolerate the wrapper shapes the Django views actually return
 * (`{"account": …}` next to a bare object) without touching the typed API client,
 * and (2) keep secret material out of the administration screens — the backend already
 * masks secrets (`env.mask_value`) and redacts audit metadata (`common/audit.py`), the
 * UI repeats that guarantee locally instead of trying to reveal anything.
 */
import { ApiRequestError } from '../../../services/http'
import type { Account, Role } from '../../../types/api'

export const PAGE_SIZE = 25

const WRAPPER_KEYS = [
  'account',
  'dataset',
  'job',
  'rule',
  'ticket',
  'feedback',
  'report',
  'model',
  'prediction',
  'notification',
  'settings',
] as const

/** `{"account": {...}}` → the account; a bare object is returned untouched. */
export function unwrapOne<T>(raw: unknown): T {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const record = raw as Record<string, unknown>
    for (const key of WRAPPER_KEYS) {
      const value = record[key]
      if (value && typeof value === 'object') return value as T
    }
  }
  return raw as T
}

/** Human note some mutations return (`{"note": "…", "status": "…"}`). */
export function noteFrom(raw: unknown): string | null {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const note = typeof record.note === 'string' ? record.note : null
  const detail = typeof record.detail === 'string' ? record.detail : null
  const status = typeof record.status === 'string' ? record.status : null
  return note ?? detail ?? (status ? humaniseStatus(status) : null)
}

function humaniseStatus(status: string): string {
  // split/join instead of String.prototype.replaceAll: the project's tsconfig targets
  // lib ES2020, where replaceAll is not typed.
  return status.split('_').join(' ')
}

export interface AdminError {
  message: string
  code: string
  status: number
  requestId?: string
  details?: unknown
  fields: Record<string, string>
  /** A 404 from a route the platform does not serve (module still being built). */
  missingRoute: boolean
  configuration: boolean
}

export function describeError(error: unknown): AdminError {
  if (error instanceof ApiRequestError) {
    return {
      message: error.message,
      code: error.code,
      status: error.status,
      requestId: error.requestId,
      details: error.details,
      fields: flattenDetails(error.details),
      missingRoute: error.status === 404,
      configuration: error.code === 'service_unavailable' || error.code === 'configuration_required' || error.status === 503,
    }
  }
  const message = error instanceof Error ? error.message : 'The request could not be completed.'
  return { message, code: 'unexpected_error', status: 0, fields: {}, missingRoute: false, configuration: false }
}

function flattenDetails(details: unknown): Record<string, string> {
  if (!details) return {}
  if (Array.isArray(details)) {
    return { _: details.map((item) => String(item)).join(' ') }
  }
  if (typeof details === 'string') return { _: details }
  if (typeof details !== 'object') return {}
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(details as Record<string, unknown>)) {
    if (Array.isArray(value)) out[key] = value.map((item) => String(item)).join(' ')
    else if (value && typeof value === 'object') out[key] = JSON.stringify(value)
    else out[key] = String(value)
  }
  return out
}

/**
 * Key names the audit trail redacts server-side (`common/audit.py` REDACTED_KEYS) plus a
 * few obvious local guards. The UI never attempts to reveal a masked value.
 */
const SECRET_KEY_PARTS = [
  'password',
  'token',
  'authorization',
  'api_key',
  'apikey',
  'secret',
  'connection_string',
  'mongodb://',
  'mongodb+srv',
  'cookie',
  'credential',
  'refresh',
  'access_key',
]

export function looksSecret(key: string): boolean {
  const needle = key.toLowerCase()
  return SECRET_KEY_PARTS.some((part) => needle.includes(part))
}

/** Values that already look like a backend mask (`configured (abc…64 chars)`). */
export function isMaskedValue(value: unknown): boolean {
  return typeof value === 'string' && /^configured\s*\(/i.test(value.trim())
}

export function redactIfNeeded(key: string, value: unknown): { value: unknown; redacted: boolean } {
  if (looksSecret(key)) return { value: '[redacted]', redacted: true }
  if (typeof value === 'string' && /mongodb(\+srv)?:\/\//.test(value)) return { value: '[redacted]', redacted: true }
  return { value, redacted: false }
}

/** `env.mask_value` output or a boolean flag → configured / not configured. */
export function maskedConfigured(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (typeof value !== 'string') return null
  const text = value.trim().toLowerCase()
  if (!text) return false
  if (text.startsWith('not configured') || text === 'none' || text === 'false' || text === 'disabled') return false
  if (text.startsWith('configured')) return true
  return null
}

export function statusBreakdown(counts: Record<string, number> | undefined): { key: string; value: number }[] {
  if (!counts) return []
  return Object.entries(counts)
    .map(([key, value]) => ({ key, value: Number(value) || 0 }))
    .filter((entry) => entry.value > 0)
    .sort((a, b) => b.value - a.value)
}

export function breakdownTotal(counts: Record<string, number> | undefined): number {
  return statusBreakdown(counts).reduce((total, entry) => total + entry.value, 0)
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours < 48) return `${hours}h ${minutes}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

/** Countdown to a lockout expiry, phrased the way the backend throttles login. */
export function lockCountdown(lockedUntil: string | null | undefined, now: number): string | null {
  if (!lockedUntil) return null
  const expiry = new Date(lockedUntil).getTime()
  if (Number.isNaN(expiry)) return null
  const remaining = Math.round((expiry - now) / 1000)
  if (remaining <= 0) return null
  const minutes = Math.floor(remaining / 60)
  const seconds = remaining % 60
  return minutes > 0 ? `${minutes}m ${seconds}s remaining` : `${seconds}s remaining`
}

export const ROLE_VALUES: Role[] = ['administrator', 'climate_analyst', 'viewer']

export const ROLE_LABEL: Record<Role, string> = {
  administrator: 'Administrator',
  climate_analyst: 'Climate analyst',
  viewer: 'Viewer',
}

export function roleLabel(role?: string | null): string {
  if (!role) return 'Unknown role'
  return ROLE_LABEL[role as Role] ?? role.split('_').join(' ')
}

export function initial(name?: string | null): string {
  const clean = (name ?? '').trim()
  if (!clean) return '?'
  return clean
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join('')
}

export function matchesUser(account: Account, term: string): boolean {
  const needle = term.trim().toLowerCase()
  if (!needle) return true
  return (
    account.full_name.toLowerCase().includes(needle) ||
    account.email.toLowerCase().includes(needle) ||
    (account.organisation ?? '').toLowerCase().includes(needle)
  )
}

/** Stable copy for the "server refused" line — verbatim message, never reworded. */
export function serverRejection(error: unknown): string {
  const described = describeError(error)
  const suffix = described.requestId ? ` (ref ${described.requestId})` : ''
  return `${described.message}${suffix}`
}
