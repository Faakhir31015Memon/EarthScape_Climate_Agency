import { cn } from "../../lib/cn";

export type DotState = "ok" | "warn" | "error" | "idle" | "running";

const DOT: Record<DotState, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  error: "bg-danger",
  idle: "bg-muted",
  running: "bg-info animate-pulse",
};

export interface StatusDotProps {
  state: DotState;
  label: string;
  className?: string;
}

/** A colour dot paired with a mandatory text label (never colour alone). */
export function StatusDot({ state, label, className }: StatusDotProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", className)}>
      <span
        aria-hidden="true"
        className={cn("h-2 w-2 shrink-0 rounded-full", DOT[state])}
      />
      <span className="text-content">{label}</span>
    </span>
  );
}
