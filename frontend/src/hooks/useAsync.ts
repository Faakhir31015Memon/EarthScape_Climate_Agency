import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiRequestError } from '../services/http'

export interface AsyncState<T> {
  data: T | null
  loading: boolean
  refreshing: boolean
  error: ApiRequestError | null
  refetch: () => Promise<void>
  setData: (next: T | ((previous: T | null) => T)) => void
}

/**
 * Runs an API call, keeps the previous data visible while refreshing, and exposes a
 * normalised error. `interval` polls — used by the job monitors so a running pipeline
 * updates without a websocket layer.
 */
export function useAsync<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean; interval?: number } = {},
): AsyncState<T> {
  const enabled = options.enabled !== false
  const [data, setDataState] = useState<T | null>(null)
  const [loading, setLoading] = useState(enabled)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<ApiRequestError | null>(null)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher
  const alive = useRef(true)
  const timer = useRef<number | null>(null)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [])

  const run = useCallback(
    async (isRefresh: boolean) => {
      if (!enabled) return
      if (isRefresh) setRefreshing(true)
      else setLoading(true)
      try {
        const result = await fetcherRef.current()
        if (!alive.current) return
        setDataState(result)
        setError(null)
      } catch (cause) {
        if (!alive.current) return
        setError(cause instanceof ApiRequestError ? cause : new ApiRequestError(String(cause), 'unexpected_error', 0))
      } finally {
        if (alive.current) {
          setLoading(false)
          setRefreshing(false)
        }
      }
    },
    [enabled],
  )

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return
    }
    void run(false)
  }, [enabled, run, ...deps])

  useEffect(() => {
    if (!enabled || !options.interval) return
    let cancelled = false
    const tick = () => {
      if (cancelled) return
      void run(true).then(() => {
        if (!cancelled) timer.current = window.setTimeout(tick, options.interval)
      })
    }
    timer.current = window.setTimeout(tick, options.interval)
    return () => {
      cancelled = true
      if (timer.current) window.clearTimeout(timer.current)
      timer.current = null
    }
  }, [enabled, options.interval, run])

  const setData = useCallback((next: T | ((previous: T | null) => T)) => {
    setDataState((previous) => (typeof next === 'function' ? (next as (p: T | null) => T)(previous) : next))
  }, [])

  return { data, loading, refreshing, error, refetch: () => run(true), setData }
}

export function useDebouncedValue<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const handle = window.setTimeout(() => setDebounced(value), delay)
    return () => window.clearTimeout(handle)
  }, [value, delay])
  return debounced
}
