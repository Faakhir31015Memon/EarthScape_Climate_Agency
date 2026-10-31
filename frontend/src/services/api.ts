/**
 * Typed API client. One function per backend endpoint. Pages never call axios
 * directly; route paths, query keys, request bodies and response-envelope unwrapping
 * live here. Every path below was verified against the mounted Django URLs and
 * `APPEND_SLASH = False` (collection routes need the trailing slash).
 */
import {
  apiDelete,
  apiDownload,
  apiGet,
  apiPatch,
  apiPost,
  anonymousPost,
  uploadForm,
} from './http'
import type {
  Account,
  Alert,
  AlertRule,
  AlertRuleDeleteResponse,
  AlertRulesListResponse,
  AlertScanResponse,
  AlertSummaryResponse,
  AlertTemplatesResponse,
  AlertsListResponse,
  AnalyticsSummariesResponse,
  AnomalyResponse,
  AnomalyScanResponse,
  AuthConfiguration,
  BackupRunResponse,
  BackupsResponse,
  ColumnSuggestionResponse,
  ConnectorTestResponse,
  ConnectorsListResponse,
  CorrelationResponse,
  CurrentUserResponse,
  DashboardActivityResponse,
  DashboardSummary,
  DataLakeResponse,
  DataQualityResponse,
  Dataset,
  DatasetFormatsResponse,
  DatasetIngestAck,
  DatasetMappingResponse,
  DatasetMappingSetResponse,
  DatasetMappingUpdate,
  DatasetPreviewResponse,
  DatasetUploadResponse,
  DatasetUpdateBody,
  DependenciesResponse,
  EndpointGroup,
  EngineHealth,
  FeedbackListResponse,
  FeedbackRecord,
  FeedbackSummary,
  FileAnalytics,
  FilterOptionsResponse,
  HistoryResponse,
  IngestionActionResult,
  IngestionCoverageResponse,
  IngestionJob,
  IngestionStartResponse,
  LogoutResponse,
  MarkAllReadResponse,
  MapDataResponse,
  MetricCatalogueResponse,
  Metric,
  ModelCatalogue,
  ModelRecord,
  Notification,
  NotificationsListResponse,
  ObservationsListResponse,
  Paginated,
  PasswordResetRequestResponse,
  PredictionCreateBody,
  PredictionListResponse,
  PredictionRecord,
  ProcessingJob,
  ProcessingLogsResponse,
  ProcessingMonitorResponse,
  ProcessingWorkflow,
  RegisterResponse,
  RegionComparison,
  RegionDetailResponse,
  RegionsListResponse,
  ReportDeleteResponse,
  ReportRecord,
  ReportSummaryResponse,
  ReportTypesResponse,
  ReportsListResponse,
  RolesResponse,
  ServicesResponse,
  StationsResponse,
  StatusNoteResponse,
  SupportCategoriesResponse,
  SupportListResponse,
  SupportSummary,
  SupportTicket,
  SystemMetricsResponse,
  SystemStatusResponse,
  TicketDeleteResponse,
  TokenPair,
  TrendResponse,
  TrainingOutcome,
  TrainingStatusResponse,
  UnreadCountResponse,
  UserDeleteResponse,
  UsersListResponse,
  ValidationReport,
} from '../types/api'

export interface PageParams {
  page?: number
  page_size?: number
  [key: string]: unknown
}

export interface RangeParams {
  start?: string
  end?: string
  region?: string
  location?: string
  [key: string]: unknown
}

export interface TrendParams extends RangeParams {
  metric: Metric
  frequency?: 'daily' | 'weekly' | 'monthly' | 'annual'
  group_by?: 'none' | 'region' | 'location' | 'country'
  require_baseline?: boolean
}

/** Body of `POST /models/train`. */
export interface TrainModelsBody {
  target_variable: Metric
  algorithms?: string[]
  algorithm?: string
  horizon_days?: number
  regions?: string[]
  locations?: string[]
  min_train_rows?: number
  name?: string
  start?: string
  end?: string
}

