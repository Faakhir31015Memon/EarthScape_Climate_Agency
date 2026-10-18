import type { ReactNode, SVGProps } from 'react'
import { metricUnit } from '../../lib/format'

export const SERIES_COLORS = ['#27633a', '#3b9af6', '#e98513', '#84bd90', '#c0562f', '#60b8fa', '#94744e', '#4d8f62']

export function colorAt(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]
}

export const AXIS_TICK: SVGProps<SVGTextElement> = { fill: 'var(--text-subtle)', fontSize: 11, fontFamily: 'Inter, system-ui, sans-serif' }

export const AXIS_LINE = { stroke: 'var(--border-strong)' }
export const GRID_STROKE = 'var(--border)'

export interface TooltipEntry {
  name?: string | number
  value?: number | string | null
  color?: string
  dataKey?: string | number
  unit?: string
}

export function ChartTooltipContent({
  active,
  payload,
  label,
  unit,
  labelFormatter,
  valueDigits = 2,
}: {
  active?: boolean
  payload?: TooltipEntry[]
  label?: string | number
  unit?: string
  labelFormatter?: (label: string | number) => string
  valueDigits?: number
}) {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="card-tight shadow-elevated min-w-[10rem] px-3 py-2 text-xs">
      <p className="font-semibold">{labelFormatter && label !== undefined ? labelFormatter(label) : label}</p>
      <ul className="mt-1.5 space-y-1">
        {payload.map((entry, index) => (
          <li key={`${String(entry.dataKey ?? index)}`} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 muted">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: entry.color }} aria-hidden="true" />
              <span className="truncate">{entry.name}</span>
            </span>
            <span className="mono font-medium">
              {entry.value === null || entry.value === undefined
                ? '—'
                : typeof entry.value === 'number'
                  ? entry.value.toFixed(valueDigits)
                  : String(entry.value)}
              {` ${entry.unit ?? unit ?? ''}`.trimEnd()}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ChartLegend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-1.5 muted">
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: item.color }} aria-hidden="true" />
          {item.label}
        </li>
      ))}
    </ul>
  )
}

export function ChartFooter({ children }: { children: ReactNode }) {
  if (!children) return null
  return <div className="mt-3 border-t pt-2.5 text-xs leading-relaxed subtle" style={{ borderColor: 'var(--border)' }}>{children}</div>
}

export { metricUnit }
