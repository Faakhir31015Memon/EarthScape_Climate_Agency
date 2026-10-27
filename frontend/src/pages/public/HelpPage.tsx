import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  FileText,
  LifeBuoy,
  ListChecks,
  ShieldCheck,
} from 'lucide-react'
import { Badge, Panel, RefreshButton, Skeleton } from '../../components/ui'
import { useAuth } from '../../features/auth/AuthContext'
import { passwordPolicySummary } from './components/publicData'
import { AnchorNav, CallToActionBar, ProvenanceNote, PublicContainer, SectionHeading } from './components/PublicSections'
import { cn } from '../../lib/cn'

const SECTIONS = [
  { id: 'start', label: 'Getting started' },
  { id: 'first-run', label: 'First run' },
  { id: 'faq', label: 'FAQ' },
  { id: 'interface', label: 'Using the interface' },
  { id: 'documents', label: 'Documentation' },
  { id: 'support', label: 'Support' },
]

const ROLE_STEPS: { role: string; title: string; audience: string; steps: { action: string; detail: string; to?: string; label?: string }[] }[] = [
  {
    role: 'viewer',
    title: 'Viewer / stakeholder',
    audience: 'What you can do the moment your account exists',
    steps: [
      {
        action: 'Read the record',
        detail:
          'The landing page shows the record without an account. Signing in adds full coverage, quality flags and the normal-period explorer.',
        to: '/login',
        label: 'Sign in to the workspace',
      },
      { action: 'Browse datasets and dashboards', detail: 'The overview dashboard, maps, anomaly lists, forecasts and published reports are all there to read.' },
      { action: 'Watch alerts', detail: 'Alerts and the notification centre are readable here. Resolving one is analyst work.' },
      { action: 'Ask a question', detail: 'Support tickets and product feedback are open to anyone signed in.', to: '/app/support', label: 'Open support' },
      { action: 'Export what you can see', detail: 'Reports and CSV downloads carry the same rows the screen shows, with the source and period attached.' },
    ],
  },
  {
    role: 'climate_analyst',
    title: 'Climate analyst',
    audience: 'The scientific path, in the order it normally runs',
    steps: [
      { action: 'Ingest', detail: "Start a provider job (NASA POWER, NOAA or the live conditions feed) or upload your institution's file, then read the validation report before ingesting.", to: '/app/ingestion', label: 'Ingestion jobs' },
      { action: 'Inspect quality', detail: 'Look over flagged rows, completeness and freshness. A value outside its range is kept for you to judge.' },
      { action: 'Process at scale', detail: 'Submit the job that aggregates, scans for anomalies and builds features, then follow it in the processing monitor.', to: '/app/processing', label: 'Processing monitor' },
      { action: 'Model', detail: 'Train on the historical matrix, read MAE / RMSE / MAPE / R² and the skill score against the climatology baseline, then activate the version you trust.', to: '/app/ml-lab', label: 'Machine learning lab' },
      { action: 'Predict and publish', detail: 'Run forecasts against the active model, export analytics, set up alert rules and generate the report.', to: '/app/reports', label: 'Reports and exports' },
    ],
  },
  {
    role: 'administrator',
    title: 'Administrator',
    audience: 'Accounts, settings and keeping an eye on things',
    steps: [
      {
        action: 'Create accounts and assign roles',
        detail:
          'You create the account and set what it can do. Where registration is open, the new account still arrives at the low end and you decide who moves up.',
        to: '/admin/users',
        label: 'User management',
      },
      { action: 'Watch the jobs', detail: 'The system health page reports running work, failures and anything the deployment is missing, so a stalled sync shows up there first.', to: '/admin/health', label: 'System health' },
      { action: 'Run backups', detail: 'A full backup and an archive export start from the administration overview. The restore steps are written down, so recovery follows a procedure.' },
    ],
  },
]

