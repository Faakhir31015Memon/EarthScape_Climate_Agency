import type { ReactNode } from 'react'
import { RefreshCw, Search } from 'lucide-react'
import { cn } from '../../lib/cn'

export function SearchInput({
  value,
  onChange,
  placeholder = 'Search…',
  label,
  className,
}: {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  label?: string
  className?: string
}) {
  return (
    <div className={cn('relative min-w-0', className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 subtle" aria-hidden="true" />
      <input
        type="search"
        className="input pl-8"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
      />
    </div>
  )
}

export function RefreshButton({ onClick, busy = false, label = 'Refresh' }: { onClick: () => void; busy?: boolean; label?: string }) {
  return (
    <button type="button" className="btn btn-secondary btn-sm" onClick={onClick} disabled={busy}>
      <RefreshCw className={cn('h-3.5 w-3.5', busy && 'animate-spin')} aria-hidden="true" /> {label}
    </button>
  )
}

export function FilterBar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('card-tight mb-4 flex flex-wrap items-end gap-3', className)} role="group" aria-label="Filters">
      {children}
    </div>
  )
}

export function Toggle({ checked, onChange, label, disabled = false }: { checked: boolean; onChange: (next: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="inline-flex items-center gap-2 text-sm"
    >
      <span className={cn('relative h-5 w-9 rounded-full transition-colors', checked ? 'bg-forest-600' : 'bg-[var(--border-strong)]')}>
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all', checked ? 'left-[1.125rem]' : 'left-0.5')} />
      </span>
      <span className="muted">{label}</span>
    </button>
  )
}
