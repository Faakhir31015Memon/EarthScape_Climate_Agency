import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, get, isApiError } from "./client";
import type { ApiMeta, Paginated, Pagination } from "./types";

export interface UseApiOptions {
  params?: Record<string, unknown>;
  /** Skip the request (e.g. wait for a required filter). */
  enabled?: boolean;
  signal?: AbortSignal;
}

export interface UseApiResult<T> {
  data: T | null;
  meta: ApiMeta | null;
  pagination: Pagination | null;
  loading: boolean;
  error: ApiError | null;
  refetch: () => void;
}

/**
 * Fetch `path` (relative to the API base) and unwrap the envelope. Re-runs when
 * `deps` change; the caller supplies the dependency list because the path/params
 * are usually derived from state. Errors surface as `ApiError` (empty otherwise).
 */
export function useApi<T>(
  path: string,
  deps: unknown[] = [],
  options: UseApiOptions = {},
): UseApiResult<T> {
  const { params, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [meta, setMeta] = useState<ApiMeta | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshId, setRefreshId] = useState(0);

  // Stable key so we only refetch when inputs actually change.
  const paramsKey = params ? JSON.stringify(params) : "";
  const depsKey = JSON.stringify(deps);

  const requestSeq = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    get<T>(path, params ? (JSON.parse(paramsKey) as Record<string, unknown>) : undefined)
      .then((res) => {
        if (seq !== requestSeq.current) return; // stale response
        setData(res.data);
        setMeta(res.meta);
        setLoading(false);
      })
      .catch((err: unknown) => {
        if (seq !== requestSeq.current) return;
        setError(isApiError(err) ? err : new ApiError({ message: String(err) }));
        setData(null);
        setLoading(false);
      });
    // depsKey/paramsKey are the intended reactive inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, depsKey, paramsKey, enabled, refreshId]);

  const refetch = useCallback(() => setRefreshId((n) => n + 1), []);

  return {
    data,
    meta,
    pagination: meta?.pagination ?? null,
    loading,
    error,
    refetch,
  };
}

export interface UseMutationResult<TInput, TData> {
  mutate: (input: TInput) => Promise<TData>;
  data: TData | null;
  error: ApiError | null;
  loading: boolean;
  reset: () => void;
}

/** Minimal mutation wrapper: track loading/error, expose reset. */
export function useMutation<TInput, TData>(
  fn: (input: TInput) => Promise<TData>,
): UseMutationResult<TInput, TData> {
  const [data, setData] = useState<TData | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(false);

  const mutate = useCallback(
    async (input: TInput) => {
      setLoading(true);
      setError(null);
      try {
        const result = await fn(input);
        setData(result);
        return result;
      } catch (err) {
        setError(isApiError(err) ? err : new ApiError({ message: String(err) }));
        throw err;
      } finally {
        setLoading(false);
      }
    },
    [fn],
  );

  const reset = useCallback(() => {
    setData(null);
    setError(null);
  }, []);

  return { mutate, data, error, loading, reset };
}

/** Convenience: turn a paginated list response into a `Paginated<T>`. */
export function toPaginated<T>(
  data: T[],
  meta: ApiMeta | null,
): Paginated<T> {
  return { items: data, pagination: meta?.pagination, meta: meta ?? { correlation_id: "" } };
}
