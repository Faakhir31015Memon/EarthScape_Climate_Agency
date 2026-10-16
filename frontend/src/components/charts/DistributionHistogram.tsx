import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTheme } from "../../lib/theme";
import { seriesColor } from "../../lib/tokens";
import { ChartFrame } from "./ChartFrame";
import { ChartTooltip } from "./ChartTooltip";
import type { ChartPoint, ChartStateProps } from "./types";

export interface DistributionHistogramProps extends ChartStateProps {
  binKey?: string;
  countKey?: string;
  countName?: string;
}

/** Frequency histogram over pre-bucketed data (bins supplied by the caller/API). */
export function DistributionHistogram({
  points,
  loading,
  error,
  emptyReason,
  emptyTitle,
  height = 260,
  binKey = "bin",
  countKey = "count",
  countName = "Frequency",
}: DistributionHistogramProps) {
  useTheme();
  const empty = points.length === 0;

  return (
    <ChartFrame
      loading={loading}
      error={error}
      empty={empty}
      emptyReason={emptyReason}
      emptyTitle={emptyTitle}
      height={height}
    >
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="rgb(var(--c-border))" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={binKey} tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} />
          <YAxis tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} width={48} allowDecimals={false} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgb(var(--c-surface-2))" }} />
          <Bar dataKey={countKey} name={countName} fill={seriesColor(2)} radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}

export type { ChartPoint };
