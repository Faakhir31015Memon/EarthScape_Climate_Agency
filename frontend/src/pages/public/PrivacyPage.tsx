import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '../../components/ui'
import { AnchorNav, Prose, PublicContainer, SectionHeading } from './components/PublicSections'

const EFFECTIVE = '16 October 2026'
const VERSION = '1.0'

const SECTIONS: { id: string; title: string; body: ReactNode }[] = [
  {
    id: 'scope',
    title: 'Scope of this policy',
    body: (
      <>
        <p>
          This policy describes what the EarthScape Climate Agency platform stores about you and about the climate record it processes. EarthScape is an
          API-only backend with a React front-end: it serves analytical data to signed-in accounts and to public read-only pages. It does not run
          advertising, marketing or behavioural-analytics code.
        </p>
        <p>
          The statements below describe the software in this repository. Where a value is controlled by the deployment operator rather than the code — token
          lifetimes, retention windows, whether email is enabled — the policy says so, and the sign-in pages show the values the running API returned.
        </p>
      </>
    ),
  },
  {
    id: 'account-data',
    title: 'Account data we store',
    body: (
      <>
        <p>When an account is created — by you through self-registration or by an administrator — the following fields are kept in the user record:</p>
        <ul>
          <li>Your full name and email address (the address is stored in a normalised, lower-cased form used for sign-in lookup).</li>
          <li>
            A <strong>bcrypt password hash</strong>. The plain password is never stored and never logged; it is only read to produce the hash. Nobody,
            including an administrator, can recover it — an administrator can only set a new one.
          </li>
          <li>Your role (administrator, climate analyst, or viewer), which the server assigns and which a registrant cannot choose.</li>
          <li>Your preferences: theme, unit system, notification channels, an optional dashboard region and interface density.</li>
          <li>An optional organisation string, used to attribute datasets you contribute.</li>
          <li>
            Sign-in bookkeeping: the last successful login time, a failed-login counter, a temporary lockout timestamp, and the timestamp of your last
            password change. Source IP is recorded on login for security auditing.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: 'climate-data',
    title: 'Climate and operational data',
    body: (
      <>
        <p>
          Beyond the account record, the platform stores the working data of a climate practice: registered datasets and their provenance, station-day
          observations (temperature, precipitation, humidity, wind, pressure, cloud and solar fields), ingestion and processing jobs, trained model
          records, generated forecasts, alerts, notification delivery records, support tickets and feedback. Observations carry their source and quality
          flags; a measurement a provider did not supply is stored as null and is never filled with a synthetic value.
        </p>
        <p>
          Personal data appears inside this operational record only where a person acted: the account that uploaded a dataset, submitted a job, acknowledged
          an alert, or opened a ticket is attached to that action so it can be audited.
        </p>
      </>
    ),
  },
  {
    id: 'processing-hosting',
    title: 'Where data is processed and stored',
    body: (
      <>
        <p>
          The application database is MongoDB. When a deployment is configured with a MongoDB Atlas connection string, Atlas is the processor and hosted
          database that holds these collections; when it is not, the platform runs against a local document store. The physical location of the data is the
          region and topology chosen by the deployment operator — it is not fixed by the software, so the operator running the installation you use is the
          data controller for where that data resides.
        </p>
        <p>
          Bulk tabular records are written to a layered data lake (raw, validated, processed, analytics, machine-learning features and exports) addressed
          through HDFS when a cluster NameNode is reachable, and to a local lake otherwise. Both modes use the same collection and file names.
        </p>
      </>
    ),
  },
  {
    id: 'trackers',
    title: 'Cookies, trackers and third parties',
    body: (
      <>
        <p>
          The platform sets no advertising or analytics cookies and embeds no third-party tracking pixels. Sign-in state is held in browser storage (see
          next section) rather than through tracking cookies, and no behavioural data leaves the deployment to a marketing or analytics vendor.
        </p>
        <p>
          Fonts are loaded from Google Fonts on first paint; that request is subject to Google&rsquo;s own privacy terms and is the only routine
          third-party asset the front-end fetches.
        </p>
      </>
    ),
  },
  {
    id: 'sessions',
    title: 'Sessions and tokens',
    body: (
      <>
        <p>The session mechanism works exactly as the code implements it:</p>
        <ul>
          <li>
            A short-lived <strong>access token</strong> — 20 minutes by default — is held <strong>only in memory</strong> and is attached to each API call.
            It is never written to disk.
          </li>
          <li>
            A <strong>refresh token</strong> — valid for 7 days by default — is persisted in <strong>local storage</strong> so that reloading the page can
            silently re-authenticate you.
          </li>
          <li>
            Using a refresh token rotates it: the old token is invalidated and a new pair is issued. Re-use of an already-rotated refresh token revokes the
            whole token family, which limits the damage from a stolen refresh token.
          </li>
        </ul>
        <p>The exact lifetimes in force are shown on the sign-in pages and are set by the deployment, not by the browser.</p>
      </>
    ),
  },
  {
    id: 'lockout',
    title: 'Login lockout',
    body: (
      <p>
        Repeated failed sign-in attempts temporarily lock the account — eight failures for a fifteen-minute window by default. After the window passes the
        account unlocks itself, and an administrator can clear the lock sooner. Both the failure threshold and the lockout duration are server-side settings.
      </p>
    ),
  },
  {
    id: 'audit',
    title: 'Audit log',
    body: (
      <>
        <p>
          State-changing actions — sign-in, sign-out, token reuse, password change, account creation, role changes, job submissions, alert acknowledgement,
          ticket actions and more — are written to an append-only audit trail. Each entry records the acting account and role, the action, the affected
          resource, the outcome (success, failure or denied) and, where available, the request identifier and source address. Failed sign-ins are recorded
          with the email domain only, not the full address.
        </p>
        <p>
          The audit trail is readable only by administrators. It is not exposed to other roles and is never rendered on a public page. When a request fails,
          the front-end shows the reference identifier so you can quote it in a support ticket.
        </p>
      </>
    ),
  },
  {
    id: 'email',
    title: 'Email and notifications',
    body: (
      <p>
        Alert notifications and password-recovery messages are emailed <strong>only when the deployment configures an SMTP transport and enables email</strong>.
        Without that configuration nothing is sent and nothing is faked: the notification is recorded with a delivery status of <span className="mono">suppressed</span>
        and a reason, and the recovery form tells you an administrator must issue the reset. In-app notifications are always recorded regardless of email
        settings.
      </p>
    ),
  },
  {
    id: 'retention',
    title: 'Retention',
    body: (
      <p>
        Account and operational records are kept for as long as the account exists and, for the scientific record, as long as the deployment operator retains
        the climate data they ingested — provenance is a feature, not a by-product, so observations are not silently purged. Generated reports expire on the
        schedule the operator sets, refresh tokens expire after the configured lifetime, and backups are managed by the operator. The software does not
        impose a fixed deletion date on your scientific data behind the operator&rsquo;s back.
      </p>
    ),
  },
  {
    id: 'rights',
    title: 'Your rights',
    body: (
      <>
        <p>You can access and correct most of your own data directly in the product: your name, preferences and optional organisation are editable from your profile, and passwords are changed or reset through the sign-in flows.</p>
        <p>
          To request a copy of the personal data held about you, correction of anything you cannot edit yourself, or deletion of your account, raise a ticket
          through the in-app support queue at <Link to="/app/support">/app/support</Link>. Deletion requests are actioned by an administrator against the
          account store; observations that belong to the shared climate record are retained for their scientific provenance even where a person&rsquo;s
          account is removed.
        </p>
      </>
    ),
  },
  {
    id: 'security',
    title: 'Security measures and their limits',
    body: (
      <>
        <p>Stated honestly, the protections this platform implements are:</p>
        <ul>
          <li>Role-based access control enforced on every API request by the server, not just hidden in the interface.</li>
          <li>Passwords stored only as bcrypt hashes; refresh tokens stored as hashes with rotation and family revocation.</li>
          <li>Connections to the hosted database are made over TLS when Atlas is used.</li>
        </ul>
        <p>
          We do not claim any security certification, accreditation or audit this project does not have. The software secures the paths described above; how
          strongly a given installation is protected also depends on the operator&rsquo;s deployment, secrets and network configuration.
        </p>
      </>
    ),
  },
  {
    id: 'changes',
    title: 'Changes to this policy',
    body: (
      <p>
        This policy is versioned with the platform. Substantive changes are reflected in the version and effective date shown at the top of this page; the
        wording above describes the behaviour of the code at that version.
      </p>
    ),
  },
  {
    id: 'contact',
    title: 'Contact',
    body: (
      <p>
        Privacy questions and data requests are handled through the support ticket queue at <Link to="/app/support">/app/support</Link> once you have an
        account, or through your institution&rsquo;s platform administrator. There is no external privacy mailbox published by the software itself; the
        operator of the installation you use is the contact of record.
      </p>
    ),
  },
]

export default function PrivacyPage() {
  return (
    <PublicContainer narrow>
      <header className="mb-8 max-w-3xl">
        <p className="panel-title mb-2">Legal</p>
        <h1 className="font-display text-[30px] font-semibold leading-tight sm:text-[36px]">Privacy policy</h1>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge tone="neutral">Version {VERSION}</Badge>
          <Badge tone="info">Effective {EFFECTIVE}</Badge>
        </div>
        <p className="mt-4 text-sm leading-relaxed muted">
          EarthScape is a data platform, so this policy is written around the actual collections, tokens and jobs the software runs — not generic boilerplate.
          Where a value belongs to the deployment rather than the code, it says so.
        </p>
      </header>

      <AnchorNav items={SECTIONS.map((section) => ({ id: section.id, label: section.title }))} />

      {SECTIONS.map((section, index) => (
        <section key={section.id} id={section.id} className="mb-10 scroll-mt-24">
          <SectionHeading title={`${index + 1}. ${section.title}`} className="mb-3" />
          <Prose>{section.body}</Prose>
        </section>
      ))}

      <p className="mt-8 border-t pt-4 text-xs leading-relaxed subtle" style={{ borderColor: 'var(--border)' }}>
        A factual statement about this platform&rsquo;s behaviour, current at version {VERSION} ({EFFECTIVE}). It is not legal advice; your institution&rsquo;s
        own data-protection obligations may be broader than the software&rsquo;s built-in behaviour.
      </p>
    </PublicContainer>
  )
}
