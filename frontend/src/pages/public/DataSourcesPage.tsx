import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, CloudSun, Radio, Satellite, UploadCloud } from 'lucide-react'
import { Badge, EmptyState, ErrorState, Panel, Skeleton, TableSkeleton } from '../../components/ui'
import { useAsync } from '../../hooks/useAsync'
import api from '../../services/api'
import { useAuth } from '../../features/auth/AuthContext'
import { formatCount, normaliseMetrics, normaliseRegions } from './components/publicData'
import { AnchorNav, DefinitionList, ProvenanceNote, PublicContainer, SectionHeading } from './components/PublicSections'

const SECTIONS = [
  { id: 'providers', label: 'Collection providers' },
  { id: 'contract', label: 'Measurement contract' },
  { id: 'locations', label: 'Monitored locations' },
  { id: 'quality', label: 'Quality & provenance' },
  { id: 'availability', label: 'Live availability' },
]

/**
 * Provider descriptions are read from the connector modules themselves
 * (``backend/apps/ingestion/connectors/*.py``): base URLs, parameter tables,
 * credential variables and documented rate limits. Nothing here is a statistic —
 * these are the source URLs and configuration keys the code actually uses.
 */
const PROVIDERS = [
  {
    id: 'nasa_power',
    name: 'NASA POWER',
    icon: Satellite,
    cadence: 'Automated daily pull',
    endpoint: 'https://power.larc.nasa.gov/api/temporal/daily/point',
    site: { label: 'power.larc.nasa.gov', href: 'https://power.larc.nasa.gov/' },
    credentials: 'None required',
    summary:
      'The primary automated source. The connector requests one daily point series per monitored latitude and longitude, for the AG (agriculture) community, in JSON, and maps ten published parameters onto the canonical fields used by the platform.',
    facts: [
      'Parameter mapping is declared in common/regions.py — T2M, T2M_MIN, T2M_MAX, PRECTOTCORR, RH2M, WS10M, WD10M, PS, ALLSKY_CLOUD_FRACTION, ALLSKY_SFC_SW_DWN.',
      'NASA sentinel values (-999 and friends) are recorded as missing, never as a measurement.',
      'Surface pressure arrives in kPa; the connector converts it to hectopascals before validation and keeps the source unit in provenance.',
    ],
  },
  {
    id: 'noaa',
    name: 'NOAA Climate Data Online',
    icon: CloudSun,
    cadence: 'Automated, paged search',
    endpoint: 'https://www.ncdc.noaa.gov/cdo-web/api/v2',
    site: { label: 'ncdc.noaa.gov/cdo-web', href: 'https://www.ncdc.noaa.gov/cdo-web/' },
    credentials: 'Token in the NOAA_API_KEY environment variable, sent as the token header',
    summary:
      'Dataset discovery, station lookup and observation search across CDO v2 collections, paged at one hundred results per request. Datatypes outside the connector’s documented mapping table are recorded rather than guessed.',
    facts: [
      'Without NOAA_API_KEY the connector reports needs_configuration; it never returns an empty success.',
      'NOAA documents five requests per thirty seconds; the connector self-limits with headroom and treats HTTP 429 as retryable rate limiting.',
      'Date ranges and location identifiers are validated locally before a request leaves the platform.',
    ],
  },
  {
    id: 'live_api',
    name: 'Live weather API (Open-Meteo by default)',
    icon: Radio,
    cadence: 'Scheduled poll of a bounded window',
    endpoint: 'https://api.open-meteo.com/v1/forecast',
    site: { label: 'open-meteo.com', href: 'https://open-meteo.com/' },
    credentials: 'None required',
    summary:
      'The configurable "live climate API" channel. EarthScape reads a bounded window of daily aggregates per request on the ingestion interval — short HTTP reads of the most recent observations, not a continuous sensor stream, and the platform describes it that way.',
    facts: [
      'The concrete provider is chosen by the LIVE_CLIMATE_PROVIDER environment variable and resolved through a provider registry, so another key-free daily API can be registered without touching application logic.',
      'Daily variables are mapped field by field with their documented units, then normalised by the same validator as every other source.',
    ],
  },
  {
    id: 'university_upload',
    name: 'University and institutional uploads',
    icon: UploadCloud,
    cadence: 'Analyst-initiated, on delivery',
    endpoint: 'Multipart upload — CSV, XLSX, XLS, Parquet',
    site: null,
    credentials: 'Signed-in climate analyst or administrator',
    summary:
      'Instruments operated by the partner institution export their own records. Files are uploaded from the platform, profiled, mapped to the canonical schema, validated, and only then ingested — with the file name, uploader and column mapping retained as provenance.',
    facts: [
      'Uploads are capped at 50 MiB by MAX_UPLOAD_BYTES, with the original file kept beside the ingested rows for reproducibility.',
      'This channel carries no external dependency: if every external provider were unreachable, the institutional record still ingests.',
    ],
  },
]

