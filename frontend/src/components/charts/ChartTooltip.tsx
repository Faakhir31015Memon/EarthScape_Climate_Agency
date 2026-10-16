import type { TooltipProps } from "recharts";
import { formatNumber } from "../../lib/format";

type Value = number | string;

/**
 * Custom Recharts tooltip: shows the axis label, then each series value with
 * its API unit. Values are formatted, never invented.
 */
export function ChartTooltip(props: TooltipProps<Value, string> & { labelPrefix?: string }) {
  const { active, payload, label, labelPrefix } = props;
  if (!active || !payload || payload.length === 0) return null;

  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow-pop">
      {label != null ? (
        <p className="mb-1 font-semibold text-content">
          {labelPrefix}
          {String(label)}
        </p>
      ) : null}
      <ul className="space-y-0.5">
        {payload.map((entry, i) => {
          const unit =
            typeof entry.name === "string" && entry.name.includes("·")
              ? entry.name.split("·")[1]?.trim()
              : undefined;
          const display = typeof entry.value === "number" ? formatNumber(entry.value, 2) : String(entry.value);
          return (
            <li key={`${entry.dataKey}-${i}`} className="flex items-center gap-2">
              <span
                aria-hidden="true"
                className="inline-block h-2 w-2 rounded-full"
                style={{ background: entry.color }}
              />
              <span className="text-muted">{entry.name}</span>
              <span className="ml-auto font-medium text-content">
                {display}
                {unit ? ` ${unit}` : ""}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
