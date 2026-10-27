/**
 * Helpers for the public (unauthenticated) pages.
 *
 * Two problems are solved here:
 *
 * 1. Some public endpoints return either a bare array or an object envelope
 *    (``{ "metrics": [...] }``, ``{ "regions": [...] }``). The typed client in
 *    ``services/api.ts`` declares the bare-array shape, so every public page
 *    normalises through these functions instead of assuming one layout.
 * 2. Several fields the backend documents with different names
 *    (``range_min``/``range_max``, ``statistics``) are read straight from the
 *    payload here; the UI only ever shows a range or count the backend actually
 *    publishes — anything the server leaves ``null`` stays ``null``.
 */
import type { AuthConfiguration, RegionCatalogLocation } from '../../../types/api'

/**
 * Defaults documented by ``backend/apps/authentication/services.py``
 * (``LOGIN_MAX_FAILED_ATTEMPTS`` = 8, ``LOGIN_LOCKOUT_MINUTES`` = 15).
 * The server is authoritative; this only drives the on-screen hint.
 */
export const LOGIN_LOCKOUT = { maxFailedAttempts: 8, minutes: 15 } as const

/** bcrypt caps the password at 72 bytes (``backend/common/security.py``). */
export const MAX_PASSWORD_LENGTH = 72

const DEFAULT_POLICY: AuthConfiguration['password_policy'] = {
  min_length: 10,
  requires_uppercase: true,
  requires_lowercase: true,
  requires_number: true,
  requires_symbol: true,
}

/** Characters the backend accepts; anything else is rejected before hashing. */
const UNSUPPORTED_CHARACTER = /[^A-Za-z0-9@.$%&*_=+!?#-]/

export function asArray<T>(payload: unknown, key: string): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const value = (payload as Record<string, unknown>)[key]
    if (Array.isArray(value)) return value as T[]
  }
  return []
}

function numberOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export interface PublicRegion {
  code: string
  name: string
  country: string | null
  climate_zone: string | null
  centroid: [number, number] | null
  location_count: number
  locations: RegionCatalogLocation[]
  observationCount: number | null
  firstDate: string | null
  lastDate: string | null
  hasData: boolean
  reason: string | null
}

/** Accepts ``Region[]`` or ``{ regions: [...] }`` and reads the ``statistics`` block. */
export function normaliseRegions(payload: unknown): PublicRegion[] {
  return asArray<Record<string, unknown>>(payload, 'regions')
    .map((entry) => {
      const code = typeof entry.code === 'string' ? entry.code : ''
      if (!code) return null
      const statistics = (entry.statistics ?? {}) as Record<string, unknown>
      const rawLocations = Array.isArray(entry.locations) ? (entry.locations as Record<string, unknown>[]) : []
      const observationCount = numberOrNull(statistics.observation_count)
      const locations: RegionCatalogLocation[] = rawLocations.map((location) => ({
        code: String(location.code ?? ''),
        name: String(location.name ?? location.code ?? ''),
        latitude: numberOrNull(location.latitude),
        longitude: numberOrNull(location.longitude),
        station_id: typeof location.station_id === 'string' ? location.station_id : undefined,
      }))
      const centroid: [number, number] | null =
        Array.isArray(entry.centroid) && typeof entry.centroid[0] === 'number' && typeof entry.centroid[1] === 'number'
          ? [entry.centroid[0], entry.centroid[1]]
          : null
      const region: PublicRegion = {
        code,
        name: typeof entry.name === 'string' && entry.name ? entry.name : code,
        country: typeof entry.country === 'string' ? entry.country : null,
        climate_zone: typeof entry.climate_zone === 'string' ? entry.climate_zone : null,
        centroid,
        locations,
        location_count: typeof entry.location_count === 'number' ? entry.location_count : locations.length,
        observationCount,
        firstDate: typeof statistics.first_date === 'string' ? statistics.first_date : null,
        lastDate: typeof statistics.last_date === 'string' ? statistics.last_date : null,
        hasData: observationCount !== null && observationCount > 0,
        reason:
          typeof statistics.reason === 'string'
            ? statistics.reason
            : typeof entry.reason === 'string'
              ? entry.reason
              : null,
      }
      return region
    })
    .filter((entry): entry is PublicRegion => entry !== null)
}

/** Sum of per-region observation counts, or ``null`` when nothing is reported. */
export function observationsFromRegions(regions: PublicRegion[]): number | null {
  const counts = regions.map((region) => region.observationCount).filter((value): value is number => value !== null)
  if (!counts.length) return null
  const total = counts.reduce((accumulator, value) => accumulator + value, 0)
  return total > 0 ? total : null
}

export function locationCount(regions: PublicRegion[]): number {
  return regions.reduce((accumulator, region) => accumulator + region.location_count, 0)
}

export interface PasswordRule {
  id: string
  label: string
  satisfied: (value: string) => boolean
}

export function passwordRules(policy?: AuthConfiguration['password_policy'] | null): PasswordRule[] {
  const active = policy ?? DEFAULT_POLICY
  const rules: PasswordRule[] = [
    {
      id: 'length',
      label: `At least ${active.min_length} characters`,
      satisfied: (value) => value.length >= active.min_length,
    },
  ]
  if (active.requires_uppercase) {
    rules.push({ id: 'upper', label: 'One uppercase letter', satisfied: (value) => /[A-Z]/.test(value) })
  }
  if (active.requires_lowercase) {
    rules.push({ id: 'lower', label: 'One lowercase letter', satisfied: (value) => /[a-z]/.test(value) })
  }
  if (active.requires_number) {
    rules.push({ id: 'number', label: 'One number', satisfied: (value) => /[0-9]/.test(value) })
  }
  if (active.requires_symbol ?? true) {
    rules.push({
      id: 'symbol',
      label: 'One symbol (@ . $ % & * _ = + - ! ? # /)',
      satisfied: (value) => /[^A-Za-z0-9]/.test(value),
    })
  }
  rules.push({
    id: 'characters',
    label: 'Only letters, numbers and @ . $ % & * _ = + - ! ? # /',
    satisfied: (value) => !UNSUPPORTED_CHARACTER.test(value),
  })
  rules.push({
    id: 'bytes',
    label: `72 bytes or fewer`,
    satisfied: (value) => new TextEncoder().encode(value).length <= MAX_PASSWORD_LENGTH,
  })
  return rules
}

export function passwordPolicySummary(policy?: AuthConfiguration['password_policy'] | null): string {
  const active = policy ?? DEFAULT_POLICY
  const parts = [`at least ${active.min_length} characters`]
  if (active.requires_uppercase) parts.push('one uppercase letter')
  if (active.requires_lowercase) parts.push('one lowercase letter')
  if (active.requires_number) parts.push('one number')
  if (active.requires_symbol ?? true) parts.push('one symbol')
  return parts.join(', ')
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}
