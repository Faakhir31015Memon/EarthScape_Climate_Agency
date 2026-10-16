import { useMemo, useState } from "react";
import type { ApiError } from "../../lib/api/client";
import type { Pagination as PaginationMeta } from "../../lib/api/types";
import { cn } from "../../lib/cn";
import { SkeletonBlock } from "./Skeleton";
import { EmptyState, ErrorState } from "./EmptyState";
import { Pagination } from "./Pagination";
import { TBody, TD, TH, THead, TR, Table, type SortDir } from "./Table";

export interface Column<T> {
  /** Stable id for the column; used as the React key and sort key. */
  key: string;
  header: string;
  sortable?: boolean;
  align?: "left" | "right" | "center";
  /** Cell content. */
  render: (row: T) => React.ReactNode;
  /** Value used for client-side sorting; required when `sortable` and not server-side. */
  sortValue?: (row: T) => string | number;
  headerClassName?: string;
  cellClassName?: string;
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  loading?: boolean;
  error?: ApiError | null;
  onRetry?: () => void;
  /** Honest empty-state content (never placeholder rows). */
  emptyTitle?: string;
  emptyReason?: string;
  caption?: string;
  pagination?: PaginationMeta | null;
  onPage?: (page: number) => void;
  /** Provide `onSortChange` for server-side sorting; omit to sort client-side. */
  onSortChange?: (key: string, dir: SortDir) => void;
  sort?: { key: string; dir: SortDir } | null;
  onRowClick?: (row: T) => void;
  className?: string;
}

const ALIGN: Record<NonNullable<Column<unknown>["align"]>, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  loading = false,
  error = null,
  onRetry,
  emptyTitle = "No records",
  emptyReason,
  caption,
  pagination,
  onPage,
  onSortChange,
  sort: controlledSort,
  onRowClick,
  className,
}: DataTableProps<T>) {
  const [internalSort, setInternalSort] = useState<{ key: string; dir: SortDir } | null>(null);
  const serverSide = Boolean(onSortChange);
  const activeSort = controlledSort ?? internalSort;

  const sortedRows = useMemo(() => {
    if (serverSide) return rows;
    if (!activeSort) return rows;
    const col = columns.find((c) => c.key === activeSort.key);
    if (!col?.sortValue) return rows;
    const dir = activeSort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = col.sortValue?.(a);
      const bv = col.sortValue?.(b);
      if (av === bv) return 0;
      if (av === undefined || av === null) return 1;
      if (bv === undefined || bv === null) return -1;
      return av > bv ? dir : -dir;
    });
  }, [rows, columns, activeSort, serverSide]);

  const handleSort = (key: string) => {
    const nextDir: SortDir =
      activeSort?.key === key && activeSort.dir === "asc" ? "desc" : "asc";
    if (serverSide) {
      onSortChange?.(key, nextDir);
    } else {
      setInternalSort({ key, dir: nextDir });
    }
  };

  if (loading) {
    return (
      <div className={cn("rounded-card border border-border bg-surface p-4", className)}>
        <SkeletonBlock rows={6} />
      </div>
    );
  }

  if (error) {
    return (
      <ErrorState
        message={error.message}
        details={error.details}
        correlationId={error.correlationId}
        onRetry={onRetry}
        className={className}
      />
    );
  }

  if (sortedRows.length === 0) {
    return (
      <EmptyState title={emptyTitle} reason={emptyReason} className={className} />
    );
  }

  return (
    <div className={cn("space-y-3", className)}>
      <div className="rounded-card border border-border bg-surface">
        <Table caption={caption}>
          <THead>
            <TR className="hover:bg-transparent">
              {columns.map((col) =>
                col.sortable ? (
                  <SortableTh
                    key={col.key}
                    label={col.header}
                    align={col.align ?? "left"}
                    active={activeSort?.key === col.key}
                    dir={activeSort?.dir ?? "asc"}
                    onSort={() => handleSort(col.key)}
                    className={col.headerClassName}
                  />
                ) : (
                  <TH
                    key={col.key}
                    className={cn(col.headerClassName, ALIGN[col.align ?? "left"])}
                  >
                    {col.header}
                  </TH>
                ),
              )}
            </TR>
          </THead>
          <TBody>
            {sortedRows.map((row) => {
              const key = rowKey(row);
              return (
                <TR
                  key={key}
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            onRowClick(row);
                          }
                        }
                      : undefined
                  }
                  className={onRowClick ? "cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary" : undefined}
                >
                  {columns.map((col) => (
                    <TD
                      key={col.key}
                      className={cn(col.cellClassName, ALIGN[col.align ?? "left"])}
                    >
                      {col.render(row)}
                    </TD>
                  ))}
                </TR>
              );
            })}
          </TBody>
        </Table>
      </div>
      {pagination && onPage ? (
        <Pagination pagination={pagination} onPage={onPage} />
      ) : null}
    </div>
  );
}

// Local wrapper so the sortable header can honour column alignment.
function SortableTh({
  label,
  active,
  dir,
  onSort,
  align,
  className,
}: {
  label: string;
  active: boolean;
  dir: SortDir;
  onSort: () => void;
  align: NonNullable<Column<unknown>["align"]>;
  className?: string;
}) {
  return (
    <TH
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn("p-0", className)}
    >
      <button
        type="button"
        onClick={onSort}
        className={cn(
          "flex w-full items-center gap-1.5 px-3 py-2 font-semibold hover:text-content focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
          align === "right" ? "justify-end" : align === "center" ? "justify-center" : "justify-start",
        )}
      >
        <span aria-hidden="true">{label}</span>
        <span aria-hidden="true" className="text-muted">
          {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </TH>
  );
}
