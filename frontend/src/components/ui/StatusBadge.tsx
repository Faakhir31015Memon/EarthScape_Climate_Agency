import type { ReactNode } from 'react'
import { CircleDot, Clock, Loader2 } from 'lucide-react'
import { humanise } from '../../lib/format'

type Tone = 'neutral' | 'success' | 'info' | 'warning' | 'danger'

const TONE_CLASS: Record<Tone, string> = {
  neutral: 'badge-neutral',
  success: 'badge-success',
  info: 'badge-info',
  warning: 'badge-warning',
  danger: 'badge-danger',
}

const JOB_TONE: Record<string, Tone> = {
  queued: 'neutral',
  running: 'info',
  completed: 'success',
  completed_with_errors: 'warning',
  failed: 'danger',
  cancelled: 'neutral',
}

const DATASET_TONE: Record<string, Tone> = {
  draft: 'neutral',
  validating: 'info',
  validated: 'info',
  ingesting: 'info',
  ready: 'success',
  failed: 'danger',
  archived: 'neutral',
}

const SEVERITY_TONE: Record<string, Tone> = {
  critical: 'danger',
  high: 'danger',
  medium: 'warning',
  low: 'info',
  info: 'neutral',
}

const GENERIC_TONE: Record<string, Tone> = {
  active: 'success',
  enabled: 'success',
  true: 'success',
  inactive: 'neutral',
  disabled: 'neutral',
  false: 'neutral',
  open: 'info',
  pending: 'warning',
  in_progress: 'info',
  resolved: 'success',
  closed: 'neutral',
  acknowledged: 'warning',
  new: 'info',
  triaged: 'info',
  delivered: 'success',
  suppressed: 'neutral',
  sent: 'success',
  healthy: 'success',
  degraded: 'warning',
  down: 'danger',
  unavailable: 'danger',
  configured: 'success',
  not_configured: 'neutral',
  local: 'info',
  verified: 'success',
}

export function statusTone(status?: string | null, kind: 'job' | 'dataset' | 'severity' | 'generic' = 'generic'): Tone {
  if (!status) return 'neutral'
  const key = status.toLowerCase()
  if (kind === 'job' && key in JOB_TONE) return JOB_TONE[key]
  if (kind === 'dataset' && key in DATASET_TONE) return DATASET_TONE[key]
  if (kind === 'severity' && key in SEVERITY_TONE) return SEVERITY_TONE[key]
  if (key in JOB_TONE) return JOB_TONE[key]
  if (key in DATASET_TONE) return DATASET_TONE[key]
  if (key in SEVERITY_TONE) return SEVERITY_TONE[key]
  return GENERIC_TONE[key] ?? 'neutral'
}

export function StatusBadge({
  value,
  kind = 'generic',
  label,
  icon,
}: {
  value?: string | null
  kind?: 'job' | 'dataset' | 'severity' | 'generic'
  label?: string
  icon?: ReactNode
}) {
  const tone = statusTone(value, kind)
  const busy = value === 'running' || value === 'queued'
  return (
    <span className={`badge ${TONE_CLASS[tone]}`}>
      {icon ?? (busy ? <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" /> : <CircleDot className="h-3 w-3" aria-hidden="true" />)}
      {label ?? humanise(value ?? 'unknown')}
    </span>
  )
}

export function Badge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${TONE_CLASS[tone]}`} title={title}>
      {children}
    </span>
  )
}

export function ModeBadge({ mode, detail }: { mode?: string | null; detail?: string }) {
  const atlas = mode === 'atlas'
  return (
    <span className={`badge ${atlas ? 'badge-success' : 'badge-info'}`} title={detail}>
      <Clock className="h-3 w-3" aria-hidden="true" />
      {atlas ? 'MongoDB Atlas' : humanise(mode ?? 'unknown')}
    </span>
  )
}
