import { Link, useLocation } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { Card } from "../../../components/ui/Card";
import { useAuth } from "../../../features/auth/useAuth";

export function ForbiddenPage() {
  const { user } = useAuth();
  const location = useLocation();
  const serverMessage = (location.state as { message?: string } | null)?.message;

  return (
    <Card className="mx-auto my-10 max-w-lg">
      <span className="inline-flex h-12 w-12 items-center justify-center rounded-full bg-warning/15 text-warning">
        <ShieldAlert size={22} aria-hidden="true" />
      </span>
      <h1 className="mt-4 text-2xl font-semibold text-content">Access not permitted</h1>
      <p className="mt-2 text-sm text-muted">
        {serverMessage
          ? serverMessage
          : user
            ? `Your ${user.role.replace("_", " ")} role is not granted this capability. Access is enforced by the server.`
            : "You do not have permission to view this area."}
      </p>
      <Link
        to="/app"
        className="mt-5 inline-flex h-10 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-fg hover:bg-primary-hover"
      >
        Back to dashboard
      </Link>
    </Card>
  );
}
