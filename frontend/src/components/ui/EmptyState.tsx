import { cn } from "../../lib/cn";
import { Button } from "./Button";

export interface EmptyStateProps {
  title: string;
  /** The reason, quoted straight from the API when available. */
  reason?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}

/** Honest "no data" panel. Never shows placeholder/demo content. */
export function EmptyState({ title, reason, icon, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-card border border-dashed border-border bg-surface px-6 py-10 text-center",
        className,
      )}
    >
      {icon ? <div className="text-muted">{icon}</div> : null}
      <p className="text-sm font-medium text-content">{title}</p>
      {reason ? <p className="max-w-prose text-sm text-muted">{reason}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export interface ErrorStateProps {
  title?: string;
  message: string;
  /** Validation/permission detail lines from the envelope. */
  details?: string[];
  correlationId?: string | null;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  details,
  correlationId,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "rounded-card border border-danger/40 bg-danger/10 px-5 py-4 text-sm",
        className,
      )}
    >
      <p className="font-semibold text-danger">{title}</p>
      <p className="mt-1 text-content">{message}</p>
      {details && details.length > 0 ? (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-muted">
          {details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onRetry ? (
          <Button size="sm" variant="outline" onClick={onRetry}>
            Retry
          </Button>
        ) : null}
        {correlationId ? (
          <span className="font-mono text-xs text-muted">ref {correlationId}</span>
        ) : null}
      </div>
    </div>
  );
}
