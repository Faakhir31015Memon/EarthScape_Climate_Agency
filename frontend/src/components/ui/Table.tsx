import type { ReactNode, ThHTMLAttributes } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "../../lib/cn";

export type SortDir = "asc" | "desc";

export interface TableProps {
  children: ReactNode;
  caption?: string;
  className?: string;
}

export function Table({ children, caption, className }: TableProps) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)}>
        {caption ? (
          <caption className="sr-only">{caption}</caption>
        ) : null}
        {children}
      </table>
    </div>
  );
}

export function THead({ children }: { children: ReactNode }) {
  return <thead className="text-left">{children}</thead>;
}

export function TBody({ children }: { children: ReactNode }) {
  return <tbody>{children}</tbody>;
}

export function TR({
  children,
  className,
  ...rest
}: React.HTMLAttributes<HTMLTableRowElement>) {
  return (
    <tr
      className={cn("border-b border-border last:border-0 hover:bg-surface-2/60", className)}
      {...rest}
    >
      {children}
    </tr>
  );
}

export function TH({
  children,
  className,
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th
      scope="col"
      className={cn("px-3 py-2 font-semibold text-muted", className)}
      {...rest}
    >
      {children}
    </th>
  );
}

export function TD({
  children,
  className,
  ...rest
}: React.TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("px-3 py-2 text-content", className)} {...rest}>
      {children}
    </td>
  );
}

export interface SortableHeaderProps {
  label: string;
  active: boolean;
  dir: SortDir;
  onSort: () => void;
  className?: string;
}

export function SortableHeader({ label, active, dir, onSort, className }: SortableHeaderProps) {
  return (
    <TH
      className={cn("p-0", className)}
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button
        type="button"
        onClick={onSort}
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left font-semibold hover:text-content focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <span aria-hidden="true">{label}</span>
        {active ? (
          dir === "asc" ? (
            <ArrowUp size={14} aria-hidden="true" />
          ) : (
            <ArrowDown size={14} aria-hidden="true" />
          )
        ) : (
          <ChevronsUpDown size={14} aria-hidden="true" className="opacity-50" />
        )}
      </button>
    </TH>
  );
}
