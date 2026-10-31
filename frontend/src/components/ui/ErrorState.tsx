import { AlertOctagon, RefreshCw, Wrench } from 'lucide-react'
import { ApiRequestError } from '../../services/http'

export function ErrorState({ error, onRetry, showDetails = false }: { error: unknown; onRetry?: () => void; showDetails?: boolean }) {
  const apiError = error instanceof ApiRequestError ? error : null
  const message = apiError?.message ?? (error instanceof Error ? error.message : 'Something went wrong.')
  const isSetup = apiError?.code === 'service_unavailable' || apiError?.code === 'configuration_required'
  return (
    <div className="card border-alert-critical/40 bg-alert-critical/5" role="alert">
      <div className="flex items-start gap-3">
        {isSetup ? <Wrench className="mt-0.5 h-5 w-5 text-solar-600" /> : <AlertOctagon className="mt-0.5 h-5 w-5 text-alert-critical" />}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{isSetup ? 'This service is not configured yet' : 'Unable to complete request'}</h3>
          <p className="mt-1 text-sm leading-relaxed muted">{message}</p>
          {showDetails ? (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] subtle">
              {apiError?.code ? <span className="badge badge-neutral">{apiError.code}</span> : null}
              {apiError?.status ? <span className="badge badge-neutral">HTTP {apiError.status}</span> : null}
              {apiError?.requestId ? (
                <span className="mono" title="Reference for support requests">
                  ref {apiError.requestId}
                </span>
              ) : null}
            </div>
          ) : null}
          {onRetry ? (
            <button type="button" className="btn btn-secondary btn-sm mt-4" onClick={onRetry}>
              <RefreshCw className="h-3.5 w-3.5" /> Try again
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}
