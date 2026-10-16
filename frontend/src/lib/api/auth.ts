import {
  clearTokens,
  getRefreshToken,
  patch,
  post,
  request,
  setTokens,
} from "./client";
import type {
  LogoutResult,
  MeResult,
  RegistrationStatus,
  TokenPair,
  User,
} from "./types";

export async function login(
  email: string,
  password: string,
): Promise<{ user: User; token: TokenPair }> {
  const res = await post<TokenPair>("/auth/login", { email, password });
  const t = res.data;
  setTokens(t.access_token, t.refresh_token);
  return { user: t.user, token: t };
}

export interface RegisterInput {
  email: string;
  full_name: string;
  password: string;
}

export async function register(input: RegisterInput): Promise<User> {
  // Backend gates this on /auth/registration-status; a disabled deployment
  // returns 403 which the caller surfaces verbatim.
  const res = await post<{ user: User }>("/auth/register", input);
  return res.data.user;
}

/** Silent re-mint using the persisted refresh token (used on cold boot). */
export async function bootstrap(): Promise<MeResult | null> {
  if (!getRefreshToken()) return null;
  const res = await request<MeResult>({ method: "GET", url: "/auth/me" });
  return res.data;
}

export async function fetchMe(): Promise<MeResult> {
  const res = await request<MeResult>({ method: "GET", url: "/auth/me" });
  return res.data;
}

export async function fetchRegistrationStatus(): Promise<RegistrationStatus> {
  const res = await request<RegistrationStatus>({
    method: "GET",
    url: "/auth/registration-status",
  });
  return res.data;
}

export async function updateProfile(
  changes: Partial<Pick<User, "full_name">> & { preferences?: User["preferences"] },
): Promise<User> {
  const res = await patch<{ user: User }>("/auth/profile", changes);
  return res.data.user;
}

export async function changePassword(
  current_password: string,
  new_password: string,
): Promise<void> {
  await post("/auth/password-change", { current_password, new_password });
}

/** Public: request a recovery link. May 503 if email is not configured. */
export async function requestPasswordReset(email: string): Promise<void> {
  await post("/auth/password-reset", { email });
}

export async function confirmPasswordReset(
  token: string,
  new_password: string,
): Promise<void> {
  await post("/auth/password-reset/confirm", { token, new_password });
}

export async function logout(): Promise<LogoutResult> {
  const refresh_token = getRefreshToken();
  let result: LogoutResult = { revoked_sessions: 0 };
  if (refresh_token) {
    const res = await post<LogoutResult>("/auth/logout", { refresh_token });
    result = res.data;
  }
  clearTokens();
  return result;
}
