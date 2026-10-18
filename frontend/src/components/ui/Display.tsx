import type { ReactNode } from 'react'

export function KeyValue({ items, columns = 1 }: { items: { label: ReactNode; value: ReactNode }[]; columns?: 1 | 2 }) {
  return (
    <dl className={columns === 2 ? 'grid gap-x-6 gap-y-3 sm:grid-cols-2' : 'space-y-3'}>
      {items.map((item, index) => (
        <div key={index} className="min-w-0 border-l pl-3" style={{ borderColor: 'var(--border)' }}>
          <dt className="text-[11px] font-semibold uppercase tracking-wide subtle">{item.label}</dt>
          <dd className="mt-0.5 break-words text-sm">{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Progress({ value, tone = 'forest', label }: { value: number; tone?: 'forest' | 'hydro' | 'solar' | 'alert'; label?: string }) {
  const clamped = Math.max(0, Math.min(100, value))
  const colors: Record<string, string> = {
    forest: 'bg-forest-600',
    hydro: 'bg-hydro-500',
    solar: 'bg-solar-500',
    alert: 'bg-alert-high',
  }
  return (
    <div>
      <div className="h-2 w-full overflow-hidden rounded-full" style={{ backgroundColor: 'var(--surface-muted)' }} role="progressbar" aria-valuenow={clamped} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? 'progress'}>
        <div className={`h-full rounded-full transition-all ${colors[tone]}`} style={{ width: `${clamped}%` }} />
      </div>
    </div>
  )
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="mono">{children}</span>
}
