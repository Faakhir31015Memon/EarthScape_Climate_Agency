/**
 * The single source of truth for the query-string names these insight/response pages
 * put in links, so the analytics/history/anomalies pages and saved analyses agree.
 *
 * AnalyticsPage / HistoryPage / AnomaliesPage are owned by other engineers; every link
 * emitted from this slice uses exactly the keys declared here.
 */

export const QUERY = {
  metric: 'metric',
  start: 'start',
  end: 'end',
  /** comma-separated region codes */
  regions: 'regions',
  /** comma-separated location codes */
  locations: 'locations',
  /** single-scope convenience keys */
  region: 'region',
  location: 'location',
  frequency: 'frequency',
  groupBy: 'group_by',
  date: 'date',
  modelId: 'model_id',
  horizon: 'horizon',
  scopeType: 'scope_type',
  saved: 'saved',
  query: 'q',
} as const

export interface AnalyticsQueryParts {
  metric?: string
  start?: string
  end?: string
  regions?: string[]
  locations?: string[]
  frequency?: string
  groupBy?: string
  saved?: string
  query?: string
}

/** Build a URLSearchParams from defined parts only (drops empty/absent values). */
export function analyticsSearchParams(parts: AnalyticsQueryParts): URLSearchParams {
  const params = new URLSearchParams()
  if (parts.metric) params.set(QUERY.metric, parts.metric)
  if (parts.start) params.set(QUERY.start, parts.start)
  if (parts.end) params.set(QUERY.end, parts.end)
  if (parts.regions?.length) params.set(QUERY.regions, parts.regions.join(','))
  if (parts.locations?.length) params.set(QUERY.locations, parts.locations.join(','))
  if (parts.frequency) params.set(QUERY.frequency, parts.frequency)
  if (parts.groupBy) params.set(QUERY.groupBy, parts.groupBy)
  if (parts.saved) params.set(QUERY.saved, parts.saved)
  if (parts.query) params.set(QUERY.query, parts.query)
  return params
}

function toList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item))
  if (typeof value === 'string' && value.trim()) return value.split(',').map((item) => item.trim()).filter(Boolean)
  return []
}

function toStringValue(value: unknown): string | undefined {
  if (typeof value === 'string' && value.trim()) return value
  if (typeof value === 'number') return String(value)
  return undefined
}

/**
 * Map a saved analysis's stored filters (a loose Record) onto the standardised query
 * parts. Unknown keys are ignored rather than guessed. Tolerates both the plural arrays
 * (regions/locations) and the single-scope keys.
 */
export function savedFiltersToParts(filters: Record<string, unknown>): AnalyticsQueryParts {
  const regions = [...toList(filters[QUERY.regions]), ...toList(filters.region), ...toList(filters.region_code)]
  const locations = [...toList(filters[QUERY.locations]), ...toList(filters.location), ...toList(filters.location_code)]
  const range = filters.range && typeof filters.range === 'object' ? (filters.range as Record<string, unknown>) : filters
  return {
    metric: toStringValue(filters[QUERY.metric]) ?? toStringValue(filters.target) ?? toStringValue(filters.target_variable),
    start: toStringValue(range.start) ?? toStringValue(filters.start_date),
    end: toStringValue(range.end) ?? toStringValue(filters.end_date),
    regions: [...new Set(regions)],
    locations: [...new Set(locations)],
    frequency: toStringValue(filters[QUERY.frequency]),
    groupBy: toStringValue(filters[QUERY.groupBy]),
    query: toStringValue(filters[QUERY.query]),
  }
}