// auth

export const authApi = {
  register(payload: { full_name: string; email: string; password: string; organisation?: string }): Promise<RegisterResponse> {
    return anonymousPost<RegisterResponse>('/auth/register', payload)
  },
  login(email: string, password: string): Promise<TokenPair> {
    return anonymousPost<TokenPair>('/auth/login', { email, password })
  },
  logout(refreshToken: string | null): Promise<LogoutResponse> {
    return apiPost<LogoutResponse>('/auth/logout', refreshToken ? { refresh_token: refreshToken } : {})
  },
  me(): Promise<CurrentUserResponse> {
    return apiGet<CurrentUserResponse>('/auth/me')
  },
  // PATCH /auth/me answers with `{account}` only; capabilities come from GET /auth/me.
  updateMe(changes: Record<string, unknown>): Promise<Account> {
    return apiPatch<{ account: Account }>('/auth/me', changes).then((response) => response.account)
  },
  changePassword(current_password: string, new_password: string): Promise<StatusNoteResponse> {
    return apiPost<StatusNoteResponse>('/auth/password-change', { current_password, new_password })
  },
  requestPasswordReset(email: string): Promise<PasswordResetRequestResponse> {
    return anonymousPost<PasswordResetRequestResponse>('/auth/password-reset', { email })
  },
  confirmPasswordReset(token: string, password: string): Promise<StatusNoteResponse> {
    return anonymousPost<StatusNoteResponse>('/auth/password-reset/confirm', { token, new_password: password })
  },
  configuration(): Promise<AuthConfiguration> {
    return apiGet<AuthConfiguration>('/auth/configuration')
  },
}

// users

export const usersApi = {
  // Backend search param is `search`, not `query`.
  list(params: PageParams & { search?: string; role?: string; status?: string; sort?: string } = {}): Promise<UsersListResponse> {
    return apiGet<UsersListResponse>('/users/', params)
  },
  get(id: string): Promise<Account> {
    return apiGet<{ account: Account }>(`/users/${id}`).then((response) => response.account)
  },
  create(payload: { full_name: string; email: string; password: string; role: string; organisation?: string }): Promise<Account> {
    return apiPost<{ account: Account }>('/users/', payload).then((response) => response.account)
  },
  update(id: string, changes: Record<string, unknown>): Promise<Account> {
    return apiPatch<{ account: Account }>(`/users/${id}`, changes).then((response) => response.account)
  },
  activate(id: string): Promise<Account> {
    return apiPost<{ account: Account }>(`/users/${id}/activate`, {}).then((response) => response.account)
  },
  deactivate(id: string): Promise<Account> {
    return apiPost<{ account: Account }>(`/users/${id}/deactivate`, {}).then((response) => response.account)
  },
  setRole(id: string, role: string): Promise<Account> {
    return apiPost<{ account: Account }>(`/users/${id}/role`, { role }).then((response) => response.account)
  },
  resetPassword(id: string, new_password: string): Promise<StatusNoteResponse> {
    return apiPost<StatusNoteResponse>(`/users/${id}/reset-password`, { new_password })
  },
  unlock(id: string): Promise<StatusNoteResponse> {
    return apiPost<StatusNoteResponse>(`/users/${id}/unlock`, {})
  },
  // Soft deactivation only - accounts are never hard-deleted server-side.
  remove(id: string): Promise<UserDeleteResponse> {
    return apiDelete<UserDeleteResponse>(`/users/${id}`)
  },
  roles(): Promise<RolesResponse> {
    return apiGet<RolesResponse>('/users/roles')
  },
}

// datasets

