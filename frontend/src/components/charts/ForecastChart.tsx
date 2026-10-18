import { useMemo } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { ForecastValue, SeriesPoint } from '../../types/api'
import { AXIS_LINE, AXIS_TICK, ChartFooter, ChartLegend, ChartTooltipContent, GRID_STROKE } from './chartKit'

/**
 * Actual observations (solid) continued by the model horizon (dashed) with the
 * uncertainty band drawn only when the backend supplied bounds.
 */
export function ForecastChart({
  history,
  forecast,
  unit,
  modelLabel,
  height = 320,
}: {
  history: SeriesPoint[]
  forecast: ForecastValue[]
  unit: string
  modelLabel?: string
  height?: number
}) {
  const rows = useMemo(() => {
    const map = new Map<string, Record<string, string | number | null | [number, number] | null>>()
    history.forEach((point) => {
      map.set(point.period, { period: point.period, actual: point.value, band: null })
    })
    const anchor = history.length ? history[history.length - 1].period : null
    forecast.forEach((point) => {
      const key = point.forecast_date.slice(0, 10)
      const lower = point.lower_bound ?? null
      const upper = point.upper_bound ?? null
      const existing = map.get(key)
      const row: Record<string, string | number | null | [number, number] | null> = existing ?? { period: key, actual: null, band: null }
      row.forecast = point.value
      if (!existing && anchor && map.has(anchor)) {
        // Bridge the visual gap so the forecast line starts at the last observation.
        const bridge = map.get(anchor) as Record<string, string | number | null>
        if (bridge.forecast === undefined) bridge.forecast = bridge.actual ?? null
      }
      row.band = lower !== null && upper !== null ? [lower, upper] : null
      map.set(key, row)
    })
    return [...map.values()].sort((left, right) => String(left.period).localeCompare(String(right.period)))
  }, [history, forecast])

  const hasBand = rows.some((row) => Array.isArray(row.band))
  const hasHistory = history.length > 0

  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-12 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
        No forecast values to plot.
      </div>
    )
  }

  return (
    <div>
      <div style={{ height }} role="img" aria-label="Forecast versus observed history">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows as never[]} margin={{ top: 6, right: 14, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis dataKey="period" tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} minTickGap={26} />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={58} unit={` ${unit}`} domain={['auto', 'auto']} />
            <Tooltip content={<ChartTooltipContent unit={unit} />} />
            {hasBand ? (
              <Area dataKey="band" name="Uncertainty range" stroke="none" fill="#3b9af6" fillOpacity={0.16} isAnimationActive={false} connectNulls={false} />
            ) : null}
            {hasHistory ? (
              <Line dataKey="actual" name="Observed" stroke="#27633a" strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />
            ) : null}
            <Line dataKey="forecast" name={modelLabel ?? 'Forecast'} stroke="#e98513" strokeWidth={2} strokeDasharray="5 4" dot={false} connectNulls isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend
        items={[
          ...(hasHistory ? [{ label: 'Observed', color: '#27633a' }] : []),
          { label: modelLabel ?? 'Forecast', color: '#e98513' },
          ...(hasBand ? [{ label: 'Uncertainty range', color: '#3b9af6' }] : []),
        ]}
      />
      <ChartFooter>
        {history.length} observed periods · {forecast.length} forecast steps
        {hasBand ? '' : ' · model did not publish prediction intervals'}
      </ChartFooter>
    </div>
  )
}
