import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Info } from 'lucide-react'
import { cn } from '../../../lib/cn'

/** Page width container used by every public page (nav and footer come from PublicLayout). */
export function PublicContainer({ children, narrow = false, className }: { children: ReactNode; narrow?: boolean; className?: string }) {
  return <div className={cn('mx-auto w-full px-5 py-10 sm:py-14', narrow ? 'max-w-[860px]' : 'max-w-[1200px]', className)}>{children}</div>
}

export function SectionHeading({
  id,
  eyebrow,
  title,
  description,
  actions,
  className,
}: {
  id?: string
  eyebrow?: string
  title: string
  description?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('mb-5 flex flex-wrap items-end justify-between gap-3', className)}>
      <div className="min-w-0">
        {eyebrow ? <p className="panel-title mb-1.5">{eyebrow}</p> : null}
        <h2 id={id} className="font-display text-xl font-semibold leading-tight sm:text-2xl">
          {title}
        </h2>
        {description ? <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}

/** Long-form policy/help text. */
export function Prose({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        'space-y-3 text-sm leading-relaxed muted',
        '[&_a]:font-medium [&_a]:text-forest-700 [&_a]:underline [&_a]:underline-offset-2 [&_a]:hover:text-forest-800 dark:[&_a]:text-forest-300',
        '[&_h3]:mb-1 [&_h3]:mt-6 [&_h3]:font-display [&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-[var(--text)]',
        '[&_li]:mt-1 [&_p]:mt-2 [&_strong]:font-semibold [&_strong]:text-[var(--text)]',
        '[&_ul]:list-disc [&_ul]:pl-5',
        className,
      )}
    >
      {children}
    </div>
  )
}

/** The recurring honesty note: nothing on a public page is invented. */
export function ProvenanceNote({ children }: { children?: ReactNode }) {
  return (
    <p className="card-tight flex items-start gap-2 text-xs leading-relaxed muted">
      <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
      <span>
        {children ??
          'Every number on this page is computed live from observations stored by the platform. When an API call fails or the record is empty, the page says so instead of showing placeholder figures.'}
      </span>
    </p>
  )
}

/** In-page jump navigation for the long documentation-style public pages. */
export function AnchorNav({ items }: { items: { id: string; label: string }[] }) {
  return (
    <nav aria-label="On this page" className="card-tight mb-6 flex flex-wrap gap-x-4 gap-y-2 text-sm">
      {items.map((item) => (
        <a key={item.id} href={`#${item.id}`} className="link">
          {item.label}
        </a>
      ))}
    </nav>
  )
}

export function CallToActionBar({ primaryLabel, primaryTo, secondaryLabel, secondaryTo, note }: {
  primaryLabel: string
  primaryTo: string
  secondaryLabel?: string
  secondaryTo?: string
  note?: ReactNode
}) {
  return (
    <div className="card flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="panel-title mb-1">Next step</p>
        <p className="text-sm leading-relaxed muted">{note ?? 'Sign in to run ingestion, analytics and forecasts against your own data record.'}</p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Link to={primaryTo} className="btn btn-primary">
          {primaryLabel}
        </Link>
        {secondaryLabel && secondaryTo ? (
          <Link to={secondaryTo} className="btn btn-secondary">
            {secondaryLabel}
          </Link>
        ) : null}
      </div>
    </div>
  )
}

/** Definition row used by the capability and policy pages. */
export function DefinitionList({ items }: { items: { term: string; detail: ReactNode }[] }) {
  return (
    <dl className="space-y-3">
      {items.map((item) => (
        <div key={item.term} className="border-l pl-3" style={{ borderColor: 'var(--border)' }}>
          <dt className="text-xs font-semibold uppercase tracking-wide subtle">{item.term}</dt>
          <dd className="mt-1 text-sm leading-relaxed muted">{item.detail}</dd>
        </div>
      ))}
    </dl>
  )
}
