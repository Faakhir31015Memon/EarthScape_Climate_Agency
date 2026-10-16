import { NavLink } from "react-router-dom";
import { CalendarClock } from "lucide-react";
import { NAV_GROUPS } from "./navigation";
import { useAuth } from "../../features/auth/useAuth";
import { cn } from "../../lib/cn";

export interface SidebarNavProps {
  onNavigate?: () => void;
  className?: string;
}

/**
 * Grouped app navigation. Items the user's role cannot access are hidden (the
 * server enforces independently). `planned` items render disabled — the honest
 * state for areas owned by later phases.
 */
export function SidebarNav({ onNavigate, className }: SidebarNavProps) {
  const { can } = useAuth();

  return (
    <nav aria-label="Primary" className={cn("flex flex-col gap-5 py-4", className)}>
      {NAV_GROUPS.map((group) => {
        const visible = group.items.filter(
          (item) => !item.capability || can(item.capability),
        );
        if (visible.length === 0) return null;
        return (
          <div key={group.title}>
            <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted">
              {group.title}
            </p>
            <ul className="space-y-0.5">
              {visible.map((item) => {
                const Icon = item.icon;
                if (item.planned) {
                  return (
                    <li key={item.to}>
                      <span
                        aria-disabled="true"
                        title="Available in a later phase"
                        className="flex cursor-not-allowed items-center gap-3 rounded-lg px-3 py-2 text-sm text-muted/70"
                      >
                        <Icon size={16} />
                        <span className="flex-1 truncate">{item.label}</span>
                        <CalendarClock size={13} aria-hidden="true" />
                      </span>
                    </li>
                  );
                }
                return (
                  <li key={item.to}>
                    <NavLink
                      to={item.to}
                      end={item.to === "/app"}
                      onClick={onNavigate}
                      className={({ isActive }) =>
                        cn(
                          "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
                          isActive
                            ? "bg-primary-soft text-primary"
                            : "text-content hover:bg-surface-2",
                        )
                      }
                    >
                      <Icon size={16} />
                      <span className="truncate">{item.label}</span>
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}