export const datasetsApi = {
  list(params: PageParams & { status?: string; source_type?: string; search?: string; format?: string; region?: string; sort?: string } = {}): Promise<Paginated<Dataset>> {
    return apiGet<Paginated<Dataset>>('/datasets/', params)
  },
  get(id: string): Promise<Dataset> {
    return apiGet<{ dataset: Dataset }>(`/datasets/${id}`).then((response) => response.dataset)
  },
  update(id: string, changes: DatasetUpdateBody): Promise<Dataset> {
    return apiPatch<{ dataset: Dataset }>(`/datasets/${id}`, changes).then((response) => response.dataset)
  },
  // Real archive route: DELETE /datasets/{id} performs the admin-only soft archive;
  // PATCH with a status value is rejected by the update serializer.
  archive(id: string): Promise<Dataset> {
    return apiDelete<{ dataset: Dataset; archived: true }>(`/datasets/${id}`).then((response) => response.dataset)
  },
  upload(
    file: File,
    metadata: {
      name: string
      description?: string
      source_type?: string
      source_name?: string
      source_url?: string
      tags?: string
      units?: string
      mapping?: string
      is_public?: string
      default_location_code?: string
      default_region_code?: string
      default_country?: string
      default_latitude?: string
      default_longitude?: string
    },
    onProgress?: (percent: number) => void,
  ): Promise<DatasetUploadResponse> {
    const form = new FormData()
    form.append('file', file)
    for (const [key, value] of Object.entries(metadata)) {
      if (value !== undefined && value !== '') form.append(key, value)
    }
    return uploadForm<DatasetUploadResponse>('/datasets/upload', form, onProgress)
  },
  mapping(id: string): Promise<DatasetMappingResponse> {
    return apiGet<DatasetMappingResponse>(`/datasets/${id}/mapping`)
  },
  setMapping(id: string, body: DatasetMappingUpdate): Promise<DatasetMappingSetResponse> {
    return apiPost<DatasetMappingSetResponse>(`/datasets/${id}/mapping`, body)
  },
  ingest(id: string): Promise<DatasetIngestAck> {
    return apiPost<DatasetIngestAck>(`/datasets/${id}/ingest`, {})
  },
  validate(id: string): Promise<ValidationReport> {
    return apiPost<ValidationReport>(`/datasets/${id}/validate`, {})
  },
  preview(id: string, rows?: number): Promise<DatasetPreviewResponse> {
    return apiGet<DatasetPreviewResponse>(`/datasets/${id}/preview`, rows ? { rows } : undefined)
  },
  download(id: string, filename: string): Promise<void> {
    return apiDownload(`/datasets/${id}/download`, filename)
  },
  formats(): Promise<DatasetFormatsResponse> {
    return apiGet<DatasetFormatsResponse>('/datasets/formats')
  },
  suggestColumns(headers: string[]): Promise<ColumnSuggestionResponse> {
    return apiPost<ColumnSuggestionResponse>('/datasets/column-suggestion', { headers })
  },
}

// file analytics

export const fileAnalyticsApi = {
  list(params: PageParams & { search?: string } = {}): Promise<Paginated<FileAnalytics>> {
    return apiGet<Paginated<FileAnalytics>>('/file-analytics/', params)
  },
  get(id: string): Promise<FileAnalytics> {
    return apiGet<{ analysis: FileAnalytics }>(`/file-analytics/${id}`).then((response) => response.analysis)
  },
  upload(files: File[], targetYear?: number, onProgress?: (percent: number) => void): Promise<FileAnalytics> {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    if (targetYear) form.append('target_year', String(targetYear))
    return uploadForm<{ analysis: FileAnalytics }>('/file-analytics/upload', form, onProgress).then(
      (response) => response.analysis,
    )
  },
  remove(id: string): Promise<{ deleted: true; id: string }> {
    return apiDelete<{ deleted: true; id: string }>(`/file-analytics/${id}`)
  },
}

// ingestion

