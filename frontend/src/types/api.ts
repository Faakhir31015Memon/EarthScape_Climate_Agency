/**
 * Typed API contract for the EarthScape Climate Agency backend.
 *
 * Every shape mirrors the measured response of the running Django/Mongo backend
 * (see BACKEND_CONTRACT.md and the presenters in `backend/apps/**`). Fields are
 * nullable only where the backend genuinely returns `null`; envelopes such as
 * `{dataset}` / `{account}` / `{rule}` are unwrapped in `services/api.ts`, so the
 * types here describe the payload pages receive.
 */

export type Role = 'administrator' | 'climate_analyst' | 'viewer'

export type JobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'completed_with_errors'
  | 'failed'
  | 'cancelled'

export type DatasetStatus =
  | 'draft'
  | 'validating'
  | 'validated'
  | 'ingesting'
  | 'ready'
  | 'failed'
  | 'archived'

export type SourceType = 'nasa_power' | 'noaa' | 'live_api' | 'university_upload' | 'sample'

export type Metric =
  | 'temperature_c'
  | 'temperature_min_c'
  | 'temperature_max_c'
  | 'precipitation_mm'
  | 'relative_humidity_pct'
  | 'wind_speed_ms'
  | 'wind_direction_deg'
  | 'surface_pressure_hpa'
  | 'cloud_coverage_pct'
  | 'solar_radiation_kwh_m2'

/** Trend bucketing. The backend vocabulary is `annual`, never `yearly`. */
export type Frequency = 'daily' | 'weekly' | 'monthly' | 'annual'

export type GroupBy = 'none' | 'region' | 'location' | 'country'

/** Alert-rule severities (`info|warning|critical`) plus anomaly detector severities. */
export type Severity = 'info' | 'warning' | 'critical' | 'low' | 'medium' | 'high'

export interface Paginated<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  pages: number
  has_next: boolean
  has_previous: boolean
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, string> | string[] | unknown
    request_id?: string
  }
}

/* --------------------------------------------------------------------- auth */

export interface Account {
  id: string
  full_name: string
  email: string
  email_normalized: string
  role: Role
  is_active: boolean
  email_verified: boolean
  organisation?: string | null
  preferences: UserPreferences
  created_at: string
  created_by?: string | null
  updated_at?: string
  last_login_at?: string | null
  password_changed_at?: string
  failed_login_count: number
  is_locked: boolean
  locked_until?: string | null
  sessions_active?: number
}

export interface UserPreferences {
  theme: 'light' | 'dark'
  units: { temperature: string; precipitation: string; wind: string }
  notification_channels: { in_app: boolean; email: boolean }
  dashboard_region?: string | null
  density?: 'comfortable' | 'compact'
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: 'Bearer'
  access_expires_at: string
  refresh_expires_at: string
  user: Account
}

/** `POST /auth/register` returns the token pair plus the fresh profile. */
export interface RegisterResponse extends TokenPair {
  account: Account
}

export interface AuthConfiguration {
  self_registration_enabled: boolean
  registration_roles: Role[]
  password_reset_enabled: boolean
  email_delivery: boolean
  password_policy: {
    min_length: number
    requires_uppercase: boolean
    requires_lowercase: boolean
    requires_number: boolean
    requires_symbol?: boolean
  }
  access_token_minutes: number
  refresh_token_days: number
}

export interface CurrentUserResponse {
  account: Account
  capabilities: string[]
}

export interface StatusNoteResponse {
  status: string
  note: string
}

export interface PasswordResetRequestResponse {
  status: string
  delivered: boolean
  note?: string
}

export interface LogoutResponse {
  status: 'logged_out'
  sessions_ended: boolean
}

/* ------------------------------------------------------------------ climate */

/** An entry of `GET /climate/metrics` (services.metric_catalogue). */
export interface MetricDefinition {
  field: Metric
  label: string
  unit: string
  aggregation: 'mean' | 'sum'
  range_min: number | null
  range_max: number | null
  missing_encoding: string[]
  populated_count: number
  total_count: number
  completeness_pct: number
  has_data: boolean
  source_parameters: string[]
}

export interface MetricCatalogueResponse {
  metrics: MetricDefinition[]
  frequencies: Frequency[]
  group_by_options: GroupBy[]
  quality_options: string[]
  anomaly_methods: string[]
  sorts: string[]
  aggregation_rules: Record<string, string>
  missing_value_policy: string
  available_count: number
  generated_at: string
}

export interface Observation {
  id?: string
  observation_date: string
  year: number
  month: number
  day: number
  location_code: string
  location_name?: string
  region_code: string
  country?: string
  latitude: number
  longitude: number
  temperature_c?: number | null
  temperature_min_c?: number | null
  temperature_max_c?: number | null
  precipitation_mm?: number | null
  relative_humidity_pct?: number | null
  wind_speed_ms?: number | null
  wind_direction_deg?: number | null
  surface_pressure_hpa?: number | null
  cloud_coverage_pct?: number | null
  solar_radiation_kwh_m2?: number | null
  source_name?: string
  source_type?: SourceType
  dataset_id?: string | null
  dataset_name?: string | null
  quality_flags?: string[]
  is_flagged?: boolean
  dedupe_key?: string
  provider_record_id?: string | null
  observed_at?: string | null
  ingested_at?: string
  schema_version?: number
  provenance?: Record<string, unknown>
}

export interface ObservationsListResponse extends Paginated<Observation> {
  sort: string
  applied_filters: {
    countries: string[]
    datasets: string[]
    end: string | null
    location_codes: string[]
    metric: string | null
    quality: string | null
    regions: string[]
    start: string | null
  }
  available_sorts: string[]
}

export interface SeriesPoint {
  period: string
  value: number | null
  observation_count: number
  min?: number | null
  max?: number | null
  std?: number | null
  aggregation?: 'mean' | 'sum'
  region_code?: string
  location_code?: string
  country?: string
}

export interface CoverageReport {
  observation_count: number
  completeness: Record<string, { populated: number; missing: number; pct: number }>
  flagged: Record<string, number>
  flagged_row_count?: number
  flagged_row_pct?: number
  date_range: { start: string; end: string } | null
  locations: number
  regions: number
  datasets: number
  sources: string[]
}

export interface TrendScope {
  start: string | null
  end: string | null
  regions: string[]
  location_codes: string[]
}

/**
 * `GET /climate/trends` (trend_payload). The backend always returns `series`
 * (empty when unavailable) plus an `available`/`reason` pair.
 */
