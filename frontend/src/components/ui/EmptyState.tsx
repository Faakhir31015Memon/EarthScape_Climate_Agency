import type { ReactNode } from 'react'
import { Inbox } from 'lucide-react'

export function EmptyState({
  icon,
  title,
  description,
  action,
  tone = 'neutral',
}: {
  icon?: ReactNode
  title: string
  description?: ReactNode
  action?: ReactNode
  tone?: 'neutral' | 'warning'
}) {
  return (
    <div
      className={`card flex flex-col items-start gap-3 ${tone === 'warning' ? 'border-solar-300 bg-solar-50/50 dark:border-solar-700 dark:bg-solar-950/20' : ''}`}
      role="status"
    >
      <div className="flex items-start gap-3">
        <span className="mt-0.5 text-[var(--text-subtle)]">{icon ?? <Inbox className="h-5 w-5" />}</span>
        <div className="min-w-0">
          <h3 className="text-sm font-semibold">{title}</h3>
          {description ? <p className="mt-1 text-sm leading-relaxed muted">{description}</p> : null}
        </div>
      </div>
      {action ? <div className="pl-8">{action}</div> : null}
    </div>
  )
}