const FIRST_RUN = [
  {
    step: '1. Copy the environment template',
    detail:
      "Start from the example environment file and fill in the connection details and the signing secrets. The template names the command that generates each secret. Keep the filled-in file out of version control.",
  },
  {
    step: '2. Build the storage layout and the first accounts',
    detail:
      'The bootstrap script prepares the storage layout, registers the region and station catalogue, and writes one set of randomly generated sign-in credentials to a file under the backend data directory. The repository ships no default or hard-coded password.',
  },
  {
    step: '3. Start the API and the site',
    detail:
      'Run the backend server, then install and start the frontend. In development the frontend forwards /api requests to the server, so there is no base URL to configure locally.',
  },
  {
    step: '4. Load observations',
    detail:
      'Nothing scientific exists until data does. Run a provider ingestion job, upload a file from your institution, or use the seeding script, which pulls daily values from the public provider instead of generating numbers.',
  },
  {
    step: '5. Decide which processing options are on',
    detail:
      'Distributed processing, messaging and scheduled-run options start off, and the platform checks what is present before using it. When something is missing, the health page names the setting and the matching actions stay disabled until it arrives.',
  },
  {
    step: '6. Turn on delivery channels',
    detail:
      'Email stays off until the mail host, account and sender address are filled in. Until then, alerts and password-reset links are recorded as suppressed, so you can see what would have gone out.',
  },
  {
    step: '7. Deploy as a container or a cluster',
    detail:
      'The compose file brings up the site and the platform services as one stack, with a profile for distributed processing when you want it. Topology, backups and scheduled runs are covered in the infrastructure guide.',
  },
]

const INTERFACE_NOTES = [
  {
    title: 'Empty is an answer',
    body: 'A chart that will not draw because the window has fewer verified periods than the method needs is working as intended. The reason and the period counts sit in its footer.',
  },
  {
    title: 'Gaps stay gaps',
    body: 'A missing measurement is stored as missing. The line breaks there instead of crossing the gap, and a day with no reading does not appear as zero millimetres or zero degrees.',
  },
  {
    title: 'Correlation needs a mechanism',
    body: 'The correlation tool reports the strength of co-movement, the sample size and the lag. The paired station test (same site, same period, same units) is a comparison. Everything else comes back marked as association.',
  },
  {
    title: 'Your account shapes the page',
    body: "Analysts get the write paths for jobs and models; other accounts read the same record. The profile page lists what yours can do, so you don't have to guess.",
  },
  {
    title: 'Every request has an id',
    body: 'A failed request returns a code, an HTTP status and a reference id. Put that id in a ticket and the log entry behind it can be found.',
  },
]

/** Documents that exist in this checkout, verified against the repository tree. */
const REPO_DOCS = [
  { path: 'README.md', note: 'Repository layout, quick start, and the data rules the project follows.' },
  { path: 'backend/.env.example', note: 'Every setting the platform reads, with its default and a comment where behaviour changes.' },
  { path: 'docker-compose.yml', note: 'The container stack for the site and the platform services, plus the opt-in cluster profile.' },
  { path: 'backend/infrastructure/README.md', note: 'Deployment on a cluster, backups and scheduled runs.' },
  { path: 'backend/data-pipeline/mapreduce/README.md', note: 'How the batch aggregation jobs are written, and how to run one locally or across a cluster.' },
  { path: 'backend/ml/README.md', note: 'Feature build, training, evaluation and inference layout.' },
  { path: 'backend/sample-data/README.md', note: 'What ships as sample data: the six city CSVs came from the public provider, and nothing here is generated.' },
]

function FaqItem({ question, children }: { question: string; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b" style={{ borderColor: 'var(--border)' }}>
      <h3>
        <button
          type="button"
          onClick={() => setOpen((previous) => !previous)}
          aria-expanded={open}
          className="flex w-full items-center justify-between gap-3 py-3.5 text-left text-sm font-semibold hover:text-clay-700 dark:hover:text-clay-300"
        >
          <span>{question}</span>
          <ChevronDown className={cn('h-4 w-4 shrink-0 subtle transition-transform', open && 'rotate-180')} aria-hidden="true" />
        </button>
      </h3>
      {open ? <div className="pb-4 pr-6 text-sm leading-relaxed muted">{children}</div> : null}
    </div>
  )
}