export interface TrendResponse {
  metric: Metric
  label: string
  unit: string
  frequency: Frequency
  group_by: GroupBy
  series: SeriesPoint[]
  point_count: number
  coverage?: CoverageReport
  first_period?: string | null
  last_period?: string | null
  note?: string
  available: boolean
  reason?: string
  available_points?: number
  required_points?: number
  observations_evaluated?: number
  matched_observations?: number
  scope?: TrendScope
  truncated?: boolean
}

/** Per-metric aggregate inside `Region.statistics.metrics`. */
export interface MetricAggregate {
  label: string
  unit: string
  aggregation: 'mean' | 'sum'
  populated: number
  mean: number | null
  max: number | null
  min: number | null
}

export interface RegionStatistics {
  observation_count: number
  first_date: string | null
  last_date: string | null
  first_year?: number | null
  last_year?: number | null
  location_count?: number
  location_codes?: string[]
  source_count?: number
  metrics: Partial<Record<Metric, MetricAggregate>>
  reason?: string
}

/** Catalogue location from `GET /climate/regions`. */
export interface RegionCatalogLocation {
  code: string
  name: string
  latitude: number | null
  longitude: number | null
  station_id?: string | null
}

/** `GET /climate/regions` item. The aggregate key is `statistics`, not `aggregates`. */
export interface Region {
  code: string
  name: string
  country: string | null
  climate_zone: string | null
  centroid: [number, number] | null
  location_count: number
  locations: RegionCatalogLocation[]
  statistics: RegionStatistics
  available: boolean
  geography_source: string
}

export interface RegionsListResponse {
  regions: Region[]
  region_count: number
  observed_region_codes: string[]
  catalogue_source: string
  aggregation_pipeline: string[]
  scope: { start: string | null; end: string | null; regions: string[] }
  available: boolean
  reason: string | null
  generated_at: string
}

/** `GET /climate/regions/{code}` (region_summary). */
export interface RegionSummaryLocation extends RegionCatalogLocation {
  observation_count: number
  first_observation_date: string | null
  last_observation_date: string | null
  lag_days: number | null
}

export interface RegionDetailResponse {
  region: {
    code: string
    name: string
    country: string | null
    climate_zone: string | null
    centroid: [number, number] | null
    source: string
  }
  statistics: RegionStatistics
  location_count: number
  locations: RegionSummaryLocation[]
  temperature_trend: TrendResponse
  available: boolean
  generated_at: string
}

export interface Anomaly {
  observation_date: string
  metric: Metric
  metric_label: string
  unit: string
  value: number
  deviation_score: number
  direction: 'above' | 'below'
  severity: Severity
  method: string
  baseline?: Record<string, unknown>
  region_code?: string
  location_code?: string
  location_name?: string
  latitude?: number | null
  longitude?: number | null
  dataset_id?: string | null
  source_name?: string
  explanation: string
  caveat?: string
  /** present when the anomaly was persisted as an alert candidate */
  id?: string
  status?: string
  created_at?: string
}

export interface AnomalyResponse {
  metric: Metric
  metric_label: string
  unit: string
  method: string
  direction: string
  baseline: Record<string, unknown> | null
  evaluated_observations?: number
  observations_evaluated?: number
  date_range: { start: string | null; end: string | null }
  anomaly_count: number
  severity_counts: Record<string, number>
  anomalies: Anomaly[]
  truncated?: boolean
  caveat?: string
  note?: string
  available: boolean
  reason?: string
  available_points?: number
  required_points?: number
  scope?: Record<string, unknown>
}

/** `POST /climate/anomalies/scan` (the only real anomaly-scan route). */
export interface AnomalyScanSummary {
  dry_run: boolean
  summary_key?: string
  summary_written?: boolean
  anomalies_detected: number
  alerts_persisted: number
  alerts_already_present: number
  alerts_over_limit?: number
  persist_limit?: number
  alert_ids?: string[]
  severity_counts: Record<string, number>
  evaluated_observations?: number
  workflow?: string
  generated_at?: string
}

export interface AnomalyScanResponse {
  scan: AnomalyScanSummary
  detection: {
    metric: Metric
    method: string
    anomaly_count: number
    severity_counts: Record<string, number>
    evaluated_observations?: number
    baseline: Record<string, unknown> | null
    date_range: { start: string | null; end: string | null }
    available?: boolean
    reason?: string | null
    caveat?: string
  }
  sample_anomalies: Anomaly[]
}

/** A row of `GET /climate/comparison` (comparison_payload). */
export interface RegionAggregate {
  region_code: string
  mean: number | null
  median?: number | null
  min: number | null
  max: number | null
  std?: number | null
  observation_count: number
  record_days?: number
  aggregation: 'mean' | 'sum'
}

export interface RegionComparison {
  metric: Metric
  label: string
  unit: string
  frequency: Frequency
  regions: RegionAggregate[]
  spread?: { highest_region?: string; lowest_region?: string; difference?: number | null }
  available: boolean
  reason?: string
  observations_evaluated: number
  scope?: Record<string, unknown>
  series: SeriesPoint[]
}

export interface CorrelationCell {
  x: string
  y: string
  coefficient: number
  sample_size: number
  strength: 'strong' | 'moderate' | 'weak' | 'negligible'
}

export interface CorrelationResponse {
  metrics: string[]
  labels?: Record<string, string>
  units?: Record<string, string>
  sample_size?: number
  cells: CorrelationCell[]
  method?: string
  caveat?: string
  note?: string
  observations_evaluated?: number
  available: boolean
  reason?: string
}

export interface MapPointMeasurement {
  value: number | null
  unit: string
}

export interface MapPoint {
  location_code: string
  location_name: string | null
  region_code: string | null
  country: string | null
  latitude: number | null
  longitude: number | null
  last_observation_date: string | null
  observation_count: number
  source_name?: string
  dataset_id?: string | null
  quality_flags?: string[]
  cluster_key?: string | null
  measurements: Partial<Record<Metric, MapPointMeasurement>>
}

export interface MapClusterMeasurement {
  mean: number | null
  min: number | null
  max: number | null
  unit: string
  stations: number
}

export interface MapCluster {
  cluster_key: string
  point_count: number
  centroid: [number, number] | null
  regions: string[]
  last_observation_date: string | null
  total_observations: number
  measurements: Partial<Record<Metric, MapClusterMeasurement>>
}

