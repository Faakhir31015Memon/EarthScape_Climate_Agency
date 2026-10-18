import { useState } from 'react'
import { CloudUpload, Database, ServerCog, X } from 'lucide-react'
import api from '../services/api'
import { useAsync } from '../hooks/useAsync'
import { humanise } from '../lib/format'

/**
 * Honest infrastructure status: when Atlas or a cluster is not configured the platform
 * says so instead of pretending production services are live.
 */
export function EnvironmentBanner({ variant = 'app' }: { variant?: 'app' | 'public' }) {
  const [dismissed, setDismissed] = useState(false)
  const { data } = useAsync(() => api.system.health(), [], { interval: 180_000 })

  if (dismissed || !data) return null
  const databaseMode = data.database?.mode ?? 'unknown'
  const processingMode = data.processing_mode ?? 'unknown'
  const atlas = databaseMode === 'atlas'
  const hdfs = processingMode.includes('hdfs') || processingMode === 'cluster'
  if (atlas && hdfs) return null

  return (
    <div
      className="border-b px-4 py-2 text-xs"
      style={{ borderColor: 'var(--border)', backgroundColor: variant === 'public' ? 'transparent' : 'var(--surface-muted)' }}
      role="note"
    >
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-5 gap-y-1">
        <span className="inline-flex items-center gap-1.5 font-semibold">
          <ServerCog className="h-3.5 w-3.5" aria-hidden="true" /> Development environment
        </span>
        {!atlas ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <Database className="h-3.5 w-3.5" aria-hidden="true" />
            Document store: <strong>{humanise(databaseMode)}</strong> — set <span className="kbd">MONGODB_URI</span> and{' '}
            <span className="kbd">FORCE_ATLAS_CONNECTION=true</span> for Atlas.
          </span>
        ) : null}
        {!hdfs ? (
          <span className="inline-flex flex-wrap items-center gap-1.5">
            <CloudUpload className="h-3.5 w-3.5" aria-hidden="true" />
            Analytical storage: <strong>{humanise(processingMode)}</strong> — Hadoop/Spark cluster not detected.
          </span>
        ) : null}
        <button type="button" className="ml-auto subtle hover:text-[var(--text)]" onClick={() => setDismissed(true)} aria-label="Hide environment notice">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
