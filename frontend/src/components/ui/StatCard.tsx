import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { Link } from 'react-router-dom'

export function StatCard({
  label,
  value,
  unit,
  hint,
  trend,
  tone = 'default',
  to,
  icon,
  loading = false,
}: {
  label: string
  value: ReactNode
  unit?: string
  hint?: ReactNode
  trend?: { direction: 'up' | 'down' | 'flat'; label: string; tone?: 'positive' | 'negative' | 'neutral' }
  tone?: 'default' | 'forest' | 'hydro' | 'solar' | 'alert'
  to?: string
  icon?: ReactNode
  loading?: boolean
}) {
  const accents: Record<string, string> = {
    default: 'text-[var(--brand)]',
    forest: 'text-forest-600 dark:text-forest-300',
    hydro: 'text-hydro-600 dark:text-hydro-300',
    solar: 'text-solar-600 dark:text-solar-300',
    alert: 'text-alert-critical',
  }
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <p className="panel-title">{label}</p>
        {icon ? <span className={`shrink-0 ${accents[tone]}`}>{icon}</span> : null}
      </div>
      <p className={`mt-2 font-display text-[26px] font-semibold leading-none ${accents[tone]}`}>
        {loading ? <span className="skeleton inline-block h-6 w-20 align-middle" /> : value}
        {unit ? <span className="ml-1 text-sm font-medium muted">{unit}</span> : null}
      </p>
      {trend ? (
        <p
          className={`mt-2 inline-flex items-center gap-1 text-xs font-medium ${
            trend.tone === 'negative' ? 'text-alert-high' : trend.tone === 'positive' ? 'text-forest-600 dark:text-forest-300' : 'muted'
          }`}
        >
          {trend.direction === 'up' ? (
            <ArrowUpRight className="h-3.5 w-3.5" />
          ) : trend.direction === 'down' ? (
            <ArrowDownRight className="h-3.5 w-3.5" />
          ) : (
            <Minus className="h-3.5 w-3.5" />
          )}
          {trend.label}
        </p>
      ) : null}
      {hint ? <p className="mt-2 text-xs leading-relaxed subtle">{hint}</p> : null}
    </>
  )
  if (to) {
    return (
      <Link to={to} className="card transition-shadow hover:shadow-elevated">
        {body}
      </Link>
    )
  }
  return <div className="card">{body}</div>
}
