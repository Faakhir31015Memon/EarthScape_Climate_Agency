import { Link, Outlet } from "react-router-dom";
import { ShieldAlert } from "lucide-react";
import { useAuth } from "../../features/auth/useAuth";
import type { Capability } from "../../lib/api/types";
import { Card } from "../ui/Card";

export interface RequireCapabilityProps {
  capability?: Capability;
  anyOf?: Capability[];
  children?: React.ReactNode;
}

/**
 * Renders children only when the user holds the required capability. Unlike
 * hiding the control, this shows an honest "your role cannot view this" state;
 * the server enforces the same rule and its 403 message is surfaced by callers.
 */
export function RequireCapability({ capability, anyOf, children }: RequireCapabilityProps) {
  const { canAny, user } = useAuth();
  const required = anyOf ?? (capability ? [capability] : []);
  const allowed = required.length === 0 || canAny(required);

  if (!allowed) {
    return (
      <Card className="mx-auto max-w-xl">
        <div className="flex flex-col items-start gap-3">
          <span className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-warning/15 text-warning">
            <ShieldAlert size={20} aria-hidden="true" />
          </span>
          <div>
            <h2 className="text-lg font-semibold text-content">Your role cannot view this</h2>
            <p className="mt-1 text-sm text-muted">
              {user ? `The ${user.role.replace("_", " ")} role is not granted` : "This area is not granted"}{" "}
              {required.map((c) => (
                <code key={c} className="rounded bg-surface-2 px-1 font-mono text-xs">
                  {c}
                </code>
              ))}
              . Access is enforced on the server as well; if you believe this is a mistake, ask an
              administrator.
            </p>
          </div>
          <Link
            to="/app"
            className="mt-1 inline-flex h-10 items-center justify-center rounded-lg border border-primary px-4 text-sm font-medium text-primary hover:bg-primary-soft focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
          >
            Back to dashboard
          </Link>
        </div>
      </Card>
    );
  }

  return <>{children ?? <Outlet />}</>;
}
