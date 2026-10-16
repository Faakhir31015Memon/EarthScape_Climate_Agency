import { PageHeader } from "../../../components/ui/PageHeader";
import { Badge } from "../../../components/ui/Badge";
import { DataTable, type Column } from "../../../components/ui/DataTable";
import { useApi } from "../../../lib/api/query";
import { formatInt, formatDateUtc, titleCase } from "../../../lib/format";
import type { Dataset } from "../../../lib/api/types";

const columns: Column<Dataset>[] = [
  { key: "name", header: "Dataset", sortable: true, sortValue: (d) => d.name, render: (d) => <span className="font-medium">{d.name}</span> },
  { key: "source", header: "Source", render: (d) => d.source_name ?? d.source_type },
  { key: "granularity", header: "Granularity", render: (d) => <span className="capitalize">{d.granularity}</span> },
  { key: "status", header: "Status", render: (d) => <Badge tone={d.status === "ready" ? "success" : d.status === "failed" ? "danger" : "neutral"}>{titleCase(d.status)}</Badge> },
  { key: "records", header: "Records", align: "right", sortable: true, sortValue: (d) => d.record_count, render: (d) => formatInt(d.record_count) },
  { key: "rejected", header: "Rejected", align: "right", render: (d) => formatInt(d.rejected_count) },
  { key: "last", header: "Last ingestion", render: (d) => formatDateUtc(d.last_ingestion_at) },
];

export function DatasetsPage() {
  const { data, pagination, loading, error, refetch } = useApi<Dataset[]>("/datasets/", []);

  return (
    <div className="space-y-6">
      <PageHeader title="Datasets" eyebrow="Data" description="Registered climate datasets and their ingestion totals." />
      <DataTable<Dataset>
        columns={columns}
        rows={data ?? []}
        rowKey={(d) => d.id}
        loading={loading}
        error={error}
        onRetry={refetch}
        caption="Datasets"
        pagination={pagination}
        emptyTitle="No datasets registered"
        emptyReason="Ingestion has not created any datasets yet on this deployment."
      />
    </div>
  );
}
