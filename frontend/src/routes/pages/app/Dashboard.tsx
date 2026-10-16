import { useMemo } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { EmptyState, ErrorState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Badge } from "../../../components/ui/Badge";
import { StatusDot } from "../../../components/ui/StatusDot";
import { TimeSeriesChart, BarChartByRegion } from "../../../components/charts";
import type { ChartPoint, SeriesDef } from "../../../components/charts";
import { useApi } from "../../../lib/api/query";
import { formatInt, formatNumber, formatRatio, formatDateUtc } from "../../../lib/format";
import { variableLabel } from "../../../lib/units";
import type { DashboardSummary } from "../../../lib/api/types";

export function DashboardPage() {
  const { data, loading, error, refetch } = useApi<DashboardSummary>("/dashboard/summary", []);

  if (error) {
    return (
      <div className="space-y-6">
        <PageHeader title="Dashboard" eyebrow="Overview" />
        <ErrorState message={error.message} details={error.details} correlationId={error.correlationId} onRetry={refetch} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        eyebrow="Overview"
        description="Live platform state assembled from the dashboard summary endpoint."
      />

      {/* Counts */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatCard label="Datasets" value={data?.counts.datasets} loading={loading} />
        <StatCard label="Observations" value={data?.counts.observations} loading={loading} />
        <StatCard label="Ingestion success" value={data?.ingestion.success} loading={loading} tone="success" />
        <StatCard label="Ingestion failed" value={data?.ingestion.failed} loading={loading} tone={data?.ingestion.failed ? "danger" : "neutral"} />
        <StatCard label="Alert events" value={data?.counts.alert_events} loading={loading} />
      </div>

      {/* Trend */}
      <Card>
        <CardHeader title="Precipitation trend" subtitle="Monthly mean/minimum/maximum from stored observations." />
        <TrendSection data={data} loading={loading} />
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Observations by region" subtitle="Real regional totals (choropleth-ready)." />
          <RegionSection data={data} loading={loading} />
        </Card>
        <Card>
          <CardHeader title="Data quality" subtitle="Completeness by variable across the store." />
          <QualitySection data={data} loading={loading} />
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader title="Pipeline status" subtitle="Batch jobs (Spark / MapReduce / threaded)." />
          <PipelineSection data={data} loading={loading} />
        </Card>
        <Card>
          <CardHeader title="Anomalies & forecasts" subtitle="Only populated when the analytics/ML jobs have run." />
          <div className="space-y-4">
            {data?.anomalies.available === false ? (
              <EmptyState title="No anomalies" reason={data.anomalies.reason ?? undefined} />
            ) : (
              <p className="text-sm text-muted">Anomaly detail is available from the alerts area.</p>
            )}
            {data?.forecasts.available === false ? (
              <EmptyState title="No forecasts" reason={data.forecasts.reason ?? undefined} />
            ) : (
              <p className="text-sm text-muted">Forecast detail is available from the models area.</p>
            )}
          </div>
        </Card>
      </div>

      <Card>
        <CardHeader title="Last successful sync" />
        <SyncSection data={data} loading={loading} />
      </Card>
    </div>
  );
}

function TrendSection({ data, loading }: { data: DashboardSummary | null; loading: boolean }) {
  const trend = data?.trends?.precipitation;
  const points: ChartPoint[] = useMemo(
    () =>
      (trend?.series ?? []).map((p) => ({
        x: p.period,
        mean: p.mean,
        minimum: p.minimum,
        maximum: p.maximum,
      })),
    [trend],
  );
  const series: SeriesDef[] = [
    { key: "mean", name: "Mean" },
    { key: "minimum", name: "Minimum" },
    { key: "maximum", name: "Maximum" },
  ];

  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!trend) return <EmptyState title="Trend unavailable" />;
  if (!trend.available)
    return <EmptyState title="No trend data" reason={trend.reason ?? undefined} />;

  return (
    <div>
      <p className="mb-2 text-xs text-muted">
        Bucket: {trend.bucket} · sample {formatInt(trend.sample_size ?? 0)} ·{" "}
        {trend.period_covered
          ? `${trend.period_covered.start} → ${trend.period_covered.end}`
          : "period unknown"}
      </p>
      <TimeSeriesChart points={points} series={series} variant="area" height={260} />
    </div>
  );
}

function RegionSection({ data, loading }: { data: DashboardSummary | null; loading: boolean }) {
  const geo = data?.geographic;
  const points: ChartPoint[] = useMemo(
    () => (geo?.regions ?? []).map((r) => ({ region: r.region, value: r.observations })),
    [geo],
  );
  if (loading) return <Skeleton className="h-56 w-full" />;
  if (!geo || !geo.available || geo.regions.length === 0)
    return <EmptyState title="No geographic data" reason={geo?.reason ?? undefined} />;
  return <BarChartByRegion points={points} valueName="Observations" height={260} />;
}

function QualitySection({ data, loading }: { data: DashboardSummary | null; loading: boolean }) {
  if (loading) return <Skeleton className="h-40 w-full" />;
  const byVar = data?.data_quality?.completeness?.by_variable;
  if (!byVar) return <EmptyState title="Quality unavailable" />;
  const rows = Object.entries(byVar);
  return (
    <ul className="space-y-2 text-sm">
      {rows.map(([variable, cell]) => (
        <li key={variable} className="flex items-center justify-between gap-3">
          <span className="text-content">{variableLabel(variable)}</span>
          <span className="flex items-center gap-3">
            <span className="text-muted">{formatInt(cell.present)} pts</span>
            <Badge tone={cell.coverage >= 0.99 ? "success" : cell.coverage >= 0.5 ? "warning" : "neutral"}>
              {formatRatio(cell.coverage)} coverage
            </Badge>
          </span>
        </li>
      ))}
    </ul>
  );
}

function PipelineSection({ data, loading }: { data: DashboardSummary | null; loading: boolean }) {
  if (loading) return <Skeleton className="h-40 w-full" />;
  const pipeline = data?.pipeline;
  if (!pipeline || !pipeline.available || pipeline.recent_jobs.length === 0)
    return <EmptyState title="No pipeline activity" reason={pipeline?.reason ?? undefined} />;
  return (
    <ul className="space-y-3 text-sm">
      {pipeline.recent_jobs.slice(0, 5).map((job) => (
        <li key={job.id} className="flex items-center justify-between gap-3">
          <StatusDot
            state={job.status === "completed" ? "ok" : job.status === "failed" ? "error" : "running"}
            label={job.label}
          />
          <span className="text-xs text-muted">
            {formatDateUtc(job.finished_at ?? job.created_at)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function SyncSection({ data, loading }: { data: DashboardSummary | null; loading: boolean }) {
  if (loading) return <Skeleton className="h-8 w-64" />;
  const sync = data?.ingestion?.last_successful_sync;
  if (!sync || !sync.available)
    return <EmptyState title="No successful sync recorded yet" reason={sync?.reason} />;
  return (
    <p className="text-sm text-content">
      {sync.source ? <Badge tone="info">{sync.source}</Badge> : null}{" "}
      <span className="ml-2">
        Completed {formatDateUtc(sync.completed_at)} ·{" "}
        {formatNumber(sync.records_accepted ?? 0, 0)} records accepted
      </span>
    </p>
  );
}

function StatCard({
  label,
  value,
  loading,
  tone = "neutral",
}: {
  label: string;
  value?: number;
  loading?: boolean;
  tone?: "neutral" | "success" | "danger";
}) {
  const color = tone === "success" ? "text-success" : tone === "danger" ? "text-danger" : "text-content";
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <p className="text-xs font-medium uppercase tracking-wide text-muted">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-7 w-12" />
      ) : (
        <p className={`mt-1 text-2xl font-semibold ${color}`}>
          {typeof value === "number" ? formatInt(value) : "—"}
        </p>
      )}
    </div>
  );
}
