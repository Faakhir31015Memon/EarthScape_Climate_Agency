import axios, {
  AxiosError,
  AxiosHeaders,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'
import type { ApiErrorBody, TokenPair } from '../types/api'

export const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '/api/v1'

const ACCESS_KEY = 'earthscape.access'
const REFRESH_KEY = 'earthscape.refresh'

export class ApiRequestError extends Error {
  readonly code: string
  readonly status: number
  readonly details?: unknown
  readonly requestId?: string

  constructor(message: string, code: string, status: number, details?: unknown, requestId?: string) {
    super(message)
    this.name = 'ApiRequestError'
    this.code = code
    this.status = status
    this.details = details
    this.requestId = requestId
  }
}

/**
 * Access tokens live in memory so a refreshed token is never read from disk twice;
 * the refresh token is persisted so a page reload can silently re-authenticate.
 */
const memory = { access: null as string | null, refresh: null as string | null }

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStored(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    /* private browsing / storage disabled — session-only auth still works */
  }
}

export const tokenStore = {
  get access(): string | null {
    return memory.access
  },
  get refresh(): string | null {
    return memory.refresh ?? readStored(REFRESH_KEY)
  },
  save(pair: TokenPair): void {
    memory.access = pair.access_token
    memory.refresh = pair.refresh_token
    writeStored(REFRESH_KEY, pair.refresh_token)
  },
  hydrate(): boolean {
    memory.refresh = readStored(REFRESH_KEY)
    return Boolean(memory.refresh)
  },
  clear(): void {
    memory.access = null
    memory.refresh = null
    writeStored(ACCESS_KEY, null)
    writeStored(REFRESH_KEY, null)
  },
}

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean; _skipAuth?: boolean }

export const http: AxiosInstance = axios.create({
  baseURL: API_BASE,
  timeout: 45_000,
  headers: { Accept: 'application/json' },
})

http.interceptors.request.use((config) => {
  const retriable = config as RetriableConfig
  if (!retriable._skipAuth && tokenStore.access) {
    const headers = AxiosHeaders.from(retriable.headers)
    headers.set('Authorization', `Bearer ${tokenStore.access}`)
    retriable.headers = headers
  }
  return retriable
})

let refreshFlight: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = tokenStore.refresh
  if (!refreshToken) return null
  try {
    const response = await axios.post<TokenPair>(
      `${API_BASE}/auth/refresh`,
      { refresh_token: refreshToken },
      { headers: { 'Content-Type': 'application/json' } },
    )
    const pair = response.data
    tokenStore.save(pair)
    return pair.access_token
  } catch {
    tokenStore.clear()
    return null
  }
}

function singleFlightRefresh(): Promise<string | null> {
  if (!refreshFlight) {
    refreshFlight = refreshAccessToken().finally(() => {
      refreshFlight = null
    })
  }
  return refreshFlight
}

export type UnauthorizedListener = () => void
const unauthorizedListeners = new Set<UnauthorizedListener>()

export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener)
  return () => unauthorizedListeners.delete(listener)
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorBody>) => {
    const config = error.config as RetriableConfig | undefined
    if (config && error.response?.status === 401 && !config._retried && !config._skipAuth) {
      config._retried = true
      const token = await singleFlightRefresh()
      if (token) {
        config.headers = AxiosHeaders.from(config.headers)
        config.headers.set('Authorization', `Bearer ${token}`)
        return http.request(config)
      }
      unauthorizedListeners.forEach((listener) => listener())
    }
    throw error
  },
)

function describe(error: unknown): ApiRequestError {
  if (error instanceof ApiRequestError) return error
  if (axios.isAxiosError(error)) {
    const body = error.response?.data as ApiErrorBody | undefined
    const status = error.response?.status ?? 0
    if (status === 0 && !error.response) {
      return new ApiRequestError(
        'The platform API is unreachable. Check that the backend service is running.',
        'network_error',
        0,
      )
    }
    return new ApiRequestError(
      body?.error?.message ?? error.message ?? 'The request could not be completed.',
      body?.error?.code ?? 'request_failed',
      status,
      body?.error?.details,
      body?.error?.request_id,
    )
  }
  if (error instanceof Error) return new ApiRequestError(error.message, 'unexpected_error', 0)
  return new ApiRequestError('Unknown error', 'unexpected_error', 0)
}

export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  try {
    const response = await http.request<T>(config)
    return response.data
  } catch (error) {
    throw describe(error)
  }
}

export async function apiGet<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  return request<T>({ url, method: 'GET', params })
}

export async function apiPost<T>(url: string, body?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return request<T>({ url, method: 'POST', data: body, ...config })
}

export async function apiPatch<T>(url: string, body?: unknown): Promise<T> {
  return request<T>({ url, method: 'PATCH', data: body })
}

export async function apiDelete<T>(url: string): Promise<T> {
  return request<T>({ url, method: 'DELETE' })
}

/** Refresh calls must not recurse through the 401 interceptor. */
export function anonymousPost<T>(url: string, body: unknown, skipAuth = true): Promise<T> {
  return request<T>({ url, method: 'POST', data: body, _skipAuth: skipAuth } as AxiosRequestConfig)
}

export function uploadForm<T>(url: string, form: FormData, onProgress?: (percent: number) => void): Promise<T> {
  return request<T>({
    url,
    method: 'POST',
    data: form,
    timeout: 300_000,
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (event) => {
      if (onProgress && event.total) onProgress(Math.round((event.loaded / event.total) * 100))
    },
  })
}

export function downloadUrl(path: string): string {
  const clean = path.startsWith('/') ? path.slice(1) : path
  return `${API_BASE}/${clean}`
}

export const authHeaders = (): Record<string, string> =>
  tokenStore.access ? { Authorization: `Bearer ${tokenStore.access}` } : {}

export async function apiDownload(path: string, filename: string): Promise<void> {
  try {
    const response = await http.get(path, { responseType: 'blob' })
    const url = window.URL.createObjectURL(response.data as Blob)
    const anchor = window.document.createElement('a')
    anchor.href = url
    anchor.download = filename
    window.document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.URL.revokeObjectURL(url)
  } catch (error) {
    throw describe(error)
  }
}
