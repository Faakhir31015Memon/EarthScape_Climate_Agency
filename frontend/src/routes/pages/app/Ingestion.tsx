import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { DataTable, type Column } from "../../../components/ui/DataTable";
import { useApi } from "../../../lib/api/query";
import { formatDateTimeUtc, formatDuration, formatInt, formatNumber } from "../../../lib/format";
import type {
  IngestionJob,
  IngestionProvidersResult,
  IngestionStatusResult,
} from "../../../lib/api/types";

const jobColumns: Column<IngestionJob>[] = [
  { key: "label", header: "Job", render: (j) => j.label },
  { key: "source", header: "Source", render: (j) => <span className="font-mono text-xs">{j.source}</span> },
  {
    key: "status",
    header: "Status",
    render: (j) => (
      <Badge tone={j.status === "completed" ? "success" : j.status === "failed" ? "danger" : "info"}>
        {j.status}
      </Badge>
    ),
  },
  { key: "accepted", header: "Accepted", align: "right", sortable: true, sortValue: (j) => j.records_accepted, render: (j) => formatInt(j.records_accepted) },
  { key: "rejected", header: "Rejected", align: "right", render: (j) => formatInt(j.records_rejected) },
  { key: "dup", header: "Duplicate", align: "right", render: (j) => formatInt(j.records_duplicate) },
  { key: "dur", header: "Duration", align: "right", render: (j) => formatDuration(j.duration_seconds) },
  { key: "started", header: "Started", render: (j) => formatDateTimeUtc(j.started_at) },
];

export function IngestionPage() {
  const status = useApi<IngestionStatusResult>("/ingestion/status", []);
  const providers = useApi<IngestionProvidersResult>("/ingestion/providers", []);
  const jobs = useApi<IngestionJob[]>("/ingestion/jobs", []);

  return (
    <div className="space-y-6">
      <PageHeader title="Ingestion" eyebrow="Processing" description="Provider connectors and recent pull jobs pulling live observations into the lake." />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Metric label="Accepted (total)" value={formatInt(status.data?.accepted_total ?? 0)} loading={status.loading} />
        <Metric label="Rejected (total)" value={formatInt(status.data?.rejected_total ?? 0)} loading={status.loading} />
        <Metric label="Avg duration" value={formatDuration(status.data?.average_duration_seconds ?? null)} loading={status.loading} />
      </div>

      <Card>
        <CardHeader title="Providers" subtitle="Configured source connectors on this deployment." />
        {providers.loading ? (
          <Skeleton className="h-24 w-full" />
        ) : providers.error ? (
          <EmptyState title="Providers unavailable" reason={providers.error.message} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(providers.data?.providers ?? {}).map(([key, p]) => (
              <div key={key} className="rounded-lg border border-border bg-bg p-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-content">{p.display_name ?? key.replace(/_/g, " ")}</span>
                  <Badge tone={p.available ? "success" : "warning"}>{p.available ? "available" : "unavailable"}</Badge>
                </div>
                {p.requires_key && !p.available ? (
                  <p className="mt-1 text-xs text-warning">Needs {p.missing_config}</p>
                ) : null}
                <p className="mt-1 text-xs text-muted">
                  {p.granularities.join(", ")} · {p.variables.length} variables
                </p>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader title="Recent jobs" />
        <DataTable<IngestionJob>
          columns={jobColumns}
          rows={jobs.data ?? []}
          rowKey={(j) => j.id}
          loading={jobs.loading}
          error={jobs.error}
          onRetry={jobs.refetch}
          caption="Ingestion jobs"
          emptyTitle="No ingestion jobs yet"
          emptyReason="Providers are configured but nothing has been pulled into the store."
        />
      </Card>

      <p className="text-xs text-muted">
        Status snapshot accepted {formatInt(status.data?.accepted_total ?? 0)} records ·{" "}
        {formatNumber(Object.keys(status.data?.providers ?? {}).length, 0)} providers reported.
      </p>
    </div>
  );
}

function Metric({ label, value, loading }: { label: string; value: string; loading: boolean }) {
  return (
    <div className="rounded-card border border-border bg-surface p-4">
      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
      {loading ? <Skeleton className="mt-2 h-6 w-16" /> : <p className="mt-1 text-xl font-semibold text-content">{value}</p>}
    </div>
  );
}
