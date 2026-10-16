import { Link } from "react-router-dom";
import { Prose } from "./PublicLayout";
import { Card } from "../../../components/ui/Card";
import { useApi } from "../../../lib/api/query";
import { formatInt } from "../../../lib/format";
import type { ReadyResult } from "../../../lib/api/types";

export function InsightsPage() {
  const ready = useApi<ReadyResult>("/ready", []);

  return (
    <div className="space-y-6">
      <Prose>
        <h1>Insights</h1>
        <p>
          Insights are computed live from stored observations — trends, regional comparisons,
          correlations and anomaly digests. They are not published as static articles here; each one
          is generated on demand by the analytics endpoints and requires a signed-in session with
          the matching capability.
        </p>
        <p>
          The only figure shown on this public page comes from the unauthenticated{" "}
          <code className="rounded bg-surface-2 px-1 font-mono text-xs">/ready</code> endpoint.
        </p>
      </Prose>

      <Card>
        <h2 className="text-base font-semibold text-content">Currently in the store</h2>
        <p className="mt-2 text-sm text-muted">
          {ready.loading
            ? "Loading live status…"
            : typeof ready.data?.observations === "number"
              ? `${formatInt(ready.data.observations)} observations are stored and available for analysis.`
              : "The observation count is not reported by the API right now."}
        </p>
        <Link
          to="/login"
          className="mt-4 inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover"
        >
          Sign in to explore insights
        </Link>
      </Card>
    </div>
  );
}