export const ingestionApi = {
  list(params: PageParams & { status?: string; source_type?: string; dataset_id?: string; job_type?: string } = {}): Promise<Paginated<IngestionJob>> {
    return apiGet<Paginated<IngestionJob>>('/ingestion/jobs', params)
  },
  get(id: string): Promise<IngestionJob> {
    return apiGet<IngestionJob>(`/ingestion/jobs/${id}`)
  },
  // `source` is the connector name; window/location options live under `parameters`.
  start(payload: {
    source: string
    parameters?: Record<string, unknown>
    source_name?: string
    triggered_by?: 'manual' | 'schedule'
    dataset_id?: string
  }): Promise<IngestionStartResponse> {
    return apiPost<IngestionStartResponse>('/ingestion/jobs', payload)
  },
  cancel(id: string): Promise<IngestionActionResult> {
    return apiPost<IngestionActionResult>(`/ingestion/jobs/${id}/cancel`, {})
  },
  retry(id: string): Promise<IngestionActionResult> {
    return apiPost<IngestionActionResult>(`/ingestion/jobs/${id}/retry`, {})
  },
  // Real connector status route (datasets/connectors does not exist).
  connectors(): Promise<ConnectorsListResponse> {
    return apiGet<ConnectorsListResponse>('/ingestion/connectors')
  },
  testConnector(name: string, parameters?: Record<string, unknown>): Promise<ConnectorTestResponse> {
    return apiPost<ConnectorTestResponse>(`/ingestion/connectors/${name}/test`, parameters ? { parameters } : {})
  },
  // Per-source observation coverage: the closest real equivalent to "ingestion stats".
  coverage(): Promise<IngestionCoverageResponse> {
    return apiGet<IngestionCoverageResponse>('/ingestion/coverage')
  },
}

// climate

export const climateApi = {
  metrics(): Promise<MetricCatalogueResponse> {
    return apiGet<MetricCatalogueResponse>('/climate/metrics')
  },
  regions(params: { start?: string; end?: string; region?: string } = {}): Promise<RegionsListResponse> {
    return apiGet<RegionsListResponse>('/climate/regions', params)
  },
  region(code: string, params: { start?: string; end?: string } = {}): Promise<RegionDetailResponse> {
    return apiGet<RegionDetailResponse>(`/climate/regions/${code}`, params)
  },
  observations(
    params: PageParams & RangeParams & {
      metrics?: string
      quality?: string
      sort?: string
      datasets?: string
      countries?: string
      location_codes?: string
    } = {},
  ): Promise<ObservationsListResponse> {
    return apiGet<ObservationsListResponse>('/climate/observations', params)
  },
  // Real trend route is the plural /climate/trends.
  trend(params: TrendParams): Promise<TrendResponse> {
    return apiGet<TrendResponse>('/climate/trends', params)
  },
  // Real comparison route is /climate/comparison.
  compareRegions(params: RangeParams & { metric?: Metric; frequency?: string; regions?: string[] } = {}): Promise<RegionComparison> {
    return apiGet<RegionComparison>('/climate/comparison', {
      ...params,
      regions: params.regions ? params.regions.join(',') : undefined,
    })
  },
  anomalies(
    params: RangeParams & {
      method?: 'statistical_baseline' | 'threshold' | 'isolation_forest'
      metric?: Metric
      threshold_sigma?: number
      absolute_threshold?: number
      direction?: 'both' | 'above' | 'below'
    } = {},
  ): Promise<AnomalyResponse> {
    return apiGet<AnomalyResponse>('/climate/anomalies', params)
  },
  scanAnomalies(payload: {
    metric?: Metric
    method?: 'statistical_baseline' | 'threshold' | 'isolation_forest'
    threshold_sigma?: number
    absolute_threshold?: number
    direction?: 'both' | 'above' | 'below'
    region?: string
    location?: string
    start?: string
    end?: string
    limit_rows?: number
    persist_limit?: number
    dry_run?: boolean
  }): Promise<AnomalyScanResponse> {
    return apiPost<AnomalyScanResponse>('/climate/anomalies/scan', payload)
  },
  // Real correlation route is plural: /climate/correlations.
  correlation(params: RangeParams & { metrics?: Metric[] } = {}): Promise<CorrelationResponse> {
    return apiGet<CorrelationResponse>('/climate/correlations', {
      ...params,
      metrics: params.metrics ? params.metrics.join(',') : undefined,
    })
  },
  // Real map route: /climate/map-data.
  map(params: RangeParams & { metrics?: Metric[]; quality?: string } = {}): Promise<MapDataResponse> {
    return apiGet<MapDataResponse>('/climate/map-data', {
      ...params,
      metrics: params.metrics ? params.metrics.join(',') : undefined,
    })
  },
  quality(params: RangeParams & { datasets?: string; regions?: string[] } = {}): Promise<DataQualityResponse> {
    return apiGet<DataQualityResponse>('/climate/quality', {
      ...params,
      regions: params.regions ? params.regions.join(',') : undefined,
    })
  },
  history(params: RangeParams & { metric?: Metric; base_from?: string; base_to?: string; group_by?: string } = {}): Promise<HistoryResponse> {
    return apiGet<HistoryResponse>('/climate/history', params)
  },
  stations(params: { regions?: string; countries?: string } = {}): Promise<StationsResponse> {
    return apiGet<StationsResponse>('/climate/stations', params)
  },
  filterOptions(): Promise<FilterOptionsResponse> {
    return apiGet<FilterOptionsResponse>('/climate/filter-options')
  },
  // Single observations-CSV download endpoint of the platform (analyst/admin only, 401/403 otherwise).
  exportCsv(params: RangeParams & { metric?: Metric; quality?: string; sort?: string; regions?: string[]; datasets?: string[]; countries?: string[]; rows?: number }): Promise<void> {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === '') continue
      search.set(key, Array.isArray(value) ? value.join(',') : String(value))
    }
    const query = search.toString()
    return apiDownload(`/climate/export${query ? `?${query}` : ''}`, 'earthscape-observations.csv')
  },
}

