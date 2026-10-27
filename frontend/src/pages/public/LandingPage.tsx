import { Suspense, lazy, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  ArrowRight,
  BellRing,
  BrainCircuit,
  ExternalLink,
  FileBarChart,
  LineChart,
  Satellite,
  ShieldCheck,
  UploadCloud,
} from 'lucide-react'
import { ChartSkeleton, EmptyState, ErrorState, Panel, RefreshButton, Badge, Reveal, Skeleton } from '../../components/ui'
import { TrendChart } from '../../components/charts/TrendChart'
import { useAsync } from '../../hooks/useAsync'
import { formatDate } from '../../lib/format'
import { meanCentre } from '../../lib/geo'
import api from '../../services/api'
import { formatCount, locationCount, normaliseRegions, observationsFromRegions } from './components/publicData'
import { ProvenanceNote, PublicContainer, SectionHeading } from './components/PublicSections'

/** WebGL is a heavy dependency and only the hero needs it, so it arrives after first paint. */
const GlobeHero = lazy(() => import('../../components/three/GlobeHero'))

const FEATURES = [
  {
    icon: <LineChart className="h-5 w-5" aria-hidden="true" />,
    title: 'Follow what changed',
    body: 'Trends, comparisons and yearly history for temperature, rainfall, humidity and wind, filtered by region, station and period. The sample size sits next to every curve.',
  },
  {
    icon: <Activity className="h-5 w-5" aria-hidden="true" />,
    title: 'Spot what is wrong',
    body: 'Anomaly scans flag heat spells, cold snaps and heavy-rain days against the local record, each with the threshold that fired and how much evidence sat behind it.',
  },
  {
    icon: <BrainCircuit className="h-5 w-5" aria-hidden="true" />,
    title: 'Look ahead',
    body: 'Forecasts for the next one to three years, published with the accuracy measured on records the model had not seen. Where the outlook is weak, it says so.',
  },
  {
    icon: <BellRing className="h-5 w-5" aria-hidden="true" />,
    title: 'Get told when a threshold breaks',
    body: 'Alert rules carry a severity, and a breach goes to the people who need it. A threshold crossing does not wait for someone to open a dashboard.',
  },
  {
    icon: <UploadCloud className="h-5 w-5" aria-hidden="true" />,
    title: 'Bring your own record',
    body: 'Upload CSV, Excel or JSON from a field campaign or a partner institution. Columns get matched, units converted, and rows checked against physical ranges before anything is stored.',
  },
  {
    icon: <FileBarChart className="h-5 w-5" aria-hidden="true" />,
    title: 'Take the answer with you',
    body: 'Any view exports to CSV, JSON or PDF with its source, period and quality flags attached, so a figure stays traceable long after it leaves the site.',
  },
]

const SOURCES = [
  {
    icon: <Satellite className="h-4 w-4" aria-hidden="true" />,
    title: 'NASA POWER',
    href: 'https://power.larc.nasa.gov/',
    body: 'Daily satellite- and model-derived surface weather, fetched per station. The primary source of the historical record.',
  },
  {
    icon: <ShieldCheck className="h-4 w-4" aria-hidden="true" />,
    title: 'NOAA Climate Data Online',
    href: 'https://www.ncdc.noaa.gov/cdo-web/',
    body: 'Station-based daily records through the same checks, used where this deployment has the provider turned on.',
  },
  {
    icon: <Activity className="h-4 w-4" aria-hidden="true" />,
    title: 'Live conditions',
    href: 'https://open-meteo.com/',
    body: 'Current readings for the live layer, from a provider that needs no key. Each one carries the time it was observed.',
  },
  {
    icon: <UploadCloud className="h-4 w-4" aria-hidden="true" />,
    title: 'Partner uploads',
    body: 'Research groups and partner departments deliver their own extracts. Each one keeps the name of the institution that supplied it.',
  },
]

