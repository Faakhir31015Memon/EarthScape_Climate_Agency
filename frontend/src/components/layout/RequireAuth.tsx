import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../features/auth/useAuth";
import { Spinner } from "../ui/Spinner";

/** Gate: waits for silent bootstrap, then routes unauthenticated users to login. */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "bootstrapping") {
    return (
      <div className="flex min-h-[50vh] items-center justify-center" role="status" aria-live="polite">
        <Spinner size={28} label="Loading your session" />
        <span className="ml-2 text-sm text-muted">Loading your session…</span>
      </div>
    );
  }

  if (status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}
