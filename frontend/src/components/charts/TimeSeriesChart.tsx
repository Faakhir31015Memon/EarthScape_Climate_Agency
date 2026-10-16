import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useTheme } from "../../lib/theme";
import { seriesColor } from "../../lib/tokens";
import { ChartFrame } from "./ChartFrame";
import { ChartTooltip } from "./ChartTooltip";
import type { ChartPoint, ChartStateProps, SeriesDef } from "./types";

export interface TimeSeriesChartProps extends ChartStateProps {
  series: SeriesDef[];
  variant?: "line" | "area";
  xKey?: string;
  showLegend?: boolean;
}

/** Line/area chart of one or more series over an ordinal x axis. */
export function TimeSeriesChart({
  points,
  series,
  loading,
  error,
  emptyReason,
  emptyTitle,
  height = 280,
  variant = "line",
  xKey = "x",
  showLegend = true,
}: TimeSeriesChartProps) {
  useTheme(); // resubscribes colours on theme change
  const empty = points.length === 0 || series.length === 0;

  const colors = series.map((s, i) => s.color ?? seriesColor(i));
  const withUnits = series.map((s, i) => ({
    ...s,
    name: s.unit ? `${s.name} · ${s.unit}` : s.name,
    color: colors[i],
  }));

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
        {variant === "area" ? (
          <AreaChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
            <CartesianGrid stroke="rgb(var(--c-border))" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey={xKey} tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} minTickGap={16} />
            <YAxis tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} width={48} />
            <Tooltip content={<ChartTooltip />} />
            {showLegend ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
            {withUnits.map((s) => (
              <Area
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.name}
                stroke={s.color}
                fill={s.color}
                fillOpacity={0.18}
                strokeWidth={2}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </AreaChart>
        ) : (
          <LineChart data={points} margin={{ top: 8, right: 16, bottom: 0, left: -8 }}>
            <CartesianGrid stroke="rgb(var(--c-border))" strokeDasharray="3 3" vertical={false} />
            <XAxis dataKey={xKey} tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} minTickGap={16} />
            <YAxis tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }} width={48} />
            <Tooltip content={<ChartTooltip />} />
            {showLegend ? <Legend wrapperStyle={{ fontSize: 12 }} /> : null}
            {withUnits.map((s) => (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                name={s.name}
                stroke={s.color}
                strokeWidth={2}
                dot={points.length <= 40}
                connectNulls
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </ChartFrame>
  );
}

/** Multi-series convenience over the same point shape. */
export function MultiSeriesChart(props: TimeSeriesChartProps) {
  return <TimeSeriesChart {...props} />;
}

export type { ChartPoint, SeriesDef };