/** `GET /climate/map-data`. */
export interface MapDataResponse {
  metrics: Metric[]
  labels: Record<string, string>
  units: Record<string, string>
  points: MapPoint[]
  point_count: number
  clusters: MapCluster[]
  clustered: boolean
  cluster_threshold: number
  cluster_key_precision: number
  bounds: [number, number, number, number] | null
  scope: { start: string | null; end: string | null; regions: string[]; location_codes: string[]; quality: string | null }
  window: { start: string | null; end: string | null }
  matched_observations: number
  generated_at: string
  data_note: string
}

/** An item of `source_freshness` / `per_source` across climate + dashboard. */
export interface SourceFreshness {
  source_name: string
  newest_date: string | null
  lag_days: number | null
  status: 'fresh' | 'lagging' | 'stalled' | 'no_data'
  status_note: string
  records?: number
}

export interface QualityScopeRow {
  scope_type: 'region' | 'dataset'
  scope: string
  observation_count: number
  date_range: { start: string; end: string } | null
  flagged_row_count: number
  flagged_row_pct: number
  flags: Record<string, number>
  temperature_completeness_pct: number
  precipitation_completeness_pct: number
  locations: number
  regions: number
  sampled_rows: number
  note: string | null
}

/** `GET /climate/quality` (quality_report). */
export interface DataQualityResponse {
  scope: { start: string | null; end: string | null; regions: string[]; datasets: string[]; countries: string[] }
  overall: CoverageReport
  available: boolean
  observations_read: number
  completeness_pct: number
  flag_totals: Record<string, number>
  flagged_rows: number
  flagged_row_pct: number
  per_source: SourceFreshness[]
  source_count: number
  worst_lag_days: number | null
  median_lag_days: number | null
  freshness_thresholds: { fresh_max_days: number; lagging_max_days: number }
  status_counts: Record<string, number>
  generated_at: string
  reason?: string
  per_region?: never[]
  flag_index_totals?: {
    clean_rows: number
    flagged_rows: number
    total_rows: number
    limit_applied: number
  }
  per_scope?: QualityScopeRow[]
}

export interface HistoryYearEntry {
  year: number
  value: number | null
  anomaly_vs_reference: number | null
  observations: number
  min: number | null
  max: number | null
}

export interface HistoryDecadeEntry {
  decade: string
  years: number[]
  years_covered: number
  mean: number | null
  total: number | null
  min: number | null
  max: number | null
  unit: string
  aggregation: 'mean' | 'sum'
}

export interface HistoryReference {
  reference_period: { start: string | null; end: string | null }
  years_in_reference_period: number
  normal: number | null
  reference_min?: number | null
  reference_max?: number | null
  reference_std?: number | null
  stable: boolean
  reason?: string
  note?: string | null
}

export interface HistoryChange {
  available: boolean
  kind?: string
  from_period?: { start: string | null; end: string | null }
  from_value?: number | null
  to_year?: number
  to_value?: number | null
  delta?: number | null
  relative_pct?: number | null
}

export interface HistoryGroup {
  group: string
  group_by: string
  years_covered: number
  first_year: number
  last_year: number
  reference: HistoryReference
  years: HistoryYearEntry[]
  decades: HistoryDecadeEntry[]
  change: HistoryChange
  record_high: { year: number; value: number } | null
  record_low: { year: number; value: number } | null
  slope: {
    available: boolean
    slope_per_period?: number | null
    periods?: number
    first_value?: number | null
    last_value?: number | null
    total_change?: number | null
    method?: string
    period_labels?: string[]
    note?: string
  }
  total_change: number | null
  available: boolean
}

/** `GET /climate/history` (historical_explorer). */
export interface HistoryResponse {
  available: boolean
  metric: Metric
  label?: string
  unit?: string
  aggregation?: 'mean' | 'sum'
  group_by?: GroupBy
  reference_period?: { start: string | null; end: string | null }
  groups: HistoryGroup[]
  group_count?: number
  year_span?: { first: number | null; last: number | null }
  year_count: number
  observations_evaluated: number
  pipeline_year_matrix?: Array<{
    region_code: string
    year: number
    value: number | null
    aggregation: 'mean' | 'sum'
    unit: string
    observation_count: number
  }>
  method?: string
  caveat?: string
  generated_at?: string
  reason?: string
}

export interface Station {
  location_code: string
  location_name: string | null
  region_code: string | null
  country: string | null
  latitude: number | null
  longitude: number | null
  elevation_m: number | null
  station_id: string | null
  observation_count: number
  first_observation_date: string | null
  last_observation_date: string | null
  lag_days: number | null
  status: 'monitoring' | 'awaiting_data'
  status_note: string
  geography_source: string
}

/** `GET /climate/stations`. */
export interface StationsResponse {
  stations: Station[]
  station_count: number
  reporting_count: number
  awaiting_data_count: number
  regions_source: string
  note: string
  generated_at: string
}

/** `GET /climate/filter-options` - merged catalogue for filter pickers. */
export interface FilterOptionsResponse {
  metrics: MetricDefinition[]
  regions: Array<{
    code: string
    name: string
    country: string | null
    climate_zone: string | null
    location_count: number
    source: string
  }>
  region_codes_observed: string[]
  locations: Station[]
  countries: string[]
  datasets: Array<{
    id: string
    name: string
    source_type: SourceType
    status: DatasetStatus
    format: string
    row_count: number
    created_at: string
  }>
  sources: string[]
  frequencies: Frequency[]
  group_by_options: GroupBy[]
  quality_options: string[]
  anomaly_methods: string[]
  sorts: string[]
  observation_window: { newest_date: string | null; total_observations: number }
  limits: { max_page_size: number; max_export_rows: number; analytics_row_limit: number }
  generated_at: string
}

/* ---------------------------------------------------------------- dashboard */

export interface DashboardRegionalComparison {
  metric: Metric
  label: string
  unit: string
  aggregation: 'mean' | 'sum'
  ranked_regions: Array<{
    region_code: string
    mean_temperature_c: number | null
    unit: string
    max_temperature_c: number | null
    min_temperature_c: number | null
    observation_count: number
    last_observation_date: string | null
  }>
  region_count: number
  available: boolean
  reason: string | null
  note: string
}

/** The dashboard projection of a stored alert (present_alert). */
export interface AlertCard {
  id: string
  rule_key: string | null
  status: string
  severity: Severity
  title: string | null
  message: string | null
  metric: Metric | null
  metric_label: string | null
  value: number | null
  unit: string | null
  deviation_score: number | null
  direction: string | null
  region_code: string | null
  location_code: string | null
  location_name: string | null
  observation_date: string | null
  triggered_at: string | null
  explanation: string | null
  method: string | null
}

