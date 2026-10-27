import { useMemo } from 'react'
import { Area, Bar, CartesianGrid, ComposedChart, Line, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Anomaly, Metric, SeriesPoint } from '../../types/api'
import { formatNumber, metricLabel } from '../../lib/format'
import { AXIS_LINE, AXIS_TICK, ChartFooter, ChartLegend, ChartTooltipContent, DRAW_MS, GRID_STROKE, colorAt, useDrawIn } from './chartKit'

export interface TrendChartProps {
  series: SeriesPoint[]
  metric: Metric
  unit: string
  label?: string
  groupBy?: 'none' | 'region' | 'location'
  variant?: 'line' | 'area' | 'bar'
  anomalies?: Anomaly[]
  height?: number
  baseline?: number | null
  showCounts?: boolean
}

interface Row {
  period: string
  counts: number
  [key: string]: string | number | null
}

export function TrendChart({
  series,
  metric,
  unit,
  label,
  groupBy = 'none',
  variant = 'line',
  anomalies = [],
  height = 300,
  baseline = null,
  showCounts = false,
}: TrendChartProps) {
  const { rows, keys, keyLabels, pointsWithData } = useMemo(() => {
    const groupKey = (point: SeriesPoint) =>
      groupBy === 'region' ? point.region_code ?? 'all' : groupBy === 'location' ? point.location_code ?? 'all' : 'value'
    const byPeriod = new Map<string, Row>()
    const keySet = new Set<string>()
    let count = 0
    series.forEach((point) => {
      const key = groupKey(point)
      keySet.add(key)
      const existing = byPeriod.get(point.period)
      if (!existing) {
        const row: Row = { period: point.period, counts: point.observation_count }
        row[key] = point.value
        byPeriod.set(point.period, row)
      } else {
        existing[key] = point.value
        existing.counts = Math.max(existing.counts ?? 0, point.observation_count)
      }
      if (point.value !== null && point.value !== undefined) count += 1
    })
    const rows = [...byPeriod.values()].sort((left, right) => String(left.period).localeCompare(String(right.period)))
    const keys = [...keySet]
    const keyLabels: Record<string, string> = {}
    keys.forEach((key) => {
      keyLabels[key] = key === 'value' ? label ?? metricLabel(metric) : key
    })
    return { rows, keys, keyLabels, pointsWithData: count }
  }, [series, groupBy, label, metric])

  const drawIn = useDrawIn()

  const markers = useMemo(() => {
    if (!keys.length) return []
    const single = keys.length === 1
    return anomalies
      .map((anomaly) => {
        const row = rows.find((candidate) => candidate.period === anomaly.observation_date.slice(0, 10))
        if (!row) return null
        const key = single ? keys[0] : anomaly.location_code && row[anomaly.location_code] !== undefined ? anomaly.location_code : anomaly.region_code && row[anomaly.region_code] !== undefined ? anomaly.region_code : keys[0]
        return { period: row.period, value: anomaly.value, key, severity: anomaly.severity }
      })
      .filter((marker): marker is NonNullable<typeof marker> => marker !== null && rowHasKey(rows, marker.period, marker.key))
  }, [anomalies, rows, keys])

  if (rows.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-12 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
        No data points in the selected range.
      </div>
    )
  }

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`${label ?? metricLabel(metric)} over time`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis
              dataKey="period"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={AXIS_LINE}
              minTickGap={28}
              angle={rows.length > 40 ? -35 : 0}
              textAnchor={rows.length > 40 ? 'end' : 'middle'}
              height={rows.length > 40 ? 52 : 30}
            />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={54} unit={` ${unit}`} domain={['auto', 'auto']} />
            <Tooltip
              content={<ChartTooltipContent unit={unit} />}
              cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }}
            />
            {baseline !== null && baseline !== undefined ? (
              <ReferenceLine y={baseline} stroke="var(--text-subtle)" strokeDasharray="4 4" label={{ value: 'baseline', position: 'right', fill: 'var(--text-subtle)', fontSize: 11 }} />
            ) : null}
            {keys.map((key, index) => {
              const color = keys.length === 1 ? colorAt(0) : colorAt(index)
              const common = {
                type: 'monotone' as const,
                dataKey: key,
                name: keyLabels[key],
                stroke: color,
                dot: false,
                connectNulls: false,
                strokeWidth: 2,
                isAnimationActive: drawIn,
                animationDuration: DRAW_MS,
                animationEasing: 'ease-out' as const,
              }
              if (variant === 'bar') {
                return (
                  <Bar
                    key={key}
                    dataKey={key}
                    name={keyLabels[key]}
                    fill={color}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive={drawIn}
                    animationDuration={DRAW_MS}
                    animationEasing="ease-out"
                  />
                )
              }
              if (variant === 'area') {
                return <Area key={key} {...common} fill={color} fillOpacity={0.14} />
              }
              return <Line key={key} {...common} />
            })}
            {markers.map((marker, index) => (
              <ReferenceDot
                key={`${marker.period}-${index}`}
                x={marker.period}
                y={marker.value}
                r={4}
                fill={marker.severity === 'critical' || marker.severity === 'high' ? '#a93b2c' : '#c1712f'}
                stroke="var(--surface)"
                strokeWidth={1.5}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {keys.length > 1 ? <ChartLegend items={keys.map((key, index) => ({ label: keyLabels[key], color: keys.length === 1 ? colorAt(0) : colorAt(index) }))} /> : null}
      <ChartFooter>
        {showCounts
          ? `${rows.length} periods · ${pointsWithData} values · peak coverage ${formatNumber(Math.max(...rows.map((row) => row.counts ?? 0), 0), 0)} observations/period`
          : `${rows.length} periods · ${pointsWithData} values`}
        {markers.length ? ` · ${markers.length} anomaly point${markers.length === 1 ? '' : 's'} marked` : ''}
      </ChartFooter>
    </div>
  )
}

function rowHasKey(rows: Row[], period: string, key: string): boolean {
  const row = rows.find((candidate) => candidate.period === period)
  return Boolean(row && row[key] !== undefined && row[key] !== null)
}