// dashboard

export const dashboardApi = {
  summary(params: { region?: string; days?: number } = {}): Promise<DashboardSummary> {
    return apiGet<DashboardSummary>('/dashboard/summary', params)
  },
  activity(params: { page?: number; page_size?: number } = {}): Promise<DashboardActivityResponse> {
    return apiGet<DashboardActivityResponse>('/dashboard/activity', params)
  },
}

// analytics

export const analyticsApi = {
  summaries(params: { metric?: string; workflow?: string; processing_mode?: string; page?: number } = {}): Promise<AnalyticsSummariesResponse> {
    return apiGet<AnalyticsSummariesResponse>('/analytics/summaries', params)
  },
  // /analytics/export does not exist; the CSV export route is /climate/export.
  exportCsv(params: RangeParams & { metric?: Metric }): Promise<void> {
    return climateApi.exportCsv(params)
  },
}

// processing

export const processingApi = {
  workflows(): Promise<ProcessingWorkflow[]> {
    return apiGet<ProcessingWorkflow[]>('/processing/workflows')
  },
  list(params: PageParams & { status?: string; engine?: string; workflow?: string } = {}): Promise<Paginated<ProcessingJob>> {
    return apiGet<Paginated<ProcessingJob>>('/processing/', params)
  },
  get(id: string): Promise<ProcessingJob> {
    return apiGet<ProcessingJob>(`/processing/${id}`)
  },
  // Request key is `parameters`, not `params`.
  start(payload: { workflow: string; parameters?: Record<string, unknown>; dataset_id?: string }): Promise<ProcessingJob> {
    return apiPost<ProcessingJob>('/processing/', payload)
  },
  cancel(id: string): Promise<ProcessingJob> {
    return apiPost<ProcessingJob>(`/processing/${id}/cancel`, {})
  },
  logs(id: string): Promise<ProcessingLogsResponse> {
    return apiGet<ProcessingLogsResponse>(`/processing/${id}/logs`)
  },
  // Bare array of a single composite health object.
  engines(): Promise<EngineHealth[]> {
    return apiGet<EngineHealth[]>('/processing/engines')
  },
  dataLake(): Promise<DataLakeResponse> {
    return apiGet<DataLakeResponse>('/processing/data-lake')
  },
  monitor(params: PageParams = {}): Promise<ProcessingMonitorResponse> {
    return apiGet<ProcessingMonitorResponse>('/processing/monitor', params)
  },
}

