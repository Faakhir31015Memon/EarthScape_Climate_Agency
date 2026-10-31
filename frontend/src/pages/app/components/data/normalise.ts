/**
 * Defensive normalisers shared by the monitoring / data-pipeline pages.
 *
 * The typed client in `services/api.ts` is the contract, but backend modules are
 * landing concurrently and a few endpoints currently wrap arrays in an envelope
 * object (e.g. `/climate/regions` returning `{regions: [...]}`) or shape
 * dashboard tiles slightly differently. These helpers accept the declared shape
 * first and only unwrap what the API really returned — they never invent values.
 */
import type {
  Anomaly,
  CoverageReport,
  ForecastSummaryItem,
  Metric,
  RegionAggregate,
} from '../../../../types/api'
import { formatNumber, humanise } from '../../../../lib/format'

// generic

export function asList<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[]
  if (value && typeof value === 'object') {
    const nested = (value as Record<string, unknown>)[key]
    if (Array.isArray(nested)) return nested as T[]
  }
  return []
}

export function unwrap<T>(value: unknown, key: string): T {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const nested = (value as Record<string, unknown>)[key]
    if (nested && typeof nested === 'object') return nested as T
  }
  return value as T
}

export function numberOrNull(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return null
}

export function joinCsv(values: string[]): string | undefined {
  return values.length ? values.join(',') : undefined
}

