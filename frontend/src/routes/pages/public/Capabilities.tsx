import { Link } from "react-router-dom";
import { Prose } from "./PublicLayout";
import { Card } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { StatusDot } from "../../../components/ui/StatusDot";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useApi } from "../../../lib/api/query";
import type { ReadyResult } from "../../../lib/api/types";

const CAPABILITY_ROWS = [
  ["Observation ingestion", "Pull live daily/hourly records from Open-Meteo and NASA POWER; validate against physical ranges; deduplicate; never store missing values as zero."],
  ["Distributed processing", "Spark and Hadoop/MapReduce engines summarise trends, quality and regional statistics from the lake."],
  ["Trend analytics", "Per-variable trend fitting with slope, sample size and period coverage, rendered as honest time series."],
  ["Correlation", "Pairwise correlation with regression line, strength and a mandatory no-causation note."],
  ["Anomaly & alerting", "Rule-driven anomaly detection producing alert events with acknowledge / resolve / reopen lifecycle."],
  ["Forecasting & ML", "Model registry and prediction jobs emit stored forecasts tied to their datasets."],
  ["Mapping", "Marker, heat and choropleth-ready layers on OpenStreetMap from real geographic summaries."],
  ["Reporting & export", "Generated reports (CSV/PDF) with row/section limits, downloadable per format."],
  ["Governance", "Administrator / analyst / viewer roles with a server-enforced capability matrix mirrored in the UI."],
];

/** Capability overview with a live (unauthenticated) deployment-readiness block. */
export function CapabilitiesPage() {
  const ready = useApi<ReadyResult>("/ready", []);

  return (
    <div className="space-y-8">
      <Prose>
        <h1>Platform capabilities</h1>
        <p>
          The workspace spans the entire climate-data lifecycle. The capabilities below describe the
          system as built; the readiness panel shows the live status of this deployment.
        </p>
      </Prose>

      <Card>
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-content">Deployment readiness</h2>
          {!ready.loading && ready.data ? (
            <Badge tone={ready.data.status === "ready" ? "success" : "warning"}>
              {ready.data.status}
            </Badge>
          ) : null}
        </div>
        <div className="mt-4 space-y-3 text-sm">
          {ready.loading ? (
            <>
              <Skeleton className="h-4 w-56" />
              <Skeleton className="h-4 w-48" />
            </>
          ) : ready.error ? (
            <p className="text-muted">Readiness could not be loaded: {ready.error.message}</p>
          ) : ready.data ? (
            <>
              <ReadinessRow
                ok={Boolean(ready.data.checks?.database?.ok)}
                label="Database"
                detail={ready.data.checks?.database?.detail ?? "unknown"}
              />
              <ReadinessRow
                ok={Boolean(ready.data.checks?.data_lake?.ok)}
                label="Data lake"
                detail={
                  ready.data.checks?.data_lake?.engine
                    ? `${String(ready.data.checks.data_lake.engine)} engine, writable: ${
                        ready.data.checks.data_lake.writable ? "yes" : "no"
                      }`
                    : "unknown"
                }
              />
              {typeof ready.data.observations === "number" ? (
                <p className="text-muted">
                  {ready.data.observations} observations currently stored.
                </p>
              ) : null}
            </>
          ) : null}
        </div>
        <p className="mt-4 text-xs text-muted">
          Full provider and job detail is available after signing in.{" "}
          <Link to="/login" className="font-medium text-primary hover:underline">
            Sign in
          </Link>
        </p>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2">
        {CAPABILITY_ROWS.map(([title, body]) => (
          <div key={title} className="rounded-card border border-border bg-surface p-4">
            <h3 className="text-sm font-semibold text-content">{title}</h3>
            <p className="mt-1 text-sm text-muted">{body}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function ReadinessRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <StatusDot state={ok ? "ok" : "error"} label={label} />
      <span className="text-muted">{detail}</span>
    </div>
  );
}
