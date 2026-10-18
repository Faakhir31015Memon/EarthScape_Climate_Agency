import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Badge } from '../../components/ui'
import { AnchorNav, Prose, PublicContainer, SectionHeading } from './components/PublicSections'

const EFFECTIVE = '16 October 2026'
const VERSION = '1.0'

const SECTIONS: { id: string; title: string; body: ReactNode }[] = [
  {
    id: 'agreement',
    title: 'Agreement and scope',
    body: (
      <p>
        These terms govern use of the EarthScape Climate Agency platform — the API, the analytical workspace and its public pages. By registering an account
        or submitting data, you accept them. The platform is an analytical instrument for a climate practice; it is provided on the terms below, and the
        operator of the installation you use may supplement them with institutional policies that take precedence for your organisation.
      </p>
    ),
  },
  {
    id: 'accounts',
    title: 'Accounts and role responsibility',
    body: (
      <>
        <p>
          Where self-registration is enabled, it grants only the <strong>least-privileged role the deployment allows</strong> — normally the viewer role. A
          registrant cannot request or obtain analyst or administrator rights through sign-up; those roles are provisioned by an existing administrator, and
          privilege is always assigned server-side.
        </p>
        <p>
          You are responsible for keeping your password and active sessions secure, for activity carried out under your account, and for using the privileges
          you are granted only for their intended purpose. Access tokens are short-lived and refresh tokens rotate, as described in the privacy policy, but a
          shared or stolen credential remains your responsibility to guard.
        </p>
      </>
    ),
  },
  {
    id: 'acceptable-use',
    title: 'Acceptable use',
    body: (
      <>
        <p>You agree not to:</p>
        <ul>
          <li>Attempt to gain privileges, access another account&rsquo;s data, or bypass the role checks the server enforces.</li>
          <li>Upload data you have no right to submit, or misrepresent the provenance or ownership of a dataset.</li>
          <li>Disrupt the service — denial-of-workload attacks, deliberate hammering of the API, or jobs designed to exhaust cluster resources.</li>
          <li>Present the platform&rsquo;s analytical output as an official or authoritative forecast where it is not.</li>
        </ul>
      </>
    ),
  },
  {
    id: 'not-warnings',
    title: 'Forecasts are model output, not warnings',
    body: (
      <p>
        <strong>This is important.</strong> Predictions and forecasts produced by the platform are statistical model output fitted to the stored record. They
        are <strong>not official weather warnings, not meteorological service products, and not a basis for decisions about safety of life or property</strong>.
        Alerts raised by the platform are analytical notifications about the data record — they do not replace any official warning channel. Use qualified,
        official sources for any protective action.
      </p>
    ),
  },
  {
    id: 'provenance',
    title: 'Data provenance and your uploads',
    body: (
      <>
        <p>
          Reference meteorological values originate from public providers — principally NASA POWER, and NOAA Climate Data Online or Open-Meteo where an API key
          or connector is configured for the deployment. Each stored observation keeps its source, ingestion job and quality flags; the platform never
          synthesises a measurement to fill a gap.
        </p>
        <p>
          Data you upload remains <strong>your responsibility and stays yours</strong>. By uploading you confirm you have the right to submit it and to grant
          the platform the access needed to validate, process and publish derived analytics within your deployment. Attribution to you and your organisation is
          preserved; deleting your account does not retroactively erase provenance already recorded against the shared scientific record.
        </p>
      </>
    ),
  },
  {
    id: 'availability',
    title: 'Service availability',
    body: (
      <p>
        The platform is offered without an availability guarantee. Its design is to report, honestly and live, which engines and connectors are configured and
        reachable — Hadoop, Spark, Kafka, NOAA connectors, email delivery — and to disable or clearly label anything that is not, rather than to promise
        capability it cannot deliver. A component being &ldquo;available&rdquo; on a given day is a statement about that deployment&rsquo;s configuration, not a
        contractual uptime commitment.
      </p>
    ),
  },
  {
    id: 'limits',
    title: 'Rate limits and usage caps',
    body: (
      <p>
        Acceptable use is enforced mechanically. API requests are rate-limited per account and per route, and a throttled call returns an explicit error with a
        retry hint instead of failing silently. Uploads are bounded by a maximum file size and a maximum row count set by the deployment, and large analytical
        reads operate over bounded windows, report how many observations they evaluated, and state when a series is truncated.
      </p>
    ),
  },
  {
    id: 'ip',
    title: 'Intellectual property and licensing',
    body: (
      <p>
        The EarthScape software — its code, interface and documentation — belongs to its authors and is distributed under the licence that accompanies the
        repository; that licence governs copying, modification and redistribution of the software itself. It does not transfer to you any rights in the underlying
        provider data, which remains subject to each provider&rsquo;s terms, nor in datasets owned by other institutions. Analytics you generate from data you are
        entitled to use are yours to work with, subject to those same provider and institutional terms.
      </p>
    ),
  },
  {
    id: 'disclaimers',
    title: 'Disclaimers',
    body: (
      <p>
        The platform is provided on an &ldquo;as is&rdquo; and &ldquo;as available&rdquo; basis. To the extent permitted by law, no warranty is given for the
        accuracy, completeness or fitness for a particular purpose of any measurement, aggregate, trend, anomaly, forecast or report the software produces.
        Analytical results carry their own coverage and caveat data precisely so they can be read critically; the absence of a result often means the record does
        not support one, and the software says so rather than inventing it.
      </p>
    ),
  },
  {
    id: 'liability',
    title: 'Limitation of liability',
    body: (
      <p>
        The authors and operators are not liable for losses arising from reliance on the platform&rsquo;s output or from its availability, to the fullest extent
        the law allows. Because this project is an analytical and demonstration platform, that limitation is stated plainly rather than papered over.
      </p>
    ),
  },
  {
    id: 'changes',
    title: 'Changes to these terms',
    body: (
      <p>
        Terms are versioned with the platform; the version and effective date at the top of this page identify the text in force. Material changes are reflected
        in that version number.
      </p>
    ),
  },
  {
    id: 'governing-law',
    title: 'Governing law',
    body: (
      <p>
        These terms are governed by <strong>[Jurisdiction]</strong>, to be set by the operator of the deployment, and any dispute is subject to the exclusive
        jurisdiction of the courts there. The software itself does not choose a jurisdiction; this placeholder is filled in by whoever runs the installation.
      </p>
    ),
  },
  {
    id: 'contact',
    title: 'Contact',
    body: (
      <p>
        Questions about these terms are handled through the support queue at <Link to="/app/support">/app/support</Link> for account holders, or with your
        platform administrator. The contact of record is the operator of the installation you use.
      </p>
    ),
  },
]

export default function TermsPage() {
  return (
    <PublicContainer narrow>
      <header className="mb-8 max-w-3xl">
        <p className="panel-title mb-2">Legal</p>
        <h1 className="font-display text-[30px] font-semibold leading-tight sm:text-[36px]">Terms of use</h1>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge tone="neutral">Version {VERSION}</Badge>
          <Badge tone="info">Effective {EFFECTIVE}</Badge>
        </div>
        <p className="mt-4 text-sm leading-relaxed muted">
          These terms match how the platform actually behaves: least-privileged self-registration, honest availability reporting, rate limits that are enforced
          rather than advisory, and forecasts that are model output — never official warnings.
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
        A statement of how this platform operates, current at version {VERSION} ({EFFECTIVE}). It is not legal advice, and the operator&rsquo;s and
        your institution&rsquo;s agreements govern above and beyond the software&rsquo;s behaviour.
      </p>
    </PublicContainer>
  )
}
