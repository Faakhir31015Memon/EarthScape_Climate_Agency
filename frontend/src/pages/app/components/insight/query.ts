/**
 * The single source of truth for the query-string names these insight/response pages
 * put in links, so the analytics/history/anomalies pages agree.
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
  query: 'q',
} as const
