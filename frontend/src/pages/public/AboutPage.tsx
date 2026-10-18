import { Link } from 'react-router-dom'
import { AlertOctagon, CheckCircle2, EyeOff, GitBranch, Layers, Scale } from 'lucide-react'
import { Badge, Panel } from '../../components/ui'
import { useAsync } from '../../hooks/useAsync'
import api from '../../services/api'
import { ProvenanceNote, PublicContainer, SectionHeading } from './components/PublicSections'
import { formatCount, normaliseRegions } from './components/publicData'

const PROBLEM = [
  {
    title: 'Volume',
    body: 'A daily record for a couple of dozen stations is trivial. The same record across a national network, several decades and ten measured variables is millions of station-day rows — large enough that a single-process request/response loop stops being a reasonable way to aggregate it.',
  },
  {
    title: 'Fragmentation',
    body: 'Each provider publishes a different schema, unit vocabulary, missing-value sentinel and update cadence. University extracts arrive as CSV, Excel or JSON with locally meaningful column names. Nothing is comparable until it is normalised.',
  },
  {
    title: 'Trust',
    body: 'A climate figure without provenance is not usable for a decision. Every record needs its source, ingestion job, unit conversion history and quality flags attached, and every analytical result needs to say which records it read.',
  },
  {
    title: 'Latency between question and answer',
    body: 'Analysts ask questions that span the whole record — seasonal baselines, regional spreads, anomaly scans, forecast skill. Those answers must be precomputed by durable jobs rather than improvised in the browser.',
  },
]

