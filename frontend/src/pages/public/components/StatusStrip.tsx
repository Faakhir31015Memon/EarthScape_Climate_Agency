import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Activity, Boxes, Database, FolderTree, MapPinned, Radio, RefreshCw } from 'lucide-react'
import { Badge, ModeBadge, StatusBadge } from '../../../components/ui'
import { RefreshButton } from '../../../components/ui'
import { useAsync } from '../../../hooks/useAsync'
import api from '../../../services/api'
import { formatCount, isAuthGated, isMissingEndpoint, locationCount, normaliseRegions, observationsFromRegions } from './publicData'
import { humanise } from '../../../lib/format'

/** Compact card body shared by the status slots: value plus one line of provenance. */
function Slot({
  label,
  icon,
  tone = 'default',
  loading,
  value,
  hint,
}: {
  label: string
  icon: ReactNode
  tone: 'default' | 'forest' | 'hydro' | 'solar' | 'alert'
  loading: boolean
  value: ReactNode
  hint?: ReactNode
}) {
  const accents: Record<string, string> = {
    default: 'text-[var(--brand)]',
    forest: 'text-forest-600 dark:text-forest-300',
    hydro: 'text-hydro-600 dark:text-hydro-300',
    solar: 'text-solar-600 dark:text-solar-300',
    alert: 'text-alert-critical',
  }
  return (
    <div className="card-tight min-w-0">
      <div className="flex items-start justify-between gap-2">
        <p className="panel-title">{label}</p>
        <span className={`shrink-0 ${accents[tone]}`} aria-hidden="true">
          {icon}
        </span>
      </div>
      <div className="mt-2 text-sm font-semibold leading-snug">
        {loading ? <span className="skeleton block h-6 w-28" aria-hidden="true" /> : value}
      </div>
      <div className="mt-1.5 text-xs leading-relaxed subtle">{loading ? <span className="skeleton block h-3 w-full" aria-hidden="true" /> : hint}</div>
    </div>
  )
}

/** Shown when an endpoint is not served or refuses anonymous callers. Honest, never a placeholder number. */
function Unavailable({ error, what }: { error: unknown; what: string }) {
  if (isAuthGated(error)) {
    return (
      <>
        <span className="muted">{what} needs a signed-in account</span>
        <Link to="/login" className="link ml-1">
          Sign in
        </Link>
      </>
    )
  }
  if (isMissingEndpoint(error)) {
    return <span className="muted">{what} is not served by this deployment yet</span>
  }
  return <span className="muted">{what} is not available right now</span>
}

