import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Card } from "../../../components/ui/Card";

export function NotFoundPage() {
  return (
    <Card className="mx-auto my-10 max-w-lg text-center">
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-muted">
        <Compass size={22} aria-hidden="true" />
      </span>
      <h1 className="mt-4 text-2xl font-semibold text-content">Page not found</h1>
      <p className="mt-2 text-sm text-muted">
        The page you asked for does not exist. It may have been moved, or its feature belongs to a
        later phase of this project.
      </p>
      <div className="mt-5 flex justify-center gap-3">
        <Link
          to="/"
          className="inline-flex h-10 items-center rounded-lg border border-border px-4 text-sm font-medium text-content hover:bg-surface-2"
        >
          Go home
        </Link>
        <Link
          to="/app"
          className="inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover"
        >
          Open workspace
        </Link>
      </div>
    </Card>
  );
}
