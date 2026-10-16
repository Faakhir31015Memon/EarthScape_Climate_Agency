import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";
import type {
  ApiEnvelope,
  ApiResult,
  User,
} from "./types";

// ----------------------------------------------------------------------------
// Typed error
// ----------------------------------------------------------------------------

/** Normalised failure from any endpoint, envelope error + transport status. */
export class ApiError extends Error {
  readonly isApiError = true;
  readonly code: string;
  readonly status: number | null;
  readonly details: string[];
  readonly correlationId: string | null;

  constructor(init: {
    message: string;
    code?: string;
    status?: number | null;
    details?: string[];
    correlationId?: string | null;
  }) {
    super(init.message);
    this.name = "ApiError";
    this.code = init.code ?? "unknown_error";
    this.status = init.status ?? null;
    this.details = init.details ?? [];
    this.correlationId = init.correlationId ?? null;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { isApiError?: boolean }).isApiError === true
  );
}

/** `details` is string[] for validation failures, `{ detail }` for others. */
function normaliseDetails(details: unknown): string[] {
  if (Array.isArray(details)) {
    return details.map((d) => (typeof d === "string" ? d : JSON.stringify(d)));
  }
  if (details && typeof details === "object") {
    const record = details as Record<string, unknown>;
    return Object.entries(record).map(([key, value]) =>
      typeof value === "string" ? value : `${key}: ${JSON.stringify(value)}`,
    );
  }
  return [];
}

/**
 * Pure envelope unwrap, exported for tests.
 * - Envelope with success:true  → ApiResult { data, meta }.
 * - Envelope with success:false → throws ApiError built from `error`.
 * - Non-enveloped payloads pass through untouched.
 */
export function unwrapEnvelope<T>(
  body: ApiEnvelope<T> | unknown,
  httpStatus: number,
): ApiResult<T> | unknown {
  if (body && typeof body === "object" && "success" in body && "meta" in body) {
    const envelope = body as ApiEnvelope<T>;
    if (envelope.success) {
      return { data: envelope.data as T, meta: envelope.meta };
    }
    throw new ApiError({
      message: envelope.error?.message ?? "Request failed",
      code: envelope.error?.code,
      status: httpStatus,
      details: normaliseDetails(envelope.error?.details),
      correlationId: envelope.meta?.correlation_id ?? null,
    });
  }
  return body;
}

// ----------------------------------------------------------------------------
// Token store — access token in memory, refresh token in localStorage.
//
// Decision: the short-lived JWT access token (15 min) lives only in memory so
// it is never persisted to disk or readable by XSS beyond the live tab. The
// long-lived refresh token is persisted to localStorage so a page reload can
// silently re-mint an access token via the rotation endpoint (see bootstrap).
// ----------------------------------------------------------------------------

const REFRESH_STORAGE_KEY = "earthscape.refresh_token";

let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  try {
    return window.localStorage.getItem(REFRESH_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setTokens(access: string, refresh: string): void {
  accessToken = access;
  try {
    window.localStorage.setItem(REFRESH_STORAGE_KEY, refresh);
  } catch {
    /* storage unavailable (private mode) — session stays in-memory only */
  }
}

export function clearTokens(): void {
  accessToken = null;
  try {
    window.localStorage.removeItem(REFRESH_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

// ----------------------------------------------------------------------------
// Auth event hooks (set by AuthProvider to avoid a circular import)
// ----------------------------------------------------------------------------

interface AuthHooks {
  /** A rotation succeeded — the store can update the cached user. */
  onRotated?: (user: User | undefined) => void;
  /** Auth is definitively gone (refresh failed / no token) — sign the user out. */
  onSignedOut?: () => void;
}

let authHooks: AuthHooks = {};

export function registerAuthHooks(hooks: AuthHooks): void {
  authHooks = hooks;
}

// ----------------------------------------------------------------------------
// Axios instance
// ----------------------------------------------------------------------------

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

const api: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: { "Content-Type": "application/json" },
  timeout: 20000,
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

// Single-flight so parallel 401s trigger exactly one rotation round-trip.
let refreshInFlight: Promise<string | null> | null = null;

async function rotateRefreshToken(): Promise<string | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;
  // Bare axios call: bypasses this instance so a failing refresh never loops.
  try {
    const res = await axios.post<ApiEnvelope<User & Partial<TokenFields>>>(
      `${API_BASE_URL}/auth/refresh`,
      { refresh_token: refreshToken },
      { headers: { "Content-Type": "application/json" }, timeout: 20000 },
    );
    const body = res.data;
    const payload = body?.data as unknown as TokenFields | undefined;
    if (body?.success && payload?.access_token && payload?.refresh_token) {
      // Rotation: the server invalidates the used refresh token, so persist the
      // new pair immediately — reusing the old one terminates the whole session.
      setTokens(payload.access_token, payload.refresh_token);
      authHooks.onRotated?.(payload.user);
      return payload.access_token;
    }
    return null;
  } catch {
    return null;
  }
}

interface TokenFields {
  access_token: string;
  refresh_token: string;
  user?: User;
}

async function rotateOnce(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = rotateRefreshToken().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

api.interceptors.response.use(
  (response) => {
    response.data = unwrapEnvelope(response.data, response.status);
    return response;
  },
  async (error: AxiosError<ApiEnvelope<unknown>>) => {
    const config = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined;
    const status = error.response?.status ?? null;

    // One refresh-then-retry on 401, only if we have not already retried this
    // request and a refresh token exists.
    if (status === 401 && config && !config._retried && getRefreshToken()) {
      config._retried = true;
      const newToken = await rotateOnce();
      if (newToken) {
        config.headers.Authorization = `Bearer ${newToken}`;
        return api.request(config);
      }
      clearTokens();
      authHooks.onSignedOut?.();
    }

    if (status === 401) {
      clearTokens();
      authHooks.onSignedOut?.();
    }

    const envelope = error.response?.data;
    const meta = envelope?.meta;
    throw new ApiError({
      message: envelope?.error?.message ?? error.message ?? "Network error",
      code: envelope?.error?.code,
      status,
      details: normaliseDetails(envelope?.error?.details),
      correlationId: meta?.correlation_id ?? null,
    });
  },
);

// ----------------------------------------------------------------------------
// Typed helpers: everything returns ApiResult<T> (envelope already unwrapped)
// ----------------------------------------------------------------------------

export async function request<T>(
  config: AxiosRequestConfig,
): Promise<ApiResult<T>> {
  const res = await api.request<ApiResult<T>>(config);
  return res.data;
}

export function get<T>(
  path: string,
  params?: Record<string, unknown>,
  config?: AxiosRequestConfig,
): Promise<ApiResult<T>> {
  return request<T>({ ...config, method: "GET", url: path, params });
}

export function post<T>(
  path: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<ApiResult<T>> {
  return request<T>({ ...config, method: "POST", url: path, data: body });
}

export function patch<T>(
  path: string,
  body?: unknown,
  config?: AxiosRequestConfig,
): Promise<ApiResult<T>> {
  return request<T>({ ...config, method: "PATCH", url: path, data: body });
}

export function del<T>(
  path: string,
  config?: AxiosRequestConfig,
): Promise<ApiResult<T>> {
  return request<T>({ ...config, method: "DELETE", url: path });
}

export { api };
