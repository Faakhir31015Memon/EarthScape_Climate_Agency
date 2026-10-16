import { useRef, useState } from "react";
import { cn } from "../../lib/cn";

export interface TabItem {
  id: string;
  label: string;
  disabled?: boolean;
}

export interface TabsProps {
  tabs: TabItem[];
  value?: string;
  defaultValue?: string;
  onChange?: (id: string) => void;
  /** Panels are rendered by the consumer; this is the tablist only. */
  className?: string;
  ariaLabel?: string;
}

/** Accessible tablist (manual activation): arrows/Home/End, roving tabindex. */
export function Tabs({
  tabs,
  value,
  defaultValue,
  onChange,
  className,
  ariaLabel,
}: TabsProps) {
  const [internal, setInternal] = useState(defaultValue ?? tabs[0]?.id ?? "");
  const active = value ?? internal;
  const listRef = useRef<HTMLDivElement>(null);

  const enabled = tabs.filter((t) => !t.disabled);

  const select = (id: string) => {
    if (value === undefined) setInternal(id);
    onChange?.(id);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const idx = enabled.findIndex((t) => t.id === active);
    if (idx === -1) return;
    let next = -1;
    if (e.key === "ArrowRight") next = (idx + 1) % enabled.length;
    else if (e.key === "ArrowLeft") next = (idx - 1 + enabled.length) % enabled.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = enabled.length - 1;
    if (next >= 0) {
      e.preventDefault();
      const t = enabled[next];
      if (t) {
        select(t.id);
        listRef.current
          ?.querySelector<HTMLElement>(`[data-tabid="${t.id}"]`)
          ?.focus();
      }
    }
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={ariaLabel}
      aria-orientation="horizontal"
      onKeyDown={onKeyDown}
      className={cn("flex flex-wrap gap-1 border-b border-border", className)}
    >
      {tabs.map((tab) => {
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            data-tabid={tab.id}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            id={`tab-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            disabled={tab.disabled}
            onClick={() => select(tab.id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
              selected
                ? "border-primary text-primary"
                : "border-transparent text-muted hover:text-content",
              tab.disabled && "cursor-not-allowed opacity-50",
            )}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  activeId,
  children,
  className,
}: {
  id: string;
  activeId: string;
  children: React.ReactNode;
  className?: string;
}) {
  if (id !== activeId) return null;
  return (
    <div
      role="tabpanel"
      id={`panel-${id}`}
      aria-labelledby={`tab-${id}`}
      tabIndex={0}
      className={cn("pt-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary", className)}
    >
      {children}
    </div>
  );
}
