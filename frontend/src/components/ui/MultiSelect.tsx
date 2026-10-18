import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, Search, X } from 'lucide-react'
import { cn } from '../../lib/cn'

export interface Option {
  value: string
  label: string
  hint?: string
}

export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = 'Select…',
  label,
  maxChips = 3,
  searchable = true,
  disabled = false,
}: {
  options: Option[]
  value: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  label?: string
  maxChips?: number
  searchable?: boolean
  disabled?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [term, setTerm] = useState('')
  const container = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => {
      if (container.current && !container.current.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const filtered = useMemo(() => {
    if (!term.trim()) return options
    const needle = term.toLowerCase()
    return options.filter((option) => option.label.toLowerCase().includes(needle) || option.value.toLowerCase().includes(needle))
  }, [options, term])

  const selected = useMemo(() => options.filter((option) => value.includes(option.value)), [options, value])

  const toggle = (option: string) => {
    onChange(value.includes(option) ? value.filter((item) => item !== option) : [...value, option])
  }

  return (
    <div className="relative min-w-0" ref={container}>
      {label ? <span className="label">{label}</span> : null}
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className={cn('select flex min-h-[38px] w-full items-center justify-between gap-2 text-left', !selected.length && 'text-[var(--text-subtle)]')}
      >
        <span className="flex flex-wrap items-center gap-1">
          {selected.length === 0 ? placeholder : null}
          {selected.slice(0, maxChips).map((option) => (
            <span key={option.value} className="badge badge-info normal-case">
              {option.label}
            </span>
          ))}
          {selected.length > maxChips ? <span className="badge badge-neutral">+{selected.length - maxChips}</span> : null}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0" aria-hidden="true" />
      </button>
      {open ? (
        <div className="card-tight absolute z-30 mt-1 w-full min-w-[16rem] p-2 shadow-elevated">
          {searchable ? (
            <div className="mb-2 flex items-center gap-2 rounded-lg border px-2" style={{ borderColor: 'var(--border-strong)' }}>
              <Search className="h-3.5 w-3.5 subtle" aria-hidden="true" />
              <input
                className="w-full bg-transparent py-1.5 text-sm outline-none"
                value={term}
                onChange={(event) => setTerm(event.target.value)}
                placeholder="Filter options"
                aria-label={`Filter ${label ?? 'options'}`}
                autoFocus
              />
              {term ? (
                <button type="button" className="subtle hover:text-[var(--text)]" onClick={() => setTerm('')} aria-label="Clear filter">
                  <X className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          ) : null}
          <div className="max-h-60 overflow-auto">
            {filtered.length === 0 ? <p className="px-2 py-3 text-xs subtle">No matching options.</p> : null}
            <ul role="listbox" aria-multiselectable="true" className="space-y-0.5">
              {filtered.map((option) => {
                const checked = value.includes(option.value)
                return (
                  <li key={option.value}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={checked}
                      onClick={() => toggle(option.value)}
                      className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-[var(--surface-muted)]"
                    >
                      <span
                        className={cn(
                          'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                          checked ? 'border-forest-600 bg-forest-600 text-white' : 'border-[var(--border-strong)]',
                        )}
                        aria-hidden="true"
                      >
                        {checked ? <Check className="h-3 w-3" /> : null}
                      </span>
                      <span className="min-w-0">
                        <span className="block truncate">{option.label}</span>
                        {option.hint ? <span className="block text-xs subtle">{option.hint}</span> : null}
                      </span>
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
          <div className="mt-2 flex items-center justify-between border-t pt-2 text-xs" style={{ borderColor: 'var(--border)' }}>
            <button type="button" className="link" onClick={() => onChange(filtered.map((option) => option.value))}>
              Select all
            </button>
            <button type="button" className="link" onClick={() => onChange([])}>
              Clear
            </button>
          </div>
        </div>
      ) : null}
    </div>
  )
}

export function Select({
  options,
  value,
  onChange,
  placeholder,
  disabled,
  id,
  className,
  ariaLabel,
}: {
  options: Option[]
  value: string
  onChange: (next: string) => void
  placeholder?: string
  disabled?: boolean
  id?: string
  className?: string
  ariaLabel?: string
}) {
  return (
    <select id={id} className={cn('select', className)} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} aria-label={ariaLabel}>
      {placeholder ? <option value="">{placeholder}</option> : null}
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
}