export function AboutPage() {
  const regionsState = useAsync(() => api.climate.regions(), [])
  const regions = normaliseRegions(regionsState.data)
  const observed = regions.filter((region) => region.hasData).length

  return (
    <PublicContainer>
      <header className="mb-10 max-w-3xl">
        <p className="panel-title mb-2">About the agency and the platform</p>
        <h1 className="font-display text-[30px] font-semibold leading-tight sm:text-[36px]">
          EarthScape Climate Agency builds the data plumbing that climate decisions are made on
        </h1>
        <p className="mt-4 text-[15px] leading-relaxed muted">
          EarthScape is a climate intelligence practice: it collects surface meteorological records from public providers and partner
          institutions, puts them through a repeatable big-data pipeline, and publishes analytics, forecasts and alerts on top of the same
          verified store. This platform is the agency&rsquo;s working instrument, not a demonstration with staged screenshots: the pages you
          are reading call the same API the analysts use.
        </p>
        <div className="mt-5 flex flex-wrap gap-2">
          <Badge tone="info">Django REST + React/TypeScript</Badge>
          <Badge tone="success">Hadoop Streaming &amp; PySpark</Badge>
          <Badge tone="neutral">MongoDB document store</Badge>
        </div>
      </header>

      <SectionHeading
        eyebrow="Mission"
        title="Make the climate record comparable, queryable and accountable"
        description="Three commitments define the work: preserve the provenance of every measurement, compute aggregates with engines that scale past a single node, and publish results that state their own uncertainty and coverage."
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="Collect without losing context">
          <p className="text-sm leading-relaxed muted">
            Ingestion keeps the source name, the original column names, the original unit and the conversion applied. A value can always be
            traced back to the file or API response it came from.
          </p>
        </Panel>
        <Panel title="Compute where the data lives">
          <p className="text-sm leading-relaxed muted">
            Bulk records go to a layered data lake addressed through HDFS when a NameNode is reachable. Aggregation runs as MapReduce or Spark
            jobs whose inputs, outputs, durations and failure reasons are recorded as jobs, not as log trivia.
          </p>
        </Panel>
        <Panel title="Publish with caveats attached">
          <p className="text-sm leading-relaxed muted">
            Trend, anomaly, correlation and forecast responses carry their own coverage report and reason text. When the record is too sparse
            for a baseline, the API says so and the UI refuses to draw a line.
          </p>
        </Panel>
      </div>

      <div className="mt-12">
        <SectionHeading
          eyebrow="The problem"
          title="Why climate data needs distributed processing"
          description="Climate records fail ordinary spreadsheet tooling in four predictable ways."
        />
        <div className="grid-responsive">
          {PROBLEM.map((item) => (
            <article key={item.title} className="card min-w-0">
              <h3 className="flex items-start gap-2 font-display text-base font-semibold">
                <AlertOctagon className="mt-0.5 h-4 w-4 shrink-0 text-solar-600 dark:text-solar-300" aria-hidden="true" />
                {item.title}
              </h3>
              <p className="mt-2.5 text-sm leading-relaxed muted">{item.body}</p>
            </article>
          ))}
        </div>
      </div>

      <div className="mt-12">
        <SectionHeading
          eyebrow="What the platform delivers"
          title="Six working capabilities"
          description="Each of these is an operational module with its own API surface, not a roadmap item."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="A governed data pipeline">
            <ul className="space-y-2 text-sm leading-relaxed muted">
              {[
                'Provider connectors plus CSV/Excel/JSON upload with column mapping and row-level validation',
                'One canonical measurement contract with units in the field name and physical range checks',
                'Layered lake: raw, validated, processed, analytics, ML features, exports',
                'Hadoop Streaming MapReduce and PySpark jobs, with optional Structured Streaming/Kafka ingestion',
              ].map((line) => (
                <li key={line} className="flex items-start gap-2">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel title="Analysis and forecasting">
            <ul className="space-y-2 text-sm leading-relaxed muted">
              {[
                'Trends at daily to annual frequency, seasonal baselines and regional comparisons',
                'Anomaly detection by statistical baseline, fixed threshold or isolation forest',
                'Regression models with MAE, RMSE, MAPE, R² and skill against a naive baseline',
                'Alert rules, severity-graded alerts, notifications, generated reports and exports',
              ].map((line) => (
                <li key={line} className="flex items-start gap-2">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-hydro-600 dark:text-hydro-300" aria-hidden="true" />
                  <span>{line}</span>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>

      <div className="mt-12">
        <SectionHeading
          eyebrow="Current record"
          title="What this deployment holds right now"
          description="Read live from the public region catalogue; the insights page charts the series themselves."
        />
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="card">
            <p className="panel-title">Regions catalogued</p>
            <p className="mt-2 font-display text-[26px] font-semibold">
              {regionsState.loading ? <span className="skeleton inline-block h-6 w-16 align-middle" /> : regionsState.error ? '—' : formatCount(regions.length)}
            </p>
            <p className="mt-1 text-xs subtle">{regionsState.error ? 'Catalogue unavailable' : 'Reference geography loaded into the platform'}</p>
          </div>
          <div className="card">
            <p className="panel-title">Regions with observations</p>
            <p className="mt-2 font-display text-[26px] font-semibold">
              {regionsState.loading ? <span className="skeleton inline-block h-6 w-16 align-middle" /> : regionsState.error ? '—' : formatCount(observed)}
            </p>
            <p className="mt-1 text-xs subtle">A region appears here once ingestion writes records for its stations.</p>
          </div>
          <div className="card">
            <p className="panel-title">Stations referenced</p>
            <p className="mt-2 font-display text-[26px] font-semibold">
              {regionsState.loading ? (
                <span className="skeleton inline-block h-6 w-16 align-middle" />
              ) : regionsState.error ? (
                '—'
              ) : (
                formatCount(regions.reduce((total, region) => total + region.location_count, 0))
              )}
            </p>
            <p className="mt-1 text-xs subtle">Observation points in the catalogue, with coordinates and station identifiers.</p>
          </div>
        </div>
        <div className="mt-3">
          <ProvenanceNote />
        </div>
      </div>

      <div className="mt-12">
        <SectionHeading
          eyebrow="Honest scope"
          title="Documented behaviour, assumptions and fallbacks"
          description="The platform is explicit about what it does when infrastructure is absent. These are the rules the code actually follows."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Production paths are implemented and executable">
            <p className="text-sm leading-relaxed muted">
              The Hadoop, HDFS and Spark integration is real code: Hadoop Streaming mapper/reducer scripts, a Spark job module, an HDFS-backed
              lake writer and job runners with recorded inputs, outputs and durations. Given a cluster, ingestion, processing and analytics run
              on it, and the API reports the engine and mode used for each job.
            </p>
            <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
              <GitBranch className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Where a workflow needs an engine that is not reachable, it is listed as unavailable with the reason instead of being silently
              offered.
            </p>
          </Panel>
          <Panel title="Development fallbacks are explicit">
            <p className="text-sm leading-relaxed muted">
              When MongoDB Atlas is not configured, the platform runs in development mode with a local document store and a local data lake, and
              every response labels the mode it used. When a NameNode does not answer, the lake reports that it fell back rather than pretending
              to have written to HDFS.
            </p>
            <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
              <Layers className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              The same layer names and file formats are used in both modes, so a development artefact is readable by the cluster paths unchanged.
            </p>
          </Panel>
          <Panel title="Email is suppressed unless SMTP is configured">
            <p className="text-sm leading-relaxed muted">
              Notification and password-recovery mailers check for SMTP settings first. Without them, delivery is recorded as suppressed or as a
              configuration requirement — the UI never claims that a message was sent, and password recovery tells you to contact an
              administrator instead of inventing a link.
            </p>
          </Panel>
          <Panel title="Data limits are enforced, not advisory">
            <p className="text-sm leading-relaxed muted">
              Upload size and row ceilings, query row limits, series truncation and pagination bounds are applied server-side. Large analytics
              read a bounded window, report the number of observations evaluated, and state when a series was truncated.
            </p>
          </Panel>
          <Panel title="Nothing is synthesised">
            <p className="text-sm leading-relaxed muted">
              There is no generated, random or illustrative measurement anywhere in the pipeline. A field the source did not supply is stored as
              <span className="mono"> null</span> and excluded from aggregates; a region with no records is reported as having none rather than
              drawn as zero.
            </p>
            <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
              <EyeOff className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Charts render only over real points, and gaps are shown as gaps.
            </p>
          </Panel>
          <Panel title="Correlation is reported as correlation">
            <p className="text-sm leading-relaxed muted">
              Pairwise coefficients come with their sample size and a standing caveat: correlation in this record is not evidence of causation,
              and confounding by season, latitude and elevation is not controlled for.
            </p>
            <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
              <Scale className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              Forecast pages carry the same discipline: skill versus a naive baseline, and uncertainty where the model supports it.
            </p>
          </Panel>
        </div>
      </div>

      <div className="mt-12 card flex flex-col gap-4 p-6 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-display text-lg font-semibold">Read the details</h2>
          <p className="mt-1 text-sm leading-relaxed muted">
            Capabilities are documented module by module, sources and field definitions on the attribution page, and deployment rules in the
            privacy and terms pages.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link to="/capabilities" className="btn btn-secondary btn-sm">
            Capabilities
          </Link>
          <Link to="/data-sources" className="btn btn-secondary btn-sm">
            Data sources
          </Link>
          <Link to="/insights" className="btn btn-primary btn-sm">
            Live insights
          </Link>
        </div>
      </div>
    </PublicContainer>
  )
}

export default AboutPage