function HeroTrend() {
  const trend = useAsync(() => api.climate.trend({ metric: 'temperature_c', frequency: 'monthly' }), [])

  return (
    <Panel
      title="Mean temperature, monthly"
      description="A live query against the record we hold for every region."
      actions={<RefreshButton onClick={() => void trend.refetch()} busy={trend.refreshing} />}
      footer={
        trend.data?.available && trend.data.point_count > 0 ? (
          <span>
            {formatCount(trend.data.point_count)} monthly periods, most recent {trend.data.last_period ?? '—'}. Computed from the stored station-day
            observations.
          </span>
        ) : trend.error ? (
          <span>This chart is a live query result. When the platform cannot answer, you get the error instead of a chart.</span>
        ) : (
          <span>This chart appears as soon as the record holds enough observations.</span>
        )
      }
    >
      {trend.loading ? (
        <ChartSkeleton height={300} />
      ) : trend.error ? (
        <ErrorState error={trend.error} onRetry={() => void trend.refetch()} />
      ) : !trend.data || trend.data.available === false || trend.data.series.length === 0 ? (
        <EmptyState
          icon={<Activity className="h-5 w-5" />}
          title="No trend series yet"
          description={
            <>
              {trend.data?.reason ?? 'The record has not been read successfully, so no series can be shown.'}
              {trend.data && trend.data.available === false ? (
                <>
                  {' '}
                  This view needs {formatCount(trend.data.required_points ?? 0)} monthly periods to draw a trend, and the record currently holds {formatCount(trend.data.available_points ?? 0)}.
                </>
              ) : null}
            </>
          }
          action={
            <Link to="/login" className="btn btn-secondary btn-sm">
              Sign in to run ingestion
            </Link>
          }
        />
      ) : (
        <TrendChart
          series={trend.data.series}
          metric={trend.data.metric}
          unit={trend.data.unit}
          label={trend.data.label}
          variant="area"
          height={300}
          showCounts
        />
      )}
    </Panel>
  )
}

