import { useMemo } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { Metric, RegionAggregate } from '../../types/api'
import { formatNumber, humanise } from '../../lib/format'
import { AXIS_LINE, AXIS_TICK, ChartFooter, ChartTooltipContent, DRAW_MS, GRID_STROKE, colorAt, useDrawIn } from './chartKit'

export function RegionBarChart({
  regions,
  metric,
  unit,
  label,
  height = 320,
  max = 12,
  showStd = false,
}: {
  regions: RegionAggregate[]
  metric: Metric
  unit: string
  label?: string
  height?: number
  max?: number
  showStd?: boolean
}) {
  const data = useMemo(
    () =>
      [...regions]
        .filter((region) => region.mean !== null && region.mean !== undefined)
        .sort((left, right) => Number(right.mean) - Number(left.mean))
        .slice(0, max)
        .map((region) => ({
          region: humanise(region.region_code),
          code: region.region_code,
          value: region.mean as number,
          stdLabel: showStd && region.std !== undefined && region.std !== null ? `±${formatNumber(region.std, 1)}` : undefined,
          observations: region.observation_count,
          observations_label: `${formatNumber(region.observation_count, 0)} obs`,
          min: region.min,
          max_value: region.max,
          aggregation: region.aggregation,
        })),
    [regions, max, showStd],
  )

  const drawIn = useDrawIn()

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-12 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
        No regional aggregates are available for this metric and range.
      </div>
    )
  }

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`${label ?? humanise(metric)} by region`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} horizontal={false} />
            <XAxis type="number" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} unit={` ${unit}`} domain={['auto', 'auto']} />
            <YAxis type="category" dataKey="region" tick={AXIS_TICK} axisLine={AXIS_LINE} tickLine={false} width={130} />
            <Tooltip content={<ChartTooltipContent unit={unit} />} cursor={{ fill: 'var(--surface-muted)' }} />
            <Bar
              dataKey="value"
              name={label ?? humanise(metric)}
              radius={[0, 4, 4, 0]}
              isAnimationActive={drawIn}
              animationDuration={DRAW_MS}
              animationEasing="ease-out"
            >
              <LabelList dataKey="observations_label" position="right" style={{ fill: 'var(--text-subtle)', fontSize: 10 }} />
              {data.map((entry, index) => (
                <Cell key={entry.code} fill={colorAt(index)} fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartFooter>
        {data.length} regions · {metric === 'precipitation_mm' ? 'totals' : 'means'} in {unit}. Regions without observations are omitted rather than shown as zero.
      </ChartFooter>
    </div>
  )
}
