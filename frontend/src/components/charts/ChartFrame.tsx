import { EmptyState } from "../ui/EmptyState";
import { ErrorState } from "../ui/ErrorState";
import { ChartSkeleton } from "../ui/Skeleton";

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
    return <ChartSkeleton height={height} />;
  }

  if (error) {
    return <ErrorState error={error} />;
  }

  if (empty) {
    return (
      <div style={{ minHeight: Math.min(height, 200) }} className="flex items-center">
        <EmptyState title={emptyTitle} description={emptyReason} />
      </div>
    );
  }

  return <>{children}</>;
}
