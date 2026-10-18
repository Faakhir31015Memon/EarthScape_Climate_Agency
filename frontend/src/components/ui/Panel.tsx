import type { ReactNode } from 'react'
import { Info } from 'lucide-react'

export function Panel({
  title,
  description,
  actions,
  children,
  footer,
  className = '',
  bodyClassName = '',
  dense = false,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
  footer?: ReactNode
  className?: string
  bodyClassName?: string
  dense?: boolean
}) {
  return (
    <section className={`${dense ? 'card-tight' : 'card'} flex min-w-0 flex-col ${className}`}>
      {title || actions ? (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title ? <h2 className="text-sm font-semibold">{title}</h2> : null}
            {description ? <p className="mt-1 text-xs leading-relaxed muted">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </div>
      ) : null}
      <div className={`min-w-0 flex-1 ${bodyClassName}`}>{children}</div>
      {footer ? <div className="mt-4 border-t pt-3 text-xs subtle" style={{ borderColor: 'var(--border)' }}>{footer}</div> : null}
    </section>
  )
}

export function Hint({ children }: { children: ReactNode }) {
  return (
    <p className="mt-2 flex items-start gap-2 text-xs leading-relaxed muted">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </p>
  )
}
