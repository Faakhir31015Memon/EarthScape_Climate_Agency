import { Link } from "react-router-dom";
import { Activity, Boxes, Database, Gauge, Leaf, Network, ShieldCheck, Sparkles } from "lucide-react";
import { PublicHeader } from "../../../components/layout/PublicHeader";
import { PublicFooter } from "../../../components/layout/PublicFooter";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useApi } from "../../../lib/api/query";
import { formatInt } from "../../../lib/format";
import type { HealthResult, ReadyResult } from "../../../lib/api/types";

const CAPABILITIES = [
  {
    icon: Database,
    title: "Ingestion & data lake",
    body: "Live observations pulled from Open-Meteo and NASA POWER, validated against documented physical ranges, and stored on a MongoDB + HDFS-style lake.",
  },
  {
    icon: Boxes,
    title: "Distributed processing",
    body: "Batch analytics run through Spark / MapReduce engines over the lake, producing trend, anomaly and correlation summaries.",
  },
  {
    icon: Activity,
    title: "Trend & anomaly analytics",
    body: "Per-variable trend fitting, correlation with an explicit no-causation caveat, and anomaly detection fed by alert rules.",
  },
  {
    icon: Sparkles,
    title: "Forecasting & ML",
    body: "Model registry and prediction jobs that emit forecasts stored alongside the source observations.",
  },
  {
    icon: Network,
    title: "Mapping & visualisation",
    body: "Geographic summaries rendered on an OpenStreetMap base with marker, heat and choropleth-ready layers.",
  },
  {
    icon: ShieldCheck,
    title: "Governance & roles",
    body: "Administrator, climate analyst and viewer roles with a server-enforced capability matrix surfaced throughout the workspace.",
  },
];

const ROLES = [
  {
    name: "Administrator",
    tone: "primary" as const,
    body: "Provisions accounts, manages datasets, services and settings across the platform.",
  },
  {
    name: "Climate Analyst",
    tone: "info" as const,
    body: "Runs ingestion, processing and predictions; saves analyses and generates reports.",
  },
  {
    name: "Viewer",
    tone: "neutral" as const,
    body: "Reads dashboards, trends, maps and exports within granted capabilities.",
  },
];

/**
 * Honest product landing. The only figures shown (system status, observation
 * count) come from the unauthenticated-safe /health and /ready endpoints; there
 * are no fabricated statistics.
 */
export function LandingPage() {
  const health = useApi<HealthResult>("/health", []);
  const ready = useApi<ReadyResult>("/ready", []);

  const observations = ready.data?.observations;

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <PublicHeader />

      <main id="main-content" className="flex-1">
        <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 md:py-24">
          <div className="max-w-3xl">
            <p className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-muted">
              <Leaf size={14} className="text-primary" aria-hidden="true" />
              University eProject · enterprise climate intelligence
            </p>
            <h1 className="mt-4 text-4xl font-semibold tracking-tight text-content md:text-5xl">
              EarthScape Climate Agency
            </h1>
            <p className="mt-4 text-lg text-muted">
              A big-data platform for ingesting, processing and interpreting climate observations —
              from provider APIs through a distributed data lake to trends, anomalies, forecasts and
              reports.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                to="/login"
                className="inline-flex h-11 items-center rounded-lg bg-primary px-5 text-sm font-medium text-primary-fg hover:bg-primary-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
              >
                Sign in to the workspace
              </Link>
              <Link
                to="/capabilities"
                className="inline-flex h-11 items-center rounded-lg border border-border px-5 text-sm font-medium text-content hover:bg-surface-2"
              >
                Explore capabilities
              </Link>
            </div>
          </div>
        </section>

        {/* Live, unauthenticated system snapshot — real values only. */}
        <section className="border-y border-border bg-surface">
          <div className="mx-auto grid max-w-7xl gap-4 px-4 py-8 sm:px-6 md:grid-cols-3">
            <SnapshotCard
              title="API health"
              loading={health.loading}
              body={
                health.data ? (
                  <span className="flex items-center gap-2">
                    <Gauge size={18} className="text-primary" aria-hidden="true" />
                    <span className="font-semibold text-content">
                      {health.data.status === "alive" ? "Operational" : health.data.status}
                    </span>
                    <code className="text-xs text-muted">v{health.data.version}</code>
                  </span>
                ) : (
                  <span className="text-sm text-muted">Status unavailable</span>
                )
              }
            />
            <SnapshotCard
              title="Readiness"
              loading={ready.loading}
              body={
                ready.data ? (
                  <Badge tone={ready.data.status === "ready" ? "success" : "warning"}>
                    {ready.data.status}
                  </Badge>
                ) : (
                  <span className="text-sm text-muted">Status unavailable</span>
                )
              }
            />
            <SnapshotCard
              title="Observations stored"
              loading={ready.loading}
              body={
                typeof observations === "number" ? (
                  <span className="text-2xl font-semibold text-content">
                    {formatInt(observations)}
                  </span>
                ) : (
                  <span className="text-sm text-muted">Not reported by the API</span>
                )
              }
            />
          </div>
        </section>

        <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
          <h2 className="text-2xl font-semibold tracking-tight text-content">
            What the platform does
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            The architecture is real: MongoDB as the system of record, an HDFS-style data lake, and
            Spark / MapReduce batch engines feeding machine-learning jobs.
          </p>
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {CAPABILITIES.map((c) => (
              <Card key={c.title}>
                <c.icon size={22} className="text-primary" aria-hidden="true" />
                <h3 className="mt-3 text-base font-semibold text-content">{c.title}</h3>
                <p className="mt-1 text-sm text-muted">{c.body}</p>
              </Card>
            ))}
          </div>
        </section>

        <section className="border-t border-border bg-surface">
          <div className="mx-auto max-w-7xl px-4 py-16 sm:px-6">
            <h2 className="text-2xl font-semibold tracking-tight text-content">Who it is for</h2>
            <div className="mt-8 grid gap-4 md:grid-cols-3">
              {ROLES.map((r) => (
                <div key={r.name} className="rounded-card border border-border bg-bg p-5">
                  <Badge tone={r.tone}>{r.name}</Badge>
                  <p className="mt-3 text-sm text-muted">{r.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      </main>

      <PublicFooter />
    </div>
  );
}

function SnapshotCard({
  title,
  body,
  loading,
}: {
  title: string;
  body: React.ReactNode;
  loading: boolean;
}) {
  return (
    <div className="rounded-card border border-border bg-bg p-4">
      <CardHeader title={<span className="text-sm">{title}</span>} className="mb-2" />
      {loading ? <Skeleton className="h-6 w-24" /> : body}
    </div>
  );
}
