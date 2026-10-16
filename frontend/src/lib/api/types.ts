/**
 * DTOs for every /api/v1 endpoint that exists today. Shapes are derived from
 * live responses (curl against the running Django/DRF backend), not guessed.
 *
 * Conventions enforced by the backend:
 *  - Mongo ids serialise to string `id`.
 *  - Datetimes are ISO-8601 UTC strings.
 *  - Enum-ish values are lowercase snake_case.
 *  - Collections that can be empty return `{ available: false, reason: "..." }`
 *    instead of fabricated data — the UI must render that honestly.
 *  - Display units for a metric come from `metrics.<var>.unit`, never hardcoded.
 *
 * Where a collection is currently empty on the live API, its item type keeps
 * only the universally-present `id` plus an `unknown` index signature. This is
 * deliberate: we do not invent field names we could not observe.
 */

// ----------------------------------------------------------------------------
// Transport envelope
// ----------------------------------------------------------------------------

export interface ApiErrorBody {
  code: string;
  message: string;
  /** Validation failures return string[]; some errors return { detail: string }. */
  details: string[] | Record<string, unknown>;
}

export interface Pagination {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  has_next: boolean;
  has_previous: boolean;
}

export interface ApiMeta extends Record<string, unknown> {
  correlation_id: string;
  pagination?: Pagination;
}

/** Raw JSON envelope returned by every endpoint. */
export interface ApiEnvelope<T> {
  success: boolean;
  data: T | null;
  error: ApiErrorBody | null;
  meta: ApiMeta;
}

/** The value the axios interceptor resolves to after unwrapping the envelope. */
export interface ApiResult<T> {
  data: T;
  meta: ApiMeta;
}

/** Payload + pagination for list endpoints. */
export interface Paginated<T> {
  items: T[];
  pagination?: Pagination;
  meta: ApiMeta;
}

// ----------------------------------------------------------------------------
// Auth & users
// ----------------------------------------------------------------------------

export type UserRole = "administrator" | "climate_analyst" | "viewer";

/** Capability keys mirror GET /users/roles → capability_matrix exactly. */
export type Capability =
  | "alert.acknowledge"
  | "alert.rule.admin"
  | "alert.rule.manage"
  | "analysis.save"
  | "anomaly.view"
  | "audit.view"
  | "dataset.admin"
  | "dataset.delete"
  | "dataset.upload"
  | "export.download"
  | "feedback.create"
  | "feedback.view"
  | "health.view"
  | "ingestion.run"
  | "ingestion.view"
  | "model.register"
  | "model.train"
  | "notification.view_own"
  | "ops.logs"
  | "platform.view"
  | "prediction.run"
  | "prediction.view"
  | "processing.run"
  | "processing.view"
  | "report.generate"
  | "report.view_any"
  | "settings.manage"
  | "support.create"
  | "support.manage"
  | "user.manage";

export type Theme = "light" | "dark";
export type Units = "metric" | "imperial";

export interface UserPreferences {
  theme: Theme;
  units: Units;
  digest: string;
}