export function StatusStrip() {
  const health = useAsync(() => api.system.health(), [])
  const summary = useAsync(() => api.dashboard.summary(), [])
  const regionsState = useAsync(() => api.climate.regions(), [])

  const regions = useMemo(() => normaliseRegions(regionsState.data), [regionsState.data])
  const totals = summary.data?.totals ?? null

  const observationTotal = totals ? totals.observations : observationsFromRegions(regions)
  const observationSource = totals ? 'dashboard summary' : 'public region aggregates'
  const regionTotal = regions.length || (totals ? totals.regions : null)
  const stationTotal = totals ? totals.locations : locationCount(regions)
  const datasetTotal = totals ? totals.datasets : null

  const refreshing = health.refreshing || summary.refreshing || regionsState.refreshing
  const refetchAll = () => {
    void health.refetch()
    void summary.refetch()
    void regionsState.refetch()
  }

  const database = health.data?.database

  return (
    <section aria-labelledby="platform-status-heading">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="panel-title mb-1.5">Live platform status</p>
          <h2 id="platform-status-heading" className="font-display text-xl font-semibold sm:text-2xl">
            Current state of this deployment
          </h2>
          <p className="mt-1.5 max-w-2xl text-sm leading-relaxed muted">
            Read directly from the running API on each visit. Nothing here is cached marketing copy.
          </p>
        </div>
        <RefreshButton onClick={refetchAll} busy={refreshing} label="Refresh status" />
      </div>

      <div className="grid-responsive">
        <Slot
          label="API"
          tone={health.data?.status === 'degraded' ? 'solar' : 'forest'}
          icon={<Radio className="h-4 w-4" />}
          loading={health.loading}
          value={
            health.error ? (
              <Unavailable error={health.error} what="Service health" />
            ) : health.data ? (
              <span className="flex flex-wrap items-center gap-2">
                <StatusBadge value={health.data.status} />
                <span className="mono text-xs subtle">v{health.data.version}</span>
              </span>
            ) : null
          }
          hint={
            health.error
              ? 'The public health endpoint did not answer.'
              : health.data
                ? `Schema v${health.data.schema_version} · ${formatCount(Math.round(health.data.uptime_seconds / 60))} min uptime · request id ${health.data.request_id ?? '—'}`
                : undefined
          }
        />

        <Slot
          label="Document store"
          tone="hydro"
          icon={<Database className="h-4 w-4" />}
          loading={health.loading}
          value={
            health.error || !database ? (
              <Unavailable error={health.error} what="Storage mode" />
            ) : (
              <span className="flex flex-wrap items-center gap-2">
                <ModeBadge mode={database.mode} detail={database.ok ? 'reachable' : 'unreachable'} />
                <span className="mono text-xs subtle">{database.database}</span>
              </span>
            )
          }
          hint={
            health.error
              ? 'Mode is reported by /api/v1/health.'
              : database?.mode === 'atlas'
                ? 'MongoDB Atlas is the application database; every read below is an Atlas aggregation.'
                : 'Atlas is not configured here, so the platform keeps application documents in its local store.'
          }
        />

        <Slot
          label="Processing engine"
          tone="solar"
          icon={<Boxes className="h-4 w-4" />}
          loading={health.loading}
          value={
            health.error || !health.data ? (
              <Unavailable error={health.error} what="Processing mode" />
            ) : (
              <Badge tone={health.data.processing_mode.includes('hadoop') || health.data.processing_mode.includes('spark') ? 'success' : 'info'}>
                {humanise(health.data.processing_mode)}
              </Badge>
            )
          }
          hint={
            health.data
              ? `Background executor: ${health.data.background.executor} · ${formatCount(health.data.background.active_jobs)} active, ${formatCount(health.data.background.queued_jobs)} queued`
              : 'Reported per request by the API.'
          }
        />

        <Slot
          label="Observations"
          tone="forest"
          icon={<Activity className="h-4 w-4" />}
          loading={summary.loading || (observationTotal === null && regionsState.loading)}
          value={
            observationTotal === null ? (
              <Unavailable error={totals ? undefined : summary.error ?? regionsState.error} what="Observation totals" />
            ) : (
              formatCount(observationTotal)
            )
          }
          hint={totals || observationTotal !== null ? `Station-day records computed from ${observationSource}.` : undefined}
        />

        <Slot
          label="Datasets"
          tone="hydro"
          icon={<FolderTree className="h-4 w-4" />}
          loading={summary.loading}
          value={
            datasetTotal === null ? <Unavailable error={summary.error} what="The dataset count" /> : formatCount(datasetTotal)
          }
          hint={datasetTotal !== null ? 'Registered ingestion and upload sources.' : undefined}
        />

        <Slot
          label="Regions & stations"
          tone="default"
          icon={<MapPinned className="h-4 w-4" />}
          loading={regionsState.loading && !regions.length}
          value={
            regionsState.error && !regions.length ? (
              <Unavailable error={regionsState.error} what="The region catalogue" />
            ) : (
              <span>
                {formatCount(regionTotal ?? 0)}
                <span className="ml-1 text-xs font-medium muted">regions</span>
                {stationTotal ? <span className="ml-2 text-xs font-medium muted">· {formatCount(stationTotal)} stations</span> : null}
              </span>
            )
          }
          hint={regions.length ? `${formatCount(regions.filter((region) => region.hasData).length)} regions hold observations.` : undefined}
        />
      </div>

      {summary.error && !totals ? (
        <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
          <RefreshCw className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          The dashboard summary endpoint is authenticated, so dataset totals are only shown once you sign in. Region and service
          figures above come from the public climate and health endpoints.
        </p>
      ) : null}
    </section>
  )
}

export default StatusStrip