// models

export const modelsApi = {
  catalogue(): Promise<ModelCatalogue> {
    return apiGet<ModelCatalogue>('/models/catalogue')
  },
  trainingStatus(): Promise<TrainingStatusResponse> {
    return apiGet<TrainingStatusResponse>('/models/training-status')
  },
  list(params: PageParams & { status?: string; algorithm?: string; target_variable?: string } = {}): Promise<Paginated<ModelRecord>> {
    return apiGet<Paginated<ModelRecord>>('/models/', params)
  },
  get(id: string): Promise<ModelRecord> {
    return apiGet<ModelRecord>(`/models/${id}`)
  },
  // 202 TrainingOutcome; training runs in the background executor.
  train(payload: TrainModelsBody): Promise<TrainingOutcome> {
    return apiPost<TrainingOutcome>('/models/train', payload)
  },
  activate(id: string): Promise<ModelRecord> {
    return apiPost<ModelRecord>(`/models/${id}/activate`, {})
  },
  retire(id: string): Promise<ModelRecord> {
    return apiPost<ModelRecord>(`/models/${id}/retire`, {})
  },
  retrain(id: string): Promise<TrainingOutcome> {
    return apiPost<TrainingOutcome>(`/models/${id}/retrain`, {})
  },
  artifact(id: string, kind: 'report' | 'metrics' | 'features' | 'model'): Promise<Record<string, unknown>> {
    return apiGet<Record<string, unknown>>(`/models/${id}/artifacts/${kind}`)
  },
}

// predictions

export const predictionsApi = {
  // Real create route is the collection POST /predictions/.
  forecast(payload: PredictionCreateBody): Promise<PredictionRecord> {
    // A multi-year horizon is generated day by day, so this request outlives the
    // default 45 s client timeout and must not be aborted mid-projection.
    return apiPost<PredictionRecord>('/predictions/', payload, { timeout: 900_000 })
  },
  list(params: { model_id?: string; target_variable?: string; region_code?: string; location_code?: string; limit?: number } = {}): Promise<PredictionListResponse> {
    return apiGet<PredictionListResponse>('/predictions/', params)
  },
  get(id: string): Promise<PredictionRecord> {
    return apiGet<PredictionRecord>(`/predictions/${id}`)
  },
  summary(params: { region_code?: string; location_code?: string; target_variable?: string } = {}): Promise<import('../types/api').ForecastSummaryDetail> {
    return apiGet<import('../types/api').ForecastSummaryDetail>('/predictions/summary', params)
  },
  remove(id: string): Promise<void> {
    return apiDelete<void>(`/predictions/${id}`)
  },
  download(id: string, filename: string): Promise<void> {
    return apiDownload(`/predictions/${id}/export`, filename)
  },
  // Anomaly scanning lives at POST /climate/anomalies/scan (no /predictions/anomaly-scan exists).
  anomalyScan(payload: Parameters<typeof climateApi.scanAnomalies>[0]): Promise<AnomalyScanResponse> {
    return climateApi.scanAnomalies(payload)
  },
}

// alerts

