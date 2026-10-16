import { ChevronLeft, ChevronRight } from "lucide-react";
import type { Pagination as PaginationMeta } from "../../lib/api/types";
import { cn } from "../../lib/cn";
import { formatInt } from "../../lib/format";

export interface PaginationProps {
  pagination: PaginationMeta | null;
  onPage: (page: number) => void;
  className?: string;
  /** Optional summary line, e.g. "20 observations". */
  noun?: string;
}

export function Pagination({ pagination, onPage, className, noun }: PaginationProps) {
  if (!pagination || pagination.total_pages <= 1) {
    if (pagination && pagination.total > 0 && noun) {
      return (
        <p className={cn("text-xs text-muted", className)}>
          {formatInt(pagination.total)} {noun}
        </p>
      );
    }
    return null;
  }

  const { page, page_size: size, total, total_pages: pages } = pagination;
  const start = (page - 1) * size + 1;
  const end = Math.min(page * size, total);

  return (
    <nav
      aria-label="Pagination"
      className={cn("flex flex-wrap items-center justify-between gap-3", className)}
    >
      <p className="text-xs text-muted" aria-live="polite">
        Showing {formatInt(start)}–{formatInt(end)} of {formatInt(total)}
        {noun ? ` ${noun}` : ""}
      </p>
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={() => onPage(page - 1)}
          disabled={!pagination.has_previous}
          aria-label="Previous page"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-content hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <ChevronLeft size={16} />
        </button>
        <span className="px-2 text-sm text-muted">
          Page {formatInt(page)} / {formatInt(pages)}
        </span>
        <button
          type="button"
          onClick={() => onPage(page + 1)}
          disabled={!pagination.has_next}
          aria-label="Next page"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-content hover:bg-surface-2 disabled:opacity-40 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </nav>
  );
}
