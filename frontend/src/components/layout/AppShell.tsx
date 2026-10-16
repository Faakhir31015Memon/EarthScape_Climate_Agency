import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Leaf } from "lucide-react";
import { SidebarNav } from "./SidebarNav";
import { Topbar } from "./Topbar";
import { Drawer } from "../ui/Drawer";
import { ChangePasswordDialog } from "../../features/auth/ChangePasswordDialog";
import { useAuth } from "../../features/auth/useAuth";

export function AppShell() {
  const [mobileOpen, setMobileOpen] = useState(false);
  const { requiresPasswordChange } = useAuth();

  return (
    <div className="min-h-screen bg-bg">
      <a href="#main-content" className="skip-link">
        Skip to main content
      </a>

      <div className="flex">
        {/* Persistent sidebar on large screens. */}
        <aside className="hidden w-64 shrink-0 flex-col border-r border-border bg-surface lg:flex">
          <div className="flex h-14 items-center gap-2 border-b border-border px-4">
            <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
              <Leaf size={18} aria-hidden="true" />
            </span>
            <span className="text-sm font-semibold text-content">EarthScape</span>
          </div>
          <div className="flex-1 overflow-y-auto px-2">
            <SidebarNav />
          </div>
        </aside>

        {/* Mobile drawer. */}
        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          title="Navigation"
          side="left"
        >
          <SidebarNav onNavigate={() => setMobileOpen(false)} />
        </Drawer>

        <div className="flex min-w-0 flex-1 flex-col">
          <Topbar onOpenSidebar={() => setMobileOpen(true)} />
          <main id="main-content" className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
            <Outlet />
          </main>
        </div>
      </div>

      {/* Forced rotation: cannot be dismissed until the password is changed. */}
      <ChangePasswordDialog
        open={requiresPasswordChange}
        onClose={() => undefined}
        forced
      />
    </div>
  );
}