export function HelpPage() {
  const { configuration, status, refreshConfiguration } = useAuth()
  const [refreshing, setRefreshing] = useState(false)

  const policy = configuration?.password_policy
  const registrationLabel = configuration ? (configuration.self_registration_enabled ? 'enabled' : 'closed') : '—'
  const resetLabel = configuration ? (configuration.password_reset_enabled ? 'enabled' : 'disabled') : '—'

  const refresh = () => {
    setRefreshing(true)
    void refreshConfiguration().finally(() => setRefreshing(false))
  }

  return (
    <PublicContainer>
      <header className="mb-6 max-w-3xl">
        <p className="panel-title mb-2">Help centre</p>
        <h1 className="font-display text-[28px] font-semibold leading-tight sm:text-[34px]">How to use the platform</h1>
        <p className="mt-3 text-sm leading-relaxed muted">
          Steps for each kind of user, the setup order for whoever runs the platform, and answers read from this deployment's own settings.
        </p>
      </header>

      <AnchorNav items={SECTIONS} />

      <section id="start" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Who does what"
          title="Getting started"
          description="The steps below follow the order people usually work in. Your account decides which of them you can take."
        />
        <div className="space-y-4">
          {ROLE_STEPS.map((track) => (
            <Panel
              key={track.role}
              title={track.title}
              description={track.audience}
              actions={
                <Badge tone={track.role === 'administrator' ? 'warning' : track.role === 'climate_analyst' ? 'info' : 'neutral'}>
                  <ShieldCheck className="mr-1 h-3 w-3" aria-hidden="true" />
                  {track.role}
                </Badge>
              }
            >
              <ol className="space-y-3">
                {track.steps.map((item, index) => (
                  <li key={item.action} className="flex gap-3">
                    <span className="mono mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-clay-600/10 text-xs font-semibold text-clay-700 dark:text-clay-300">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-sm font-semibold">{item.action}</p>
                      <p className="mt-0.5 text-sm leading-relaxed muted">{item.detail}</p>
                      {item.to ? (
                        status === 'authenticated' ? (
                          <Link to={item.to} className="link mt-1 inline-flex items-center gap-1 text-xs">
                            {item.label} <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                          </Link>
                        ) : (
                          <Link to="/login" className="link mt-1 inline-flex items-center gap-1 text-xs">
                            Sign in to open {item.label?.toLowerCase()} <ArrowUpRight className="h-3.5 w-3.5" aria-hidden="true" />
                          </Link>
                        )
                      ) : null}
                    </div>
                  </li>
                ))}
              </ol>
            </Panel>
          ))}
        </div>
      </section>

      <section id="first-run" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="For the deployment operator"
          title="First run"
          description="The order that works: settings, storage, reference data, then the ingestion that makes every other screen mean something."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Sequence">
            <ol className="space-y-3.5">
              {FIRST_RUN.map((item) => (
                <li key={item.step}>
                  <p className="text-sm font-semibold">{item.step}</p>
                  <p className="mt-0.5 text-sm leading-relaxed muted">{item.detail}</p>
                </li>
              ))}
            </ol>
          </Panel>
          <div className="space-y-4">
            <Panel title="How long until there is data to see?">
              <p className="text-sm leading-relaxed muted">
                The first sync over the catalogued stations takes a few minutes. Each request brings back daily values for one station within a fixed
                window. Before that job finishes, dashboards, maps and trend charts have nothing to show and say so.
              </p>
              <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                <span>
                  An empty panel at the start is the right answer. A figure on screen before the first sync would have to be made up.
                </span>
              </p>
            </Panel>
            <Panel title="When a setting explains an empty panel">
              <ul className="space-y-2 text-sm leading-relaxed muted">
                <li>
                  <span className="font-medium text-[var(--text)]">NOAA key missing</span>: the connector shows as needing setup and adds nothing until
                  its token exists.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">Mail settings unset</span>: alert email and password-reset links are recorded as
                  suppressed, and the record of the attempt still gets written.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">Remote storage unset</span>: the platform runs on its own local store, and the health
                  page shows which mode it is in.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">Distributed processing unset</span>: large jobs fall back to the single-node path and
                  the run is labelled as single-node.
                </li>
              </ul>
            </Panel>
          </div>
        </div>
      </section>

      <section id="faq" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Answers from this deployment"
          title="Frequently asked questions"
          description="Where an answer depends on configuration, the page reads it from this deployment."
          actions={
            <RefreshButton onClick={refresh} busy={refreshing} label="Re-read configuration" />
          }
        />
        <Panel>
          {configuration === null ? (
            <div className="space-y-3">
              <Skeleton className="h-4 w-64" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
              <p className="text-xs leading-relaxed subtle">
                These settings have not loaded yet. Answers that depend on them wait until they do.
              </p>
            </div>
          ) : (
            <div>
              <p className="mb-3 text-xs leading-relaxed subtle">
                Current values: self-registration <strong className="text-[var(--text)]">{registrationLabel}</strong> · password recovery{' '}
                <strong className="text-[var(--text)]">{resetLabel}</strong> · email delivery{' '}
                <strong className="text-[var(--text)]">{configuration.email_delivery ? 'configured' : 'not configured'}</strong> · access token{' '}
                <strong className="mono text-[var(--text)]">{configuration.access_token_minutes}</strong> min · refresh token{' '}
                <strong className="mono text-[var(--text)]">{configuration.refresh_token_days}</strong> days.
              </p>
              <FaqItem question="How do I get an account?">
                {configuration.self_registration_enabled ? (
                  <>
                    Register from the sign-in page. Registration is open here, so a new account arrives as a{' '}
                    <span className="mono">viewer</span> and an administrator raises it later. The roles this deployment hands out at sign-up are{' '}
                    {configuration.registration_roles.join(', ') || 'viewer'}.
                  </>
                ) : (
                  <>
                    Self-registration is closed here, so an administrator creates the account and picks what it can do. Once you have access, the support
                    queue is the quickest way to reach us. For a first account, contact the platform owner; that address is set by the deployment and
                    does not appear on a public page.
                  </>
                )}
              </FaqItem>
              <FaqItem question="What makes a password acceptable?">
                The policy needs {passwordPolicySummary(policy)}. The checklist on the registration form reads the same settings, so the two agree.
                Passwords are kept as hashes: an administrator can set you a new one, and nobody can read the old one back.
              </FaqItem>
              <FaqItem question="I forgot my password. What now?">
                {configuration.password_reset_enabled ? (
                  configuration.email_delivery ? (
                    <>
                      Use <Link to="/forgot-password">Forgot password</Link>. The reset link that arrives works once and expires. If nothing comes, check
                      the spam folder and then ask an administrator. Delivery failures are logged, so the attempt is on record.
                    </>
                  ) : (
                    <>
                      Resets are switched on, but this deployment has no mail server set up, so a link cannot be delivered. Your request is recorded and
                      an administrator sets the password for you.
                    </>
                  )
                ) : (
                  <>
                    Self-service reset is off here. Ask an administrator to set a temporary password, then change it from your profile.
                  </>
                )}
              </FaqItem>
              <FaqItem question="Why is my account locked?">
                Too many failed attempts lock the account for a fixed window, and it unlocks itself afterwards. An administrator can clear the lock
                sooner. The attempt limit and the window are set on the server, so the browser cannot change them.
              </FaqItem>
              <FaqItem question="How long am I signed in?">
                An access token lives {configuration.access_token_minutes} minutes and stays in memory. A refresh token, kept locally, renews it in the
                background. Each refresh hands out a new pair and retires the old one, and if a retired token is replayed the whole family is revoked.
                That is how a stolen refresh token gets cut off. Signing out discards both.
              </FaqItem>
              <FaqItem question="Why does a chart say it needs more points?">
                Trend, anomaly and model views need a minimum number of verified periods. When the window is too short, the page says why and shows the
                counts it has.
              </FaqItem>
              <FaqItem question="Are the public pages showing different data to the signed-in workspace?">
                No. Public pages read the same stored observations the workspace does; they only expose the reading side. Uploads, jobs, models, users and
                quality summaries need a signed-in session with the matching access.
              </FaqItem>
              <FaqItem question="Does the platform store anything about me beyond the account record?">
                Session records, an audit trail of the actions that change state, and your notification preferences. Credentials and full tokens are not
                stored.
              </FaqItem>
              <FaqItem question="What happens if I hit the API too fast?">
                Requests are throttled per account and per route, and a throttled call returns an error with a retry hint so you can see it. The public
                pages fetch a handful of endpoints once and only ask again when you act.
              </FaqItem>
            </div>
          )}
        </Panel>
      </section>

      <section id="interface" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Reading it correctly"
          title="Using the interface"
          description="Conventions that hold everywhere in the workspace. They are the difference between reading a dashboard and guessing from one."
        />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {INTERFACE_NOTES.map((note) => (
            <article key={note.title} className="card-tight">
              <h3 className="text-sm font-semibold">{note.title}</h3>
              <p className="mt-1.5 text-sm leading-relaxed muted">{note.body}</p>
            </article>
          ))}
          <article className="card-tight">
            <h3 className="flex items-center gap-2 text-sm font-semibold">
              <ListChecks className="h-4 w-4 text-forest-600 dark:text-forest-300" aria-hidden="true" /> Keyboard and contrast
            </h3>
            <p className="mt-1.5 text-sm leading-relaxed muted">
              Every control is a standard button or input with a visible label. Filters are announced to screen readers, icon-only buttons carry
              accessible names, and the dark theme is designed rather than inverted.
            </p>
          </article>
        </div>
      </section>

      <section id="documents" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="What ships in this repository"
          title="Documentation"
          description="The list below names files that exist in this checkout."
        />
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <Panel title="Present in the repository">
            <ul className="space-y-2.5">
              {REPO_DOCS.map((document) => (
                <li key={document.path} className="flex gap-2.5">
                  <FileText className="mt-0.5 h-4 w-4 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                  <div className="min-w-0">
                    <p className="mono text-xs font-semibold">{document.path}</p>
                    <p className="mt-0.5 text-sm leading-relaxed muted">{document.note}</p>
                  </div>
                </li>
              ))}
            </ul>
            <p className="mt-4 text-xs leading-relaxed subtle">
              Setup, configuration and the data rules live in <span className="mono">README.md</span>; the guides above
              cover each part. If a document is missing from your copy, ask for it instead of guessing how the platform behaves.
            </p>
          </Panel>
          <div className="space-y-4">
            <Panel title="Data dictionary">
              <p className="text-sm leading-relaxed muted">
                Field names, units, aggregation rules and validation ranges are written down once, and this deployment reports what it enforces today.
                The project report covers the scientific definition of each field.
              </p>
            </Panel>
          </div>
        </div>
      </section>

      <section id="support" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="When the answer is not here"
          title="Support"
          description="Tickets and feedback are filed inside the platform, so they can be traced and prioritised."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Ticket route">
            <p className="text-sm leading-relaxed muted">
              A ticket takes a description, the area it affects, a severity and the reference id of any failed request. It lands in a shared queue that
              analysts and administrators can see, and an administrator answers and closes it.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              {status === 'authenticated' ? (
                <Link to="/app/support" className="btn btn-primary btn-sm">
                  <LifeBuoy className="h-3.5 w-3.5" aria-hidden="true" /> Open support workspace
                </Link>
              ) : (
                <Link to="/login" className="btn btn-primary btn-sm">
                  Sign in to raise a ticket
                </Link>
              )}
            </div>
            <p className="mt-3 text-xs leading-relaxed subtle">
              Include the page and the action, the metric and region you had selected, the approximate time, and the request reference if one appeared.
              Ticket creation is rate-limited, so put related problems in one ticket.
            </p>
          </Panel>
          <Panel title="Before you write in">
            <ul className="space-y-2 text-sm leading-relaxed muted">
              <li>Check whether the panel is reporting an empty or unset state. That is an answer.</li>
              <li>Re-run the query with the refresh control: a transient upstream failure usually clears on the second attempt.</li>
              <li>Confirm your account has the action you expect; the profile page lists what it grants.</li>
              <li>If a number looks wrong, name the metric, region and date range so the rows behind it can be inspected.</li>
            </ul>
          </Panel>
        </div>
      </section>

      <CallToActionBar
        primaryLabel={status === 'authenticated' ? 'Back to the workspace' : 'Sign in'}
        primaryTo={status === 'authenticated' ? '/app' : '/login'}
        secondaryLabel="Create an account"
        secondaryTo="/register"
        note="Most of these end the same way: an account with the right access, and an ingestion job that has finished."
      />
      <div className="mt-6">
        <ProvenanceNote>
          Policy answers here come from the authentication settings of the deployment you are using, and the document names above are files that exist
          in this checkout. Lockout limits, token lifetimes and rate limits are set per deployment; the values on the page are the ones it returned.
        </ProvenanceNote>
      </div>
    </PublicContainer>
  )
}

export default HelpPage