export interface RecentAnomalies {
  source: string
  available: boolean
  count: number
  items: Anomaly[]
  note?: string
  reason?: string
  anomaly_count?: number
  severity_counts?: Record<string, number>
}

export interface ForecastSummaryItem {
  id: string
  region_code: string | null
  location_code: string | null
  target_variable: Metric
  forecast_date: string
  predicted_value: number | null
  unit: string
  confidence_low: number | null
  confidence_high: number | null
  model_id: string
  model_name: string
  generated_at: string
  horizon_days: number
}

export interface ForecastSummariesResponse {
  available: boolean
  items: ForecastSummaryItem[]
  prediction_count?: number
  reason?: string
  note?: string
}

export interface PipelineStatus {
  running: number
  queued: number
  completed: number
  failed: number
  by_status: Record<string, number>
  last_duration_seconds: number | null
  average_duration_seconds: number | null
  completed_jobs_sampled: number
  processing_mode_breakdown: Record<string, number>
  ingestion_running: number
  ingestion_last_duration_seconds: number | null
  reason?: string
}

export interface QuickAction {
  id: string
  label: string
  route: string
  requires: string[]
  enabled: boolean
  disabled_reason?: string
}

/** `GET /dashboard/summary` - the whole presenter, envelope-free. */
export interface DashboardSummary {
  totals: {
    datasets: number
    datasets_by_status: Record<string, number>
    observations: number
    locations: number
    regions: number
    regions_source: 'regions_collection' | 'observations' | 'none'
    ingestion_jobs: Record<string, number>
    processing_jobs: Record<string, number>
    active_models: number
    predictions_generated: number
    open_alerts: number
    unread_notifications: number
    support_tickets: Record<string, number>
  }
  last_successful_sync: {
    source_name: string
    completed_at: string
    records_accepted: number
    records_rejected: number
    status: JobStatus
  } | null
  current_data: {
    available: boolean
    newest_observation_date: string | null
    lag_days: number | null
    status: string
    status_note: string
    per_source: SourceFreshness[]
    source_count: number
    reason: string | null
  }
  temperature_trend: TrendResponse
  precipitation_trend: TrendResponse
  regional_comparison: DashboardRegionalComparison
  recent_anomalies: RecentAnomalies
  forecast_summaries: ForecastSummariesResponse
  data_quality: {
    window: { start: string | null; end: string | null }
    coverage: CoverageReport
    observations_in_window: number
    per_source: SourceFreshness[]
    available: boolean
    reason: string | null
  }
  pipeline_status: PipelineStatus
  alerts: AlertCard[]
  quick_actions: QuickAction[]
  scope: { user_id: string; role: Role }
  generated_at: string
}

/**
 * `GET /dashboard/activity` items. Audit rows carry `action`/`actor_role`; job
 * rows carry `status`/`label`. The timestamp key is `at`, not `created_at`.
 */
export interface ActivityEvent {
  id: string
  kind: 'audit' | 'ingestion' | 'processing'
  action: string
  resource_type: string
  resource_id: string | null
  outcome: 'success' | 'partial' | 'failure' | 'in_progress' | 'denied' | 'unknown'
  at: string
  actor_user_id?: string | null
  actor_role?: string
  request_id?: string | null
  metadata?: Record<string, unknown>
  status?: JobStatus
  label?: string
  records_accepted?: number
  records_rejected?: number
  duration_seconds?: number | null
}

export interface DashboardActivityResponse extends Paginated<ActivityEvent> {
  sources: string[]
  note: string
  generated_at: string
}

/* ---------------------------------------------------------------- datasets */

export interface Dataset {
  id: string
  name: string
  description?: string | null
  source_type: SourceType
  source_name: string
  source_url?: string | null
  file_name?: string | null
  /** only present for administrators, analysts and the owner */
  storage_uri?: string | null
  format: 'csv' | 'json' | 'xlsx' | 'parquet' | 'api'
  schema_version: number
  geographic_coverage?: {
    regions?: string[]
    countries?: string[]
    location_codes?: string[]
    bbox?: number[]
  }
  temporal_coverage?: { start?: string | null; end?: string | null; years?: number[]; frequency?: string }
  record_count: number
  status: DatasetStatus
  uploaded_by?: string | null
  created_at: string
  updated_at?: string
  tags?: string[]
  units?: Record<string, string>
  is_public?: boolean
  source_metadata?: Record<string, unknown>
  validation_report?: Record<string, unknown>
  ingestion_job_ids?: string[]
  can_edit: boolean
  is_owner: boolean
}

/** `PATCH /datasets/{id}` accepts only this whitelist (apps/datasets/views.py:165-167). */
export interface DatasetUpdateBody {
  name?: string
  description?: string
  tags?: string[]
  is_public?: boolean
  units?: Record<string, string>
  geographic_coverage?: Dataset['geographic_coverage']
}

/** `POST /datasets/upload` - 202, import starts immediately. */
export interface DatasetUploadResponse {
  dataset: Dataset
  ingestion_job_id: string
  status: 'ingesting'
  executor: string
  detected_format: string
  column_mapping: Record<string, string>
  headers: string[]
}

/** `GET /datasets/{id}/mapping`. */
export interface DatasetMappingResponse {
  headers: string[]
  suggested_mapping: Record<string, string>
  current_mapping: Record<string, string>
  units: Record<string, string>
  mappable_fields: string[]
}

/** `POST /datasets/{id}/mapping` body + ack. */
export interface DatasetMappingUpdate {
  mapping: Record<string, string>
  units?: Record<string, string>
  location_defaults?: Record<string, unknown>
}

export interface DatasetMappingSetResponse {
  dataset_id: string
  mapping: Record<string, string>
  units: Record<string, string>
}

/** `POST /datasets/{id}/ingest` (202) and `POST /ingestion/jobs` share ack shapes. */
export interface DatasetIngestAck {
  dataset_id: string
  ingestion_job_id: string
  status: 'ingesting'
  executor: string
}

/** `GET /datasets/{id}/preview`. */
export interface DatasetPreviewResponse {
  format_detected: string
  headers: string[]
  column_mapping: Record<string, string>
  accepted: Record<string, unknown>[]
  rejected: Record<string, unknown>[]
  counts_preview: { fetched: number; accepted: number; rejected: number }
}