export const alertsApi = {
  summary(): Promise<AlertSummaryResponse> {
    return apiGet<AlertSummaryResponse>('/alerts/summary')
  },
  list(params: PageParams & { severity?: string; status?: string; region?: string; rule_key?: string; rule_id?: string; start_date?: string; end_date?: string } = {}): Promise<AlertsListResponse> {
    return apiGet<AlertsListResponse>('/alerts/', params)
  },
  get(id: string): Promise<Alert> {
    return apiGet<{ alert: Alert }>(`/alerts/${id}`).then((response) => response.alert)
  },
  acknowledge(id: string, comment?: string): Promise<Alert> {
    return apiPost<{ alert: Alert }>(`/alerts/${id}/acknowledge`, comment ? { comment } : {}).then((response) => response.alert)
  },
  // `note` is required by the backend for resolve/dismiss.
  resolve(id: string, note: string, status: 'resolved' | 'dismissed' = 'resolved'): Promise<Alert> {
    return apiPost<{ alert: Alert }>(`/alerts/${id}/resolve`, { note, status }).then((response) => response.alert)
  },
  rules(params: PageParams & { enabled?: string; search?: string } = {}): Promise<AlertRulesListResponse> {
    return apiGet<AlertRulesListResponse>('/alerts/rules', params)
  },
  createRule(payload: Record<string, unknown>): Promise<AlertRule> {
    return apiPost<{ rule: AlertRule }>('/alerts/rules', payload).then((response) => response.rule)
  },
  updateRule(id: string, changes: Record<string, unknown>): Promise<AlertRule> {
    return apiPatch<{ rule: AlertRule }>(`/alerts/rules/${id}`, changes).then((response) => response.rule)
  },
  deleteRule(id: string): Promise<AlertRuleDeleteResponse> {
    return apiDelete<AlertRuleDeleteResponse>(`/alerts/rules/${id}`)
  },
  setRuleActive(id: string, enabled: boolean): Promise<AlertRule> {
    return apiPost<{ rule: AlertRule }>(`/alerts/rules/${id}/active`, { enabled }).then((response) => response.rule)
  },
  scan(): Promise<AlertScanResponse> {
    return apiPost<AlertScanResponse>('/alerts/scan', {})
  },
  templates(): Promise<AlertTemplatesResponse> {
    return apiGet<AlertTemplatesResponse>('/alerts/templates')
  },
}

// notifications

export const notificationsApi = {
  list(params: PageParams & { unread?: string; severity?: string; type?: string } = {}): Promise<NotificationsListResponse> {
    return apiGet<NotificationsListResponse>('/notifications/', params)
  },
  markRead(id: string): Promise<Notification> {
    return apiPost<{ notification: Notification }>(`/notifications/${id}/read`, {}).then((response) => response.notification)
  },
  acknowledge(id: string): Promise<Notification> {
    return apiPost<{ notification: Notification }>(`/notifications/${id}/acknowledge`, {}).then((response) => response.notification)
  },
  markAllRead(): Promise<MarkAllReadResponse> {
    return apiPost<MarkAllReadResponse>('/notifications/read-all', {})
  },
  unread(): Promise<UnreadCountResponse> {
    return apiGet<UnreadCountResponse>('/notifications/unread-count')
  },
}

// reports

export const reportsApi = {
  types(): Promise<ReportTypesResponse> {
    return apiGet<ReportTypesResponse>('/reports/types')
  },
  summary(): Promise<ReportSummaryResponse> {
    return apiGet<ReportSummaryResponse>('/reports/summary')
  },
  list(params: PageParams & { status?: string; report_type?: string; format?: string } = {}): Promise<ReportsListResponse> {
    return apiGet<ReportsListResponse>('/reports/', params)
  },
  get(id: string): Promise<ReportRecord> {
    return apiGet<ReportRecord>(`/reports/${id}`)
  },
  create(payload: { report_type: string; format?: 'csv' | 'pdf' | 'json'; filters?: Record<string, unknown>; title?: string }): Promise<ReportRecord> {
    return apiPost<ReportRecord>('/reports/', payload)
  },
  cancel(id: string): Promise<void> {
    return apiPost<void>(`/reports/${id}/cancel`, {})
  },
  remove(id: string): Promise<ReportDeleteResponse> {
    return apiDelete<ReportDeleteResponse>(`/reports/${id}`)
  },
  download(id: string, filename: string): Promise<void> {
    return apiDownload(`/reports/${id}/download`, filename)
  },
  // /reports/export does not exist; direct data export goes through /climate/export.
  instantExport(params: Record<string, string>): Promise<void> {
    return climateApi.exportCsv(params)
  },
}

// support

