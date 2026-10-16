import { Link } from "react-router-dom";

const FOOTER_LINKS = [
  { label: "About", to: "/about" },
  { label: "Capabilities", to: "/capabilities" },
  { label: "Insights", to: "/insights" },
  { label: "Help", to: "/help" },
  { label: "Privacy", to: "/privacy" },
  { label: "Terms", to: "/terms" },
];

export function PublicFooter() {
  return (
    <footer className="border-t border-border bg-surface">
      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-8 sm:px-6 md:flex-row md:items-start md:justify-between">
        <div className="max-w-sm">
          <p className="text-sm font-semibold text-content">EarthScape Climate Agency</p>
          <p className="mt-1 text-sm text-muted">
            A university eProject platform for enterprise-scale climate intelligence: observation
            ingestion, trend and anomaly analysis, and forecasting on a MongoDB + Hadoop/Spark data
            lake.
          </p>
        </div>
        <nav aria-label="Footer" className="grid grid-cols-2 gap-x-8 gap-y-2 sm:grid-cols-3">
          {FOOTER_LINKS.map((l) => (
            <Link
              key={l.to}
              to={l.to}
              className="text-sm text-muted hover:text-content focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
            >
              {l.label}
            </Link>
          ))}
        </nav>
      </div>
      <div className="border-t border-border px-4 py-4 text-center text-xs text-muted sm:px-6">
        University eProject demonstration platform. Figures shown are computed from live ingested
        observations.
      </div>
    </footer>
  );
}
