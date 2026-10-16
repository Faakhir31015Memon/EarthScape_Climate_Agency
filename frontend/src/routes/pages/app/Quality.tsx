import { useMemo } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Badge } from "../../../components/ui/Badge";
import { EmptyState } from "../../../components/ui/EmptyState";
import { DataTable, type Column } from "../../../components/ui/DataTable";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useApi } from "../../../lib/api/query";
import { formatInt, formatNumber, formatRatio } from "../../../lib/format";
import { variableLabel } from "../../../lib/units";
import type { QualityDataset, QualityResult } from "../../../lib/api/types";

const columns: Column<QualityDataset>[] = [
  {
    key: "dataset",
    header: "Dataset",
    render: (d) => <span className="font-mono text-xs">{d.dataset_id.slice(-8)}</span>,
  },
  { key: "obs", header: "Observations", align: "right", sortable: true, sortValue: (d) => d.observations, render: (d) => formatInt(d.observations) },
  { key: "valid", header: "Valid", align: "right", render: (d) => formatInt(d.quality.valid ?? 0) },
  { key: "suspect", header: "Suspect", align: "right", render: (d) => formatInt(d.quality.suspect ?? 0) },
  { key: "rejected", header: "Rejected", align: "right", render: (d) => formatInt(d.quality.rejected ?? 0) },
  { key: "dup", header: "Duplicates", align: "right", render: (d) => formatInt(d.duplicates) },
];

export function QualityPage() {
  const { data, loading, error, refetch } = useApi<QualityResult>("/climate/quality", []);

  // Aggregate coverage per variable across all datasets.
  const coverage = useMemo(() => {
    const acc = new Map<string, { present: number; total: number }>();
    for (const ds of data?.datasets ?? []) {
      for (const [variable, cell] of Object.entries(ds.completeness)) {
        const cur = acc.get(variable) ?? { present: 0, total: 0 };
        cur.present += cell.present;
        cur.total += ds.observations;
        acc.set(variable, cur);
      }
    }
    return [...acc.entries()].map(([variable, v]) => ({
      variable,
      coverage: v.total > 0 ? v.present / v.total : 0,
      present: v.present,
    }));
  }, [data]);

  return (
    <div className="space-y-6">
      <PageHeader title="Data quality" eyebrow="Data" description="Validation status, completeness and applied transformations across datasets." />

      {error ? (
        <EmptyState title="Quality unavailable" reason={error.message} action={<button onClick={refetch} className="text-primary underline">Retry</button>} />
      ) : (
        <>
          <div className="flex flex-wrap gap-2">
            {Object.entries(data?.by_status ?? {}).map(([status, count]) => (
              <Badge key={status} tone={status === "valid" ? "success" : status === "rejected" ? "danger" : "warning"}>
                {variableLabel(status)}: {formatInt(count)}
              </Badge>
            ))}
          </div>

          <Card>
            <CardHeader title="Per-dataset quality" />
            <DataTable<QualityDataset>
              columns={columns}
              rows={data?.datasets ?? []}
              rowKey={(d) => d.dataset_id}
              loading={loading}
              caption="Dataset quality"
              emptyTitle="No datasets to assess"
            />
          </Card>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader title="Variable completeness" subtitle="Share of observations carrying each variable." />
              {loading ? (
                <Skeleton className="h-32 w-full" />
              ) : coverage.length === 0 ? (
                <EmptyState title="No completeness data" />
              ) : (
                <ul className="space-y-2 text-sm">
                  {coverage.map((c) => (
                    <li key={c.variable} className="flex items-center justify-between gap-3">
                      <span className="text-content">{variableLabel(c.variable)}</span>
                      <span className="flex items-center gap-2">
                        <span className="text-muted">{formatInt(c.present)} pts</span>
                        <span className="w-24 overflow-hidden rounded-full bg-surface-2">
                          <span className="block h-1.5 bg-primary" style={{ width: `${Math.round(c.coverage * 100)}%` }} />
                        </span>
                        <span className="w-12 text-right text-muted">{formatRatio(c.coverage)}</span>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>

            <Card>
              <CardHeader title="Top transformations applied" subtitle="Unit normalisation recorded by the validator." />
              {loading ? (
                <Skeleton className="h-32 w-full" />
              ) : (data?.transformations_applied.length ?? 0) === 0 ? (
                <EmptyState title="No transformations recorded" />
              ) : (
                <ul className="space-y-1 text-sm">
                  {(data?.transformations_applied ?? []).slice(0, 8).map((t, i) => (
                    <li key={i} className="flex items-center justify-between gap-3">
                      <span className="truncate text-muted">{t.transformation}</span>
                      <span className="text-content">×{formatNumber(t.count, 0)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
