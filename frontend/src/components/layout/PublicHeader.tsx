import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { Leaf, Menu, X } from "lucide-react";
import { cn } from "../../lib/cn";
import { useAuth } from "../../features/auth/useAuth";
import { ThemeToggle } from "./ThemeToggle";

const PUBLIC_LINKS = [
  { label: "About", to: "/about" },
  { label: "Capabilities", to: "/capabilities" },
  { label: "Insights", to: "/insights" },
  { label: "Help", to: "/help" },
];

export function PublicHeader() {
  const [open, setOpen] = useState(false);
  const { status } = useAuth();
  const signedIn = status === "authenticated";

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface/95 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-4 sm:px-6">
        <Link to="/" className="flex items-center gap-2">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
            <Leaf size={18} aria-hidden="true" />
          </span>
          <span className="text-base font-semibold text-content">EarthScape Climate Agency</span>
        </Link>

        <nav aria-label="Public" className="hidden items-center gap-1 md:flex">
          {PUBLIC_LINKS.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-2 text-sm font-medium text-muted hover:text-content",
                  isActive && "text-content",
                )
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <ThemeToggle />
          {signedIn ? (
            <Link
              to="/app"
              className="inline-flex h-9 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover"
            >
              Open workspace
            </Link>
          ) : (
            <>
              <Link
                to="/login"
                className="inline-flex h-9 items-center rounded-lg border border-border px-4 text-sm font-medium text-content hover:bg-surface-2"
              >
                Sign in
              </Link>
              <Link
                to="/register"
                className="inline-flex h-9 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover"
              >
                Register
              </Link>
            </>
          )}
        </div>

        <div className="flex items-center gap-2 md:hidden">
          <ThemeToggle />
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls="public-mobile-nav"
            aria-label="Toggle navigation"
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-content"
          >
            {open ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </div>

      {open ? (
        <nav
          id="public-mobile-nav"
          aria-label="Public mobile"
          className="border-t border-border bg-surface px-4 py-3 md:hidden"
        >
          <ul className="space-y-1">
            {PUBLIC_LINKS.map((l) => (
              <li key={l.to}>
                <Link
                  to={l.to}
                  onClick={() => setOpen(false)}
                  className="block rounded-md px-3 py-2 text-sm font-medium text-content hover:bg-surface-2"
                >
                  {l.label}
                </Link>
              </li>
            ))}
            <li className="pt-2">
              <Link
                to={signedIn ? "/app" : "/login"}
                onClick={() => setOpen(false)}
                className="block rounded-md bg-primary px-3 py-2 text-center text-sm font-medium text-primary-fg"
              >
                {signedIn ? "Open workspace" : "Sign in"}
              </Link>
            </li>
          </ul>
        </nav>
      ) : null}
    </header>
  );
}
