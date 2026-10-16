import { useId, useState } from "react";
import { cn } from "../../lib/cn";

export interface TooltipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  side?: "top" | "bottom";
  className?: string;
}

/**
 * Accessible tooltip: shows on hover AND keyboard focus, hidden otherwise,
 * linked via aria-describedby. Purely visual supplement (content is never
 * conveyed only here for essential information).
 */
export function Tooltip({ content, children, side = "top", className }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <span aria-describedby={open ? id : undefined}>{children}</span>
      {open ? (
        <span
          role="tooltip"
          id={id}
          className={cn(
            "absolute left-1/2 z-50 w-max max-w-xs -translate-x-1/2 rounded-md bg-content px-2 py-1 text-xs text-bg shadow-pop",
            side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
            className,
          )}
        >
          {content}
        </span>
      ) : null}
    </span>
  );
}
