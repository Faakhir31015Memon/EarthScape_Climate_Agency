import { cn } from "../../lib/cn";

export interface SkeletonProps {
  className?: string;
  /** Render as a circle when true. */
  circle?: boolean;
}

export function Skeleton({ className, circle = false }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        "animate-pulse bg-surface-2",
        circle ? "rounded-full" : "rounded-md",
        className,
      )}
    />
  );
}

/** A block skeleton for a chart/card body while data loads. */
export function SkeletonBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-4 w-full" />
      ))}
    </div>
  );
}
