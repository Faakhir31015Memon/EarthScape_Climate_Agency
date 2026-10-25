import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  FileText,
  LifeBuoy,
  ListChecks,
  ShieldCheck,
  Terminal,
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
          'The public insights page works without an account; signing in adds full coverage, quality flags and the historical normal-period explorer.',
        to: '/insights',
        label: 'Open climate insights',
      },
      { action: 'Browse datasets and dashboards', detail: 'Overview dashboard, maps, anomaly lists, forecasts and published reports are all read-only available.' },
      { action: 'Watch alerts', detail: 'Alerts and the notification centre are readable; acknowledging or resolving them requires the analyst role.' },
      { action: 'Ask a question', detail: 'Support tickets and product feedback are available to every signed-in role.', to: '/app/support', label: 'Open support' },
      { action: 'Export what you can see', detail: 'Report and CSV downloads are issued against the same server-side permission checks as the rest of the workspace.' },
    ],
  },
  {
    role: 'climate_analyst',
    title: 'Climate analyst',
    audience: 'The scientific path, in the order it is normally run',
    steps: [
      { action: 'Ingest', detail: 'Run a provider job (NASA POWER, NOAA CDO or the live API) or upload an institutional file, then validate before ingesting.', to: '/app/ingestion', label: 'Ingestion jobs' },
      { action: 'Inspect quality', detail: 'Review flagged rows, completeness and freshness. Records outside a validation range are flagged for your review, not deleted.' },
      { action: 'Process at scale', detail: 'Submit the MapReduce or Spark workflow for aggregation, anomaly scanning and feature building; watch it in the processing monitor.', to: '/app/processing', label: 'Processing monitor' },
      { action: 'Model', detail: 'Train on the historical matrix, read MAE / RMSE / MAPE / R² and the skill score against the climatology baseline, then activate the version you trust.', to: '/app/ml-lab', label: 'Machine learning lab' },
      { action: 'Predict and publish', detail: 'Run forecasts against the active model, export analytics, schedule alert rules and generate the report.', to: '/app/reports', label: 'Reports and exports' },
    ],
  },
  {
    role: 'administrator',
    title: 'Administrator',
    audience: 'Provisioning, configuration and oversight',
    steps: [
      {
        action: 'Create accounts and assign roles',
        detail:
          'Roles are set server-side at creation. Self-registration, where enabled, always produces the restricted viewer role — a registrant cannot choose privileges.',
        to: '/admin/users',
        label: 'User management',
      },
      { action: 'Configure the deployment', detail: 'Connection strings, storage backend, distributed engines, rate limits and notification settings are editable in the configuration screen.', to: '/admin/settings', label: 'Configuration' },
      { action: 'Watch the engines', detail: 'Database mode, queue workers, storage paths and job failures are reported on the system health page and in the audit log.', to: '/admin/health', label: 'System health' },
      { action: 'Run backups', detail: 'A database snapshot and the lake export are triggered from the administration overview; restore is a documented procedure, not an assumption.' },
      { action: 'Read the audit trail', detail: 'Every state-changing request is recorded with actor, action, resource, outcome and a request id you can quote in a ticket.', to: '/admin/audit', label: 'Audit logs' },
    ],
  },
]

const FIRST_RUN = [
  {
    step: '1. Copy the environment template',
    detail:
      'cp .env.example .env, then set MONGODB_URI, DJANGO_SECRET_KEY and JWT_SIGNING_KEY. The template names the command that generates each secret. Never commit the real file.',
  },
  {
    step: '2. Bootstrap collections, indexes and accounts',
    detail:
      'Run backend/scripts/bootstrap_platform.py. It creates the document collections and indexes, registers the region and station catalogue, and prints randomly generated sign-in credentials once into backend/.data/bootstrap-credentials.txt. There is no default or hardcoded administrator password in the repository.',
  },
  {
    step: '3. Start the API and the app',
    detail:
      'python backend/manage.py runserver 0.0.0.0:8000, then npm install && npm run dev inside frontend/. The Vite dev server proxies /api to the backend, so no extra base-URL configuration is needed locally.',
  },
  {
    step: '4. Put real observations in',
    detail:
      'Nothing scientific exists until data does. Run a provider ingestion job, upload an institutional file, or run backend/scripts/seed_real_data.py — which fetches genuine daily values from the public provider rather than synthesising them.',
  },
  {
    step: '5. Decide which engines are enabled',
    detail:
      'SPARK_ENABLED, HADOOP_BIN, KAFKA_ENABLED, AIRFLOW_ENABLED and MLFLOW_ENABLED default to the conservative local behaviour. The platform probes for them; when one is absent the administrator health page names the missing setting and the matching actions are disabled instead of pretending a cluster exists.',
  },
  {
    step: '6. Turn on delivery channels',
    detail:
      'ENABLE_EMAIL_NOTIFICATIONS stays false until SMTP_HOST, SMTP_USER and EMAIL_FROM_ADDRESS are set. Until then alerts and password-reset links are recorded as suppressed rather than silently dropped.',
  },
  {
    step: '7. Container or cluster deployment',
    detail:
      'docker-compose.yml defines the backend, worker, scheduler, frontend and Redis services with an optional Hadoop/Spark profile. Cluster topology, backups and Airflow scheduling are covered in infrastructure/README.md.',
  },
]