/** `POST /datasets/column-suggestion`. */
export interface ColumnSuggestionResponse {
  headers: string[]
  suggested_mapping: Record<string, string>
  suggested_units: Record<string, string>
  unmapped_headers: string[]
  mappable_fields: string[]
}

export interface FormatDocumentation {
  format: string
  delimiter?: string
  shapes?: string[]
  notes: string
  required_columns: string[]
  optional_columns: string[]
  unit_rules: Record<string, string[]>
}

/** `GET /datasets/formats`. */
export interface DatasetFormatsResponse {
  formats: FormatDocumentation[]
  canonical_fields: { measurement_fields: string[]; context_fields: string[] }
  nasa_power_parameters: Record<string, string>
}

export interface ValidationIssue {
  code: string
  severity: 'error' | 'warning' | 'info'
  field: string | null
  message: string
  row?: number | null
  count?: number
}

/** `POST /datasets/{id}/validate` (build_validation_report + view extras). */
export interface ValidationReport {
  generated_at?: string
  format_detected?: string
  error?: string
  headers: string[]
  column_mapping: Record<string, string>
  counts: {
    fetched: number
    accepted: number
    rejected: number
    skipped_in_batch_duplicates?: number
    duplicates_against_database?: number
    new_records?: number
  }
  reconciliation?: string
  column_profile?: Array<{
    header: string
    canonical_field: string | null
    guessed_dtype: string
    role: string
    guessed_unit: string | null
    missing_percentage_estimate?: number | null
  }>
  validation_errors?: ValidationIssue[]
  error_count?: number
  coverage?: Record<string, unknown>
  aborted_reason?: string | null
  truncated?: boolean
  duplicate_key_estimate?: {
    records_sampled: number
    unique_keys: number
    in_sample_duplicates: number
    duplicate_percentage: number
  }
  geospatial?: {
    bbox: number[] | null
    regions: string[]
    location_codes: string[]
    zero_coordinate_warnings: number
    coordinate_errors: number
  }
  sample_accepted?: Record<string, unknown>[]
  sample_rejected?: Record<string, unknown>[]
}

/* --------------------------------------------------------------- ingestion */

export interface IngestionJob {
  id: string
  source_name: string
  source_type: SourceType
  job_type: 'api_sync' | 'file_import' | 'live_provider'
  dataset_id?: string | null
  status: JobStatus
  started_at: string
  completed_at?: string | null
  records_fetched: number
  records_accepted: number
  records_rejected: number
  records_skipped: number
  duplicate_count?: number
  validation_errors?: ValidationIssue[]
  retry_count: number
  max_retries?: number
  data_period?: { start?: string | null; end?: string | null }
  geographic_coverage?: Record<string, unknown>
  requested_by?: string | null
  triggered_by?: 'manual' | 'schedule'
  error_message?: string | null
  cancel_requested?: boolean
  processing_mode?: string
  parameters?: Record<string, unknown>
  log_lines?: { at: string; level: string; message: string }[]
  executor?: string
}

/** `POST /ingestion/jobs` answers with a queue acknowledgement, not a job doc. */
export interface IngestionStartResponse {
  job_id: string
  status: 'queued'
  executor: string
  dataset_id: string | null
  data_period: { start: string | null; end: string | null }
}

/** `POST /ingestion/jobs/{id}/retry` and `/cancel` result payloads. */
export interface IngestionActionResult {
  ok: true
  status: string
  executor?: string
}

/** `GET /ingestion/connectors` item (connector.configuration_status()). */
export interface ConnectorStatus {
  connector: string
  source_type: SourceType
  configured: boolean
  state: 'configured' | 'needs_configuration'
  requires_key: boolean
  setup_hint: string
  secret_present?: boolean | null
  secret_summary?: string | null
  provider?: string | null
  key_summary?: string | null
  polling_model?: string | null
  reachability?: Record<string, unknown> | null
}

export interface ConnectorsListResponse {
  connectors: ConnectorStatus[]
  note: string
}

/** `POST /ingestion/connectors/{name}/test`. */
export interface ConnectorTestResponse {
  connector: string
  success: boolean
  state: string
  points_requested?: number
  days_requested?: number
  raw_responses?: number
  observations_produced?: number
  window?: Record<string, unknown>
  error?: string
  detail?: string
  records_fetched?: number
  records_accepted?: number
}

/** `GET /ingestion/coverage`. */
export interface IngestionCoverageResponse {
  sources: Array<{
    source_name: string
    source_type: string
    observation_count: number
    temporal: { start: string | null; end: string | null }
    regions: string[]
    countries: string[]
    location_codes: string[]
    dataset_ids: string[]
  }>
  computed_at: string
  note: string
}

/* --------------------------------------------------------------- processing */

export interface ProcessingParameterSpec {
  type: 'int' | 'float' | 'str' | 'bool' | 'choice' | 'date'
  required: boolean
  default?: unknown
  min?: number
  max?: number
  choices?: string[]
  label?: string
  help?: string
}

export interface ProcessingWorkflow {
  id: string
  label: string
  description: string
  engine: 'mapreduce' | 'pyspark' | 'streaming' | 'mongodb'
  requires: string[]
  available: boolean
  unavailable_reason: string | null
  parameter_spec: Record<string, ProcessingParameterSpec>
}

/** `present_job` output - every key is always present (nulls included). */
export interface ProcessingJob {
  id: string
  workflow: string
  workflow_label: string
  engine: string
  dataset_id: string | null
  dataset_name: string | null
  status: JobStatus
  submitted_at: string
  started_at: string | null
  finished_at: string | null
  duration_seconds: number | null
  submitted_by: string | null
  submitted_by_name: string | null
  parameters: Record<string, unknown>
  metrics: Record<string, unknown> | null
  output: Record<string, unknown> | null
  log_lines: { at: string; level: string; message: string }[]
  error_message: string | null
  processing_mode: string
  retry_of: string | null
  attempt: number
}

/** `GET /processing/monitor` adds an ordering hint and active counters. */
export interface ProcessingMonitorResponse extends Paginated<ProcessingJob> {
  ordering: string
  active_counts: { queued: number; running: number }
}

export interface ProcessingLogsResponse {
  lines: { timestamp: string; level: string; message: string }[]
  job: ProcessingJob
}

