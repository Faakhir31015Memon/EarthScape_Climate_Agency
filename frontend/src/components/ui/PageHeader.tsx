import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  backTo,
  meta,
}: {
  eyebrow?: string
  title: string
  description?: ReactNode
  actions?: ReactNode
  backTo?: string
  meta?: ReactNode
}) {
  return (
    <header className="mb-6 flex flex-col gap-4">
      {backTo ? (
        <Link to={backTo} className="link inline-flex w-fit items-center gap-1.5 text-sm">
          <ArrowLeft className="h-4 w-4" /> Back
        </Link>
      ) : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow ? <p className="panel-title mb-1.5">{eyebrow}</p> : null}
          <h1 className="text-2xl font-semibold leading-tight sm:text-[28px]">{title}</h1>
          {description ? <p className="mt-2 max-w-3xl text-sm leading-relaxed muted">{description}</p> : null}
          {meta ? <div className="mt-3 flex flex-wrap items-center gap-2 text-xs subtle">{meta}</div> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  )
}
