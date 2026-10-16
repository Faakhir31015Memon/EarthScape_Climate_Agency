import { EmptyState, ErrorState } from "../ui/EmptyState";
import { SkeletonBlock } from "../ui/Skeleton";

export interface ChartFrameProps {
  loading?: boolean;
  error?: string | null;
  empty: boolean;
  emptyReason?: string;
  emptyTitle?: string;
  height: number;
  children: React.ReactNode;
}

/** Shared state shell for every chart: loading / error / empty / content. */
export function ChartFrame({
  loading,
  error,
  empty,
  emptyReason,
  emptyTitle = "No data to chart",
  height,
  children,
}: ChartFrameProps) {
  if (loading) {
    return (
      <div style={{ height }} className="flex items-center justify-center">
        <div className="w-full max-w-sm">
          <SkeletonBlock rows={5} />
        </div>
      </div>
    );
  }

  if (error) {
    return <ErrorState message={error} />;
  }

  if (empty) {
    return (
      <div style={{ minHeight: Math.min(height, 200) }} className="flex items-center">
        <EmptyState className="w-full" title={emptyTitle} reason={emptyReason} />
      </div>
    );
  }

  return <>{children}</>;
}
