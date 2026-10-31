import type { ModelRecord } from '../../../../types/api'
import { formatNumber, metricUnit } from '../../../../lib/format'

// hyperparameters

export interface HyperparameterRow {
  key: string
  raw: string
}

export interface ParsedHyperparameters {
  ok: boolean
  values: Record<string, unknown>
  errors: string[]
}

/**
 * Free-form key/value rows coerced to JSON-safe scalars only (number, boolean, null,
 * string). Nested objects/arrays are rejected so nothing ambiguous reaches the trainer.
 */
export function parseHyperparameters(rows: HyperparameterRow[]): ParsedHyperparameters {
  const values: Record<string, unknown> = {}
  const errors: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    const key = row.key.trim()
    const raw = row.raw.trim()
    if (!key && !raw) continue
    if (!key) {
      errors.push(`Value "${raw}" has no parameter name.`)
      continue
    }
    if (seen.has(key)) {
      errors.push(`Duplicate parameter "${key}".`)
      continue
    }
    seen.add(key)
    if (!raw) {
      errors.push(`Parameter "${key}" has no value.`)
      continue
    }
    // Attempt strict JSON coercion, fall back to a plain string.
    let parsed: unknown
    try {
      const candidate = JSON.parse(raw)
      if (candidate !== null && typeof candidate === 'object') {
        errors.push(`Parameter "${key}" must be a scalar (number, boolean, null or string); objects and arrays are not accepted.`)
        continue
      }
      parsed = candidate
    } catch {
      // Accept simple booleans/numbers that aren't strict JSON, else keep the string.
      if (/^-?\d+(\.\d+)?$/.test(raw)) parsed = Number(raw)
      else if (/^(true|false)$/i.test(raw)) parsed = raw.toLowerCase() === 'true'
      else if (/^null$/i.test(raw)) parsed = null
      else parsed = raw
    }
    values[key] = parsed
  }
  return { ok: errors.length === 0, values, errors }
}

// evaluation compare

export interface EvalMetricMeta {
  key: keyof ModelRecord['evaluation_metrics']
  label: string
  better: 'lower' | 'higher'
  digits: number
  /** returns a display string given the raw numeric value and the model's target unit */
  format: (value: number, unit: string) => string
  isPercent?: boolean
}

export const EVAL_METRICS: EvalMetricMeta[] = [
  { key: 'mae', label: 'MAE', better: 'lower', digits: 2, format: (v, unit) => `${formatNumber(v, 2)} ${unit}` },
  { key: 'rmse', label: 'RMSE', better: 'lower', digits: 2, format: (v, unit) => `${formatNumber(v, 2)} ${unit}` },
  { key: 'mape_or_null', label: 'MAPE', better: 'lower', digits: 2, format: (v) => `${formatNumber(v, 2)}%`, isPercent: true },
  { key: 'r2', label: 'R²', better: 'higher', digits: 3, format: (v) => formatNumber(v, 3) },
  { key: 'skill_vs_baseline', label: 'Skill vs baseline', better: 'higher', digits: 3, format: (v) => formatNumber(v, 3) },
]

/** Absolute span used to normalise a bar; ignores negatives beyond their own magnitude. */
export function evalValues(models: ModelRecord[], key: keyof ModelRecord['evaluation_metrics']): number[] {
  return models.map((model) => {
    const value = model.evaluation_metrics?.[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : NaN
  })
}

export function modelUnit(model: ModelRecord): string {
  return metricUnit(model.target_variable) || 'unit'
}

/**
 * Returns the id of the best model for a metric (respecting `better` direction) and a
 * per-model worst id so the UI can annotate which number the API considers stronger.
 * Purely derived from returned metrics — never guessed.
 */
export function bestModelId(models: ModelRecord[], meta: EvalMetricMeta): string | null {
  let best: { id: string; value: number } | null = null
  for (const model of models) {
    const raw = model.evaluation_metrics?.[meta.key]
    if (typeof raw !== 'number' || !Number.isFinite(raw)) continue
    if (best === null) {
      best = { id: model.id, value: raw }
      continue
    }
    if (meta.better === 'lower' ? raw < best.value : raw > best.value) best = { id: model.id, value: raw }
  }
  return best?.id ?? null
}

export function splitStrategies(models: ModelRecord[]): string[] {
  const set = new Set<string>()
  models.forEach((model) => {
    const strategy = model.evaluation_metrics?.split_strategy
    if (strategy) set.add(strategy)
  })
  return [...set]
}

export function horizons(models: ModelRecord[]): number[] {
  const set = new Set<number>()
  models.forEach((model) => {
    const horizon = model.evaluation_metrics?.horizon_days
    if (typeof horizon === 'number') set.add(horizon)
  })
  return [...set].sort((left, right) => left - right)
}

export function modelLabel(model: ModelRecord): string {
  return `${model.name} · v${model.version}`
}
