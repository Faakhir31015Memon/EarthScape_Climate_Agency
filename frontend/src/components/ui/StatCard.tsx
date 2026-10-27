import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useCountUp } from '../../hooks/useCountUp'

export function StatCard({
  label,
  value,
  unit,
  hint,
  trend,
  tone = 'default',
  size = 'number',
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
  /** 'number' for a metric, 'text' when the value is a phrase that must stay readable. */
  size?: 'number' | 'text'
  to?: string
  icon?: ReactNode
  loading?: boolean
}) {
  /* The figure itself stays in ink; colour is spent on the rail and the icon. */
  const accents: Record<string, string> = {
    default: 'text-[var(--text)]',
    forest: 'text-forest-600 dark:text-forest-300',
    hydro: 'text-hydro-600 dark:text-hydro-300',
    solar: 'text-solar-600 dark:text-solar-300',
    alert: 'text-alert-critical',
  }
  const rails: Record<string, string> = {
    default: 'bg-[var(--brand)]/70',
    forest: 'bg-forest-500',
    hydro: 'bg-hydro-500',
    solar: 'bg-solar-500',
    alert: 'bg-alert-critical',
  }
  const numeric = typeof value === 'number' ? value.toLocaleString('en-US') : typeof value === 'string' ? value : null
  const { ref, display } = useCountUp<HTMLParagraphElement>(numeric ?? '')
  const body = (
    <>
      <span aria-hidden="true" className={`absolute inset-y-4 left-0 w-[3px] rounded-r-full ${rails[tone]}`} />
      <div className="flex items-start justify-between gap-2">
        <p className="panel-title">{label}</p>
        {icon ? <span className={`shrink-0 ${tone === 'default' ? 'subtle' : accents[tone]}`}>{icon}</span> : null}
      </div>
      <p
        ref={ref}
        className={size === 'text'
          ? `mt-2 break-words text-[15px] font-semibold leading-snug ${accents[tone]}`
          : `mt-2.5 font-display text-[27px] font-semibold leading-none tracking-tight ${accents[tone]}`}
      >
        {loading ? <span className="skeleton inline-block h-6 w-20 align-middle" /> : numeric === null ? value : display}
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
      <Link to={to} className="card relative overflow-hidden pl-6 transition-shadow hover:shadow-elevated">
        {body}
      </Link>
    )
  }
  return <div className="card relative overflow-hidden pl-6">{body}</div>
}