export const supportApi = {
  summary(): Promise<SupportSummary> {
    return apiGet<SupportSummary>('/support/summary')
  },
  categories(): Promise<SupportCategoriesResponse> {
    return apiGet<SupportCategoriesResponse>('/support/categories')
  },
  list(params: PageParams & { status?: string; category?: string; priority?: string; search?: string } = {}): Promise<SupportListResponse> {
    return apiGet<SupportListResponse>('/support/tickets', params)
  },
  get(id: string): Promise<SupportTicket> {
    return apiGet<SupportTicket>(`/support/tickets/${id}`)
  },
  create(payload: {
    subject: string
    description: string
    category: string
    priority?: string
    page_context?: string
    attached_dataset_id?: string
  }): Promise<SupportTicket> {
    return apiPost<SupportTicket>('/support/tickets', payload)
  },
  update(id: string, changes: Record<string, unknown>): Promise<SupportTicket> {
    return apiPatch<SupportTicket>(`/support/tickets/${id}`, changes)
  },
  message(id: string, body: string, internal = false): Promise<SupportTicket> {
    return apiPost<SupportTicket>(`/support/tickets/${id}/messages`, { body, internal })
  },
  close(id: string, resolution: string, status: 'resolved' | 'closed' = 'closed'): Promise<SupportTicket> {
    return apiPost<SupportTicket>(`/support/tickets/${id}/close`, { resolution, status })
  },
  remove(id: string): Promise<TicketDeleteResponse> {
    return apiDelete<TicketDeleteResponse>(`/support/tickets/${id}`)
  },
}

// feedback

export const feedbackApi = {
  summary(): Promise<FeedbackSummary> {
    return apiGet<FeedbackSummary>('/feedback/summary')
  },
  list(params: PageParams & { rating?: string; status?: string; category?: string } = {}): Promise<FeedbackListResponse> {
    return apiGet<FeedbackListResponse>('/feedback/', params)
  },
  get(id: string): Promise<FeedbackRecord> {
    return apiGet<FeedbackRecord>(`/feedback/${id}`)
  },
  create(payload: { rating: number; message: string; category: string; page_context?: string }): Promise<FeedbackRecord> {
    return apiPost<FeedbackRecord>('/feedback/', payload)
  },
  update(id: string, changes: Record<string, unknown>): Promise<FeedbackRecord> {
    return apiPatch<FeedbackRecord>(`/feedback/${id}`, changes)
  },
}

// system

export const systemApi = {
  health(): Promise<import('../types/api').HealthResponse> {
    return apiGet('/health')
  },
  status(): Promise<SystemStatusResponse> {
    return apiGet<SystemStatusResponse>('/system/status')
  },
  metrics(): Promise<SystemMetricsResponse> {
    return apiGet<SystemMetricsResponse>('/system/metrics')
  },
  endpoints(): Promise<{ groups: EndpointGroup[] }> {
    return apiGet<{ groups: EndpointGroup[] }>('/system/endpoints')
  },
  backups(): Promise<BackupsResponse> {
    return apiGet<BackupsResponse>('/system/backups')
  },
  runBackup(): Promise<BackupRunResponse> {
    return apiPost<BackupRunResponse>('/system/backups', {})
  },
  dependencies(): Promise<DependenciesResponse> {
    return apiGet<DependenciesResponse>('/system/dependencies')
  },
  services(): Promise<ServicesResponse> {
    return apiGet<ServicesResponse>('/system/services')
  },
}

export const api = {
  auth: authApi,
  users: usersApi,
  datasets: datasetsApi,
  fileAnalytics: fileAnalyticsApi,
  ingestion: ingestionApi,
  climate: climateApi,
  dashboard: dashboardApi,
  analytics: analyticsApi,
  processing: processingApi,
  models: modelsApi,
  predictions: predictionsApi,
  alerts: alertsApi,
  notifications: notificationsApi,
  reports: reportsApi,
  support: supportApi,
  feedback: feedbackApi,
  system: systemApi,
}

export default api
