import { useMemo, useState } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Select } from "../../../components/ui/Input";
import { EmptyState, ErrorState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { TimeSeriesChart, type ChartPoint, type SeriesDef } from "../../../components/charts";
import { useApi } from "../../../lib/api/query";
import { formatInt, formatNumber } from "../../../lib/format";
import { variableLabel } from "../../../lib/units";
import type { TrendResult, VariablesResult } from "../../../lib/api/types";

const GRANULARITIES = ["month", "daily", "year"];

export function TrendsPage() {
  const variables = useApi<VariablesResult>("/climate/variables", []);
  const options = variables.data?.variables ?? [];
  const [variable, setVariable] = useState<string>("");
  const [granularity, setGranularity] = useState<string>("month");

  // Default once the variable list arrives.
  const activeVariable = variable || options[0]?.variable || "";
  const enabled = Boolean(activeVariable);

  const trends = useApi<TrendResult>("/climate/trends", [activeVariable, granularity], {
    params: { variable: activeVariable, granularity },
    enabled,
  });

  const points: ChartPoint[] = useMemo(
    () =>
      (trends.data?.series ?? []).map((p) => ({
        x: p.period,
        mean: p.mean,
        minimum: p.minimum,
        maximum: p.maximum,
      })),
    [trends.data],
  );
  const series: SeriesDef[] = [
    { key: "mean", name: "Mean" },
    { key: "minimum", name: "Minimum" },
    { key: "maximum", name: "Maximum" },
  ];

  return (
    <div className="space-y-6">
      <PageHeader title="Trends" eyebrow="Analytics" description="Per-variable trend fitting from stored observations." />

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="trend-variable" className="mb-1 block text-sm font-medium text-content">
              Variable
            </label>
            <Select
              id="trend-variable"
              value={activeVariable}
              onChange={(e) => setVariable(e.target.value)}
              disabled={variables.loading || options.length === 0}
            >
              {variables.loading ? (
                <option>Loading…</option>
              ) : options.length === 0 ? (
                <option value="">No variables available</option>
              ) : (
                options.map((o) => (
                  <option key={o.variable} value={o.variable}>
                    {variableLabel(o.variable)} ({formatInt(o.observations)} obs)
                  </option>
                ))
              )}
            </Select>
          </div>
          <div>
            <label htmlFor="trend-gran" className="mb-1 block text-sm font-medium text-content">
              Granularity
            </label>
            <Select
              id="trend-gran"
              value={granularity}
              onChange={(e) => setGranularity(e.target.value)}
            >
              {GRANULARITIES.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </Select>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title={`Trend · ${variableLabel(activeVariable)}`} />
        {trends.loading ? (
          <Skeleton className="h-64 w-full" />
        ) : trends.error ? (
          <ErrorState message={trends.error.message} details={trends.error.details} onRetry={trends.refetch} />
        ) : !trends.data ? (
          <EmptyState title="No selection" />
        ) : !trends.data.available ? (
          <EmptyState title="No trend data" reason={trends.data.reason ?? undefined} />
        ) : (
          <div className="space-y-3">
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Stat label="Sample size" value={formatInt(trends.data.sample_size ?? 0)} />
              <Stat label="Mean" value={formatNumber(trends.data.mean ?? null, 2)} />
              <Stat label="Slope" value={formatNumber(trends.data.slope ?? null, 4)} />
              <Stat
                label="Period"
                value={
                  trends.data.period_covered
                    ? `${trends.data.period_covered.start} → ${trends.data.period_covered.end}`
                    : "—"
                }
              />
            </dl>
            <TimeSeriesChart
              points={points}
              series={series}
              emptyReason={trends.data.reason ?? undefined}
              height={300}
            />
          </div>
        )}
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-border bg-bg px-3 py-2">
      <dt className="text-xs uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-0.5 text-sm font-semibold text-content">{value}</dd>
    </div>
  );
}
