import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTheme } from "../../lib/theme";
import { seriesColor } from "../../lib/tokens";
import { ChartFrame } from "./ChartFrame";
import { ChartTooltip } from "./ChartTooltip";
import type { ChartStateProps } from "./types";

export interface BarChartByRegionProps extends ChartStateProps {
  /** Key holding the categorical region name on each point. */
  categoryKey?: string;
  valueKey?: string;
  valueName?: string;
  unit?: string;
}

/** Horizontal-agnostic categorical bar chart (regions on the x axis). */
export function BarChartByRegion({
  points,
  loading,
  error,
  emptyReason,
  emptyTitle,
  height = 280,
  categoryKey = "region",
  valueKey = "value",
  valueName = "Value",
  unit,
}: BarChartByRegionProps) {
  useTheme();
  const empty = points.length === 0;
  const color = seriesColor(1);
  const seriesName = unit ? `${valueName} · ${unit}` : valueName;

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
          <XAxis
            dataKey={categoryKey}
            tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }}
            interval={0}
            angle={points.length > 6 ? -30 : 0}
            textAnchor={points.length > 6 ? "end" : "middle"}
            height={points.length > 6 ? 50 : 30}
          />
          <YAxis tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} width={48} />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgb(var(--c-surface-2))" }} />
          <Bar dataKey={valueKey} name={seriesName} radius={[4, 4, 0, 0]} maxBarSize={48}>
            {points.map((_, i) => (
              <Cell key={i} fill={color} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
