/**
 * Re-export of the auth hook + a short record of the token-storage decision so
 * later phases do not have to re-derive it from client.ts.
 *
 * ACCESS token  → memory only (module variable). Never persisted; a reload
 *                 re-mints it via the refresh endpoint.
 * REFRESH token → localStorage under `earthscape.refresh_token`. This is the
 *                 deliberate trade-off: it lets a page reload restore the
 *                 session silently, at the cost of XSS exposure. Mitigations:
 *                 rotation is single-use (reusing a rotated token ends the
 *                 whole session server-side) and the access token is short
 *                 lived (15 min), so a stolen refresh token is high-value but a
 *                 stolen access token is not persisted anywhere.
 */
export { useAuth } from "./AuthProvider";
export type { AuthContextValue, AuthStatus } from "./AuthProvider";
export type { RegisterInput } from "../../lib/api/auth";
