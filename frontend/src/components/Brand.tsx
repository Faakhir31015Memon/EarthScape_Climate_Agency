import { Link } from 'react-router-dom'
import { Globe2 } from 'lucide-react'
import { cn } from '../lib/cn'

export function Brand({ compact = false, to = '/app' }: { compact?: boolean; to?: string }) {
  return (
    <Link to={to} className="flex items-center gap-2.5" aria-label="EarthScape Climate Agency home">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-forest-700 text-white dark:bg-forest-600">
        <Globe2 className="h-4.5 w-4.5" strokeWidth={2} aria-hidden="true" />
      </span>
      <span className={cn('min-w-0 leading-tight', compact && 'hidden')}>
        <span className="block font-display text-[15px] font-semibold">EarthScape</span>
        <span className="block text-[11px] uppercase tracking-[0.14em] subtle">Climate Agency</span>
      </span>
    </Link>
  )
}