export function parseCsv(raw: string | null | undefined): string[] {
  if (!raw) return []
  return raw
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

export function formatDuration(seconds: number | null | undefined): string {
  const value = numberOrNull(seconds)
  if (value === null) return '—'
  const total = Math.max(0, Math.round(value))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`
}

// freshness

export function freshnessTone(status?: string | null): 'success' | 'warning' | 'danger' | 'neutral' {
  switch ((status ?? '').toLowerCase()) {
    case 'fresh':
    case 'ok':
    case 'current':
      return 'success'
    case 'lagging':
    case 'recent':
      return 'warning'
    case 'stale':
      return 'danger'
    default:
      return 'neutral'
  }
}

export function dateFreshness(iso: string | null | undefined): 'fresh' | 'lagging' | 'stale' | 'unknown' {
  if (!iso) return 'unknown'
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return 'unknown'
  const days = (Date.now() - parsed.getTime()) / 86_400_000
  if (days <= 3) return 'fresh'
  if (days <= 14) return 'lagging'
  return 'stale'
}

// trend

export interface ReadyTrend {
  kind: 'ready'
  series: import('../../../../types/api').SeriesPoint[]
  unit: string
  label?: string
  metric: Metric
  frequency?: string
  coverage?: CoverageReport
  note?: string
  observations_evaluated?: number
}

export interface UnavailableTrend {
  kind: 'unavailable'
  reason: string
  available_points?: number
  required_points?: number
}

export type TrendOutcome = ReadyTrend | UnavailableTrend

/**
 * Interprets a TrendResponse (or the dashboard's `{available:false, reason}`
 * variant) without fabricating a series: an empty series is treated as
 * unavailable with the backend's own reason text.
 */
export function trendOutcome(
  payload: unknown,
  fallbackMetric: Metric,
): TrendOutcome {
  if (!payload || typeof payload !== 'object') {
    return { kind: 'unavailable', reason: 'No trend data is available for this panel yet.' }
  }
  const record = payload as Record<string, unknown>
  const series = Array.isArray(record.series) ? (record.series as ReadyTrend['series']) : []
  const metric = (typeof record.metric === 'string' ? record.metric : fallbackMetric) as Metric
  if (record.available === false || series.length === 0) {
    const reason =
      typeof record.reason === 'string' && record.reason
        ? record.reason
        : typeof record.note === 'string' && record.note
          ? record.note
          : 'There are not enough stored observations for this selection yet.'
    return {
      kind: 'unavailable',
      reason,
      available_points: numberOrNull(record.available_points) ?? (record.available === false ? 0 : series.length),
      required_points: numberOrNull(record.required_points) ?? undefined,
    }
  }
  return {
    kind: 'ready',
    series,
    metric,
    unit: typeof record.unit === 'string' ? record.unit : '',
    label: typeof record.label === 'string' ? record.label : undefined,
    frequency: typeof record.frequency === 'string' ? record.frequency : undefined,
    coverage: (record.coverage && typeof record.coverage === 'object' ? record.coverage : undefined) as CoverageReport | undefined,
    note: typeof record.note === 'string' ? record.note : undefined,
    observations_evaluated: numberOrNull(record.observations_evaluated) ?? undefined,
  }
}

// dashboard sections

export interface ForecastItemView {
  id: string
  model_name: string
  target: string
  unit: string
  scope: string
  horizon_days: number | null
  value: number | null
  generated_at: string | null
}

/** Accepts ForecastSummaryItem[] or the `{available, items, reason}` envelope. */
export function forecastItems(value: unknown): { items: ForecastItemView[]; reason: string | null } {
  const list = asList<Record<string, unknown>>(value, 'items')
  const reason =
    value && typeof value === 'object' && !Array.isArray(value) && typeof (value as Record<string, unknown>).reason === 'string'
      ? String((value as Record<string, unknown>).reason)
      : null
  const items = list.map((raw): ForecastItemView => ({
    id: String(raw.prediction_id ?? raw.id ?? ''),
    model_name: String(raw.model_name ?? '—'),
    target: String(raw.target_variable ?? raw.target ?? ''),
    unit: String(raw.unit ?? ''),
    scope: String(raw.scope ?? raw.region_code ?? raw.location_code ?? 'global'),
    horizon_days: numberOrNull(raw.horizon_days),
    value: numberOrNull(raw.first_value ?? raw.predicted_value ?? raw.last_value),
    generated_at: typeof raw.generated_at === 'string' ? raw.generated_at : null,
  }))
  return { items, reason }
}

/** Alias kept for pages that describe a forecast-summary row before normalisation. */
export type ForecastSummaryShape = ForecastSummaryItem

/** Normalises regional_comparison into RegionAggregate rows for the bar chart. */
export function regionAggregates(comparison: unknown): { regions: RegionAggregate[]; unit: string; label: string; reason: string | null } {
  if (!comparison || typeof comparison !== 'object') {
    return { regions: [], unit: '', label: '', reason: null }
  }
  const record = comparison as Record<string, unknown>
  const direct = asList<RegionAggregate>(record.regions, 'regions')
  if (direct.length) {
    return {
      regions: direct,
      unit: typeof record.unit === 'string' ? record.unit : '',
      label: typeof record.label === 'string' ? record.label : '',
      reason: typeof record.reason === 'string' ? record.reason : null,
    }
  }
  const ranked = asList<Record<string, unknown>>(record, 'ranked_regions')
  if (ranked.length) {
    return {
      regions: ranked.map((row) => ({
        region_code: String(row.region_code ?? ''),
        mean: numberOrNull(row.mean_temperature_c ?? row.mean),
        median: numberOrNull(row.median),
        min: numberOrNull(row.min_temperature_c ?? row.min),
        max: numberOrNull(row.max_temperature_c ?? row.max),
        std: numberOrNull(row.std),
        observation_count: numberOrNull(row.observation_count) ?? 0,
        record_days: numberOrNull(row.record_days) ?? undefined,
        aggregation: 'mean' as const,
      })),
      unit: typeof record.unit === 'string' ? record.unit : '°C',
      label: typeof record.label === 'string' ? record.label : 'Mean temperature',
      reason: typeof record.reason === 'string' ? record.reason : null,
    }
  }
  return {
    regions: [],
    unit: typeof record.unit === 'string' ? record.unit : '',
    label: typeof record.label === 'string' ? record.label : '',
    reason: typeof record.reason === 'string' ? record.reason : null,
  }
}

export function anomalyList(value: unknown): Anomaly[] {
  const list = asList<Anomaly>(value, 'items')
  return list
}

/** Accepts a CoverageReport directly or the dashboard's `{coverage: ...}` envelope. */
export function coverageFrom(value: unknown): CoverageReport | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  if ('completeness' in record || 'observation_count' in record) return value as CoverageReport
  if (record.coverage && typeof record.coverage === 'object') return record.coverage as CoverageReport
  return null
}

// quick actions

export interface QuickActionView {
  id: string
  label: string
  to: string
  enabled: boolean
  disabled_reason?: string | null
}

const REAL_ROUTES = new Set([
  '/app',
  '/app/analytics',
  '/app/history',
  '/app/map',
  '/app/anomalies',
  '/app/datasets',
  '/app/uploads',
  '/app/ingestion',
  '/app/processing',
  '/app/big-data',
  '/app/models',
  '/app/forecasts',
  '/app/alerts',
  '/app/notifications',
  '/app/reports',
  '/app/support',
  '/admin',
  '/admin/alert-rules',
])

/**
 * Backend quick-actions reference API-side route names; these are the only
 * allowed translations into routes that actually exist in `app/router.tsx`.
 * Anything unmappable is dropped.
 */
const ROUTE_ALIASES: Record<string, string> = {
  '/climate/explorer': '/app/analytics',
  '/climate/map': '/app/map',
  '/climate/anomalies': '/app/anomalies',
  '/climate/observations': '/app/history',
  '/analytics': '/app/analytics',
  '/support/new': '/app/support',
  '/ingestion': '/app/ingestion',
  '/datasets/upload': '/app/uploads',
  '/datasets': '/app/datasets',
  '/processing': '/app/processing',
  '/alerts': '/app/alerts',
  '/models': '/app/models',
}

const LABEL_ROUTE_HINTS: [RegExp, string][] = [
  [/ingest/i, '/app/ingestion'],
  [/upload/i, '/app/uploads'],
  [/\bmap\b/i, '/app/map'],
  [/anomal/i, '/app/anomalies'],
  [/train|model/i, '/app/models'],
  [/ticket|support|question/i, '/app/support'],
  [/export|observation/i, '/app/history'],
  [/aggregat|analysis|analytic|trend|explor/i, '/app/analytics'],
]

function resolveRoute(route?: string, label?: string): string | null {
  if (route) {
    if (REAL_ROUTES.has(route)) return route
    const alias = ROUTE_ALIASES[route]
    if (alias) return alias
  }
  if (label) {
    for (const [pattern, to] of LABEL_ROUTE_HINTS) {
      if (pattern.test(label)) return to
    }
  }
  return null
}

export function normaliseQuickActions(actions: unknown): QuickActionView[] {
  if (!Array.isArray(actions)) return []
  return actions.flatMap((action): QuickActionView[] => {
    if (typeof action === 'string') {
      const to = resolveRoute(undefined, action)
      return to ? [{ id: action, label: action, to, enabled: true }] : []
    }
    if (action && typeof action === 'object') {
      const record = action as Record<string, unknown>
      const label = String(record.label ?? record.id ?? record.name ?? '')
      if (!label) return []
      const to = resolveRoute(typeof record.route === 'string' ? record.route : undefined, label)
      if (!to) return []
      return [
        {
          id: String(record.id ?? label),
          label,
          to,
          enabled: record.enabled !== false,
          disabled_reason: typeof record.disabled_reason === 'string' ? record.disabled_reason : null,
        },
      ]
    }
    return []
  })
}

// stats

export interface CountedStat {
  label: string
  value: number
  parent?: string
}

/**
 * Flattens the untyped `Record<string, unknown>` stat endpoints into numeric
 * cards. Only values the API actually returned are shown, nothing is defaulted
 * to zero.
 */
export function numberEntries(record: Record<string, unknown> | null | undefined, max = 8): CountedStat[] {
  if (!record) return []
  const out: CountedStat[] = []
  Object.entries(record).forEach(([key, value]) => {
    const direct = numberOrNull(value)
    if (direct !== null) {
      out.push({ label: humanise(key), value: direct })
      return
    }
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      Object.entries(value as Record<string, unknown>).forEach(([inner, innerValue]) => {
        const numeric = numberOrNull(innerValue)
        if (numeric !== null) out.push({ label: humanise(inner), value: numeric, parent: humanise(key) })
      })
    }
  })
  return out.sort((left, right) => right.value - left.value).slice(0, max)
}

export function formatCount(value: number): string {
  return formatNumber(value, 0)
}