export function LandingPage() {
  const regionsState = useAsync(() => api.climate.regions(), [])
  const regions = useMemo(() => normaliseRegions(regionsState.data), [regionsState.data])
  // Keyed on the payload, not on a per-render array, so the WebGL scene builds once.
  const stations = useMemo(
    () =>
      regions.flatMap((region) =>
        region.locations
          .filter((location) => location.latitude !== null && location.longitude !== null)
          .map((location) => ({
            name: location.name,
            latitude: location.latitude as number,
            longitude: location.longitude as number,
            region: region.name,
          })),
      ),
    [regions],
  )
  const focus = useMemo(() => meanCentre(stations), [stations])
  const observations = observationsFromRegions(regions)
  const observedRegions = regions.filter((region) => region.hasData).length
  const latestRecord = regions.reduce<string | null>(
    (latest, region) => (region.lastDate && (!latest || region.lastDate > latest) ? region.lastDate : latest),
    null,
  )

  return (
    <>
      <PublicContainer className="pb-6">
        <section aria-labelledby="hero-heading" className="grid items-start gap-8 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="success">
                <Activity className="h-3 w-3" aria-hidden="true" /> Climate data platform
              </Badge>
              <Badge tone="info">Analytics &amp; forecasting</Badge>
            </div>
            <h1 id="hero-heading" className="mt-4 font-display text-[30px] font-semibold leading-[1.15] sm:text-[38px]">
              Trends, anomalies and forecasts built on daily station records
            </h1>
            <p className="mt-4 max-w-2xl text-[15px] leading-relaxed muted">
              EarthScape collects station and satellite observations, runs the same checks over them whoever supplied the file, and turns what is left
              into trends, anomaly lists, forecasts and alerts. The figures on this page are read from the record while the page loads.
            </p>
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <Link to="/register" className="btn btn-primary">
                Create an account <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <Link to="/login" className="btn btn-secondary">
                Sign in
              </Link>
            </div>
            <dl className="reveal mt-6 grid gap-3 sm:grid-cols-3">
              <div className="card-tight">
                <dt className="panel-title">Observations</dt>
                <dd className="mt-1.5 text-xl font-semibold">
                  {regionsState.loading ? <Skeleton className="h-6 w-16" /> : regionsState.error ? '—' : formatCount(observations)}
                </dd>
                <dd className="mt-1 text-xs subtle">
                  {regionsState.error ? 'The record could not be read' : 'Daily station observations the platform holds'}
                </dd>
              </div>
              <div className="card-tight">
                <dt className="panel-title">Coverage</dt>
                <dd className="mt-1.5 text-xl font-semibold">
                  {regionsState.loading ? (
                    <Skeleton className="h-6 w-16" />
                  ) : regionsState.error ? (
                    '—'
                  ) : (
                    <>
                      {formatCount(regions.length)} <span className="text-sm font-medium muted">regions</span>
                    </>
                  )}
                </dd>
                <dd className="mt-1 text-xs subtle">
                  {regionsState.error
                    ? 'The region catalogue could not be read'
                    : `${formatCount(observedRegions)} with observations · ${formatCount(locationCount(regions))} locations`}
                </dd>
              </div>
              <div className="card-tight">
                <dt className="panel-title">Record through</dt>
                <dd className="mt-1.5 text-xl font-semibold">
                  {regionsState.loading ? <Skeleton className="h-6 w-16" /> : regionsState.error ? '—' : latestRecord ? formatDate(latestRecord) : '—'}
                </dd>
                <dd className="mt-1 text-xs subtle">The most recent day any station in the catalogue reports.</dd>
              </div>
            </dl>
          </div>

          <div className="min-w-0">
            <Suspense
              fallback={
                <div className="card flex h-full min-h-[420px] items-center justify-center !p-0">
                  <Skeleton className="h-52 w-52 rounded-full" />
                </div>
              }
            >
              <GlobeHero
                stations={stations}
                focus={focus}
                regionsLabel={formatCount(regions.length)}
                stationsLabel={formatCount(stations.length)}
              />
            </Suspense>
          </div>
        </section>
      </PublicContainer>

      <PublicContainer className="py-6">
        <ProvenanceNote />
      </PublicContainer>

      <PublicContainer className="py-8">
        <Reveal as="section">
          <HeroTrend />
        </Reveal>
      </PublicContainer>

      <PublicContainer className="py-10">
        <SectionHeading
          eyebrow="What you can do"
          title="One record, from a provider file to a published alert"
          description="Every screen reads the same validated observations, so a figure on a chart, in a forecast and in an export is the same figure."
        />
        <div className="grid-responsive">
          {FEATURES.map((feature, index) => (
            <Reveal as="article" key={feature.title} delay={index * 70} className="card min-w-0">
              <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-clay-50 text-clay-700 dark:bg-clay-900/40 dark:text-clay-200">
                {feature.icon}
              </span>
              <h3 className="mt-3 font-display text-base font-semibold">{feature.title}</h3>
              <p className="mt-2 text-sm leading-relaxed muted">{feature.body}</p>
            </Reveal>
          ))}
        </div>
      </PublicContainer>

      <PublicContainer className="py-10">
        <SectionHeading
          eyebrow="Where the data comes from"
          title="Named providers, attributed uploads"
          description="Every observation keeps the name of the source it came from. A missing day stays missing."
        />
        <div className="grid-responsive">
          {SOURCES.map((source, index) => (
            <Reveal as="article" key={source.title} delay={index * 70} className="card min-w-0">
              <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-2.5">
                  <span className="mt-0.5 text-hydro-600 dark:text-hydro-300" aria-hidden="true">
                    {source.icon}
                  </span>
                  <h3 className="font-display text-base font-semibold">{source.title}</h3>
                </div>
                {source.href ? (
                  <a href={source.href} target="_blank" rel="noreferrer" className="link inline-flex shrink-0 items-center gap-1 text-xs">
                    Provider <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                  </a>
                ) : null}
              </div>
              <p className="mt-3 text-sm leading-relaxed muted">{source.body}</p>
            </Reveal>
          ))}
        </div>
      </PublicContainer>

      <PublicContainer className="py-10">
        <div className="card flex flex-col gap-5 p-6 sm:p-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <p className="panel-title mb-2">Start with your own record</p>
            <h2 className="font-display text-2xl font-semibold">Register, upload a dataset, and see what the record says</h2>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed muted">
              A new account can read every analytical view straight away. Uploads, forecast runs and administration are opened up as your team needs them.
            </p>
            <ul className="mt-4 flex flex-wrap gap-x-5 gap-y-2 text-sm muted">
              <li className="inline-flex items-center gap-2">
                <FileBarChart className="h-4 w-4 text-forest-600 dark:text-forest-300" aria-hidden="true" /> Reports with sources named
              </li>
              <li className="inline-flex items-center gap-2">
                <Activity className="h-4 w-4 text-hydro-600 dark:text-hydro-300" aria-hidden="true" /> Anomaly and trend analytics
              </li>
              <li className="inline-flex items-center gap-2">
                <BrainCircuit className="h-4 w-4 text-solar-600 dark:text-solar-300" aria-hidden="true" /> Multi-year forecasts
              </li>
            </ul>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <Link to="/register" className="btn btn-primary">
              Create account
            </Link>
            <Link to="/login" className="btn btn-secondary">
              Sign in
            </Link>
          </div>
        </div>
      </PublicContainer>
    </>
  )
}

export default LandingPage
