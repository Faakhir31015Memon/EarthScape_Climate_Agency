import { Link } from "react-router-dom";
import { useEffect } from "react";
import { Leaf } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

export interface AuthShellProps {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  documentTitle: string;
}

export function AuthShell({ title, subtitle, children, footer, documentTitle }: AuthShellProps) {
  useEffect(() => {
    document.title = `${documentTitle} · EarthScape Climate Agency`;
  }, [documentTitle]);

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <header className="flex items-center justify-between px-4 py-4 sm:px-6">
        <Link to="/" className="flex items-center gap-2">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-fg">
            <Leaf size={18} aria-hidden="true" />
          </span>
          <span className="text-sm font-semibold text-content">EarthScape Climate Agency</span>
        </Link>
        <ThemeToggle />
      </header>

      <main id="main-content" className="flex flex-1 items-center justify-center px-4 py-8">
        <div className="w-full max-w-md">
          <div className="card p-6 sm:p-8">
            <h1 className="text-xl font-semibold tracking-tight text-content">{title}</h1>
            {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
            <div className="mt-6">{children}</div>
          </div>
          {footer ? <div className="mt-4 text-center text-sm text-muted">{footer}</div> : null}
        </div>
      </main>
    </div>
  );
}