export interface EngineHealth {
  hdfs: { configured: boolean; reachable: boolean; mode: string; endpoint: string | null; detail: string }
  spark: {
    installed: boolean
    version: string | null
    session_startable: boolean | null
    error?: string | null
    note?: string
  }
  hadoop: { bin_configured: boolean; version: string | null; cluster_configured: boolean; detail: string }
  data_lake: {
    mode: string
    root: string | null
    reachable: boolean
    free_bytes: number | null
    detail: string
  }
  current_processing_mode: string
  note: string
}

/** `GET /processing/data-lake`. */
export interface DataLakeResponse {
  mode: string
  roots: { path: string; files: number; bytes: number }[]
  partitions: number
  message: string
}

/* ------------------------------------------------------------ models / ML */

export interface ModelRecord {
  id: string
  name: string
  version: number
  algorithm: string
  target_variable: Metric
  feature_definition?: {
    features: string[]
    encodings?: Record<string, unknown>
    imputation?: string
    window?: Record<string, unknown>
  }
  training_period: { start: string; end: string; locations?: string[]; regions?: string[] }
  train_size: number
  test_size: number
  evaluation_metrics: {
    mae?: number | null
    rmse?: number | null
    mape_or_null?: number | null
    r2?: number | null
    evaluated_points?: number
    baseline_mae?: number | null
    skill_vs_baseline?: number | null
    seasonal_baseline_mae?: number | null
    skill_vs_seasonal_baseline?: number | null
    climatology_baseline_mae?: number | null
    skill_vs_climatology_baseline?: number | null
    mae_by_lead?: Record<string, number | null>
    climatology_mae_by_lead?: Record<string, number | null>
    skillful_lead_days?: number
    horizon_days?: number
    split_strategy?: string
  }
  skillful_lead_days?: number
  artifact_uri?: string | null
  artifact_format?: string
  status: 'training' | 'candidate' | 'active' | 'retired' | 'failed'
  created_at: string
  trained_by?: string | null
  dataset_ids?: string[]
  notes?: string | null
  limitations?: string[]
  feature_importance?: { feature: string; importance: number }[] | null
  uncertainty_method?: string | null
  error_message?: string | null
  duration_seconds?: number | null
  /** present on models still training */
  progress_stage?: string
  training_started_at?: string | null
  requested_by?: string | null
  training_options?: Record<string, unknown>
  /** extra key on `POST /models/{id}/activate` */
  retired_previous_versions?: number
}

export interface ForecastValue {
  forecast_date: string
  value: number | null
  unit: string
  lower_bound?: number | null
  upper_bound?: number | null
}

/** `POST /predictions/` / `GET /predictions/{id}` document. `scope` is an object. */
export interface PredictionRecord {
  id: string
  model_id: string
  model_name: string
  model_version: number
  target_variable: Metric
  scope: { kind: string; code: string; locations: string[] }
  region_code: string | null
  location_code: string | null
  horizon_days: number
  generated_at: string
  forecast_origin_date: string
  values: ForecastValue[]
  units: string
  method?: string
  training_period?: { start: string; end: string }
  evaluation_metrics_snapshot?: ModelRecord['evaluation_metrics']
  data_freshness_days?: number | null
  uncertainty: { available: boolean; method?: string | null; level?: number; note?: string }
  caveats: string[]
  provenance?: {
    requested_by?: string
    requested_by_name?: string
    feature_source?: string
    actual_observations_used?: number
  }
  status?: string
  created_at?: string
}

/** `GET /predictions/` items omit the `values` array; the response has no pagination keys. */
export type PredictionListItem = Omit<PredictionRecord, 'values'>

export interface PredictionListResponse {
  items: PredictionListItem[]
  note: string
}

export interface PredictionCreateBody {
  model_id: string
  horizon_days?: number
  scope: { region_code?: string; location_code?: string }
  forecast_origin_date?: string
}

/** `GET /predictions/summary`. */
export interface ForecastSummaryDetail {
  available: boolean
  note?: string
  scope?: { region_code: string | null; location_code: string | null }
  prediction_id?: string
  model_name?: string
  model_version?: number
  target_variable?: Metric
  unit?: string
  horizon_days?: number
  forecast_origin_date?: string
  generated_at?: string
  age_hours?: number | null
  first_value?: number | null
  point_count?: number
  uncertainty_available?: boolean
  data_freshness_days?: number | null
}

/** `GET /models/training-status` item projection (apps/machine_learning/services.py:233-242). */
export interface TrainingStatusItem {
  model_id: string
  name: string | null
  algorithm: string | null
  stage?: string | null
  started_at?: string | null
  elapsed_seconds: number | null
}

/** `GET /models/training-status`. */
export interface TrainingStatusResponse {
  training: TrainingStatusItem[]
  status_counts: Record<string, number>
  background_backend: string
  algorithms: Record<string, Record<string, unknown>>
  xgboost_available: boolean
}

/** `POST /models/train` (202) and `POST /models/{id}/retrain`. */
export interface TrainingOutcome {
  status: 'training'
  model_ids: string[]
  models: Array<{ model_id: string; name: string; version: number; algorithm: string; created_at: string }>
  note: string
  /** present on retrain responses */
  retrain_of?: string
}

/** `GET /models/catalogue` (services.catalogue). */
export interface ModelCatalogue {
  targets: Array<{
    target_variable: Metric
    label: string
    unit: string
    rows_with_value: number
    distinct_days: number
    locations: number
    first_date: string | null
    last_date: string | null
    trainable: boolean
    blocker?: string
  }>
  default_feature_set: {
    description: string
    lags: number[]
    rolling_windows_days: number[]
    covariates_per_target: Record<string, string[]>
  }
  algorithms: Array<{
    name: string
    label?: string
    available: boolean
    blocked_by?: string | null
    min_rows?: number
    nan_native?: boolean
    requirements?: string
    hyperparameters?: Record<string, unknown>
  }>
  horizon: { max_forecast_horizon_days: number; split_strategy: string }
  database_mode: string | null
}

/* ------------------------------------------------------ alerts / notices */

export interface AlertRule {
  id: string
  document_type: 'rule'
  rule_key: string
  name: string
  description: string
  is_enabled: boolean
  target_variable: Metric
  scope: { regions?: string[]; location_codes?: string[]; countries?: string[]; datasets?: string[] }
  condition: {
    operator: 'gt' | 'lt' | 'between' | 'outside_band' | 'deviation_gt_sigma' | 'anomaly'
    value?: number | null
    value_upper?: number | null
    sigma?: number | null
  }
  applicable_sources?: string[]
  severity: 'info' | 'warning' | 'critical'
  cooldown_minutes: number
  recipients: string[]
  created_by?: string | null
  created_at: string
  updated_at?: string
  last_evaluated_at?: string | null
  last_triggered_at?: string | null
  evaluation_state?: Record<string, unknown>
}

