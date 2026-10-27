import { useState, type ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface TabItem {
  id: string
  label: string
  badge?: ReactNode
  content: ReactNode
}

export function Tabs({ items, initial = 0, className }: { items: TabItem[]; initial?: number; className?: string }) {
  const [active, setActive] = useState(items[Math.min(initial, items.length - 1)]?.id ?? '')
  const current = items.find((item) => item.id === active) ?? items[0]
  return (
    <div className={className}>
      <div role="tablist" className="mb-4 flex flex-wrap gap-1 border-b" style={{ borderColor: 'var(--border)' }}>
        {items.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={item.id === active}
            aria-controls={`panel-${item.id}`}
            onClick={() => setActive(item.id)}
            className={cn(
              '-mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-150',
              item.id === active ? 'border-[var(--brand)] text-[var(--brand)]' : 'border-transparent muted hover:text-[var(--text)]',
            )}
          >
            {item.label}
            {item.badge}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${current?.id}`} aria-labelledby={`tab-${current?.id}`}>
        {current?.content}
      </div>
    </div>
  )
}
