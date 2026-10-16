import { useId } from "react";
import { X } from "lucide-react";
import { Overlay } from "./Overlay";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  side?: "right" | "left";
}

export function Drawer({ open, onClose, title, children, side = "right" }: DrawerProps) {
  const titleId = useId();
  return (
    <Overlay
      open={open}
      onClose={onClose}
      labelledBy={titleId}
      panelClassName={
        side === "right"
          ? "absolute right-0 top-0 flex h-full w-[min(26rem,100%)] animate-slide-in flex-col bg-surface shadow-pop"
          : "absolute left-0 top-0 flex h-full w-[min(26rem,100%)] flex-col bg-surface shadow-pop"
      }
    >
      <div className="flex items-center justify-between gap-4 border-b border-border px-5 py-4">
        <h2 id={titleId} className="text-base font-semibold text-content">
          {title}
        </h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="rounded-md p-1 text-muted hover:bg-surface-2 hover:text-content focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
        >
          <X size={18} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-sm">{children}</div>
    </Overlay>
  );
}
