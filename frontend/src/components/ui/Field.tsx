import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export function Field({
  label,
  htmlFor,
  error,
  hint,
  required = false,
  children,
  className,
}: {
  label: ReactNode
  htmlFor?: string
  error?: string | null
  hint?: ReactNode
  required?: boolean
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cn('min-w-0', className)}>
      <label className="label" htmlFor={htmlFor}>
        {label}
        {required ? <span className="ml-1 text-alert-critical" aria-hidden="true">*</span> : null}
      </label>
      {children}
      {error ? (
        <p className="mt-1.5 text-xs font-medium text-alert-critical" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="mt-1.5 text-xs leading-relaxed subtle">{hint}</p>
      ) : null}
    </div>
  )
}

export function FieldSet({ legend, description, children }: { legend: string; description?: ReactNode; children: ReactNode }) {
  return (
    <fieldset className="space-y-4">
      <legend className="panel-title mb-1">{legend}</legend>
      {description ? <p className="mb-2 -mt-1 text-xs leading-relaxed muted">{description}</p> : null}
      {children}
    </fieldset>
  )
}
