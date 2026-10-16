import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import { useTheme } from "../../lib/theme";
import { seriesColor } from "../../lib/tokens";
import type { ChartPoint } from "./types";

export interface SparklineProps {
  points: ChartPoint[];
  dataKey: string;
  color?: string;
  height?: number;
}

/** Minimal inline trend line. Renders a quiet dash when there is no data. */
export function Sparkline({ points, dataKey, color, height = 40 }: SparklineProps) {
  useTheme();
  if (points.length === 0) {
    return (
      <div style={{ height }} className="flex items-center text-xs text-muted">
        no data
      </div>
    );
  }
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
          <YAxis hide domain={["dataMin", "dataMax"]} />
          <Line
            type="monotone"
            dataKey={dataKey}
            stroke={color ?? seriesColor(0)}
            strokeWidth={1.5}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
