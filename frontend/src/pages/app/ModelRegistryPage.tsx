import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { RefreshCw, RotateCcw, ShieldCheck, Trash2 } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import {
  Badge,
  Column,
  ConfirmDialog,
  DataTable,
  ErrorState,
  Field,
  KeyValue,
  Mono,
  Panel,
  Pagination,
  Select,
  Skeleton,
  StatCard,
  StatusBadge,
} from '../../components/ui'
import { formatDate, formatNumber, humanise, metricLabel, metricUnit, relativeTime } from '../../lib/format'
import type { ModelRecord } from '../../types/api'

const STATUSES = ['training', 'candidate', 'active', 'retired', 'failed'] as const
const ALGORITHMS = ['ridge', 'random_forest', 'xgboost'] as const

type StatusCount = { status: string; total: number }

export default function ModelRegistryPage() {
  const { can, hasRole } = useAuth()
  const isAdmin = hasRole('administrator')
  const canRetrain = can('models:train')

  const [status, setStatus] = useState('')
  const [algorithm, setAlgorithm] = useState('')
  const [page, setPage] = useState(1)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [pending, setPending] = useState<{ action: 'activate' | 'retire'; model: ModelRecord } | null>(null)
  const [working, setWorking] = useState(false)
  const [actionError, setActionError] = useState<ApiRequestError | null>(null)
  const [hasTraining, setHasTraining] = useState(false)

  const list = useAsync(
    () => api.models.list({ page, page_size: 20, status: status || undefined, algorithm: algorithm || undefined }),
    [page, status, algorithm],
    { interval: hasTraining ? 5000 : undefined },
  )

  useEffect(() => {
    setHasTraining(list.data?.items.some((model) => model.status === 'training') ?? false)
  }, [list.data])

  const counts = useAsync(async () => {
    const results = await Promise.all(STATUSES.map((key) => api.models.list({ status: key, page_size: 1 })))
    return results.map((result, index) => ({ status: STATUSES[index], total: result.total })) as StatusCount[]
  }, [])

  const detail = useAsync(() => api.models.get(selectedId as string), [selectedId], { enabled: Boolean(selectedId), interval: hasTraining ? 5000 : undefined })

  const rows: ModelRecord[] = list.data?.items ?? []

  async function runAction() {
    if (!pending) return
    setWorking(true)
    setActionError(null)
    try {
      if (pending.action === 'activate') await api.models.activate(pending.model.id)
      else await api.models.retire(pending.model.id)
      setPending(null)
      void list.refetch()
      if (selectedId === pending.model.id) void detail.refetch()
    } catch (error) {
      setActionError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setWorking(false)
    }
  }

  async function retrain(model: ModelRecord) {
    setActionError(null)
    try {
      // POST /models/{id}/retrain answers 202 with a TrainingOutcome (no job object);
      // the new version enters `training` and the list poll below tracks it.
      await api.models.retrain(model.id)
      setHasTraining(true)
      void list.refetch()
      void counts.refetch()
    } catch (error) {
      setActionError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    }
  }

  return (
    <div className="space-y-6">
      <header className="mb-2">
        <p className="panel-title mb-1.5">Models &amp; forecasts</p>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Model registry</h1>
            <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
              Every trained model with its real evaluation metrics, lineage and lifecycle actions. Only one version per model name can be active at a time;
              activating retires the previous active version server-side.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link to="/app/ml-lab" className="btn btn-secondary btn-sm">Train a model</Link>
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => { void list.refetch(); void counts.refetch() }} disabled={list.refreshing}>
              <RefreshCw className={`h-3.5 w-3.5 ${list.refreshing ? 'animate-spin' : ''}`} /> Refresh
            </button>
          </div>
        </div>
      </header>

      <div className="grid-responsive">
        {counts.loading && !counts.data ? <Skeleton className="h-24 w-full" /> : null}
        {(counts.data ?? []).map((entry) => (
          <StatCard
            key={entry.status}
            label={humanise(entry.status)}
            value={formatNumber(entry.total, 0)}
            tone={entry.status === 'active' ? 'forest' : entry.status === 'failed' ? 'alert' : 'default'}
            loading={false}
          />
        ))}
      </div>

      {actionError ? <ErrorState error={actionError} /> : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel
          title="Registered models"
          description="Click a row to inspect its definition, metrics and artifacts."
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <Field label="Status" className="w-32">
                <Select
                  options={[{ value: '', label: 'All statuses' }, ...STATUSES.map((key) => ({ value: key, label: humanise(key) }))]}
                  value={status}
                  onChange={(value) => { setStatus(value); setPage(1) }}
                  ariaLabel="Filter by status"
                />
              </Field>
              <Field label="Algorithm" className="w-36">
                <Select
                  options={[{ value: '', label: 'All algorithms' }, ...ALGORITHMS.map((key) => ({ value: key, label: humanise(key) }))]}
                  value={algorithm}
                  onChange={(value) => { setAlgorithm(value); setPage(1) }}
                  ariaLabel="Filter by algorithm"
                />
              </Field>
            </div>
          }
        >
          {hasTraining ? (
            <p className="mb-2 text-xs subtle">Some models are still training — this table refreshes every 5s.</p>
          ) : null}
          <DataTable
            columns={columns(setSelectedId)}
            rows={rows}
            rowKey={(model) => model.id}
            loading={list.loading}
            onRowClick={(model) => setSelectedId(model.id)}
            empty={<span>No models match. <Link to="/app/ml-lab" className="link">Train one in the ML lab.</Link></span>}
          />
          <Pagination meta={list.data ?? undefined} onPage={setPage} label="models" />
        </Panel>

        <div className="space-y-6">
          {!selectedId ? (
            <Panel title="Model detail">
              <p className="text-sm muted">Select a model to view its feature definition, evaluation metrics with baseline comparison, artifact and limitations.</p>
            </Panel>
          ) : detail.loading && !detail.data ? (
            <Panel title="Model detail">
              <Skeleton className="h-40 w-full" />
            </Panel>
          ) : detail.error && !detail.data ? (
            <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />
          ) : detail.data ? (
            <ModelDetail
              model={detail.data}
              isAdmin={isAdmin}
              canRetrain={canRetrain}
              onRetrain={() => void retrain(detail.data as ModelRecord)}
              onActivate={() => setPending({ action: 'activate', model: detail.data as ModelRecord })}
              onRetire={() => setPending({ action: 'retire', model: detail.data as ModelRecord })}
            />
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(pending)}
        title={pending?.action === 'activate' ? 'Activate model' : 'Retire model'}
        tone={pending?.action === 'retire' ? 'danger' : 'primary'}
        confirmLabel={pending ? humanise(pending.action) : 'Confirm'}
        busy={working}
        onClose={() => setPending(null)}
        onConfirm={() => void runAction()}
        message={
          pending?.action === 'activate' ? (
            <div className="space-y-2">
              <p>
                Activate <strong>{pending.model.name}</strong> v{pending.model.version} ({pending.model.algorithm}, {metricLabel(pending.model.target_variable)})?
              </p>
              <p className="muted">Server rule: only one active version per model name is kept. Any previously active version is retired automatically, and only models with a persisted artifact can be activated.</p>
            </div>
          ) : (
            <div className="space-y-2">
              <p>Retire <strong>{pending?.model.name}</strong> v{pending?.model.version}? A retired model cannot produce new forecasts.</p>
              <p className="muted">Delete is not offered here: removing a model requires an administrator through the API and stored forecasts keep their metric snapshot regardless.</p>
            </div>
          )
        }
      />
    </div>
  )
}

