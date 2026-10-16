import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { StatusDot, type DotState } from "../../../components/ui/StatusDot";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Tooltip } from "../../../components/ui/Tooltip";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useApi } from "../../../lib/api/query";
import { formatBytes, formatDateUtc, formatDuration, formatInt } from "../../../lib/format";
import type { ServicesStatusResult } from "../../../lib/api/types";

function serviceState(configured: boolean, required: boolean): DotState {
  if (configured) return "ok";
  return required ? "error" : "warn";
}

export function ServicesPage() {
  const { data, loading, error, refetch } = useApi<ServicesStatusResult>("/services/status", []);

  if (error) {
    return (
      <div className="space-y-6">
        <PageHeader title="Services" eyebrow="Operations" />
        <EmptyState title="Service status unavailable" reason={error.message} action={<button onClick={refetch} className="text-primary underline">Retry</button>} />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader title="Services" eyebrow="Operations" description="Backend dependency health, the data lake, ingestion connectors and runtime." />

      {loading || !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : (
        <>
          <Card>
            <CardHeader title="Database" />
            <div className="flex flex-wrap gap-4 text-sm">
              <StatusDot state={data.database.reachable && data.database.ping ? "ok" : "error"} label={data.database.reachable ? "Connected" : "Unreachable"} />
              <span className="text-muted">Database: <strong className="text-content">{data.database.database}</strong></span>
              <span className="text-muted">Version: {data.database.server_version}</span>
              <span className="text-muted">Collections: {formatInt(data.database.collections)}</span>
              <Badge tone={data.database.atlas ? "info" : "neutral"}>{data.database.atlas ? "Atlas" : "self-hosted"}</Badge>
            </div>
          </Card>

          <Card>
            <CardHeader title="Collection counts" />
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
              {Object.entries(data.collection_counts).map(([name, count]) => (
                <div key={name} className="rounded-lg border border-border bg-bg px-3 py-2">
                  <p className="text-xs capitalize text-muted">{name.replace(/_/g, " ")}</p>
                  <p className="text-sm font-semibold text-content">{formatInt(count)}</p>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader title="Services & connectors" subtitle="Hovers show the remediation hint." />
            <ul className="space-y-2 text-sm">
              {data.services.map((s) => (
                <li key={s.name} className="flex items-center justify-between gap-3">
                  <StatusDot
                    state={serviceState(s.configured, s.required)}
                    label={s.name}
                  />
                  <span className="flex items-center gap-2 text-right">
                    {s.mode ? <span className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">{s.mode}</span> : null}
                    <Tooltip content={<span className="max-w-xs">{s.detail} — {s.hint}</span>}>
                      <span className="text-muted underline decoration-dotted">detail</span>
                    </Tooltip>
                  </span>
                </li>
              ))}
            </ul>
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Data lake" />
              <dl className="space-y-2 text-sm">
                <Row label="Engine" value={data.data_lake.engine} />
                <Row label="Writable" value={data.data_lake.writable ? "yes" : "no"} />
                <Row label="Files" value={formatInt(data.data_lake.files)} />
                <Row label="Size" value={formatBytes(data.data_lake.bytes)} />
                <Row label="Free disk" value={`${formatNumber1(data.data_lake.free_disk_gb)} GB`} />
                <div>
                  <dt className="mb-1 text-muted">Layers present</dt>
                  <dd className="flex flex-wrap gap-1.5">
                    {Object.entries(data.data_lake.layers_present).map(([layer, ok]) => (
                      <Badge key={layer} tone={ok ? "success" : "warning"}>{layer}</Badge>
                    ))}
                  </dd>
                </div>
              </dl>
            </Card>

            <Card>
              <CardHeader title="Runtime & sync" />
              <dl className="space-y-2 text-sm">
                <Row label="Python" value={data.runtime.python} />
                <Row label="Platform" value={data.runtime.platform} />
                <Row label="Uptime" value={formatDuration(data.runtime.uptime_seconds)} />
                <Row label="Background engine" value={data.runtime.background_engine} />
                <Row label="Self-registration" value={data.runtime.self_registration ? "open" : "closed"} />
                <Row label="Last sync" value={`${data.last_sync.status}${data.last_sync.source ? ` · ${data.last_sync.source}` : ""}`} />
                <Row label="Last sync finished" value={formatDateUtc(data.last_sync.finished_at)} />
              </dl>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="text-content">{value}</dd>
    </div>
  );
}

function formatNumber1(n: number): string {
  return formatInt(Math.round(n));
}
