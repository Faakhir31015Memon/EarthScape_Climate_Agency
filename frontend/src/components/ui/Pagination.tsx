import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Paginated } from '../../types/api'

export function Pagination({
  meta,
  onPage,
  disabled = false,
  label = 'records',
}: {
  meta: Pick<Paginated<unknown>, 'page' | 'pages' | 'total' | 'has_next' | 'has_previous'> | undefined
  onPage: (page: number) => void
  disabled?: boolean
  label?: string
}) {
  if (!meta) return null
  const start = meta.total === 0 ? 0 : (meta.page - 1) * (meta.pages > 0 ? Math.ceil(meta.total / meta.pages) : 0) + 1
  return (
    <nav className="mt-4 flex flex-wrap items-center justify-between gap-3" aria-label="Pagination">
      <p className="text-xs subtle">
        {meta.total === 0 ? (
          <>No {label}</>
        ) : (
          <>
            Showing <span className="font-medium">{start}</span>–
            <span className="font-medium">{Math.min(start + (meta.pages ? Math.ceil(meta.total / meta.pages) : 0) - 1, meta.total)}</span> of{' '}
            <span className="font-medium">{meta.total.toLocaleString()}</span> {label}
          </>
        )}
      </p>
      <div className="flex items-center gap-1.5">
        <button type="button" className="btn btn-secondary btn-sm" disabled={disabled || !meta.has_previous} onClick={() => onPage(meta.page - 1)}>
          <ChevronLeft className="h-3.5 w-3.5" /> Previous
        </button>
        <span className="px-1 text-xs font-medium">
          Page {meta.page} / {Math.max(meta.pages, 1)}
        </span>
        <button type="button" className="btn btn-secondary btn-sm" disabled={disabled || !meta.has_next} onClick={() => onPage(meta.page + 1)}>
          Next <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </nav>
  )
}