function columns(setSelected: (id: string) => void): Column<ModelRecord>[] {
  return [
    {
      key: 'name',
      header: 'Model',
      cell: (model) => (
        <button type="button" className="link text-left" onClick={() => setSelected(model.id)}>
          <span className="block max-w-[16rem] truncate font-medium">{model.name}</span>
          <span className="mono text-xs subtle">v{model.version}</span>
        </button>
      ),
    },
    { key: 'algorithm', header: 'Algorithm', cell: (model) => <span className="text-xs">{humanise(model.algorithm)}</span> },
    { key: 'target', header: 'Target', cell: (model) => <span className="text-xs">{metricLabel(model.target_variable)}</span> },
    {
      key: 'status',
      header: 'Status',
      cell: (model) => <StatusBadge value={model.status} kind="job" />,
    },
    {
      key: 'sizes',
      header: 'Train / test',
      align: 'right',
      cell: (model) => <span className="mono text-xs">{formatNumber(model.train_size, 0)}/{formatNumber(model.test_size, 0)}</span>,
    },
    {
      key: 'metrics',
      header: 'MAE · RMSE · R² · skill',
      cell: (model) => {
        const m = model.evaluation_metrics ?? {}
        const num = (value?: number | null) => (typeof value === 'number' ? formatNumber(value, 2) : '—')
        return (
          <span className="mono text-xs">
            {num(m.mae)} · {num(m.rmse)} · {num(m.r2)} · {num(m.skill_vs_baseline)}
          </span>
        )
      },
    },
    {
      key: 'created',
      header: 'Created',
      cell: (model) => <span className="text-xs" title={formatDate(model.created_at, true)}>{relativeTime(model.created_at)}</span>,
    },
    {
      key: 'duration',
      header: 'Duration',
      align: 'right',
      cell: (model) => <span className="mono text-xs">{model.duration_seconds != null ? `${formatNumber(model.duration_seconds, 0)}s` : '—'}</span>,
    },
  ]
}