function MetricContract() {
  const metricsState = useAsync(() => api.climate.metrics(), [])
  const metrics = useMemo(() => normaliseMetrics(metricsState.data), [metricsState.data])

  if (metricsState.loading && metrics.length === 0) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-56" />
        <TableSkeleton rows={5} columns={6} />
      </div>
    )
  }
  if (metricsState.error) {
    return <ErrorState error={metricsState.error} onRetry={() => void metricsState.refetch()} />
  }
  if (metrics.length === 0) {
    return (
      <EmptyState
        title="The metric catalogue is not answering"
        description="GET /api/v1/climate/metrics returned no definitions, so this page cannot describe the measurement contract accurately."
        action={
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => void metricsState.refetch()}>
            Retry
          </button>
        }
      />
    )
  }

  return (
    <>
      <div className="table-wrap">
        <table className="data">
          <caption className="sr-only">Canonical fields, units, validation ranges and populated record counts</caption>
          <thead>
            <tr>
              <th scope="col">Field</th>
              <th scope="col">Reported as</th>
              <th scope="col">Unit</th>
              <th scope="col">Accepted range</th>
              <th scope="col">Aggregation</th>
              <th scope="col">Populated records</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((metric) => (
              <tr key={metric.field}>
                <td>
                  <span className="mono text-xs">{metric.field}</span>
                </td>
                <td className="font-medium">{metric.label}</td>
                <td className="muted">{metric.unit}</td>
                <td className="mono text-xs">
                  {metric.range_min === null && metric.range_max === null ? (
                    <span className="muted">not published</span>
                  ) : (
                    <>{formatCount(metric.range_min)} – {formatCount(metric.range_max)}</>
                  )}
                </td>
                <td className="muted">{metric.aggregation === 'sum' ? 'Summed' : 'Averaged'}</td>
                <td>
                  {metric.totalCount === null ? (
                    <span className="muted">not reported</span>
                  ) : (
                    <span className="mono text-xs">
                      {formatCount(metric.populatedCount)} / {formatCount(metric.totalCount)}
                      {metric.completenessPct !== null ? ` · ${metric.completenessPct.toFixed(1)}%` : ''}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs leading-relaxed subtle">
        Field names, units and ranges are read from the live catalogue served by <span className="mono">/api/v1/climate/metrics</span>, which is
        generated from <span className="mono">backend/common/validators.py</span>. Populated-record counts are computed against the stored
        observations for this deployment: a value outside the accepted range is stored and flagged for review, never silently discarded.
      </p>
    </>
  )
}

function LocationCatalogue() {
  const regionsState = useAsync(() => api.climate.regions(), [])
  const regions = useMemo(() => normaliseRegions(regionsState.data), [regionsState.data])
  const stations = regions.reduce((total, region) => total + region.locations.length, 0)

  if (regionsState.loading && regions.length === 0) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-4 w-48" />
        <TableSkeleton rows={6} columns={6} />
      </div>
    )
  }
  if (regionsState.error) {
    return <ErrorState error={regionsState.error} onRetry={() => void regionsState.refetch()} />
  }
  if (regions.length === 0) {
    return (
      <EmptyState
        title="No monitored locations are registered"
        description="GET /api/v1/climate/regions answered with an empty list, so there is no station catalogue to show."
        action={
          <Link to="/login" className="btn btn-secondary btn-sm">
            Sign in to provision locations
          </Link>
        }
      />
    )
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Badge tone="info">{formatCount(regions.length)} regions</Badge>
        <Badge tone="neutral">{formatCount(stations)} observation points</Badge>
        <Badge tone="success">{formatCount(regions.filter((region) => region.hasData).length)} with stored data</Badge>
      </div>
      <div className="table-wrap">
        <table className="data">
          <caption className="sr-only">Regions, their stations and how much verified data each holds</caption>
          <thead>
            <tr>
              <th scope="col">Region</th>
              <th scope="col">Country</th>
              <th scope="col">Climate zone</th>
              <th scope="col">Stations</th>
              <th scope="col">Stored observations</th>
              <th scope="col">Coverage window</th>
            </tr>
          </thead>
          <tbody>
            {regions.map((region) => (
              <tr key={region.code}>
                <td>
                  <span className="font-medium">{region.name}</span>
                  <span className="mono ml-2 text-xs subtle">{region.code}</span>
                </td>
                <td className="muted">{region.country || '—'}</td>
                <td className="muted">{region.climate_zone || '—'}</td>
                <td>
                  <span className="mono text-xs" title={region.locations.map((location) => `${location.code} ${location.name}`).join('\n')}>
                    {formatCount(region.location_count)}
                  </span>
                </td>
                <td>
                  {region.hasData ? (
                    <span className="mono text-xs">{formatCount(region.observationCount)}</span>
                  ) : (
                    <span className="muted text-xs">{region.reason ?? 'catalogued, no observations ingested yet'}</span>
                  )}
                </td>
                <td className="mono text-xs">
                  {region.firstDate && region.lastDate ? `${region.firstDate.slice(0, 10)} → ${region.lastDate.slice(0, 10)}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs leading-relaxed subtle">
        Station codes and coordinates come from <span className="mono">backend/common/regions.py</span>, served through{' '}
        <span className="mono">/api/v1/climate/regions</span>, and every value above is whatever that call returns right now. Hover the station
        count for the individual observation points and their WMO identifiers. Each row is a station measurement: the platform never republishes a
        single station reading as a regional average without labelling it as such.
      </p>
    </>
  )
}

export function DataSourcesPage() {
  const { status } = useAuth()

  return (
    <PublicContainer>
      <header className="mb-6 max-w-3xl">
        <p className="panel-title mb-2">Data sources</p>
        <h1 className="font-display text-[28px] font-semibold leading-tight sm:text-[34px]">Where every record in the lake came from</h1>
        <p className="mt-3 text-sm leading-relaxed muted">
          Four collection channels feed the platform. Two are automated external APIs, one is a scheduled key-free weather API, and one is
          institutional files uploaded by researchers. All of them converge on a single canonical schema, one validator, and one provenance model —
          so a value on any chart can be traced back to the file or response that produced it.
        </p>
      </header>

      <AnchorNav items={SECTIONS} />

      <section id="providers" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Automated collection and institutional delivery"
          title="The four providers"
          description="Base URLs, credential variables and limits below are the values the connector code actually uses."
        />
        <div className="grid-responsive">
          {PROVIDERS.map((provider) => {
            const Icon = provider.icon
            return (
              <article key={provider.id} className="card flex flex-col gap-3 p-5">
                <header className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2.5">
                    <Icon className="mt-0.5 h-5 w-5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                    <div>
                      <h3 className="text-sm font-semibold">{provider.name}</h3>
                      <p className="mt-0.5 text-xs subtle">{provider.cadence}</p>
                    </div>
                  </div>
                  <Badge tone={provider.id === 'university_upload' ? 'info' : 'neutral'} title="Credential requirement">
                    {provider.credentials === 'None required' ? 'key free' : provider.id === 'university_upload' ? 'signed in' : 'token'}
                  </Badge>
                </header>
                <p className="text-sm leading-relaxed muted">{provider.summary}</p>
                <p className="mono break-words text-[11px] subtle" title={provider.endpoint}>
                  {provider.endpoint}
                </p>
                <ul className="space-y-1.5">
                  {provider.facts.map((fact) => (
                    <li key={fact} className="flex gap-2 text-xs leading-relaxed muted">
                      <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-forest-500" />
                      <span>{fact}</span>
                    </li>
                  ))}
                </ul>
                {provider.site ? (
                  <a
                    href={provider.site.href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="link mt-auto inline-flex items-center gap-1 text-xs"
                  >
                    {provider.site.label} <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </a>
                ) : (
                  <Link to="/app/uploads" className="link mt-auto inline-flex items-center gap-1 text-xs">
                    Upload workspace (signed in) <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                  </Link>
                )}
              </article>
            )
          })}
        </div>
      </section>

      <section id="contract" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="One schema, three ingestion paths"
          title="The canonical measurement contract"
          description="Every channel is normalised into these fields before validation, so unit drift in a source file cannot reach a chart."
          actions={
            <Link to="/capabilities" className="btn btn-secondary btn-sm">
              Validation pipeline
            </Link>
          }
        />
        <Panel className="overflow-hidden">
          <MetricContract />
        </Panel>
      </section>

      <section id="locations" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Reference geography"
          title="Monitored locations"
          description="The station catalogue this deployment registers — read live, not transcribed from a specification document."
        />
        <Panel className="overflow-hidden">
          <LocationCatalogue />
        </Panel>
      </section>

      <section id="quality" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="What makes a record trustworthy"
          title="Quality flags and provenance"
          description="Rules enforced by backend/common/validators.py on every row, whatever the channel."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Normalisation and validation">
            <DefinitionList
              items={[
                {
                  term: 'Unit conversion',
                  detail: 'Declared source units convert to the canonical unit — Fahrenheit, Kelvin, centimetres, inches, km/h, mph, knots, millibars, MJ/m² and more. The original unit stays in provenance so the conversion is reproducible.',
                },
                {
                  term: 'Missing never becomes zero',
                  detail: 'Empty cells, "n/a", "nan", "null" and the -999 family are stored as null. A gap in a chart is a gap, not a dry day or a freezing night.',
                },
                {
                  term: 'Range checking, not silent dropping',
                  detail: 'Values outside the accepted range are kept and flagged so a human reviews them; the issue is recorded against the row rather than discarded.',
                },
                {
                  term: 'Schema version',
                  detail: 'Rows carry the canonical schema version (3) alongside the field set, so a later reader knows which rules produced them.',
                },
              ]}
            />
          </Panel>
          <Panel title="Provenance attached to every row">
            <DefinitionList
              items={[
                {
                  term: 'Source identity',
                  detail: 'Dataset, connector name, request window or uploaded file name, and the ingestion job that created the record.',
                },
                {
                  term: 'Location identity',
                  detail: 'Station code, latitude, longitude and elevation, plus the reported source value before conversion.',
                },
                {
                  term: 'Quality state',
                  detail: 'Flagged or rejected rows keep their issue codes and severities, which is what the signed-in quality dashboard aggregates.',
                },
                {
                  term: 'Auditability',
                  detail: 'Raw payloads land in the lake’s raw layer first, so a validated record can always be re-derived from what the provider actually sent.',
                },
              ]}
            />
          </Panel>
        </div>
      </section>

      <section id="availability" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Honest availability"
          title="Live connector status"
          description="Whether a provider is configured and reachable is a runtime property of this deployment, so it is never printed here as a static claim."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel
            title="Where to read it"
            footer={
              <span>
                Reachability checks issue real HTTP requests, which is why the endpoint sits behind authentication and is not fetched for anonymous visitors.
              </span>
            }
          >
            <p className="text-sm leading-relaxed muted">
              Connector configuration — whether <span className="mono">NOAA_API_KEY</span> is present, whether a provider answered its last health
              probe, which parameters each supports and when a sync last completed — is served by{' '}
              <span className="mono">GET /api/v1/ingestion/connectors</span> to signed-in accounts only. The platform surfaces it in the ingestion
              workspace.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {status === 'authenticated' ? (
                <Link to="/app/ingestion" className="btn btn-primary btn-sm">
                  Open ingestion jobs
                </Link>
              ) : (
                <Link to="/login" className="btn btn-primary btn-sm">
                  Sign in to check providers
                </Link>
              )}
              <Link to="/data-sources" className="btn btn-ghost btn-sm" aria-current="page">
                This page
              </Link>
            </div>
          </Panel>
          <Panel title="What is never shown as data">
            <ul className="space-y-2 text-sm leading-relaxed muted">
              <li className="flex gap-2">
                <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-solar-500" />
                <span>
                  A provider that is not configured contributes nothing. Its absence is reported as a configuration gap; no substitute values, no
                  interpolation, no backfill from a neighbouring station.
                </span>
              </li>
              <li className="flex gap-2">
                <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-solar-500" />
                <span>
                  A failed sync leaves its job record with the error it returned. Partial windows show as partial coverage on the charts rather than
                  as a complete series.
                </span>
              </li>
              <li className="flex gap-2">
                <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-solar-500" />
                <span>
                  Public pages read only stored observations through the public analytics endpoints, so a visitor and an analyst see the same record —
                  an analyst simply sees more of it.
                </span>
              </li>
            </ul>
          </Panel>
        </div>
      </section>

      <ProvenanceNote>
        Counts, ranges, station lists and coverage windows on this page are read from <span className="mono">/api/v1/climate/metrics</span> and{' '}
        <span className="mono">/api/v1/climate/regions</span> at the moment you loaded it. If a call failed, its panel shows the failure instead of a
        number.
      </ProvenanceNote>
    </PublicContainer>
  )
}

export default DataSourcesPage
