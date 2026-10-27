import { useEffect, useState, type ReactNode, type SVGProps } from 'react'
import { metricUnit } from '../../lib/format'
import { prefersReducedMotion } from '../../lib/motion'

/**
 * A chart draws itself in on the first paint and stays still after that. A background poll
 * that returns the same record should not make the operator watch the line being redrawn,
 * and the OS motion veto skips the draw entirely.
 */
export function useDrawIn(): boolean {
  const [first, setFirst] = useState(() => !prefersReducedMotion())
  useEffect(() => {
    if (!first) return undefined
    const id = window.setTimeout(() => setFirst(false), 1600)
    return () => window.clearTimeout(id)
  }, [first])
  return first
}

export const DRAW_MS = 760

/* Mid-tone hues that stay legible on field paper and on graphite alike. */
export const SERIES_COLORS = ['#c97145', '#4e8aa8', '#b98b2a', '#6c9c62', '#a0855d', '#77a9c2', '#c1712f', '#8fb883']

export function colorAt(index: number): string {
  return SERIES_COLORS[index % SERIES_COLORS.length]
}

export const AXIS_TICK: SVGProps<SVGTextElement> = { fill: 'var(--text-subtle)', fontSize: 11, fontFamily: '"IBM Plex Sans", system-ui, sans-serif' }

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
