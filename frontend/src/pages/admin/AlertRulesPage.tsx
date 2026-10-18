import { useCallback, useEffect, useMemo, useState } from 'react'
import { BellRing, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import {
  Badge,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  FilterBar,
  Hint,
  Modal,
  MultiSelect,
  PageHeader,
  Panel,
  Pagination,
  RefreshButton,
  Select,
  StatusBadge,
  TableSkeleton,
  Toggle,
  type Column,
  type Option,
} from '../../components/ui'
import { ActionFeedback, Breakdown, CodeCell, type Feedback } from './components/AdminBits'
import { PAGE_SIZE, ROLE_VALUES, describeError, noteFrom, roleLabel, unwrapOne } from './components/adminUtils'
import { formatNumber, humanise, metricLabel, relativeTime, severityTone } from '../../lib/format'
import type { Alert, AlertRule, Metric, MetricDefinition } from '../../types/api'

type Operator = 'gt' | 'lt' | 'between' | 'outside_band' | 'deviation_gt_sigma' | 'anomaly'

const FALLBACK_OPERATORS: Operator[] = ['gt', 'lt', 'between', 'outside_band', 'deviation_gt_sigma', 'anomaly']
const BAND_OPERATORS: Operator[] = ['between', 'outside_band']
/** The typed contract allows more severities than the rule engine accepts. */
const FALLBACK_SEVERITIES = ['info', 'warning', 'critical']

interface RuleTemplate {
  key: string
  label: string
  description: string
  defaults: Record<string, unknown>
  supported_variable?: boolean
}

interface EvaluationSummary {
  rules_evaluated?: number
  rules_skipped_cooldown?: number
  rules_with_errors?: number
  observations_scanned?: number
  predictions_scanned?: number
  alerts_created?: number
  notifications_queued?: number
  suppressed_by_dedupe?: number
  duration_ms?: number
}

interface ScanResponse {
  job?: unknown
  matched?: number
  detail?: string
  evaluation?: EvaluationSummary
  scheduler?: Record<string, unknown>
  note?: string
}

interface RuleFormState {
  name: string
  description: string
  target_variable: string
  operator: Operator
  value: string
  value_upper: string
  sigma: string
  severity: string
  cooldown_minutes: string
  is_enabled: boolean
  regions: string[]
  location_codes: string
  countries: string[]
  datasets: string[]
  applicable_sources: string[]
  roleRecipients: string[]
  userRecipients: string[]
  everyone: boolean
}

/** The rule engine stores evaluation state as an open record; only real values are read. */
function stateNumber(state: Record<string, unknown> | undefined, key: string): number | null {
  const value = state?.[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stateText(state: Record<string, unknown> | undefined, key: string): string | null {
  const value = state?.[key]
  return typeof value === 'string' && value.length > 0 ? value : null
}

/**
 * `GET /api/v1/climate/metrics` publishes `range_min` / `range_max` as nullable numbers, so a
 * metric can legitimately have no published bounds. Nothing is inferred here: when both bounds
 * are null the copy states that the API did not publish them.
 */
function metricBoundsText(entry: MetricDefinition): string {
  if (entry.range_min === null && entry.range_max === null) return 'not published'
  const low = entry.range_min === null ? '—' : entry.range_min
  const high = entry.range_max === null ? '—' : entry.range_max
  return `${low}…${high}${entry.unit ? ` ${entry.unit}` : ''}`
}

/**
 * `/alerts/rules` stores `created_by` as a bare account id and returns no display name, so the
 * name is looked up in the account directory this page already loads. An id that directory does
 * not cover is shown as the id tail rather than turned into a made-up name.
 */
function createdByLabel(createdBy: string | null | undefined, names: Map<string, string>): string {
  if (!createdBy) return 'not recorded'
  const name = names.get(createdBy)
  return name ? `${name} (account ${createdBy.slice(-6)})` : `account ${createdBy.slice(-6)}`
}

function emptyForm(): RuleFormState {
  return {
    name: '',
    description: '',
    target_variable: 'temperature_c',
    operator: 'gt',
    value: '',
    value_upper: '',
    sigma: '2.5',
    severity: 'warning',
    cooldown_minutes: '720',
    is_enabled: true,
    regions: [],
    location_codes: '',
    countries: [],
    datasets: [],
    applicable_sources: ['observations'],
    roleRecipients: ['role:climate_analyst'],
    userRecipients: [],
    everyone: false,
  }
}

function formFromRule(rule: AlertRule): RuleFormState {
  const recipients = rule.recipients ?? []
  return {
    name: rule.name,
    description: rule.description ?? '',
    target_variable: rule.target_variable,
    operator: rule.condition?.operator ?? 'gt',
    value: rule.condition?.value === null || rule.condition?.value === undefined ? '' : String(rule.condition.value),
    value_upper:
      rule.condition?.value_upper === null || rule.condition?.value_upper === undefined ? '' : String(rule.condition.value_upper),
    sigma: rule.condition?.sigma === null || rule.condition?.sigma === undefined ? '' : String(rule.condition.sigma),
    severity: rule.severity,
    cooldown_minutes: String(rule.cooldown_minutes ?? 0),
    is_enabled: rule.is_enabled,
    regions: rule.scope?.regions ?? [],
    location_codes: (rule.scope?.location_codes ?? []).join(', '),
    countries: rule.scope?.countries ?? [],
    datasets: rule.scope?.datasets ?? [],
    applicable_sources: rule.applicable_sources ?? ['observations'],
    roleRecipients: recipients.filter((entry) => entry.startsWith('role:')),
    userRecipients: recipients.filter((entry) => /^[0-9a-f]{24}$/i.test(entry)),
    everyone: recipients.includes('all'),
  }
}

/** Recipients the engine accepts: `role:<role>`, 24-hex user ids, or the `all` token. */
function buildRecipients(form: RuleFormState): string[] {
  const list = [...form.roleRecipients, ...form.userRecipients]
  if (form.everyone) list.push('all')
  return Array.from(new Set(list.map((entry) => entry.trim()).filter(Boolean)))
}

function describeCondition(rule: AlertRule): string {
  const condition = rule.condition ?? { operator: 'gt' as Operator }
  switch (condition.operator) {
    case 'gt':
      return `greater than ${condition.value ?? '—'}`
    case 'lt':
      return `less than ${condition.value ?? '—'}`
    case 'between':
      return `within ${condition.value ?? '—'} … ${condition.value_upper ?? '—'}`
    case 'outside_band':
      return `outside ${condition.value ?? '—'} … ${condition.value_upper ?? '—'}`
    case 'deviation_gt_sigma':
      return `more than ${condition.sigma ?? '—'} σ from the stored climatology`
    case 'anomaly':
      return `flagged anomalous (σ ${condition.sigma ?? '—'})`
    default:
      return String(condition.operator)
  }
}

/**
 * Alert-rule administration.
 *
 * Rules are evaluated by the server-side engine (`backend/apps/alerts/rules.py`), either
 * by its bounded scheduler or on demand through `POST /api/v1/alerts/scan`. This page only
 * edits rule documents; every number it shows about an evaluation comes from the API.
 */
export default function AlertRulesPage() {
  const { can } = useAuth()
  const canManage = can('alerts:manage')

  const [enabledFilter, setEnabledFilter] = useState('')
  const [page, setPage] = useState(1)
  const [editing, setEditing] = useState<AlertRule | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<RuleFormState>(emptyForm())
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [feedback, setFeedback] = useState<Feedback | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState<AlertRule | null>(null)
  const [confirmScan, setConfirmScan] = useState(false)
  const [lastScan, setLastScan] = useState<ScanResponse | null>(null)
  const [inspected, setInspected] = useState<AlertRule | null>(null)

  const rules = useAsync(
    () => api.alerts.rules({ page, page_size: PAGE_SIZE, enabled: enabledFilter || undefined }),
    [page, enabledFilter],
  )
  const templates = useAsync(() => api.alerts.templates(), [])
  const metrics = useAsync(() => api.climate.metrics(), [])
  const regions = useAsync(() => api.climate.regions(), [])
  const datasets = useAsync(() => api.datasets.list({ page: 1, page_size: 50 }), [])
  const accounts = useAsync(() => api.users.list({ page: 1, page_size: 100 }), [])
  const summary = useAsync(() => api.alerts.summary(), [])
  const fired = useAsync(
    () => (inspected ? api.alerts.list({ rule_id: inspected.id, page: 1, page_size: 5 }) : Promise.resolve(null)),
    [inspected?.id],
    { enabled: Boolean(inspected) },
  )

  const rows = rules.data?.items ?? []

  const metricList = useMemo<MetricDefinition[]>(() => metrics.data?.metrics ?? [], [metrics.data])
  const operators = useMemo<string[]>(() => {
    const payload = templates.data as unknown as { operators?: string[] } | null
    return payload?.operators?.length ? payload.operators : FALLBACK_OPERATORS
  }, [templates.data])
  const severities = useMemo<string[]>(() => {
    const payload = templates.data as unknown as { severities?: string[] } | null
    return payload?.severities?.length ? payload.severities : FALLBACK_SEVERITIES
  }, [templates.data])
  const templateList = useMemo<RuleTemplate[]>(() => {
    const payload = templates.data as unknown as { templates?: unknown } | null
    const raw = payload?.templates
    if (!Array.isArray(raw)) return []
    return raw
      .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === 'object')
      .map((entry) => ({
        key: String(entry.key ?? entry.id ?? ''),
        label: String(entry.label ?? entry.key ?? ''),
        description: String(entry.description ?? ''),
        defaults: (entry.defaults ?? {}) as Record<string, unknown>,
        supported_variable: typeof entry.supported_variable === 'boolean' ? entry.supported_variable : undefined,
      }))
  }, [templates.data])

  const scopeOptions = useMemo(() => {
    const list = regions.data?.regions ?? []
    const countries = list
      .map((region) => region.country)
      .filter((country): country is string => typeof country === 'string' && country.length > 0)
    return {
      regions: list.map((region) => ({ value: region.code, label: `${region.code} — ${region.name}` })),
      countries: Array.from(new Set(countries)).sort().map((country) => ({ value: country, label: country })),
    }
  }, [regions.data])

  const datasetOptions = useMemo<Option[]>(
    () => (datasets.data?.items ?? []).map((dataset) => ({ value: dataset.id, label: `${dataset.name} · ${humanise(dataset.status)}` })),
    [datasets.data],
  )

  const recipientOptions = useMemo<Option[]>(
    () => (accounts.data?.items ?? []).map((account) => ({ value: account.id, label: `${account.full_name} — ${account.email}`, hint: roleLabel(account.role) })),
    [accounts.data],
  )

  const accountNames = useMemo<Map<string, string>>(
    () => new Map((accounts.data?.items ?? []).map((account) => [account.id, account.full_name] as const)),
    [accounts.data],
  )

  const reload = useCallback(async () => {
    setFeedback(null)
    await rules.refetch()
    await summary.refetch()
  }, [rules, summary])

  const setField = useCallback(<K extends keyof RuleFormState>(key: K, value: RuleFormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }))
    setFieldErrors((current) => ({ ...current, [key as string]: '' }))
  }, [])

  /** Local validation against the metric ranges the API itself publishes. */
  function validate(): boolean {
    const errors: Record<string, string> = {}
    if (form.name.trim().length < 3) errors.name = 'Give the rule a name of at least three characters.'
    const definition = metricList.find((entry) => entry.field === form.target_variable)
    const numberValue = form.value === '' ? null : Number(form.value)
    if (['gt', 'lt', ...BAND_OPERATORS].includes(form.operator) && numberValue === null) {
      errors.value = 'This operator needs a threshold value.'
    }
    if (numberValue !== null && Number.isNaN(numberValue)) errors.value = 'Threshold must be a number.'
    if (numberValue !== null && !Number.isNaN(numberValue) && definition) {
      if (
        (definition.range_min !== null && numberValue < definition.range_min) ||
        (definition.range_max !== null && numberValue > definition.range_max)
      ) {
        errors.value = `Outside the range the API reports for ${definition.label} (published range ${metricBoundsText(definition)}). The engine will still evaluate it, but the alert can never fire.`
      }
    }
    if (BAND_OPERATORS.includes(form.operator)) {
      const upper = Number(form.value_upper)
      if (form.value_upper === '' || Number.isNaN(upper)) errors.value_upper = `The ${form.operator} operator needs an upper bound.`
      else if (numberValue !== null && upper <= numberValue) errors.value_upper = 'Upper bound must be greater than the lower bound.'
    }
    if (['deviation_gt_sigma', 'anomaly'].includes(form.operator)) {
      const sigma = Number(form.sigma)
      if (form.sigma === '' || Number.isNaN(sigma)) errors.sigma = 'A deviation rule needs a σ value.'
      else if (sigma < 0.1 || sigma > 20) errors.sigma = 'σ must be between 0.1 and 20 (serializer limit).'
    }
    const cooldown = Number(form.cooldown_minutes)
    if (form.cooldown_minutes === '' || Number.isNaN(cooldown)) errors.cooldown_minutes = 'Cooldown must be a number of minutes (0 to disable).'
    else if (cooldown < 0 || cooldown > 10_080) errors.cooldown_minutes = 'The engine accepts 0–10080 minutes (a week).'
    if (buildRecipients(form).length === 0) errors.recipients = 'Pick at least one recipient role, account, or “everyone”.'
    setFieldErrors(errors)
    return Object.keys(errors).length === 0
  }

  function payload(): Record<string, unknown> {
    const condition: Record<string, unknown> = { operator: form.operator }
    if (['gt', 'lt', ...BAND_OPERATORS].includes(form.operator)) condition.value = Number(form.value)
    if (BAND_OPERATORS.includes(form.operator)) condition.value_upper = Number(form.value_upper)
    if (['deviation_gt_sigma', 'anomaly'].includes(form.operator)) condition.sigma = Number(form.sigma)
    return {
      name: form.name.trim(),
      description: form.description.trim() || undefined,
      target_variable: form.target_variable,
      severity: form.severity,
      is_enabled: form.is_enabled,
      cooldown_minutes: Number(form.cooldown_minutes),
      condition,
      scope: {
        regions: form.regions,
        location_codes: form.location_codes.split(/[,\s]+/).map((entry) => entry.trim()).filter(Boolean),
        countries: form.countries,
        datasets: form.datasets,
      },
      applicable_sources: form.applicable_sources,
      recipients: buildRecipients(form),
    }
  }

  async function save() {
    if (!validate()) return
    setBusy(true)
    setFeedback(null)
    try {
      if (editing) {
        const raw = await api.alerts.updateRule(editing.id, payload())
        const rule = unwrapOne<AlertRule>(raw)
        setFeedback({ tone: 'success', text: `Rule “${rule?.name ?? editing.name}” updated. The engine re-reads rules on its next pass.` })
      } else {
        const raw = await api.alerts.createRule(payload())
        const rule = unwrapOne<AlertRule>(raw)
        setFeedback({ tone: 'success', text: `Rule “${rule?.name ?? form.name}” created${rule?.is_enabled === false ? ' (stored disabled)' : ''}. It is evaluated on the scheduler’s next pass.` })
      }
      closeForm()
      await rules.refetch()
      await summary.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFieldErrors(described.fields)
      setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
      await rules.refetch()
    } finally {
      setBusy(false)
    }
  }

  function closeForm() {
    setEditing(null)
    setCreating(false)
    setForm(emptyForm())
    setFieldErrors({})
  }

  const toggleActive = useCallback(
    async (rule: AlertRule, enabled: boolean) => {
      setFeedback(null)
      try {
        const raw = await api.alerts.setRuleActive(rule.id, enabled)
        const updated = unwrapOne<AlertRule>(raw)
        setFeedback({
          tone: 'success',
          text: `“${rule.name}” is now ${enabled ? 'enabled' : 'disabled'}.${updated?.evaluation_state?.note ? ` ${updated.evaluation_state.note}` : ''}`,
        })
      } catch (cause) {
        const described = describeError(cause)
        setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
      } finally {
        await rules.refetch()
      }
    },
    [rules],
  )

  const removeRule = useCallback(async () => {
    const target = confirmDelete
    if (!target) return
    setBusy(true)
    setFeedback(null)
    try {
      const raw = await api.alerts.deleteRule(target.id)
      const note = noteFrom(raw)
      setFeedback({ tone: 'success', text: `Rule “${target.name}” deleted.${note ? ` ${note}` : ''}` })
      setConfirmDelete(null)
      if (inspected?.id === target.id) setInspected(null)
      await rules.refetch()
      await summary.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
      await rules.refetch()
    } finally {
      setBusy(false)
    }
  }, [confirmDelete, inspected, rules, summary])

  const runScan = useCallback(async () => {
    setBusy(true)
    setFeedback(null)
    try {
      const raw = (await api.alerts.scan()) as unknown as ScanResponse
      setLastScan(raw)
      const evaluation = raw.evaluation
      setFeedback({
        tone: 'info',
        text:
          raw.detail ??
          `Evaluation finished${evaluation ? `: ${evaluation.rules_evaluated ?? 0} rule(s), ${evaluation.alerts_created ?? 0} alert(s) created` : ''}.`,
      })
      await Promise.all([rules.refetch(), summary.refetch()])
      if (inspected) await fired.refetch()
    } catch (cause) {
      const described = describeError(cause)
      setFeedback({ tone: 'error', text: `${described.message}${described.requestId ? ` (ref ${described.requestId})` : ''}` })
    } finally {
      setBusy(false)
      setConfirmScan(false)
    }
  }, [fired, inspected, rules, summary])

  useEffect(() => setPage(1), [enabledFilter])

  const columns = useMemo<Column<AlertRule>[]>(
    () => [
      {
        key: 'rule',
        header: 'Rule',
        cell: (row) => (
          <div className="min-w-0">
            <button type="button" className="link truncate text-sm font-medium" onClick={() => setInspected(row)}>
              {row.name}
            </button>
            <p className="truncate text-[11px] subtle">{row.description || metricLabel(row.target_variable as Metric)}</p>
          </div>
        ),
      },
      {
        key: 'target',
        header: 'Target',
        cell: (row) => (
          <div className="text-xs">
            <p className="font-medium">{metricLabel(row.target_variable as Metric)}</p>
            <p className="subtle">
              {describeCondition(row)} {metricList.find((entry) => entry.field === row.target_variable)?.unit ?? ''}
            </p>
          </div>
        ),
      },
      {
        key: 'scope',
        header: 'Scope',
        cell: (row) => {
          const parts = [
            row.scope?.regions?.length ? `${row.scope.regions.length} region(s)` : '',
            row.scope?.location_codes?.length ? `${row.scope.location_codes.length} location(s)` : '',
            row.scope?.countries?.length ? `${row.scope.countries.length} country(ies)` : '',
            row.scope?.datasets?.length ? `${row.scope.datasets.length} dataset(s)` : '',
          ].filter(Boolean)
          return <span className="text-xs muted">{parts.length ? parts.join(', ') : 'platform-wide'}</span>
        },
      },
      { key: 'severity', header: 'Severity', cell: (row) => <Badge tone={severityTone(row.severity)}>{humanise(row.severity)}</Badge> },
      {
        key: 'cooldown',
        header: 'Cooldown',
        align: 'right',
        cell: (row) => <span className="mono text-[12px]">{row.cooldown_minutes ? `${formatNumber(row.cooldown_minutes, 0)} min` : 'off'}</span>,
      },
      {
        key: 'recipients',
        header: 'Recipients',
        cell: (row) => (
          <div className="flex flex-wrap gap-1">
            {(row.recipients ?? []).slice(0, 4).map((entry) => (
              <code key={entry} className="kbd">
                {entry.startsWith('role:') ? roleLabel(entry.slice(5)) : entry === 'all' ? 'everyone' : `${entry.slice(0, 8)}…`}
              </code>
            ))}
            {(row.recipients ?? []).length > 4 ? <span className="text-[11px] subtle">+{(row.recipients ?? []).length - 4}</span> : null}
          </div>
        ),
      },
      {
        key: 'enabled',
        header: 'Enabled',
        cell: (row) => (
          <Toggle
            checked={row.is_enabled}
            label={`Enable ${row.name}`}
            disabled={!canManage || busy}
            onChange={(next) => void toggleActive(row, next)}
          />
        ),
      },
      {
        key: 'evaluation',
        header: 'Last evaluation',
        cell: (row) => {
          const state = row.evaluation_state
          const observations = stateNumber(state, 'observations_scanned')
          const predictions = stateNumber(state, 'predictions_scanned')
          const stateNote = state ? stateText(state, 'note') : null
          const insufficient = state ? stateText(state, 'insufficient_data') : null
          const stateError = state ? stateText(state, 'error') : null
          return (
            <div className="text-[11px] muted">
              <p>
                {row.last_evaluated_at ? `ran ${relativeTime(row.last_evaluated_at)}` : 'never evaluated'}
                {row.last_triggered_at ? <span className="subtle"> · fired {relativeTime(row.last_triggered_at)}</span> : null}
              </p>
              {state ? (
                <p className="flex flex-wrap items-center gap-1">
                  <span className="subtle">
                    {observations === null ? 'scan counts not reported' : `${observations} observation(s) scanned`}
                    {predictions !== null ? ` · ${predictions} prediction(s) scanned` : ''}
                  </span>
                  {insufficient ? <Badge tone="neutral">insufficient data: {insufficient}</Badge> : null}
                  {stateError ? <Badge tone="danger">engine error: {stateError}</Badge> : null}
                  {stateNote ? <span className="subtle">{stateNote}</span> : null}
                </p>
              ) : (
                <p className="subtle">evaluation state is only returned to staff accounts</p>
              )}
            </div>
          )
        },
      },
      {
        key: 'actions',
        header: 'Actions',
        align: 'right',
        cell: (row) => (
          <div className="flex justify-end gap-1">
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Edit ${row.name}`}
              disabled={!canManage}
              title={canManage ? 'Edit this rule document' : 'Requires the alerts:manage capability'}
              onClick={() => {
                setEditing(row)
                setForm(formFromRule(row))
                setFieldErrors({})
                setCreating(false)
              }}
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              className="btn btn-secondary btn-icon"
              aria-label={`Delete ${row.name}`}
              disabled={!canManage || busy}
              title={canManage ? 'Delete the rule (already-fired alerts stay)' : 'Requires the alerts:manage capability'}
              onClick={() => setConfirmDelete(row)}
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ),
      },
    ],
    [busy, canManage, metricList, toggleActive],
  )

  return (
    <div>
      <PageHeader
        eyebrow="Administration"
        title="Alert-rule administration"
        description="Rules are evaluated by the server engine against stored observations and predictions. Thresholds, cooldowns and recipients live on the rule document; nothing here fires an alert itself."
        meta={
          <>
            <Badge tone="neutral">{rules.data ? formatNumber(rules.data.total, 0) : '—'} rule(s)</Badge>
            {summary.data ? <Badge tone="success">{summary.data.rules_enabled} enabled</Badge> : null}
            {summary.data?.last_evaluation ? <span className="subtle">last evaluation {relativeTime(summary.data.last_evaluation)}</span> : null}
            {summary.data ? <span className="subtle">{summary.data.open} open alert(s)</span> : null}
          </>
        }
        actions={
          <>
            <RefreshButton onClick={() => void reload()} busy={rules.refreshing} />
            <button
              type="button"
              className="btn btn-secondary"
              disabled={!canManage || busy}
              title={canManage ? 'Run the engine once now (POST /api/v1/alerts/scan)' : 'Requires the alerts:manage capability'}
              onClick={() => setConfirmScan(true)}
            >
              <Play className="h-4 w-4" /> Evaluate now
            </button>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!canManage}
              title={canManage ? 'Create a rule' : 'Requires the alerts:manage capability'}
              onClick={() => {
                setCreating(true)
                setEditing(null)
                setForm(emptyForm())
                setFieldErrors({})
              }}
            >
              <Plus className="h-4 w-4" /> New rule
            </button>
          </>
        }
      />

      <ActionFeedback feedback={feedback} onDismiss={() => setFeedback(null)} />
      {rules.error ? <div className="mb-4"><ErrorState error={rules.error} onRetry={() => void rules.refetch()} /></div> : null}
      {templates.error ? (
        <p className="mb-4 text-xs leading-relaxed subtle">
          {describeError(templates.error).message} — the operator and severity lists fall back to the values declared in{' '}
          <span className="mono">apps/alerts/rules.py</span>, and no template presets are offered.
        </p>
      ) : null}

      <FilterBar>
        <Field label="Enabled" htmlFor="rule-enabled-filter">
          <Select
            id="rule-enabled-filter"
            ariaLabel="Filter rules by enabled state"
            placeholder="All rules"
            value={enabledFilter}
            onChange={setEnabledFilter}
            options={[
              { value: 'true', label: 'Enabled only' },
              { value: 'false', label: 'Disabled only' },
            ]}
          />
        </Field>
        <p className="ml-auto max-w-xl text-[11px] leading-relaxed subtle">
          Recipients are addressed as <code className="kbd">role:&lt;role&gt;</code>, an account id, or <code className="kbd">all</code>.
          Delivery itself is per-recipient: the notification feed an account sees is scoped to that account by{' '}
          <span className="mono">GET /api/v1/notifications</span>.
        </p>
      </FilterBar>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Panel title="Rules" description="GET /api/v1/alerts/rules — evaluation state is included because this account is staff." dense>
          {rules.loading ? <TableSkeleton rows={5} columns={9} /> : (
            <DataTable
              caption="Alert rules"
              columns={columns}
              rows={rows}
              rowKey={(row) => row.id}
              empty="No alert rule matches this filter."
              onRowClick={(row) => setInspected(row)}
            />
          )}
          <Pagination meta={rules.data ?? undefined} onPage={setPage} label="rules" />
        </Panel>

        <div className="min-w-0 space-y-6">
          <Panel
            title="Rule templates"
            description="GET /api/v1/alerts/templates — starting payloads the engine can actually create."
            dense
            actions={<RefreshButton onClick={() => void templates.refetch()} busy={templates.refreshing} label="Reload" />}
          >
            {templates.error ? <ErrorState error={templates.error} onRetry={() => void templates.refetch()} /> : null}
            {templateList.length === 0 && !templates.loading && !templates.error ? (
              <p className="text-xs subtle">The endpoint returned no templates; build a rule from the empty form instead.</p>
            ) : null}
            <ul className="space-y-2">
              {templateList.map((template) => {
                const target = typeof template.defaults.target_variable === 'string' ? template.defaults.target_variable : ''
                const supported = template.supported_variable === false
                return (
                  <li key={template.key} className="rounded-xl border p-2.5 text-xs" style={{ borderColor: 'var(--border)' }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <BellRing className="h-3.5 w-3.5 text-[var(--text-subtle)]" aria-hidden="true" />
                      <span className="font-medium">{template.label}</span>
                      {supported ? <Badge tone="neutral">variable not stored yet</Badge> : null}
                      <button
                        type="button"
                        className="link ml-auto text-xs"
                        disabled={!canManage}
                        title={!canManage ? 'Requires the alerts:manage capability' : 'Copy these defaults into the create form'}
                        onClick={() => {
                          setCreating(true)
                          setEditing(null)
                          setForm({ ...emptyForm(), ...applyTemplate(template, metricList) })
                          setFieldErrors({})
                        }}
                      >
                        Use
                      </button>
                    </div>
                    <p className="mt-1 leading-relaxed muted">{template.description}</p>
                    {target ? <p className="text-[11px] subtle">target {metricLabel(target as Metric)}</p> : null}
                  </li>
                )
              })}
            </ul>
          </Panel>

          <Panel
            title="Engine state"
            description="The last manual evaluation plus the scheduler counters the API reports."
            dense
            footer={
              <Hint>
                Alerts already fired are never deleted when a rule is removed, and a rule inside its cooldown window is counted as{' '}
                <span className="mono">suppressed_by_cooldown</span> rather than skipped silently.
              </Hint>
            }
          >
            {lastScan?.evaluation ? (
              <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs muted">
                <li className="flex justify-between"><span>Rules evaluated</span><span className="mono">{lastScan.evaluation.rules_evaluated ?? 0}</span></li>
                <li className="flex justify-between"><span>Alerts created</span><span className="mono">{lastScan.evaluation.alerts_created ?? 0}</span></li>
                <li className="flex justify-between"><span>In cooldown</span><span className="mono">{lastScan.evaluation.rules_skipped_cooldown ?? 0}</span></li>
                <li className="flex justify-between"><span>Rule errors</span><span className="mono">{lastScan.evaluation.rules_with_errors ?? 0}</span></li>
                <li className="flex justify-between"><span>Observations scanned</span><span className="mono">{formatNumber(lastScan.evaluation.observations_scanned ?? 0, 0)}</span></li>
                <li className="flex justify-between"><span>Predictions scanned</span><span className="mono">{formatNumber(lastScan.evaluation.predictions_scanned ?? 0, 0)}</span></li>
                <li className="flex justify-between"><span>Notifications queued</span><span className="mono">{lastScan.evaluation.notifications_queued ?? 0}</span></li>
                <li className="flex justify-between"><span>Suppressed by dedupe</span><span className="mono">{lastScan.evaluation.suppressed_by_dedupe ?? 0}</span></li>
                <li className="flex justify-between"><span>Duration</span><span className="mono">{lastScan.evaluation.duration_ms ?? 0} ms</span></li>
              </ul>
            ) : (
              <p className="text-xs subtle">No manual evaluation has been run from this page yet. The numbers above only appear after an evaluation the API confirms.</p>
            )}
            {summary.data ? (
              <div className="mt-3">
                <p className="panel-title mb-1">Open alerts by severity</p>
                <Breakdown counts={summary.data.by_severity} kind="generic" empty="No open alert right now." />
              </div>
            ) : null}
            {lastScan?.scheduler ? (
              <div className="mt-3">
                <p className="panel-title mb-1">Scheduler (as reported)</p>
                <pre className="max-h-32 overflow-auto rounded-lg border p-2 text-[11px]" style={{ borderColor: 'var(--border)' }}>
                  {JSON.stringify(lastScan.scheduler, null, 2)}
                </pre>
              </div>
            ) : null}
            <p className="mt-3 text-[11px] leading-relaxed subtle">
              Notification delivery is per recipient; the platform-wide log lives in the notification centre (
              <span className="mono">/app/notifications</span>) which the API scopes to the signed-in account — this page cannot filter it by
              rule id because <span className="mono">GET /api/v1/notifications</span> accepts no rule parameter.
            </p>
          </Panel>

          {inspected ? (
            <Panel
              title={`“${inspected.name}”`}
              description="Rule document plus the alerts this rule actually fired."
              dense
              actions={
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setInspected(null)}>
                  Close
                </button>
              }
            >
              <dl className="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
                <Line label="Operator" value={inspected.condition?.operator ?? '—'} />
                <Line label="Threshold" value={`${inspected.condition?.value ?? '—'}${inspected.condition?.value_upper ? ` … ${inspected.condition.value_upper}` : ''}`} />
                <Line label="Sigma" value={inspected.condition?.sigma ?? '—'} />
                <Line label="Sources" value={(inspected.applicable_sources ?? []).join(', ') || 'any'} />
                <Line label="Created by" value={createdByLabel(inspected.created_by, accountNames)} />
                <Line label="Created" value={relativeTime(inspected.created_at)} />
              </dl>
              <p className="panel-title mt-3 mb-1">Fired alerts</p>
              {fired.error ? <ErrorState error={fired.error} onRetry={() => void fired.refetch()} /> : null}
              {fired.loading && (fired.data?.items ?? []).length === 0 ? <p className="text-xs muted">Reading alerts for this rule…</p> : null}
              {(fired.data?.items ?? []).length === 0 && !fired.loading && !fired.error ? (
                <p className="text-xs subtle">This rule has not fired an alert that the API returns for this account.</p>
              ) : null}
              <ul className="space-y-2">
                {(fired.data?.items ?? []).map((alert: Alert) => (
                  <li key={alert.id} className="rounded-xl border p-2.5 text-xs" style={{ borderColor: 'var(--border)' }}>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusBadge value={alert.status} />
                      <Badge tone={severityTone(alert.severity)}>{humanise(alert.severity)}</Badge>
                      <span className="font-medium">{metricLabel(alert.target_variable as Metric)}</span>
                      <span className="subtle">{relativeTime(alert.triggered_at)}</span>
                    </div>
                    <p className="mt-1 leading-relaxed muted">
                      value {alert.value ?? '—'}
                      {alert.unit ? ` ${alert.unit}` : ''} vs threshold/baseline {alert.threshold_or_baseline ?? '—'}
                      {typeof alert.deviation_score === 'number' ? ` (${alert.deviation_score.toFixed(1)} σ)` : ''}
                    </p>
                    <p className="mt-1 leading-relaxed">{alert.explanation}</p>
                    {alert.location_name || alert.region_code ? <p className="text-[11px] subtle">{alert.location_name ?? alert.region_code}</p> : null}
                  </li>
                ))}
              </ul>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <CodeCell value={inspected.id} label="rule id" />
                <a className="btn btn-secondary btn-sm" href={`/app/alerts?rule_id=${inspected.id}`}>
                  Open alerts view
                </a>
              </div>
            </Panel>
          ) : null}
        </div>
      </div>

      {creating || editing ? (
        <Modal
          open
          width="max-w-3xl"
          title={editing ? `Edit “${editing.name}”` : 'Create an alert rule'}
          description="Validated by the same serializer the engine uses; a rejected field comes back with the server’s own wording."
          onClose={closeForm}
          footer={
            <>
              <button type="button" className="btn btn-secondary" onClick={closeForm} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-primary" disabled={busy || !canManage} onClick={() => void save()}>
                {busy ? 'Working…' : editing ? 'Save rule' : 'Create rule'}
              </button>
            </>
          }
        >
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" htmlFor="rule-name" required error={fieldErrors.name}>
                <input id="rule-name" className="input" value={form.name} onChange={(event) => setField('name', event.target.value)} />
              </Field>
              <Field label="Target metric" htmlFor="rule-target" required hint="Choices come from GET /api/v1/climate/metrics.">
                <Select
                  id="rule-target"
                  value={form.target_variable}
                  onChange={(next) => setField('target_variable', next)}
                  options={
                    metricList.length
                      ? metricList.map((entry) => ({ value: entry.field, label: `${entry.label} (${entry.unit})`, hint: `published range ${metricBoundsText(entry)}` }))
                      : [{ value: 'temperature_c', label: 'temperature_c', hint: 'The metric catalogue could not be read, so only the field name is shown — no label or unit is inferred.' }]
                  }
                />
              </Field>
              <Field label="Severity" htmlFor="rule-severity" required hint={severities === FALLBACK_SEVERITIES ? 'Backend rule engine accepts info / warning / critical (the typed Severity union is wider).' : `Choices reported by the templates endpoint: ${severities.join(', ')}.`}>
                <Select id="rule-severity" value={form.severity} onChange={(next) => setField('severity', next)} options={severities.map((value) => ({ value, label: humanise(value) }))} />
              </Field>
              <Field label="Cooldown (minutes)" htmlFor="rule-cooldown" required error={fieldErrors.cooldown_minutes} hint="0 disables suppression; the engine accepts 0–10080.">
                <input id="rule-cooldown" type="number" min={0} max={10_080} className="input" value={form.cooldown_minutes} onChange={(event) => setField('cooldown_minutes', event.target.value)} />
              </Field>
              <Field label="Operator" htmlFor="rule-operator" required>
                <Select id="rule-operator" value={form.operator} onChange={(next) => setField('operator', next as Operator)} options={operators.map((value) => ({ value, label: operatorLabel(value) }))} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                {['gt', 'lt', ...BAND_OPERATORS].includes(form.operator) ? (
                  <Field label={BAND_OPERATORS.includes(form.operator) ? 'Lower bound' : 'Threshold'} htmlFor="rule-value" required error={fieldErrors.value}>
                    <input id="rule-value" type="number" className="input" value={form.value} onChange={(event) => setField('value', event.target.value)} />
                  </Field>
                ) : null}
                {BAND_OPERATORS.includes(form.operator) ? (
                  <Field label="Upper bound" htmlFor="rule-value-upper" required error={fieldErrors.value_upper}>
                    <input id="rule-value-upper" type="number" className="input" value={form.value_upper} onChange={(event) => setField('value_upper', event.target.value)} />
                  </Field>
                ) : null}
                {['deviation_gt_sigma', 'anomaly'].includes(form.operator) ? (
                  <Field label="Sigma" htmlFor="rule-sigma" required error={fieldErrors.sigma}>
                    <input id="rule-sigma" type="number" step="0.1" min={0.1} max={20} className="input" value={form.sigma} onChange={(event) => setField('sigma', event.target.value)} />
                  </Field>
                ) : null}
              </div>
              <Field label="Enabled" htmlFor="rule-enabled" hint="Disabled rules stay in the catalogue but the engine skips them.">
                <Toggle checked={form.is_enabled} onChange={(next) => setField('is_enabled', next)} label="Rule enabled" disabled={!canManage} />
              </Field>
            </div>

            <Field label="Description" htmlFor="rule-description">
              <textarea id="rule-description" className="textarea" rows={2} value={form.description} onChange={(event) => setField('description', event.target.value)} placeholder="What this rule watches and why" />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Regions" htmlFor="rule-regions" hint="Empty means the rule is not narrowed by region.">
                <MultiSelect options={scopeOptions.regions} value={form.regions} onChange={(next) => setField('regions', next)} placeholder="All regions" label="Regions" />
              </Field>
              <Field label="Countries" htmlFor="rule-countries">
                <MultiSelect options={scopeOptions.countries} value={form.countries} onChange={(next) => setField('countries', next)} placeholder="All countries" label="Countries" />
              </Field>
              <Field label="Location codes" htmlFor="rule-locations" hint="Comma-separated station codes, matched case-insensitively and upper-cased by the engine.">
                <input id="rule-locations" className="input" value={form.location_codes} onChange={(event) => setField('location_codes', event.target.value)} placeholder="KHI, LHE" />
              </Field>
              <Field label="Datasets" htmlFor="rule-datasets" hint="Restrict evaluation to specific dataset ids.">
                <MultiSelect options={datasetOptions} value={form.datasets} onChange={(next) => setField('datasets', next)} placeholder="Any dataset" label="Datasets" />
              </Field>
              <Field label="Evaluation sources" htmlFor="rule-sources" hint="Registry source names plus the control tokens observations / predictions.">
                <MultiSelect
                  options={[
                    { value: 'observations', label: 'observations (stored daily records)' },
                    { value: 'predictions', label: 'predictions (model output)' },
                    { value: 'nasa_power', label: 'nasa_power' },
                    { value: 'noaa_cdo', label: 'noaa_cdo' },
                    { value: 'open_meteo', label: 'open_meteo' },
                  ]}
                  value={form.applicable_sources}
                  onChange={(next) => setField('applicable_sources', next)}
                  placeholder="Any source"
                  label="Sources"
                />
              </Field>
            </div>

            <fieldset className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
              <legend className="panel-title px-1">Recipients</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Roles" htmlFor="rule-role-recipients" hint="Every account holding the role receives the alert.">
                  <MultiSelect
                  options={ROLE_VALUES.map((role) => ({ value: `role:${role}`, label: `role:${role}`, hint: roleLabel(role) }))}
                    value={form.roleRecipients}
                    onChange={(next) => setField('roleRecipients', next)}
                    placeholder="No role"
                    label="Recipient roles"
                  />
                </Field>
                <Field label="Specific accounts" htmlFor="rule-user-recipients" error={fieldErrors.recipients}>
                  <MultiSelect options={recipientOptions} value={form.userRecipients} onChange={(next) => setField('userRecipients', next)} placeholder="Search accounts" label="Accounts" />
                </Field>
              </div>
              <label className="mt-2 flex items-center gap-2 text-sm">
                <input type="checkbox" checked={form.everyone} onChange={(event) => setField('everyone', event.target.checked)} />
                Deliver to <code className="kbd">all</code> (every active account)
              </label>
              <p className="mt-2 flex flex-wrap items-center gap-1 text-[11px] subtle">
                sending to: {buildRecipients(form).length ? buildRecipients(form).map((entry) => <code key={entry} className="kbd">{entry}</code>) : 'nothing selected'}
              </p>
            </fieldset>

            <Hint>
              In-app delivery always happens; email only when SMTP is configured on the server, and the notification engine deduplicates
              repeat alerts on the same rule and period (<span className="mono">suppressed_by_dedupe</span>).
            </Hint>
          </div>
        </Modal>
      ) : null}

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        title="Delete this alert rule?"
        tone="danger"
        busy={busy}
        confirmLabel="Delete rule"
        onClose={() => setConfirmDelete(null)}
        onConfirm={() => void removeRule()}
        message={
          <span>
            <strong>{confirmDelete?.name}</strong> will stop being evaluated immediately, so it can never fire again. Alerts it already
            raised, and the notifications built from them, are retained — the server deletes only the rule document. The action is audited as{' '}
            <span className="mono">alert_rule.deleted</span>.
          </span>
        }
      />

      <ConfirmDialog
        open={confirmScan}
        title="Run the rule engine now?"
        busy={busy}
        confirmLabel="Evaluate rules"
        onClose={() => setConfirmScan(false)}
        onConfirm={() => void runScan()}
        message={
          <span>
            This scans stored observations and predictions for every enabled rule right now (bounded by the server’s lookback and row
            limits), creates alerts for matches and queues recipient notifications. Real work on the database, and recipients may be
            contacted immediately.
          </span>
        }
      />
    </div>
  )
}

function Line({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="flex items-start justify-between gap-2 border-b py-1" style={{ borderColor: 'var(--border)' }}>
      <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{label}</dt>
      <dd className="text-right text-xs muted">{value === null || value === undefined || value === '' ? '—' : String(value)}</dd>
    </div>
  )
}

function operatorLabel(operator: string): string {
  switch (operator) {
    case 'gt':
      return 'greater than'
    case 'lt':
      return 'less than'
    case 'between':
      return 'inside a band'
    case 'outside_band':
      return 'outside a band'
    case 'deviation_gt_sigma':
      return 'deviation above σ'
    case 'anomaly':
      return 'anomalous (σ)'
    default:
      return humanise(operator)
  }
}

/** Copies a template’s defaults into form state, keeping only fields the form understands. */
function applyTemplate(template: RuleTemplate, metricList: MetricDefinition[]): Partial<RuleFormState> {
  const defaults = template.defaults ?? {}
  const condition = (defaults.condition ?? {}) as Record<string, unknown>
  const target = typeof defaults.target_variable === 'string' ? defaults.target_variable : undefined
  const definition = metricList.find((entry) => entry.field === target)
  return {
    name: typeof defaults.name === 'string' ? defaults.name : template.label,
    description: template.description,
    target_variable: definition ? target as string : 'temperature_c',
    operator: (typeof condition.operator === 'string' ? condition.operator : 'gt') as Operator,
    value: condition.value === undefined || condition.value === null ? '' : String(condition.value),
    value_upper: condition.value_upper === undefined || condition.value_upper === null ? '' : String(condition.value_upper),
    sigma: condition.sigma === undefined || condition.sigma === null ? '2.5' : String(condition.sigma),
    severity: typeof defaults.severity === 'string' ? defaults.severity : 'warning',
    cooldown_minutes: typeof defaults.cooldown_minutes === 'number' ? String(defaults.cooldown_minutes) : '720',
    applicable_sources: Array.isArray(defaults.applicable_sources) ? defaults.applicable_sources.map(String) : ['observations'],
    roleRecipients: Array.isArray(defaults.recipients) ? defaults.recipients.map(String).filter((entry) => entry.startsWith('role:')) : [],
    userRecipients: Array.isArray(defaults.recipients) ? defaults.recipients.map(String).filter((entry) => /^[0-9a-f]{24}$/i.test(entry)) : [],
    everyone: Array.isArray(defaults.recipients) ? defaults.recipients.map(String).includes('all') : false,
    is_enabled: true,
  }
}
