import { PolarAngleAxis, RadialBar, RadialBarChart, ResponsiveContainer } from "recharts";
import { useTheme } from "../../lib/theme";
import { resolveCssColor, semanticTokens } from "../../lib/tokens";
import { formatNumber } from "../../lib/format";

export interface GaugeStatProps {
  label: string;
  /** Current value; null renders the honest "no value" state. */
  value: number | null;
  min?: number;
  max: number;
  unit?: string;
  fractionDigits?: number;
  height?: number;
}

/** Single-value radial gauge. Colours a success/warning/danger band by position. */
export function GaugeStat({
  label,
  value,
  min = 0,
  max,
  unit,
  fractionDigits = 1,
  height = 160,
}: GaugeStatProps) {
  useTheme();

  if (value === null || Number.isNaN(value)) {
    return (
      <figure className="flex flex-col items-center justify-center rounded-card border border-dashed border-border p-4" style={{ height }}>
        <figcaption className="text-sm font-medium text-content">{label}</figcaption>
        <p className="mt-1 text-sm text-muted">No value reported</p>
      </figure>
    );
  }

  const pct = Math.min(1, Math.max(0, (value - min) / (max - min)));
  const tone =
    pct >= 0.66 ? semanticTokens.danger : pct >= 0.33 ? semanticTokens.warning : semanticTokens.success;
  const fill = resolveCssColor(tone);
  const data = [{ name: label, value, fill }];

  return (
    <figure className="flex flex-col items-center rounded-card border border-border bg-surface p-4">
      <figcaption className="text-sm font-medium text-content">{label}</figcaption>
      <div className="relative w-full" style={{ height: height - 40 }}>
        <ResponsiveContainer width="100%" height="100%">
          <RadialBarChart
            data={data}
            innerRadius="70%"
            outerRadius="100%"
            startAngle={220}
            endAngle={-40}
          >
            <PolarAngleAxis type="number" domain={[min, max]} tick={false} />
            <RadialBar dataKey="value" cornerRadius={6} background={{ fill: "rgb(var(--c-surface-2))" }} isAnimationActive={false} />
          </RadialBarChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-xl font-semibold text-content">
            {formatNumber(value, fractionDigits)}
            {unit ? <span className="ml-1 text-sm text-muted">{unit}</span> : null}
          </span>
        </div>
      </div>
    </figure>
  );
}