function ModelDetail({
  model,
  isAdmin,
  canRetrain,
  onActivate,
  onRetire,
  onRetrain,
}: {
  model: ModelRecord
  isAdmin: boolean
  canRetrain: boolean
  onActivate: () => void
  onRetire: () => void
  onRetrain: () => void
}) {
  const metrics = model.evaluation_metrics ?? {}
  const importance = useMemo(() => [...(model.feature_importance ?? [])].sort((left, right) => right.importance - left.importance), [model])
  const maxImportance = Math.max(...importance.map((item) => Math.abs(item.importance)), 1)

  const feature: NonNullable<ModelRecord['feature_definition']> = model.feature_definition ?? { features: [] }

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {model.name} <span className="mono text-xs subtle">v{model.version}</span>
        </span>
      }
      description={`${humanise(model.algorithm)} · ${metricLabel(model.target_variable)} (${model.target_variable})`}
      actions={<StatusBadge value={model.status} kind="job" />}
      footer={<span>Created {formatDate(model.created_at, true)}</span>}
    >
      <div className="mb-4 flex flex-wrap gap-2">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={onActivate}
          disabled={!isAdmin || model.status === 'active'}
          title={!isAdmin ? 'Only administrators can activate models.' : model.status === 'active' ? 'Already active.' : 'Activate this version'}
        >
          <ShieldCheck className="h-3.5 w-3.5" /> Activate
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onRetire}
          disabled={!isAdmin || model.status === 'retired' || model.status === 'training'}
          title={!isAdmin ? 'Only administrators can retire models.' : model.status === 'training' ? 'Still training.' : 'Retire this version'}
        >
          Retire
        </button>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onRetrain}
          disabled={!canRetrain || model.status === 'training'}
          title={!canRetrain ? 'Requires climate analyst or administrator role.' : 'Retrain a new version from the stored options'}
        >
          <RotateCcw className="h-3.5 w-3.5" /> Retrain
        </button>
        <span
          className="inline-flex items-center gap-1 self-center text-xs subtle"
          title="Models cannot be deleted from the registry UI; deleting a model is an administrator-only API operation and stored forecasts keep their metric snapshot."
        >
          <Trash2 className="h-3.5 w-3.5" /> Delete not permitted here
        </span>
      </div>

      {model.error_message ? (
        <div className="mb-4">
          <ErrorState error={new ApiRequestError(model.error_message, 'model_failed', 0)} />
        </div>
      ) : null}

      <div className="space-y-5">
        <section>
          <h3 className="panel-title mb-2">Evaluation metrics</h3>
          <KeyValue
            columns={2}
            items={[
              { label: 'MAE', value: <Num value={metrics.mae} unit={unitOf(model)} /> },
              { label: 'RMSE', value: <Num value={metrics.rmse} unit={unitOf(model)} /> },
              { label: 'MAPE', value: <Num value={metrics.mape} unit="%" /> },
              { label: 'R²', value: <Num value={metrics.r2} /> },
              { label: 'Baseline MAE', value: <Num value={metrics.baseline_mae} unit={unitOf(model)} /> },
              { label: 'Skill vs baseline', value: <Num value={metrics.skill_vs_baseline} /> },
              { label: 'Split strategy', value: metrics.split_strategy ? <Mono>{metrics.split_strategy}</Mono> : '—' },
              { label: 'Horizon', value: metrics.horizon_days != null ? `${metrics.horizon_days} days` : '—' },
            ]}
          />
          {metrics.baseline_mae != null && metrics.mae != null ? (
            <p className="mt-2 text-xs muted">
              Model MAE <span className="mono">{formatNumber(metrics.mae, 2)}</span> vs baseline <span className="mono">{formatNumber(metrics.baseline_mae, 2)}</span> —
              a positive skill means the model beat the naive baseline on real test rows.
            </p>
          ) : null}
        </section>

        <section>
          <h3 className="panel-title mb-2">Training period</h3>
          <KeyValue
            items={[
              { label: 'Window', value: <span className="mono">{model.training_period?.start ?? '—'} → {model.training_period?.end ?? '—'}</span> },
              { label: 'Regions', value: model.training_period?.regions?.length ? model.training_period.regions.join(', ') : '—' },
              { label: 'Locations', value: model.training_period?.locations?.length ? model.training_period.locations.join(', ') : '—' },
            ]}
          />
        </section>

        <section>
          <h3 className="panel-title mb-2">Feature definition</h3>
          <div className="flex flex-wrap gap-1">
            {feature.features?.length ? feature.features.map((field) => <Badge key={field} tone="neutral">{field}</Badge>) : <span className="text-xs subtle">No features listed</span>}
          </div>
          <div className="mt-3">
            <KeyValue
              items={[
                { label: 'Imputation', value: feature.imputation ? <Mono>{feature.imputation}</Mono> : '—' },
                { label: 'Encodings', value: feature.encodings && Object.keys(feature.encodings).length ? <Mono>{JSON.stringify(feature.encodings)}</Mono> : '—' },
                { label: 'Window', value: feature.window && Object.keys(feature.window).length ? <Mono>{JSON.stringify(feature.window)}</Mono> : '—' },
              ]}
            />
          </div>
        </section>

        {importance.length ? (
          <section>
            <h3 className="panel-title mb-2">Feature importance</h3>
            <ul className="space-y-1.5">
              {importance.map((item) => (
                <li key={item.feature} className="flex items-center gap-2 text-xs">
                  <span className="w-40 shrink-0 truncate" title={item.feature}>{item.feature}</span>
                  <span className="h-2 flex-1 overflow-hidden rounded-full" style={{ backgroundColor: 'var(--surface-muted)' }}>
                    <span className="block h-full rounded-full bg-forest-600" style={{ width: `${Math.max(4, (Math.abs(item.importance) / maxImportance) * 100)}%` }} />
                  </span>
                  <span className="mono w-14 text-right">{formatNumber(item.importance, 3)}</span>
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <section>
          <h3 className="panel-title mb-2">Artifact &amp; uncertainty</h3>
          <KeyValue
            columns={2}
            items={[
              { label: 'Artifact URI', value: model.artifact_uri ? <Mono>{model.artifact_uri}</Mono> : '—' },
              { label: 'Format', value: model.artifact_format ? <Mono>{model.artifact_format}</Mono> : '—' },
              { label: 'Uncertainty method', value: model.uncertainty_method ? <Mono>{model.uncertainty_method}</Mono> : '—' },
              { label: 'Datasets', value: model.dataset_ids?.length ? <Mono>{model.dataset_ids.join(', ')}</Mono> : '—' },
            ]}
          />
        </section>

        {model.notes ? (
          <section>
            <h3 className="panel-title mb-1">Notes</h3>
            <p className="text-sm muted">{model.notes}</p>
          </section>
        ) : null}

        <section>
          <h3 className="panel-title mb-1">Limitations / caveats</h3>
          {model.limitations?.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm muted">
              {model.limitations.map((limit, index) => (
                <li key={index}>{limit}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm subtle">No limitations were reported by the trainer.</p>
          )}
        </section>
      </div>
    </Panel>
  )
}

function unitOf(model: ModelRecord): string {
  return metricUnit(model.target_variable)
}

function Num({ value, unit }: { value?: number | null; unit?: string }) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return <span className="subtle">—</span>
  return <span className="mono">{formatNumber(value, 3)}{unit ? ` ${unit}` : ''}</span>
}
