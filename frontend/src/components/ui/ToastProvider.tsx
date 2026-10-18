import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from 'lucide-react'

export type ToastTone = 'success' | 'info' | 'warning' | 'danger'

export interface Toast {
  id: number
  tone: ToastTone
  title: string
  description?: string
}

interface ToastApi {
  push: (toast: Omit<Toast, 'id'>) => void
  success: (title: string, description?: string) => void
  info: (title: string, description?: string) => void
  warning: (title: string, description?: string) => void
  danger: (title: string, description?: string) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastApi | null>(null)
const ICONS: Record<ToastTone, typeof Info> = {
  success: CheckCircle2,
  info: Info,
  warning: AlertTriangle,
  danger: XCircle,
}

const TONES: Record<ToastTone, string> = {
  success: 'border-forest-300 bg-forest-50 text-forest-900 dark:border-forest-700 dark:bg-forest-950/60 dark:text-forest-100',
  info: 'border-hydro-300 bg-hydro-50 text-hydro-900 dark:border-hydro-700 dark:bg-hydro-950/60 dark:text-hydro-100',
  warning: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950/60 dark:text-amber-100',
  danger: 'border-red-300 bg-red-50 text-red-900 dark:border-red-700 dark:bg-red-950/60 dark:text-red-100',
}

let counter = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (toast: Omit<Toast, 'id'>) => {
      counter += 1
      const id = counter
      setToasts((current) => [...current, { ...toast, id }].slice(-4))
      window.setTimeout(() => dismiss(id), toast.tone === 'danger' ? 9000 : 5200)
    },
    [dismiss],
  )

  const value = useMemo<ToastApi>(
    () => ({
      push,
      dismiss,
      success: (title, description) => push({ tone: 'success', title, description }),
      info: (title, description) => push({ tone: 'info', title, description }),
      warning: (title, description) => push({ tone: 'warning', title, description }),
      danger: (title, description) => push({ tone: 'danger', title, description }),
    }),
    [push, dismiss],
  )

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,90vw)] flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone]
          return (
            <div key={toast.id} className={`card-tight card pointer-events-auto flex items-start gap-3 border ${TONES[toast.tone]} animate-fade-in`}>
              <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold leading-tight">{toast.title}</p>
                {toast.description ? <p className="mt-1 text-xs leading-snug opacity-80">{toast.description}</p> : null}
              </div>
              <button type="button" className="btn btn-ghost btn-icon h-6 w-6" onClick={() => dismiss(toast.id)} aria-label="Dismiss notification">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside ToastProvider')
  return context
}