export interface AlertRulesListResponse extends Paginated<AlertRule> {
  role_view: 'staff' | 'restricted'
}

export interface AlertRuleDeleteResponse {
  status: 'deleted'
  deleted: boolean
  note: string
}

/** Fired alert - shape covers both rule-engine and anomaly-scan documents. */
export interface Alert {
  id: string
  rule_id?: string | null
  rule_key?: string
  name?: string | null
  kind?: string
  title?: string | null
  message?: string | null
  description?: string | null
  severity: Severity
  status: 'open' | 'acknowledged' | 'resolved' | 'dismissed'
  triggered_at: string
  target_variable?: Metric | null
  metric?: Metric | null
  metric_label?: string | null
  unit?: string | null
  value?: number | null
  threshold_or_baseline?: number | null
  deviation_score?: number | null
  direction?: string | null
  method?: string | null
  baseline?: Record<string, unknown> | null
  region_code?: string | null
  location_code?: string | null
  location_name?: string | null
  latitude?: number | null
  longitude?: number | null
  observation_date?: string | null
  dataset_id?: string | null
  prediction_id?: string | null
  source_name?: string | null
  dedupe_key?: string
  caveat?: string
  scan?: Record<string, unknown>
  explanation: string
  acknowledged_by?: string | null
  acknowledged_at?: string | null
  resolved_by?: string | null
  resolved_at?: string | null
  resolution_note?: string | null
  source?: string
  metadata?: Record<string, unknown>
  created_at: string
  updated_at?: string
  schema_version?: number
  /** detail only: `GET /alerts/{id}` embeds the rule summary */
  rule?: AlertRuleSummary | null
}

export interface AlertRuleSummary {
  id: string
  rule_key: string | null
  name: string | null
  condition: AlertRule['condition'] | null
  scope: AlertRule['scope'] | null
  is_enabled: boolean | null
}

export interface AlertsListResponse extends Paginated<Alert> {
  unread_notifications: number | null
}

export interface AlertSummaryResponse {
  total: number
  open: number
  acknowledged: number
  resolved: number
  dismissed: number
  by_status: Record<string, number>
  by_severity: Record<string, number>
  by_region: Array<{ region_code: string; count: number }>
  open_by_region: Record<string, number>
  open_by_region_sampled: boolean
  newest: string | null
  newest_alert: Alert | null
  rule_count: number
  rules_enabled: number
  enabled_rule_count: number
  last_evaluation: string | null
  note: string
  notification_delivery: { failed: number; pending: number; sent: number; suppressed: number; total: number }
  email_delivery: { enabled: boolean; host_configured: boolean; note: string; ready: boolean; sender: string | null }
}

export interface AlertTemplatesResponse {
  templates: Array<{
    key: string
    label: string
    description: string
    defaults: Record<string, unknown>
    supported_variable?: boolean
  }>
  operators: string[]
  severities: string[]
  metrics: Metric[]
  note: string
}

/** `POST /alerts/scan` (live evaluation against the stored record). */
export interface AlertScanResponse {
  job: ProcessingJob | null
  matched: number
  detail: string
  evaluation: Record<string, unknown>
  scheduler: Record<string, unknown>
}

export interface Notification {
  id: string
  recipient_user_id: string
  channel: 'in_app' | 'email'
  type: 'alert' | 'report' | 'system' | 'ticket'
  severity: Severity
  title: string
  body: string
  dedupe_key?: string
  related_resource?: { type: string; id: string } | null
  delivery_status: 'pending' | 'sent' | 'failed' | 'suppressed'
  attempt_count: number
  next_retry_at?: string | null
  read_at?: string | null
  acknowledged_at?: string | null
  created_at: string
  sent_at?: string | null
  failure_reason?: string | null
}

export interface NotificationUnreadBreakdown {
  total: number
  unread: number
  critical: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  sampled_limit: number | null
  exact: boolean
}

export interface NotificationsListResponse extends Paginated<Notification> {
  unread: NotificationUnreadBreakdown
  scoped_to: string
}

export interface MarkAllReadResponse {
  status: 'read'
  updated: number
  remaining_unread: number
}

export interface UnreadCountResponse {
  total: number
  unread: number
  critical: number
  by_type: Record<string, number>
  by_severity: Record<string, number>
  sampled_limit: number | null
  exact: boolean
  email_delivery_configured: boolean
}

/* ---------------------------------------------------------------- reports */

export interface ReportRecord {
  id: string
  report_type: string
  title: string
  label: string
  requested_by: string
  requested_by_role: Role
  filters: Record<string, unknown>
  format: 'csv' | 'pdf' | 'json'
  status: 'queued' | 'running' | 'generated' | 'failed' | 'expired' | 'cancelled'
  created_at: string
  started_at?: string | null
  completed_at?: string | null
  download_available?: boolean
  download_count?: number
  download_name?: string | null
  download_url?: string | null
  file_size_bytes?: number | null
  row_count?: number | null
  executor?: string | null
  expired?: boolean
  expires_at?: string | null
  error_message?: string | null
  insufficient_data?: boolean
  reason?: string | null
}

export interface ReportsListResponse extends Paginated<ReportRecord> {
  scope: 'all_reports' | 'own_reports'
  retention_days: number
}

export interface ReportTypeCatalogue {
  report_type: string
  label: string
  description: string
  formats: string[]
  administrator_only: boolean
  computable_now: boolean
  default_filters: Record<string, unknown> | null
  documents_available: Record<string, number>
  required_data: string
  reason?: string | null
}

export interface ReportTypesResponse {
  types: ReportTypeCatalogue[]
  formats: string[]
  statuses: string[]
  filter_help: Record<string, string>
  storage: Record<string, unknown>
}

export interface ReportSummaryResponse {
  by_status: Record<string, number>
  by_type: Record<string, number>
  retention_days: number
  total: number
  storage: Record<string, unknown>
}

export interface ReportDeleteResponse {
  deleted: boolean
  artefact_removed: boolean
  note: string
}

/* ---------------------------------------------------------------- support */

