import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  footer,
  width = 'max-w-lg',
}: {
  open: boolean
  title: string
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  width?: string
}) {
  const panel = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    panel.current?.focus()
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previous
    }
  }, [open, onClose])

  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-black/45 backdrop-blur-[2px]" onClick={onClose} aria-hidden="true" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={`card relative z-10 max-h-[92vh] w-full overflow-auto ${width} animate-fade-in`}
      >
        <div className="mb-4 flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">{title}</h2>
            {description ? <p className="mt-1 text-sm muted">{description}</p> : null}
          </div>
          <button type="button" className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close dialog">
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-w-0">{children}</div>
        {footer ? (
          <div className="mt-5 flex flex-wrap justify-end gap-2 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  )
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = 'Confirm',
  tone = 'primary',
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean
  title: string
  message: ReactNode
  confirmLabel?: string
  tone?: 'primary' | 'danger'
  busy?: boolean
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <Modal
      open={open}
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn-secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className={`btn ${tone === 'danger' ? 'btn-danger' : 'btn-primary'}`} onClick={onConfirm} disabled={busy}>
            {busy ? 'Working…' : confirmLabel}
          </button>
        </>
      }
    >
      <div className="text-sm leading-relaxed muted">{message}</div>
    </Modal>
  )
}
