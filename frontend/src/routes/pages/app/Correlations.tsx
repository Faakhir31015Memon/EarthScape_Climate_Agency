import { useMemo, useState } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { Card, CardHeader } from "../../../components/ui/Card";
import { Select } from "../../../components/ui/Input";
import { ErrorState } from "../../../components/ui/EmptyState";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Badge } from "../../../components/ui/Badge";
import { ScatterCorrelation, type ScatterPoint } from "../../../components/charts";
import { useApi } from "../../../lib/api/query";
import { formatNumber } from "../../../lib/format";
import { variableLabel } from "../../../lib/units";
import type {
  ClimateObservation,
  CorrelationResult,
  VariablesResult,
} from "../../../lib/api/types";

const PAGE_SIZE = 100;

export function CorrelationsPage() {
  const variables = useApi<VariablesResult>("/climate/variables", []);
  const options = variables.data?.variables ?? [];
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const va = a || options[0]?.variable || "";
  const vb = b || options[1]?.variable || options[0]?.variable || "";

  const enabled = Boolean(va && vb && va !== vb);

  const stats = useApi<CorrelationResult>("/climate/correlations", [va, vb], {
    params: { variable_a: va, variable_b: vb },
    enabled,
  });

  // Real scatter points are derived from paired metric values in stored
  // observations — never synthesised.
  const obs = useApi<ClimateObservation[]>("/climate/observations", [va, vb], {
    params: { granularity: "daily", page_size: PAGE_SIZE },
    enabled,
  });

  const points: ScatterPoint[] = useMemo(() => {
    const rows = obs.data ?? [];
    const out: ScatterPoint[] = [];
    for (const row of rows) {
      const xv = row.metrics[va]?.value;
      const yv = row.metrics[vb]?.value;
      if (typeof xv === "number" && typeof yv === "number") out.push({ x: xv, y: yv });
    }
    return out;
  }, [obs.data, va, vb]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Correlations"
        eyebrow="Analytics"
        description="Pairwise correlation computed from stored samples. Association is not causation."
      />

      <Card>
        <div className="grid gap-4 sm:grid-cols-2">
          <VarSelect id="corr-a" label="Variable A" options={options} value={va} loading={variables.loading} onChange={setA} exclude={vb} />
          <VarSelect id="corr-b" label="Variable B" options={options} value={vb} loading={variables.loading} onChange={setB} exclude={va} />
        </div>
        {!enabled ? (
          <p className="mt-3 text-sm text-muted">Choose two different variables to compute a correlation.</p>
        ) : null}
      </Card>

      {enabled ? (
        <div className="grid gap-6 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title={`Scatter · ${variableLabel(va)} vs ${variableLabel(vb)}`} />
            {obs.loading ? (
              <Skeleton className="h-72 w-full" />
            ) : obs.error ? (
              <ErrorState message={obs.error.message} onRetry={obs.refetch} />
            ) : (
              <ScatterCorrelation
                points={points}
                xLabel={variableLabel(va)}
                yLabel={variableLabel(vb)}
                regression={stats.data?.regression}
                loading={stats.loading}
                emptyReason="No observations carry values for both selected variables."
                emptyTitle="No paired samples"
                height={320}
              />
            )}
          </Card>

          <Card>
            <CardHeader title="Statistics" />
            {stats.loading ? (
              <Skeleton className="h-40 w-full" />
            ) : stats.error ? (
              <ErrorState message={stats.error.message} details={stats.error.details} onRetry={stats.refetch} />
            ) : stats.data ? (
              <div className="space-y-3 text-sm">
                <Row label="Method" value={<Badge tone="neutral">{stats.data.method}</Badge>} />
                <Row label="n" value={formatNumber(stats.data.n, 0)} />
                <Row label="Correlation (r)" value={formatNumber(stats.data.correlation ?? null, 3)} />
                <Row
                  label="Strength"
                  value={
                    <Badge tone={strengthTone(stats.data.strength)}>
                      {stats.data.strength} / {stats.data.sign}
                    </Badge>
                  }
                />
                <Row label="Slope" value={formatNumber(stats.data.regression.slope, 4)} />
                <Row label="Intercept" value={formatNumber(stats.data.regression.intercept, 3)} />
                <p className="rounded-lg bg-surface-2 px-3 py-2 text-xs text-muted">{stats.data.note}</p>
              </div>
            ) : null}
          </Card>
        </div>
      ) : null}
    </div>
  );
}

function VarSelect({
  id,
  label,
  options,
  value,
  onChange,
  loading,
  exclude,
}: {
  id: string;
  label: string;
  options: { variable: string; observations: number }[];
  value: string;
  onChange: (v: string) => void;
  loading: boolean;
  exclude: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-medium text-content">
        {label}
      </label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} disabled={loading || options.length === 0}>
        {options.map((o) => (
          <option key={o.variable} value={o.variable} disabled={o.variable === exclude}>
            {variableLabel(o.variable)}
          </option>
        ))}
      </Select>
    </div>
  );
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted">{label}</span>
      <span className="font-medium text-content">{value}</span>
    </div>
  );
}

function strengthTone(strength: string): "success" | "warning" | "neutral" {
  if (strength === "strong" || strength === "moderate") return "warning";
  if (strength === "weak") return "neutral";
  return "neutral";
}
