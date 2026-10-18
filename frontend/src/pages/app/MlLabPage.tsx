import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { BrainCircuit } from 'lucide-react'
import api from '../../services/api'
import { useAsync } from '../../hooks/useAsync'
import { useAuth } from '../../features/auth/AuthContext'
import { ApiRequestError } from '../../services/http'
import {
  Badge,
  Column,
  DataTable,
  EmptyState,
  ErrorState,
  Field,
  FieldSet,
  Hint,
  MultiSelect,
  Panel,
  Skeleton,
  StatCard,
  statusTone,
} from '../../components/ui'
import { EVAL_METRICS, bestModelId, horizons, modelUnit, splitStrategies } from './components/insight/ml'
import { formatNumber, metricLabel, metricUnit } from '../../lib/format'
import type { Metric, ModelCatalogue, ModelRecord, TrainingOutcome } from '../../types/api'

export default function MlLabPage() {
  const { can } = useAuth()
  const canTrain = can('models:train')

  const catalogue = useAsync(() => api.models.catalogue(), [])
  const regions = useAsync(() => api.climate.regions(), [])
  const registry = useAsync(() => api.models.list({ page_size: 60 }), [])
  const training = useAsync(() => api.models.trainingStatus(), [], { interval: 5000 })

  const [target, setTarget] = useState<Metric | ''>('')
  const [algorithm, setAlgorithm] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [selectedRegions, setSelectedRegions] = useState<string[]>([])
  const [selectedLocations, setSelectedLocations] = useState<string[]>([])
  const [horizon, setHorizon] = useState(7)
  const [name, setName] = useState('')
  const [outcome, setOutcome] = useState<TrainingOutcome | null>(null)
  const [submitError, setSubmitError] = useState<ApiRequestError | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const [compareIds, setCompareIds] = useState<string[]>([])

  const cat = catalogue.data
  const maxHorizon = cat?.horizon.max_forecast_horizon_days ?? 90
  const targetInfo = cat?.targets.find((item) => item.target_variable === target) ?? null
  const algorithmInfo = cat?.algorithms.find((item) => item.name === algorithm) ?? null

  const regionOptions = useMemo(
    () => (regions.data?.regions ?? []).map((region) => ({ value: region.code, label: region.name, hint: `${region.code} · ${region.location_count} locations` })),
    [regions.data],
  )
  const locationOptions = useMemo(
    () => (regions.data?.regions ?? []).flatMap((region) => region.locations.map((loc) => ({ value: loc.code, label: loc.name, hint: region.code }))),
    [regions.data],
  )

  const modelsWithMetrics = useMemo(
    () => (registry.data?.items ?? []).filter((model) => model.evaluation_metrics && Object.keys(model.evaluation_metrics).length > 0),
    [registry.data],
  )
  const compareOptions = useMemo(
    () => modelsWithMetrics.map((model) => ({ value: model.id, label: `${model.name} · v${model.version}`, hint: `${model.algorithm} · ${metricLabel(model.target_variable)}` })),
    [modelsWithMetrics],
  )
  const compareModels = useMemo(() => modelsWithMetrics.filter((model) => compareIds.includes(model.id)), [modelsWithMetrics, compareIds])

  const validationProblem = (() => {
    if (!canTrain) return 'Your role cannot train models.'
    if (!target) return 'Choose a target variable.'
    if (!targetInfo?.trainable) return 'The selected target is not trainable.'
    if (!algorithm) return 'Choose an algorithm.'
    if (!algorithmInfo?.available) return 'The selected algorithm is not available on this engine.'
    if (horizon < 1 || horizon > maxHorizon) return `Horizon must be between 1 and ${maxHorizon} days.`
    return null
  })()

  async function submit() {
    if (!target || !algorithm || validationProblem) return
    setSubmitting(true)
    setSubmitError(null)
    setOutcome(null)
    try {
      // POST /models/train answers 202 with a TrainingOutcome (no job object). Training
      // runs in the background executor; the registry + training-status fill in as it goes.
      const result = await api.models.train({
        target_variable: target,
        algorithm,
        horizon_days: horizon,
        regions: selectedRegions,
        locations: selectedLocations,
        start: start || undefined,
        end: end || undefined,
        name: name.trim() || undefined,
      })
      setOutcome(result)
      void registry.refetch()
      void training.refetch()
    } catch (error) {
      setSubmitError(error instanceof ApiRequestError ? error : new ApiRequestError(String(error), 'unexpected_error', 0))
    } finally {
      setSubmitting(false)
    }
  }

  if (catalogue.loading && !cat) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <PageBlock />

      {catalogue.error ? <ErrorState error={catalogue.error} onRetry={() => void catalogue.refetch()} /> : null}

      {cat ? (
        <div className="grid-responsive">
          <StatCard label="Trainable targets" value={cat.targets.filter((item) => item.trainable).length} unit={`/ ${cat.targets.length}`} tone="hydro" hint="Targets with enough real observations to train." />
          <StatCard label="Available algorithms" value={cat.algorithms.filter((item) => item.available).length} unit={`/ ${cat.algorithms.length}`} tone="solar" hint="Disabled when the engine lacks the library." />
          <StatCard label="Max forecast horizon" value={cat.horizon.max_forecast_horizon_days} unit="days" tone="forest" hint="Upper bound the backend accepts for a forecast." icon={<BrainCircuit className="h-4 w-4" />} />
          <StatCard label="Train / test split" value={cat.horizon.split_strategy} tone="default" hint="Honest split strategy reported by the trainer." />
        </div>
      ) : null}

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel title="Training configuration" description="Every field below maps to a documented POST /models/train parameter. Nothing is auto-filled with guesses.">
          <div className="space-y-6">
            <FieldSet legend="Target variable" description="Only metrics backed by real observations are trainable.">
              {cat ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {cat.targets.map((item) => (
                    <TargetCard key={item.target_variable} item={item} selected={target === item.target_variable} onSelect={() => setTarget(item.target_variable)} />
                  ))}
                </div>
              ) : null}
              {targetInfo && !targetInfo.trainable ? (
                <div className="mt-3">
                  <EmptyState
                    tone="warning"
                    title="This target cannot be trained yet"
                    description={
                      <>
                        {targetInfo.blocker ?? 'Insufficient real observations.'} Rows with a value:{' '}
                        <span className="mono">{formatNumber(targetInfo.rows_with_value, 0)}</span>.
                      </>
                    }
                    action={
                      <Link to="/app/ingestion" className="btn btn-secondary btn-sm">
                        Run an ingestion job
                      </Link>
                    }
                  />
                </div>
              ) : null}
            </FieldSet>

            <FieldSet legend="Algorithm" description="Unavailable algorithms are shown disabled with the exact blocker or requirement they report.">
              {cat ? (
                <div className="grid gap-2 sm:grid-cols-3">
                  {cat.algorithms.map((item) => (
                    <AlgorithmCard key={item.name} item={item} selected={algorithm === item.name} onSelect={() => setAlgorithm(item.name)} />
                  ))}
                </div>
              ) : null}
            </FieldSet>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Start date" htmlFor="ml-start">
                <input id="ml-start" type="date" className="input" value={start} onChange={(event) => setStart(event.target.value)} />
              </Field>
              <Field label="End date" htmlFor="ml-end">
                <input id="ml-end" type="date" className="input" value={end} onChange={(event) => setEnd(event.target.value)} />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Regions" htmlFor="ml-regions" hint="Optional — leave empty to train across every region for the target.">
                <MultiSelect options={regionOptions} value={selectedRegions} onChange={setSelectedRegions} placeholder="All regions" label="Regions" />
              </Field>
              <Field label="Locations" htmlFor="ml-locations" hint="Optional — narrow training to specific stations.">
                <MultiSelect options={locationOptions} value={selectedLocations} onChange={setSelectedLocations} placeholder="All locations" label="Locations" />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Forecast horizon (days)" htmlFor="ml-horizon" hint={`Between 1 and ${maxHorizon}. Stored on the model and used for forecasting.`}>
                <input id="ml-horizon" type="number" min={1} max={maxHorizon} className="input" value={horizon} onChange={(event) => setHorizon(Number(event.target.value))} />
              </Field>
              <Field label="Model name (optional)" htmlFor="ml-name" hint="Leave blank to let the trainer derive a versioned name.">
                <input id="ml-name" className="input" value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. temperature ridge v3" />
              </Field>
            </div>

            {cat ? (
              <Hint>
                The feature set is fixed by the backend — {cat.default_feature_set.description} Lags{' '}
                <span className="mono">{cat.default_feature_set.lags.join(', ') || 'none'}</span> · rolling windows{' '}
                <span className="mono">{cat.default_feature_set.rolling_windows_days.join(', ') || 'none'}</span> days.
              </Hint>
            ) : null}

            <div>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void submit()}
                disabled={Boolean(validationProblem) || submitting}
                title={!canTrain ? 'Requires climate analyst or administrator role.' : validationProblem ?? undefined}
              >
                <BrainCircuit className="h-4 w-4" /> {submitting ? 'Submitting…' : 'Start training run'}
              </button>
              {validationProblem && canTrain ? <Hint>{validationProblem}</Hint> : null}
            </div>

            {submitError ? <ErrorState error={submitError} /> : null}

            {outcome ? (
              <Panel title="Training accepted" description="The trainer runs in the background; the registry fills in as versions complete." dense>
                <p className="text-sm muted">{outcome.note}</p>
                <ul className="mt-2 space-y-1 text-xs">
                  {outcome.models.map((model) => (
                    <li key={model.model_id} className="flex items-center gap-2">
                      <span className="min-w-0 truncate font-medium">{model.name}</span>
                      <span className="mono subtle">v{model.version} · {model.algorithm}</span>
                    </li>
                  ))}
                </ul>
                <div className="mt-2">
                  <Link to="/app/models" className="link">Open the model registry</Link>
                </div>
              </Panel>
            ) : null}
          </div>
        </Panel>

        <div className="space-y-6">
          <Panel title="Row availability" description="Honest, current observation counts — the trainer needs real rows.">
            {targetInfo ? (
              <div className="space-y-2 text-sm">
                <p>
                  <span className="mono">{formatNumber(targetInfo.rows_with_value, 0)}</span> rows with a value for{' '}
                  <strong>{metricLabel(targetInfo.target_variable)}</strong> ({targetInfo.unit}).
                </p>
                <p className="muted">
                  Coverage: <span className="mono">{targetInfo.first_date ?? '—'} → {targetInfo.last_date ?? '—'}</span> · {targetInfo.locations} locations ·{' '}
                  {targetInfo.distinct_days} distinct days.
                </p>
                {algorithmInfo?.min_rows != null ? (
                  <p className="muted">
                    Minimum rows for {algorithmInfo.label ?? algorithmInfo.name}: <span className="mono">{formatNumber(algorithmInfo.min_rows, 0)}</span>.
                  </p>
                ) : null}
                <Badge tone={statusTone(targetInfo.trainable ? 'completed' : 'failed', 'job')}>
                  {targetInfo.trainable ? 'Trainable' : 'Not trainable'}
                </Badge>
              </div>
            ) : (
              <p className="text-sm muted">Select a target to see how many real observations exist for it.</p>
            )}
            {algorithmInfo?.requirements ? <Hint>Requirements: {algorithmInfo.requirements}</Hint> : null}
          </Panel>

          <Panel title="Live training runs" description="Models the backend is training right now (polled every 5s)." dense>
            {training.loading && !training.data ? <Skeleton className="h-16 w-full" /> : null}
            {training.error && !training.data ? <ErrorState error={training.error} onRetry={() => void training.refetch()} /> : null}
            {training.data ? (
              training.data.training.length ? (
                <ul className="space-y-2 text-xs">
                  {training.data.training.map((item) => (
                    <li key={item.model_id} className="flex items-center justify-between gap-2">
                      <span className="min-w-0 truncate font-medium">{item.name ?? item.model_id}</span>
                      <span className="mono subtle shrink-0">
                        {item.algorithm ?? '—'} · {item.stage ?? 'training'} · {item.elapsed_seconds != null ? `${formatNumber(item.elapsed_seconds, 0)}s` : '—'}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState
                  title={registry.data?.items.length ? 'Nothing is training right now' : 'No model has been trained yet'}
                  description={
                    registry.data?.items.length
                      ? `The ${registry.data.items.length} registered model(s) have finished training. Queue another run above to watch a live one.`
                      : 'Nothing is training right now and the registry is empty. Use the training configuration on the left to queue a run; the registry fills in as versions finish.'
                  }
                />
              )
            ) : null}
          </Panel>
        </div>
      </div>

      <Panel
        title="Evaluation comparison"
        description="Pick trained models to line up their reported metrics. Values are exactly what the API returned — units shown, no interpolation."
        actions={<span className="text-xs subtle">{compareOptions.length} models have metrics</span>}
      >
        {registry.loading ? <Skeleton className="h-40 w-full" /> : null}
        {registry.error ? <ErrorState error={registry.error} onRetry={() => void registry.refetch()} /> : null}
        {!registry.loading && compareOptions.length === 0 ? (
          <EmptyState title="No trained models to compare yet" description="Train a model above; its evaluation metrics will appear here once the run completes and the registry repopulates." action={<Link to="/app/models" className="link">Open the model registry</Link>} />
        ) : null}
        {compareOptions.length ? (
          <div className="space-y-4">
            <Field label="Compare models" htmlFor="ml-compare">
              <MultiSelect options={compareOptions} value={compareIds} onChange={setCompareIds} placeholder="Select models to compare" label="Models" />
            </Field>

            {compareModels.length === 0 ? (
              <p className="text-sm muted">Select at least one model to build the comparison.</p>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-2 text-xs subtle">
                  <span>Split strategy: {splitStrategies(compareModels).join(' · ') || 'not reported'}</span>
                  <span>·</span>
                  <span>Horizon(s): {horizons(compareModels).map((day) => `${day}d`).join(' · ') || 'not reported'}</span>
                </div>
                <EvaluationTable models={compareModels} />
                <div className="space-y-3">
                  {compareModels.map((model) => (
                    <div key={model.id} className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
                      <p className="font-semibold">{model.name} · v{model.version}</p>
                      <p className="mt-1 muted">
                        Uncertainty method: <span className="mono">{model.uncertainty_method ?? 'not reported'}</span>
                      </p>
                      {model.limitations?.length ? (
                        <ul className="mt-1 list-disc pl-5 muted">
                          {model.limitations.map((limit, index) => (
                            <li key={index}>{limit}</li>
                          ))}
                        </ul>
                      ) : (
                        <p className="mt-1 subtle">No limitations reported.</p>
                      )}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        ) : null}
      </Panel>
    </div>
  )
}

function PageBlock() {
  return (
    <header className="mb-2">
      <p className="panel-title mb-1.5">Models &amp; forecasts</p>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">Machine learning lab</h1>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">
            Configure and launch model training against the live catalogue. Training is accepted with a 202 and runs in the background — metrics and forecast
            values only ever come from the API. A target that cannot train reports its blocker, never a fabricated score.
          </p>
        </div>
        <Link to="/app/models" className="btn btn-secondary btn-sm">
          Registry
        </Link>
      </div>
    </header>
  )
}

function TargetCard({ item, selected, onSelect }: { item: ModelCatalogue['targets'][number]; selected: boolean; onSelect: () => void }) {
  const tone = item.trainable ? 'success' : 'warning'
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`card-tight text-left transition-colors ${selected ? 'ring-2 ring-forest-500/60' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{item.label}</p>
          <p className="mono text-xs subtle">{item.target_variable} · {item.unit}</p>
        </div>
        <Badge tone={tone}>{item.trainable ? 'trainable' : 'blocked'}</Badge>
      </div>
      <p className="mt-2 text-xs muted">
        <span className="mono">{formatNumber(item.rows_with_value, 0)}</span> rows available
      </p>
      {!item.trainable && item.blocker ? <p className="mt-1 text-xs text-alert-high">{item.blocker}</p> : null}
    </button>
  )
}

function AlgorithmCard({ item, selected, onSelect }: { item: ModelCatalogue['algorithms'][number]; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={!item.available}
      aria-pressed={selected}
      title={item.available ? item.name : `Unavailable${item.blocked_by ? ` — ${item.blocked_by}` : ''}`}
      className={`card-tight text-left transition-colors ${selected ? 'ring-2 ring-forest-500/60' : ''} ${!item.available ? 'opacity-60' : ''}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{item.label ?? item.name}</p>
          <p className="mono text-xs subtle">{item.name}</p>
        </div>
        <Badge tone={item.available ? 'success' : 'neutral'}>{item.available ? 'available' : 'off'}</Badge>
      </div>
      {item.min_rows != null ? <p className="mt-1 text-xs muted">Needs <span className="mono">{formatNumber(item.min_rows, 0)}</span> rows</p> : null}
      {!item.available && item.blocked_by ? <p className="mt-1 text-xs text-alert-high">{item.blocked_by}</p> : null}
      {item.requirements ? <p className="mt-1 text-xs subtle">Requires: {item.requirements}</p> : null}
    </button>
  )
}

function EvaluationTable({ models }: { models: ModelRecord[] }) {
  const columns: Column<ModelRecord>[] = useMemo(
    () => [
      {
        key: 'model',
        header: 'Model',
        cell: (model) => (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{model.name}</p>
            <p className="mono text-xs subtle">v{model.version} · {model.algorithm}</p>
          </div>
        ),
      },
      {
        key: 'target',
        header: 'Target',
        cell: (model) => (
          <span className="text-xs">
            {metricLabel(model.target_variable)} <span className="subtle">({metricUnit(model.target_variable)})</span>
          </span>
        ),
      },
      ...EVAL_METRICS.map<Column<ModelRecord>>((meta) => ({
        key: String(meta.key),
        header: (
          <span title={`${meta.better === 'lower' ? 'lower' : 'higher'} is better`}>
            {meta.label} <span className="subtle">{meta.better === 'lower' ? '↓' : '↑'}</span>
          </span>
        ),
        align: 'left',
        cell: (model) => <MetricCell model={model} meta={meta} models={models} />,
      })),
      {
        key: 'sizes',
        header: 'Train / test',
        align: 'right',
        cell: (model) => (
          <span className="mono text-xs">
            {formatNumber(model.train_size, 0)} / {formatNumber(model.test_size, 0)}
          </span>
        ),
      },
    ],
    [models],
  )
  return <DataTable columns={columns} rows={models} rowKey={(model) => model.id} compact caption="Model evaluation metric comparison" />
}

function MetricCell({ model, meta, models }: { model: ModelRecord; meta: (typeof EVAL_METRICS)[number]; models: ModelRecord[] }) {
  const raw = model.evaluation_metrics?.[meta.key]
  if (typeof raw !== 'number' || !Number.isFinite(raw)) {
    return <span className="text-xs subtle">—</span>
  }
  const values = models
    .map((candidate) => candidate.evaluation_metrics?.[meta.key])
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const maxAbs = Math.max(...values.map((value) => Math.abs(value)), 1)
  const width = Math.max(6, Math.min(100, (Math.abs(raw) / maxAbs) * 100))
  const best = bestModelId(models, meta) === model.id
  return (
    <div className="min-w-[8rem]">
      <div className="flex items-center justify-between gap-2">
        <span className="mono text-xs font-medium">{meta.format(raw, modelUnit(model))}</span>
        {best && models.length > 1 ? <Badge tone="success">best</Badge> : null}
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full" style={{ backgroundColor: 'var(--surface-muted)' }}>
        <div className="h-full rounded-full bg-forest-600" style={{ width: `${width}%` }} />
      </div>
    </div>
  )
}