export interface User {
  id: string;
  full_name: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  email_verified: boolean;
  must_change_password: boolean;
  locked_until: string | null;
  created_at: string;
  last_login_at: string | null;
  preferences: UserPreferences;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface LogoutResult {
  revoked_sessions: number;
}

export interface SessionInfo {
  id: string;
  issued_at: string;
  expires_at: string;
  device_hint: string;
}

export interface MeResult {
  user: User;
  role: UserRole;
  capabilities: Capability[];
  sessions: SessionInfo[];
}

export interface RegistrationStatus {
  self_registration: boolean;
  default_role: UserRole;
  password_min_length: number;
  recovery_channel: string;
  administrator_present: boolean;
}

export interface RoleMatrix {
  roles: UserRole[];
  counts: Record<UserRole, number>;
  capability_matrix: Record<Capability, UserRole[]>;
}

// ----------------------------------------------------------------------------
// Climate observations
// ----------------------------------------------------------------------------

export type Granularity = "hourly" | "daily" | "monthly" | "yearly";
export type QualityStatus = "valid" | "suspect" | "rejected";

/** The canonical variable keys seen across providers/quality/completeness. */
export type ClimateVariable =
  | "temperature"
  | "temperature_max"
  | "temperature_min"
  | "precipitation"
  | "humidity"
  | "wind_speed"
  | "wind_direction"
  | "pressure"
  | "cloud_cover"
  | "solar_radiation"
  | "snow";

export interface MetricValue {
  value: number;
  /** Provider-declared display unit (e.g. "C", "mm", "%", "m/s", "hPa"). */
  unit: string;
}

export interface ObservationSource {
  endpoint: string;
  request_params: Record<string, string>;
  model_gcm: string | null;
  generationtime_ms: number;
  provider: string;
  kind: string;
  retrieved_at: string;
}

export interface ObservationLocation {
  name: string;
  region: string;
  latitude: number;
  longitude: number;
}

export interface ObservationQuality {
  status: QualityStatus;
  flags: string[];
  missing_fields: string[];
  validated_at: string;
  validator_version: string;
}

export interface ObservationProvenance {
  ingestion_job_id: string;
  ingested_at: string;
  schema_version: string;
  unit_source: string;
  transformations: string[];
}

export interface ClimateObservation {
  id: string;
  dataset_id: string;
  dedupe_key: string;
  timestamp: string;
  granularity: Granularity;
  source: ObservationSource;
  location: ObservationLocation;
  metrics: Record<string, MetricValue>;
  quality: ObservationQuality;
  provenance: ObservationProvenance;
}

export interface ObservationFilters {
  dataset_id?: string;
  region?: string;
  station?: string;
  variable?: string;
  granularity?: Granularity;
  quality?: QualityStatus;
  date_from?: string;
  date_to?: string;
  bbox?: string;
  page?: number;
  /** Backend caps this at 100 and 422s on a filter-less call. */
  page_size?: number;
}

// ----------------------------------------------------------------------------
// Trends, regions, variables, quality, correlations
// ----------------------------------------------------------------------------

export interface ObservedRange {
  available: boolean;
  from: string | null;
  to: string | null;
  granularities: string[];
}

export interface TrendPoint {
  period: string;
  sample_size: number;
  mean: number | null;
  minimum: number | null;
  maximum: number | null;
}

/** GET /climate/trends — the `available` flag is the honest empty signal. */
export interface TrendResult {
  available: boolean;
  variable: string;
  reason: string | null;
  region: string | null;
  bucket: string;
  sample_size?: number;
  mean?: number | null;
  slope?: number | null;
  period_covered?: { start: string; end: string } | null;
  series?: TrendPoint[];
}

export interface BoundingBox {
  min_lat: number;
  max_lat: number;
  min_lon: number;
  max_lon: number;
}

export interface RegionSummary {
  region: string;
  country: string | null;
  station_id: string | null;
  observations: number;
  latitude: number;
  longitude: number;
  bounding_box: BoundingBox;
  latest_timestamp: string | null;
  latest_values: Record<string, number | null>;
  averages: Record<string, number | null>;
  suspect_observations: number;
}

export interface RegionsResult {
  available: boolean;
  region_count: number;
  reason: string | null;
  regions: RegionSummary[];
}

export interface VariablesResult {
  observed_range: ObservedRange;
  variables: { variable: string; observations: number }[];
}

export interface CompletenessCell {
  present: number;
  coverage: number;
}

export interface QualityDataset {
  dataset_id: string;
  observations: number;
  quality: Record<QualityStatus | string, number>;
  completeness: Record<string, CompletenessCell>;
  duplicates: number;
}

export interface TransformationCount {
  transformation: string;
  count: number;
}

export interface QualityResult {
  datasets: QualityDataset[];
  transformations_applied: TransformationCount[];
  by_status: Record<string, number>;
  variables_available: { variable: string; observations: number }[];
}

export interface CorrelationResult {
  pair: [string, string];
  region: string | null;
  window: { from: string; to: string };
  n: number;
  method: string;
  status: string;
  correlation: number | null;
  strength: string;
  sign: string;
  regression: { slope: number; intercept: number };
  summary: Record<string, { mean: number; std: number }>;
  note: string;
}

// ----------------------------------------------------------------------------
// Map data
// ----------------------------------------------------------------------------

export type MapMode = "markers" | "heatmap" | "choropleth";

export interface MapPoint {
  region: string;
  latitude: number;
  longitude: number;
  temperature: number | null;
  precipitation: number | null;
  anomaly_count: number;
  observations: number;
}

export interface MapDataResult {
  mode: MapMode;
  available: boolean;
  reason: string | null;
  cap: number;
  count: number;
  points: MapPoint[];
}

// ----------------------------------------------------------------------------
// Anomalies & analytics summaries
// ----------------------------------------------------------------------------

export interface AnomaliesSummaryResult {
  events_by_variable: Record<string, unknown>;
  event_total: number;
  recent_events: unknown[];
  anomaly_feature_summaries: unknown[];
}

/** List items for currently-empty collections expose `id` + unknown fields. */
export interface AnalyticsSummary {
  id: string;
  [key: string]: unknown;
}

// ----------------------------------------------------------------------------
// Datasets
// ----------------------------------------------------------------------------

export type DatasetStatus = "ready" | "processing" | "failed" | "archived" | string;

export interface Dataset {
  id: string;
  name: string;
  description: string | null;
  source_type: string;
  source_name: string | null;
  source_url: string | null;
  file_name: string | null;
  storage_uri: string | null;
  format: string;
  schema_version: string;
  geographic_coverage: { regions: string[]; bounding_box: BoundingBox | null };
  temporal_coverage: { start: string | null; end: string | null };
  record_count: number;
  rejected_count: number;
  duplicate_count: number;
  quality_score: number | null;
  status: DatasetStatus;
  tags: string[];
  column_map: Record<string, string>;
  units_hint: Record<string, string>;
  granularity: Granularity | string;
  default_location: Record<string, unknown>;
  uploaded_by: string | null;
  created_at: string;
  updated_at: string;
  last_ingestion_at: string | null;
  archived_at: string | null;
}

export interface DatasetRegionsResult {
  regions: string[];
}

export interface UploadValidateResult {
  valid: boolean;
  issues: string[];
  [key: string]: unknown;
}

export interface UploadCommitResult {
  dataset_id: string;
  [key: string]: unknown;
}

// ----------------------------------------------------------------------------
// Analyses
// ----------------------------------------------------------------------------

export interface Analysis {
  id: string;
  [key: string]: unknown;
}

// ----------------------------------------------------------------------------
// Ingestion
// ----------------------------------------------------------------------------

export interface ProviderInfo {
  available: boolean;
  requires_key: boolean;
  granularities: string[];
  variables: string[];
  display_name?: string;
  note?: string;
  missing_config?: string;
}

export interface IngestionProvidersResult {
  providers: Record<string, ProviderInfo>;
  granularities: string[];
  plausible_ranges_note: string;
}

export type IngestionJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | string;

export interface IngestionJob {
  id: string;
  job_ref: string;
  kind: string;
  label: string;
  source: string;
  dataset_id: string | null;
  status: IngestionJobStatus;
  records_fetched: number;
  records_accepted: number;
  records_rejected: number;
  records_duplicate: number;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  retry_count: number;
  error: string | null;
}

export interface IngestionStatusResult {
  by_status: Record<string, number>;
  accepted_total: number;
  rejected_total: number;
  average_duration_seconds: number;
  recent_jobs: IngestionJob[];
  providers: Record<string, ProviderInfo>;
}

// ----------------------------------------------------------------------------
// Alerts
// ----------------------------------------------------------------------------

export interface AlertCounts {
  open: number;
  acknowledged: number;
  resolved: number;
}

export interface AlertsSummaryResult {
  by_status: AlertCounts;
  open_by_severity: Record<string, number>;
  rules: { total: number; enabled: number };
  email_enabled: boolean;
}

export interface AlertEvent {
  id: string;
  [key: string]: unknown;
}

export interface AlertRule {
  id: string;
  [key: string]: unknown;
}

// ----------------------------------------------------------------------------
// Notifications
// ----------------------------------------------------------------------------

export interface Notification {
  id: string;
  [key: string]: unknown;
}

export interface UnreadCountResult {
  unread: number;
}

// ----------------------------------------------------------------------------
// Reports, support, feedback
// ----------------------------------------------------------------------------

export interface Report {
  id: string;
  [key: string]: unknown;
}

export interface ReportsMetaExtras {
  report_types?: string[];
  limits?: Record<string, number>;
}

export interface SupportTicket {
  id: string;
  [key: string]: unknown;
}

export interface SupportQueueResult {
  by_status: Record<string, number>;
  scope: string;
}

export interface TicketMessage {
  id: string;
  [key: string]: unknown;
}

export interface Feedback {
  id: string;
  [key: string]: unknown;
}

export interface FeedbackSummary {
  total: number;
  average_rating: number | null;
  rating_distribution: Record<string, number>;
  by_module: { module: string; total: number; average_rating: number }[];
  first_at: string | null;
  last_at: string | null;
  note: string;
}

// ----------------------------------------------------------------------------
// Dashboard summary
// ----------------------------------------------------------------------------

export interface DashboardCounts {
  datasets: number;
  observations: number;
  analytics_summaries: number;
  predictions: number;
  alert_events: number;
}

export interface LastSuccessfulSync {
  available: boolean;
  job_ref?: string;
  dataset_id?: string;
  source?: string;
  completed_at?: string;
  records_accepted?: number;
  reason?: string;
}

export interface DashboardIngestion {
  success: number;
  failed: number;
  by_status: Record<string, number>;
  records_accepted: number;
  records_rejected: number;
  records_duplicate: number;
  last_successful_sync: LastSuccessfulSync;
}

/** Per-variable trend entry inside the dashboard payload. */
export interface DashboardTrendEntry {
  available: boolean;
  variable: string;
  reason: string | null;
  bucket?: string;
  sample_size?: number;
  period_covered?: { start: string; end: string } | null;
  series?: TrendPoint[];
}

export interface DashboardGeographic {
  available: boolean;
  region_count: number;
  reason: string | null;
  regions: RegionSummary[];
}

export interface DashboardCompleteness {
  total_observations: number;
  by_variable: Record<string, CompletenessCell>;
}

export interface DashboardDataQuality {
  by_status: Record<string, number>;
  completeness: DashboardCompleteness;
  transformations_applied: TransformationCount[];
  per_dataset: QualityDataset[];
}

/** A pipeline job (report.generate / provider_pull / …) as embedded in the dashboard. */
export interface PipelineJob {
  id: string;
  job_ref: string;
  kind: string;
  label: string;
  status: string;
  engine: string;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  logs: { at: string; level: string; message: string }[];
  metrics: Record<string, unknown>;
  attempt: number;
  max_attempts: number;
  created_by: string | null;
  queued_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  duration_seconds: number | null;
}

export interface DashboardPipeline {
  available: boolean;
  by_status: Record<string, number>;
  by_engine: Record<string, number>;
  recent_jobs: PipelineJob[];
  reason: string | null;
}

export interface DashboardAnomalies {
  available: boolean;
  reason: string | null;
  by_status: Record<string, unknown>;
  recent: unknown[];
}

export interface DashboardForecasts {
  available: boolean;
  reason: string | null;
  latest: unknown[];
}

export interface DashboardRecentAlerts {
  available: boolean;
  reason: string | null;
  items: unknown[];
}

export interface DashboardSummary {
  counts: DashboardCounts;
  ingestion: DashboardIngestion;
  trends: Record<string, DashboardTrendEntry>;
  geographic: DashboardGeographic;
  observed_range: ObservedRange;
  anomalies: DashboardAnomalies;
  forecasts: DashboardForecasts;
  data_quality: DashboardDataQuality;
  pipeline: DashboardPipeline;
  recent_alerts: DashboardRecentAlerts;
}

// ----------------------------------------------------------------------------
// Ops / health / ready
// ----------------------------------------------------------------------------

export interface ServiceCheck {
  name: string;
  configured: boolean;
  required: boolean;
  detail: string;
  hint: string;
  mode: string;
}

export interface DataLakeStatus {
  root: string;
  engine: string;
  hdfs_configured: boolean;
  layers_present: Record<string, boolean>;
  writable: boolean;
  files: number;
  bytes: number;
  free_disk_gb: number;
}

export interface LastSyncStatus {
  status: string;
  finished_at: string | null;
  age_seconds: number | null;
  source: string | null;
  records_accepted: number | null;
}

export interface RuntimeStatus {
  python: string;
  platform: string;
  uptime_seconds: number;
  debug: boolean;
  signing_key_persisted: boolean;
  background_engine: string;
  self_registration: boolean;
}

export interface ServicesStatusResult {
  database: {
    reachable: boolean;
    database: string;
    ping: boolean;
    server_version: string;
    atlas: boolean;
    collections: number;
  };
  collection_counts: Record<string, number>;
  collection_counts_error: string | null;
  services: ServiceCheck[];
  data_lake: DataLakeStatus;
  last_sync: LastSyncStatus;
  runtime: RuntimeStatus;
}

export interface HealthResult {
  status: string;
  service: string;
  version: string;
}

export interface ReadyResult {
  status: string;
  checks: Record<string, { ok: boolean; detail?: string; [key: string]: unknown }>;
  observations?: number;
}
