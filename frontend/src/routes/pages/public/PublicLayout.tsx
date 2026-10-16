import { Outlet } from "react-router-dom";
import { useEffect } from "react";
import { PublicHeader } from "../../../components/layout/PublicHeader";
import { PublicFooter } from "../../../components/layout/PublicFooter";

export interface PublicLayoutProps {
  title?: string;
}

/** Shell for public marketing/informational pages. */
export function PublicLayout({ title }: PublicLayoutProps) {
  useEffect(() => {
    document.title = `${title ?? "EarthScape"} · EarthScape Climate Agency`;
  }, [title]);

  return (
    <div className="flex min-h-screen flex-col bg-bg">
      <PublicHeader />
      <main id="main-content" className="mx-auto w-full max-w-4xl flex-1 px-4 py-12 sm:px-6">
        <Outlet />
      </main>
      <PublicFooter />
    </div>
  );
}

export function Prose({ children }: { children: React.ReactNode }) {
  return (
    <article className="space-y-4 text-sm leading-relaxed text-muted [&_h1]:text-3xl [&_h1]:font-semibold [&_h1]:tracking-tight [&_h1]:text-content [&_h2]:mt-8 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-content [&_p]:text-muted [&_ul]:list-disc [&_ul]:space-y-1 [&_ul]:pl-5 [&_strong]:text-content">
      {children}
    </article>
  );
}
