/**
 * Shared filter widgets for the monitoring pages. They stay intentionally thin:
 * labelled inputs, no hidden state — pages own their filter values (usually in
 * the URL query string so a view is shareable).
 */
import { Link } from 'react-router-dom'
import type { Metric } from '../../../../types/api'
import { EmptyState, Field } from '../../../../components/ui'
import { TrendChart } from '../../../../components/charts/TrendChart'
import type { TrendOutcome } from './normalise'

export function DateRangeFields({
  start,
  end,
  onChange,
  idPrefix,
}: {
  start: string
  end: string
  onChange: (next: { start?: string; end?: string }) => void
  idPrefix: string
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="From" htmlFor={`${idPrefix}-start`} className="w-40">
        <input
          id={`${idPrefix}-start`}
          type="date"
          className="input"
          value={start}
          max={end || undefined}
          onChange={(event) => onChange({ start: event.target.value || undefined, end: end || undefined })}
        />
      </Field>
      <Field label="To" htmlFor={`${idPrefix}-end`} className="w-40">
        <input
          id={`${idPrefix}-end`}
          type="date"
          className="input"
          value={end}
          min={start || undefined}
          onChange={(event) => onChange({ start: start || undefined, end: event.target.value || undefined })}
        />
      </Field>
    </div>
  )
}

/**
 * Renders the honest availability contract: when the backend says a trend is
 * not computable the reason and the point counts are shown verbatim with a
 * concrete next action — never a flat line or a decorative chart.
 */
export function TrendOutcomeView({
  outcome,
  metric,
  groupBy = 'none',
  variant = 'line',
  height = 300,
  actionHref = '/app/ingestion',
  actionLabel = 'Run an ingestion job',
}: {
  outcome: TrendOutcome
  metric: Metric
  groupBy?: 'none' | 'region' | 'location'
  variant?: 'line' | 'area' | 'bar'
  height?: number
  actionHref?: string
  actionLabel?: string
}) {
  if (outcome.kind === 'unavailable') {
    return (
      <EmptyState
        tone="warning"
        title="Not enough stored observations for a trend"
        description={
          <>
            <span>{outcome.reason}</span>
            {outcome.available_points !== undefined || outcome.required_points !== undefined ? (
              <span className="mono mt-1 block text-xs">
                available periods: {outcome.available_points ?? '—'} · required: {outcome.required_points ?? '—'}
              </span>
            ) : null}
          </>
        }
        action={
          <Link to={actionHref} className="btn btn-primary btn-sm">
            {actionLabel}
          </Link>
        }
      />
    )
  }
  return (
    <TrendChart
      series={outcome.series}
      metric={outcome.metric ?? metric}
      unit={outcome.unit}
      label={outcome.label}
      groupBy={groupBy}
      variant={variant}
      height={height}
      showCounts
    />
  )
}