const INTERFACE_NOTES = [
  {
    title: 'Empty is an answer',
    body: 'A chart that refuses to draw because the window has fewer verified periods than the method requires is correct behaviour. The reason and the point counts are printed in its footer.',
  },
  {
    title: 'Gaps stay gaps',
    body: 'Missing measurements are stored as null. Lines break rather than cross a gap, and a missing day is never shown as zero millimetres or zero degrees.',
  },
  {
    title: 'Correlation needs a mechanism',
    body: 'The correlation tool reports strength of co-movement plus sample size and lag. Only the paired station test — same site, same period, same units — is a comparison; anything else is labelled as not causal.',
  },
  {
    title: 'Roles change what you see, not what exists',
    body: 'Analysts get write paths for jobs and models. Viewers read the same record. Nothing is hidden to make a role look better: capability names are listed in the platform page.',
  },
  {
    title: 'Every request has an id',
    body: 'Failed requests surface a code, an HTTP status and a reference id. Quote the reference id in a ticket and the audit log entry can be found immediately.',
  },
]

/** Documents that exist in this checkout, verified against the repository tree. */
const REPO_DOCS = [
  { path: 'README.md', note: 'Repository layout, quick start and the configuration and honesty rules.' },
  { path: '.env.example', note: 'Every setting the platform reads, with its default and a comment where behaviour changes.' },
  { path: 'docker-compose.yml', note: 'Container stack: API, worker, scheduler, frontend, Redis and the optional Hadoop/Spark profile.' },
  { path: 'backend/infrastructure/README.md', note: 'Container deployment, cluster profile, backups and Airflow scheduling.' },
  { path: 'backend/data-pipeline/mapreduce/README.md', note: 'Streaming mapper/reducer contract and how a job is run locally or on a cluster.' },
  { path: 'backend/ml/README.md', note: 'Feature build, training, evaluation and inference layout.' },
  { path: 'backend/sample-data/README.md', note: 'What is real in this repository: the six city CSVs are provider-retrieved data, and no synthetic corpus ships here.' },
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
          className="flex w-full items-center justify-between gap-3 py-3.5 text-left text-sm font-semibold hover:text-forest-700 dark:hover:text-forest-300"
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
        <h1 className="font-display text-[28px] font-semibold leading-tight sm:text-[34px]">How to use the platform, and what it will not tell you</h1>
        <p className="mt-3 text-sm leading-relaxed muted">
          Practical steps per role, the first-run sequence for an operator, and answers that read this deployment’s live authentication configuration
          instead of repeating a generic manual.
        </p>
      </header>

      <AnchorNav items={SECTIONS} />

      <section id="start" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Three roles, three paths"
          title="Getting started"
          description="Capabilities are enforced on the server; the steps below match the capability set each role is issued."
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
                    <span className="mono mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-forest-600/10 text-xs font-semibold text-forest-700 dark:text-forest-300">
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
          description="The order that works: configuration, storage, reference data, then the ingestion that makes every other screen meaningful."
          actions={
            <Link to="/capabilities" className="btn btn-secondary btn-sm">
              <Terminal className="h-3.5 w-3.5" aria-hidden="true" /> Execution paths
            </Link>
          }
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
                A first provider sync over the catalogued stations is a matter of minutes, not hours: each request returns daily values for one station
                over a bounded window. Until that job completes, dashboards, maps and trend charts are legitimately empty and say so.
              </p>
              <p className="mt-3 flex items-start gap-2 text-xs leading-relaxed subtle">
                <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-forest-600 dark:text-forest-300" aria-hidden="true" />
                <span>
                  Empty is the honest starting state. A page that produced numbers before any ingestion had run would be fabricating them.
                </span>
              </p>
            </Panel>
            <Panel title="Things that are configuration, not faults">
              <ul className="space-y-2 text-sm leading-relaxed muted">
                <li>
                  <span className="font-medium text-[var(--text)]">NOAA_API_KEY empty</span> — the NOAA connector reports{' '}
                  <span className="mono">needs_configuration</span> and contributes nothing until the token exists.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">SMTP unset</span> — alert mail and password-reset links are recorded as suppressed;
                  the record of them is still written.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">No Atlas URI</span> — the platform runs on its local document store and the storage
                  mode is displayed rather than assumed.
                </li>
                <li>
                  <span className="font-medium text-[var(--text)]">No Hadoop or Spark binaries</span> — distributed workflows fall back to the
                  single-node reference implementation and label themselves as such.
                </li>
              </ul>
            </Panel>
          </div>
        </div>
      </section>

      <section id="faq" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Answers from the live configuration"
          title="Frequently asked questions"
          description="Where an answer depends on how this deployment is configured, it is read from the authentication configuration endpoint."
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
                The configuration endpoint has not answered yet, so policy-dependent answers are held back rather than guessed.
              </p>
            </div>
          ) : (
            <div>
              <p className="mb-3 text-xs leading-relaxed subtle">
                Live values: self-registration <strong className="text-[var(--text)]">{registrationLabel}</strong> · password recovery{' '}
                <strong className="text-[var(--text)]">{resetLabel}</strong> · email delivery{' '}
                <strong className="text-[var(--text)]">{configuration.email_delivery ? 'configured' : 'not configured'}</strong> · access token{' '}
                <strong className="mono text-[var(--text)]">{configuration.access_token_minutes}</strong> min · refresh token{' '}
                <strong className="mono text-[var(--text)]">{configuration.refresh_token_days}</strong> days.
              </p>
              <FaqItem question="How do I get an account?">
                {configuration.self_registration_enabled ? (
                  <>
                    Register from the sign-in page. Because registration is open here, new accounts are issued the{' '}
                    <span className="mono">viewer</span> role automatically — an administrator upgrades it afterwards. The roles this deployment allows
                    a registrant to receive are {configuration.registration_roles.join(', ') || 'viewer'}.
                  </>
                ) : (
                  <>
                    Self-registration is closed on this deployment, so an administrator creates the account and chooses the role. Use the support ticket
                    queue once you have access, or contact the platform owner for a first account — that address is set by the deployment and is not
                    published on a public page.
                  </>
                )}
              </FaqItem>
              <FaqItem question="What makes a password acceptable?">
                Requires {passwordPolicySummary(policy)}. The rules are enforced by the API on submission; the checklist on the registration form mirrors
                the same configuration so the two can never disagree. Passwords are stored only as bcrypt hashes and are never retrievable — including
                by an administrator, who can set a new one but not read the old one.
              </FaqItem>
              <FaqItem question="I forgot my password. What now?">
                {configuration.password_reset_enabled ? (
                  configuration.email_delivery ? (
                    <>
                      Use <Link to="/forgot-password">Forgot password</Link>. A single-use reset token is mailed; it expires and can be consumed once. If
                      the mail does not arrive, check the spam folder and then ask an administrator — delivery failures are logged rather than hidden.
                    </>
                  ) : (
                    <>
                      Password resets are enabled, but this deployment has no mail transport configured, so a reset link cannot be delivered. The
                      request is recorded and an administrator issues the reset directly. The page says this instead of claiming an email was sent.
                    </>
                  )
                ) : (
                  <>
                    Self-service reset is disabled here. Ask an administrator to set a temporary password; you can change it from your profile afterwards.
                  </>
                )}
              </FaqItem>
              <FaqItem question="Why is my account locked?">
                Repeated failed sign-in attempts lock the account for a fixed window, after which it unlocks by itself. An administrator can clear the
                lock sooner. The lockout counter, the failure threshold and the duration are server-side settings — not something the browser controls.
              </FaqItem>
              <FaqItem question="How long am I signed in?">
                The access token is short-lived ({configuration.access_token_minutes} minutes) and held only in memory; a rotating refresh token, persisted
                locally, silently renews it. Using a refresh token issues a new pair and invalidates the old one, and re-use of an already-rotated token
                revokes the whole token family — the standard defence against a stolen refresh token. Signing out discards both.
              </FaqItem>
              <FaqItem question="Why does a chart say it needs more points?">
                Trend, anomaly and model views require a minimum number of verified periods. When the window is too short the API returns{' '}
                <span className="mono">available: false</span> with the reason and the counts, and the page shows that instead of drawing something
                unsupported.
              </FaqItem>
              <FaqItem question="Are the public pages showing different data to the signed-in workspace?">
                No. Public pages read the same stored observations through the public analytics endpoints — they simply expose the read side. Uploads,
                jobs, models, users and quality aggregates require an authenticated session with the matching capability.
              </FaqItem>
              <FaqItem question="Does the platform store anything about me beyond the account record?">
                Session records, an audit trail of state-changing actions, and notification preferences. Credentials, full tokens and card-style secrets
                are never stored. The privacy page lists exactly which fields exist and who can read them.
              </FaqItem>
              <FaqItem question="What happens if I hit the API too fast?">
                Requests are throttled per account and per route, and a throttled call returns an explicit error with a retry hint rather than a silent
                failure. The public pages fetch a small number of endpoints once and only re-request on your action.
              </FaqItem>
            </div>
          )}
        </Panel>
      </section>

      <section id="interface" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="Reading it correctly"
          title="Using the interface"
          description="Five conventions that apply everywhere in the workspace, and that make the difference between a dashboard and a guess."
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
              Controls are real buttons and inputs with visible labels; filters are announced to assistive technology, icon-only buttons carry
              accessible names, and both light and dark themes are designed rather than inverted.
            </p>
          </article>
        </div>
      </section>

      <section id="documents" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="What ships in this repository"
          title="Documentation"
          description="Only files that exist in the checkout are named; anything still delivered with the release package is described as such."
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
              Setup, configuration and the honesty rules live in <span className="mono">README.md</span>; the
              per-package guides are listed above. If a document is missing from your copy, request it rather than
              assuming the platform behaves differently from what is written above.
            </p>
          </Panel>
          <div className="space-y-4">
            <Panel title="Data dictionary">
              <p className="text-sm leading-relaxed muted">
                Field names, units, aggregation rules and validation ranges are defined once in code and reported live by this deployment, which is
                what the data-sources page renders. The project report explains each field’s scientific definition; the running platform tells you
                what it enforces today.
              </p>
              <Link to="/data-sources" className="link mt-3 inline-flex items-center gap-1 text-xs">
                <BookOpen className="h-3.5 w-3.5" aria-hidden="true" /> Open data sources and the live contract
              </Link>
            </Panel>
          </div>
        </div>
      </section>

      <section id="support" className="mb-12 scroll-mt-24">
        <SectionHeading
          eyebrow="When the answer is not here"
          title="Support"
          description="Tickets and feedback are recorded in the platform, so they can be traced, prioritised and audited."
        />
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title="Ticket route">
            <p className="text-sm leading-relaxed muted">
              Signed-in users raise a ticket from the support workspace: description, affected area, severity and the reference id of any failed
              request. Analysts and administrators see the queue; administrators can triage, respond and close.
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
              <Link to="/capabilities" className="btn btn-ghost btn-sm">
                What is monitored
              </Link>
            </div>
            <p className="mt-3 text-xs leading-relaxed subtle">
              Include: the page and action, the metric and region you selected, the approximate time, and the request reference if one was shown. Ticket
              creation is rate-limited, so batch related observations into one ticket.
            </p>
          </Panel>
          <Panel title="Before you write in">
            <ul className="space-y-2 text-sm leading-relaxed muted">
              <li>Check whether the panel is reporting an empty or configuration state — those are answers, not faults.</li>
              <li>Re-run the query with the refresh control: a transient upstream failure usually clears on the second attempt.</li>
              <li>Confirm your role actually holds the action you expect; capability lists are on the platform page.</li>
              <li>If a number looks wrong, name the metric, region and date range so the underlying rows can be inspected directly.</li>
            </ul>
          </Panel>
        </div>
      </section>

      <CallToActionBar
        primaryLabel={status === 'authenticated' ? 'Back to the workspace' : 'Sign in'}
        primaryTo={status === 'authenticated' ? '/app' : '/login'}
        secondaryLabel="Create an account"
        secondaryTo="/register"
        note="Most help questions end the same way: an account with the right role, and a completed ingestion job."
      />
      <div className="mt-6">
        <ProvenanceNote>
          This page reads the authentication configuration endpoint live for policy answers and names only documentation files that exist in the
          checkout. Lockout thresholds, token lifetimes and rate limits are set by the deployment you are using; the values shown are the ones it
          returned to you.
        </ProvenanceNote>
      </div>
    </PublicContainer>
  )
}

export default HelpPage
