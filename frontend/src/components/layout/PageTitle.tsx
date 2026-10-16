import { useEffect } from "react";
import { cn } from "../../lib/cn";

export interface PageTitleProps {
  /** Page heading text. */
  title: string;
  /** Browser tab title suffix; defaults to "EarthScape". */
  documentTitle?: string;
  description?: string;
  className?: string;
}

/** Sets both the visible <h1> and document.title for the active page. */
export function PageTitle({ title, documentTitle, description, className }: PageTitleProps) {
  useEffect(() => {
    const label = documentTitle ?? title;
    document.title = `${label} · EarthScape Climate Agency`;
  }, [title, documentTitle]);

  return (
    <div className={cn("mb-5", className)}>
      <h1 className="text-2xl font-semibold tracking-tight text-content">{title}</h1>
      {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
    </div>
  );
}