export interface SupportTicket {
  id: string
  subject: string
  description: string
  category: 'bug' | 'data_request' | 'access' | 'feature' | 'other'
  priority: 'low' | 'medium' | 'high' | 'urgent'
  status: 'open' | 'in_progress' | 'resolved' | 'closed'
  created_by: string
  created_by_name?: string | null
  created_by_role?: Role
  assigned_to?: string | null
  assigned_to_name?: string | null
  attached_dataset_id?: string | null
  page_context?: string | null
  resolution_note?: string | null
  first_response_at?: string | null
  resolved_at?: string | null
  closed_at?: string | null
  comments: TicketComment[]
  comment_count_total: number
  comment_count_visible: number
  can_assign?: boolean
  can_reply?: boolean
  can_triage?: boolean
  created_at: string
  updated_at?: string
}

export interface TicketComment {
  author_user_id: string
  author_name?: string
  author_role: Role
  body: string
  created_at: string
  internal: boolean
}

export interface SupportListResponse extends Paginated<SupportTicket> {
  scope: string
  staff_view: boolean
}

export interface SupportSummary {
  open: number
  in_progress: number
  resolved: number
  closed: number
  open_total: number
  by_status: Record<string, number>
  by_priority: Record<string, number>
  unassigned: number
  median_first_response_hours: number | null
  first_response_sample: number
  first_response_sampled: boolean
  scope: string
  generated_at: string
  note: string
}

export interface SupportCategoriesResponse {
  categories: string[]
  priorities: string[]
  statuses: string[]
  category_labels: Record<string, string>
  open_by_category: Record<string, number>
  ticket_counts_by_category: Record<string, number>
  staff_view: boolean
}

export interface TicketDeleteResponse {
  status: 'deleted'
  deleted: boolean
}

export interface FeedbackRecord {
  id: string
  rating: number
  category: string
  message: string
  page_context?: string | null
  created_by: string
  created_by_name?: string | null
  status: 'new' | 'reviewed' | 'actioned'
  administrator_reply?: string | null
  replied_by?: string | null
  replied_at?: string | null
  created_at: string
  updated_at?: string
}

export interface FeedbackListResponse extends Paginated<FeedbackRecord> {
  scope: string
}

export interface FeedbackSummary {
  average_rating: number | null
  count: number
  by_category: Record<string, number>
  by_status: Record<string, number>
  generated_at: string
  scope: string
}

/* -------------------------------------------------------------- operations */

export interface ComponentStatus {
  name: string
  configured: boolean
  status: 'available' | 'unavailable' | 'not-configured' | 'degraded'
  detail: string
  critical: boolean
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  database: { mode: string; database: string; ok: boolean; counts?: Record<string, number> }
  version: string
  schema_version: number
  uptime_seconds: number
  processing_mode: string
  /** Deployment label from ``DEPLOYMENT_ENVIRONMENT``. */
  environment?: string
  components: ComponentStatus[]
  background: { executor: string; active_jobs: number; queued_jobs: number }
  request_id?: string
  generated_at: string
}

export interface SystemStatusResponse extends HealthResponse {
  environment: string
  config_summary?: Record<string, unknown>
  collection_counts: Record<string, number>
  jobs: { ingestion: Record<string, number>; processing: Record<string, number>; reports: Record<string, number> }
  ml: { models: Record<string, number>; predictions: number; active_model?: string | null }
  storage: Record<string, unknown>
  limits: Record<string, unknown>
  import_compat?: Record<string, unknown>
  alert_monitor?: Record<string, unknown>
  ingestion_scheduler?: Record<string, unknown>
}

export interface SystemMetricsResponse {
  requests: { total: number; by_status_class: Record<string, number>; error_rate_pct: number | null }
  latency: { p50_ms: number | null; p95_ms: number | null; p99_ms: number | null; max_ms: number | null; mean_ms: number | null }
  by_endpoint_group: { group: string; count: number; mean_ms: number | null; errors: number }[]
  jobs: { active: number; queued: number; completed_last_hour: number; failed_last_hour: number; mean_duration_seconds: number | null }
  resources: { cpu_count: number; disk_free_bytes: number | null; process_memory_mb: number | null }
  window: string
  unavailable: string[]
  note?: string
  generated_at: string
}

export interface BackupRecord {
  id: string
  created_at: string
  mode: string
  collections: string[]
  collection_counts: Record<string, number>
  document_count: number
  file_name: string
  size_bytes: number
  sha256: string
  created_by?: string | null
  database_mode: string
  restore_verified: boolean
  restored_at?: string | null
  note?: string
}

export interface BackupsResponse {
  items: BackupRecord[]
  policy: Record<string, unknown>
}

/** `POST /system/backups` returns the fresh record twice. */
export interface BackupRunResponse {
  job: BackupRecord
  record: BackupRecord
}

export interface DependencyRecord {
  name: string
  installed: boolean
  version: string | null
  required: string | null
  role: string
}

export interface DependenciesResponse {
  items: DependencyRecord[]
  python: Record<string, unknown>
  platform: Record<string, unknown>
}

export interface ServicesResponse {
  items: { name: string; status: string; detail?: string }[]
  checked_at: string
}

/* ------------------------------------------------------------------ users */

export interface RoleCatalogueEntry {
  role: Role
  label: string
  description: string
  capabilities: string[]
  account_count: number
  assignable_by_administrators: boolean
  self_registerable: boolean
}

export interface RolesResponse {
  roles: RoleCatalogueEntry[]
  note: string
}

export interface UsersListResponse extends Paginated<Account> {
  filters_applied: { search: string | null; role: string | null; status: string; sort: string }
}

/** `DELETE /users/{id}` is a soft deactivation; it never removes the document. */
export interface UserDeleteResponse {
  account: Account
  deleted: false
  note: string
}

/* ---------------------------------------------------------------- analytics */

export interface AnalyticsSummaryRecord {
  id: string
  summary_key: string
  metric: Metric
  scope: Record<string, unknown>
  payload: Record<string, unknown>
  payload_size: number
  source_datasets: string[]
  source_dataset_versions?: Record<string, number>
  computed_at: string
  workflow: string
  processing_mode: string
  schema_version: number
}

export interface AnalyticsSummariesResponse {
  items: AnalyticsSummaryRecord[]
  total: number
  filters: Record<string, unknown>
  available_metrics: string[]
  available_workflows: string[]
  available_processing_modes: string[]
  note: string
  generated_at: string
}

export interface EndpointGroup {
  group: string
  prefix: string
  description: string
  roles: string[]
  endpoints: { method: string; path: string; description: string; roles: string[] }[]
}
