import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts";
import { useTheme } from "../../lib/theme";
import { seriesColor } from "../../lib/tokens";
import { ChartFrame } from "./ChartFrame";
import type { ChartStateProps } from "./types";

export interface ScatterPoint {
  x: number;
  y: number;
}

export interface ScatterCorrelationProps extends Omit<ChartStateProps, "points"> {
  points: ScatterPoint[];
  xLabel: string;
  yLabel: string;
  /** Optional fitted line y = slope·x + intercept, drawn across the domain. */
  regression?: { slope: number; intercept: number };
}

/** Correlation scatter plot. Empty/null pairs render an honest empty state. */
export function ScatterCorrelation({
  points,
  xLabel,
  yLabel,
  regression,
  loading,
  error,
  emptyReason,
  emptyTitle,
  height = 300,
}: ScatterCorrelationProps) {
  useTheme();
  const empty = points.length === 0;
  const color = seriesColor(0);

  const xs = points.map((p) => p.x);
  const minX = xs.length ? Math.min(...xs) : 0;
  const maxX = xs.length ? Math.max(...xs) : 1;

  const line =
    regression && points.length > 1
      ? [
          { x: minX, y: regression.slope * minX + regression.intercept },
          { x: maxX, y: regression.slope * maxX + regression.intercept },
        ]
      : null;

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
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: -8 }}>
          <CartesianGrid stroke="rgb(var(--c-border))" strokeDasharray="3 3" />
          <XAxis
            type="number"
            dataKey="x"
            name={xLabel}
            tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }}
            label={{ value: xLabel, position: "insideBottom", offset: -2, fontSize: 12, fill: "rgb(var(--c-muted))" }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name={yLabel}
            width={56}
            domain={["auto", "auto"]}
            tick={{ fontSize: 12, fill: "rgb(var(--c-muted))" }}
          />
          <ZAxis range={[40, 40]} />
          <Tooltip
            cursor={{ strokeDasharray: "3 3" }}
            contentStyle={{
              borderRadius: 8,
              border: "1px solid rgb(var(--c-border))",
              background: "rgb(var(--c-surface))",
              fontSize: 12,
            }}
          />
          {line ? (
            <ReferenceLine
              segment={line}
              stroke={color}
              strokeOpacity={0.5}
              strokeDasharray="4 4"
            />
          ) : null}
          <Scatter data={points} fill={color} isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
