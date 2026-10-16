import { useMemo, useState } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Card } from "../../../components/ui/Card";
import { Select, Input } from "../../../components/ui/Input";
import { Button } from "../../../components/ui/Button";
import { Badge, type Tone } from "../../../components/ui/Badge";
import { EmptyState, ErrorState } from "../../../components/ui/EmptyState";
import { DataTable, type Column } from "../../../components/ui/DataTable";
import { useApi } from "../../../lib/api/query";
import { formatDateTimeUtc, formatInt, formatMetric, formatNumber } from "../../../lib/format";
import { variableLabel } from "../../../lib/units";
import type {
  ClimateObservation,
  QualityStatus,
  RegionsResult,
} from "../../../lib/api/types";

const GRANULARITIES = ["hourly", "daily", "monthly", "yearly"];
const QUALITIES: QualityStatus[] = ["valid", "suspect", "rejected"];

const QUALITY_TONE: Record<QualityStatus, Tone> = {
  valid: "success",
  suspect: "warning",
  rejected: "danger",
};

export function ObservationsPage() {
  const [granularity, setGranularity] = useState<string>("daily");
  const [region, setRegion] = useState<string>("");
  const [quality, setQuality] = useState<string>("");
  const [from, setFrom] = useState<string>("");
  const [to, setTo] = useState<string>("");
  const [page, setPage] = useState(1);

  const regions = useApi<RegionsResult>("/climate/regions", []);

  const filters = useMemo(() => {
    const f: Record<string, unknown> = { page, page_size: 25 };
    if (granularity) f.granularity = granularity;
    if (region) f.region = region;
    if (quality) f.quality = quality;
    if (from) f.date_from = from;
    if (to) f.date_to = to;
    return f;
  }, [granularity, region, quality, from, to, page]);

  const hasFilter = Boolean(granularity || region || quality || from || to);

  const { data, pagination, loading, error, refetch } = useApi<ClimateObservation[]>(
    "/climate/observations",
    [JSON.stringify(filters)],
    { params: filters, enabled: hasFilter },
  );

  const columns: Column<ClimateObservation>[] = [
    {
      key: "timestamp",
      header: "Timestamp (UTC)",
      sortable: true,
      sortValue: (r) => r.timestamp,
      render: (r) => <span className="whitespace-nowrap">{formatDateTimeUtc(r.timestamp)}</span>,
    },
    { key: "region", header: "Region", render: (r) => r.location.region },
    { key: "granularity", header: "Granularity", render: (r) => <span className="capitalize">{r.granularity}</span> },
    {
      key: "metrics",
      header: "Metrics",
      render: (r) => (
        <span className="flex flex-wrap gap-1.5">
          {Object.entries(r.metrics).map(([key, m]) => (
            <span key={key} className="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-content">
              {variableLabel(key)} {formatMetric(m.value, m.unit, key.startsWith("temperature") ? 1 : 2)}
            </span>
          ))}
        </span>
      ),
    },
    {
      key: "quality",
      header: "Quality",
      render: (r) => <Badge tone={QUALITY_TONE[r.quality.status]}>{r.quality.status}</Badge>,
    },
    {
      key: "source",
      header: "Source",
      render: (r) => <span className="text-xs text-muted">{r.source.provider}</span>,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Observations"
        eyebrow="Data"
        description="Filter-gated explorer over stored climate observations. At least one filter is required (the API rejects a filter-less call)."
      />

      <Card>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Granularity">
            <Select value={granularity} onChange={(e) => { setGranularity(e.target.value); setPage(1); }}>
              <option value="">Any</option>
              {GRANULARITIES.map((g) => (
                <option key={g} value={g}>{g}</option>
              ))}
            </Select>
          </Field>
          <Field label="Region">
            <Select value={region} onChange={(e) => { setRegion(e.target.value); setPage(1); }} disabled={regions.loading}>
              <option value="">Any</option>
              {(regions.data?.regions ?? []).map((r) => (
                <option key={r.region} value={r.region}>{r.region}</option>
              ))}
            </Select>
          </Field>
          <Field label="Quality">
            <Select value={quality} onChange={(e) => { setQuality(e.target.value); setPage(1); }}>
              <option value="">Any</option>
              {QUALITIES.map((q) => (
                <option key={q} value={q}>{q}</option>
              ))}
            </Select>
          </Field>
          <Field label="From (date)">
            <Input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1); }} />
          </Field>
          <Field label="To (date)">
            <Input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1); }} />
          </Field>
          <div className="flex items-end">
            <Button variant="ghost" onClick={() => { setGranularity("daily"); setRegion(""); setQuality(""); setFrom(""); setTo(""); setPage(1); }}>
              Reset
            </Button>
          </div>
        </div>
      </Card>

      {!hasFilter ? (
        <EmptyState
          title="At least one filter is required"
          reason="The observations endpoint returns a 422 without a filter (dataset_id, region, station, variable, granularity, quality or a date/bbox window)."
        />
      ) : error ? (
        <ErrorState message={error.message} details={error.details} correlationId={error.correlationId} onRetry={refetch} />
      ) : (
        <DataTable<ClimateObservation>
          columns={columns}
          rows={data ?? []}
          rowKey={(r) => r.id}
          loading={loading}
          caption="Climate observations"
          pagination={pagination}
          onPage={setPage}
          emptyTitle="No observations match these filters"
          emptyReason={
            pagination ? `${formatInt(pagination.total)} total across all pages.` : undefined
          }
        />
      )}

      {pagination && pagination.total > 0 ? (
        <p className="text-xs text-muted">
          Page {formatNumber(pagination.page, 0)} of {formatNumber(pagination.total_pages, 0)} ·{" "}
          max page size 100 enforced by the API.
        </p>
      ) : null}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <span className="mb-1 block text-sm font-medium text-content">{label}</span>
      {children}
    </div>
  );
}
