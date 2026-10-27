import { Link } from 'react-router-dom'
import { cn } from '../lib/cn'

/**
 * Isobars around a station: two nested quarter-arc families in opposite corners, with the
 * reading point on the diagonal between them. Circular arcs on a fixed centre rather than
 * freehand curves, so the silhouette holds together at 16 px and the mark has 180° symmetry.
 */
function StationMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      className={cn('brand-mark h-[19px] w-[19px]', className)}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.9}
      strokeLinecap="round"
    >
      <path className="brand-arc" pathLength={1} d="M3 9.6a6.6 6.6 0 0 1 6.6-6.6" opacity=".62" />
      <path className="brand-arc" pathLength={1} d="M5.9 9.6a3.7 3.7 0 0 1 3.7-3.7" opacity=".62" />
      <path className="brand-arc" pathLength={1} d="M21 14.4a6.6 6.6 0 0 1-6.6 6.6" opacity=".62" />
      <path className="brand-arc" pathLength={1} d="M18.1 14.4a3.7 3.7 0 0 1-3.7 3.7" opacity=".62" />
      <circle className="brand-node" cx="12" cy="12" r="2.15" fill="currentColor" stroke="none" />
    </svg>
  )
}

export function Brand({ compact = false, to = '/app' }: { compact?: boolean; to?: string }) {
  return (
    <Link to={to} className="group flex items-center gap-2.5" aria-label="EarthScape Climate Agency home">
      <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-clay-600 text-white ring-1 ring-inset ring-white/15 transition-[transform,box-shadow] duration-200 group-hover:shadow-pop dark:bg-clay-500 dark:text-[#2a140b]">
        <StationMark />
      </span>
      <span className={cn('min-w-0 leading-tight', compact && 'hidden')}>
        <span className="block font-display text-[15px] font-semibold">EarthScape</span>
        <span className="block text-[10px] uppercase tracking-[0.18em] subtle">Climate Agency</span>
      </span>
    </Link>
  )
}

export { StationMark }
