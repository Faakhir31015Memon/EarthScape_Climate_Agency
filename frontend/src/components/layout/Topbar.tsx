import { LogOut, Menu, User as UserIcon } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Badge, type Tone } from "../ui/Badge";
import { Button } from "../ui/Button";
import { useAuth } from "../../features/auth/useAuth";
import { useApi } from "../../lib/api/query";
import type { UnreadCountResult, UserRole } from "../../lib/api/types";
import { ThemeToggle } from "./ThemeToggle";
import { formatInt } from "../../lib/format";

const ROLE_LABEL: Record<UserRole, string> = {
  administrator: "Administrator",
  climate_analyst: "Climate Analyst",
  viewer: "Viewer",
};

const ROLE_TONE: Record<UserRole, Tone> = {
  administrator: "primary",
  climate_analyst: "info",
  viewer: "neutral",
};

export interface TopbarProps {
  onOpenSidebar: () => void;
}

export function Topbar({ onOpenSidebar }: TopbarProps) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { data } = useApi<UnreadCountResult>("/notifications/unread", [], {
    enabled: Boolean(user),
  });
  const unread = data?.unread ?? 0;

  async function handleSignOut() {
    await logout();
    navigate("/login");
  }

  return (
    <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-surface px-4 py-3">
      <button
        type="button"
        onClick={onOpenSidebar}
        aria-label="Open navigation menu"
        className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-content hover:bg-surface-2 lg:hidden"
      >
        <Menu size={18} />
      </button>

      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        <div
          role="status"
          aria-label={`Notifications${unread ? `, ${unread} unread` : ", none unread"}`}
          className="relative inline-flex h-9 min-w-9 items-center justify-center rounded-lg border border-border px-2 text-content"
        >
          <span aria-hidden="true" className="text-sm font-semibold">
            {formatInt(unread)}
          </span>
          <span className="ml-1 hidden text-xs text-muted sm:inline">unread</span>
          {unread > 0 ? (
            <span
              aria-hidden="true"
              className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-danger"
            />
          ) : null}
        </div>

        <ThemeToggle />

        {user ? (
          <div className="flex items-center gap-2 rounded-lg border border-border px-2 py-1">
            <UserIcon size={16} className="text-muted" aria-hidden="true" />
            <span className="hidden text-sm font-medium text-content sm:inline">
              {user.full_name}
            </span>
            <Badge tone={ROLE_TONE[user.role]}>{ROLE_LABEL[user.role]}</Badge>
          </div>
        ) : null}

        {user ? (
          <Button size="sm" variant="ghost" onClick={handleSignOut} leftIcon={<LogOut size={16} />}>
            <span className="hidden sm:inline">Sign out</span>
          </Button>
        ) : null}
      </div>
    </header>
  );
}
