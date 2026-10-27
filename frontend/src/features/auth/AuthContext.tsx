import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import api from '../../services/api'
import { ensureAccessToken, onUnauthorized, tokenStore } from '../../services/http'
import type { Account, AuthConfiguration, Role } from '../../types/api'

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous'

export interface AuthState {
  status: AuthStatus
  account: Account | null
  capabilities: string[]
  configuration: AuthConfiguration | null
  error: string | null
  login: (email: string, password: string) => Promise<void>
  register: (payload: { full_name: string; email: string; password: string; organisation?: string }) => Promise<void>
  logout: () => Promise<void>
  reload: () => Promise<void>
  refreshConfiguration: () => Promise<void>
  setAccount: (account: Account) => void
  can: (capability: string) => boolean
  hasRole: (...roles: Role[]) => boolean
}

const AuthContext = createContext<AuthState | null>(null)

const FALLBACK_CONFIGURATION: AuthConfiguration = {
  self_registration_enabled: true,
  registration_roles: ['viewer'],
  password_reset_enabled: false,
  email_delivery: false,
  password_policy: { min_length: 10, requires_uppercase: true, requires_lowercase: true, requires_number: true, requires_symbol: true },
  access_token_minutes: 20,
  refresh_token_days: 7,
}

/**
 * Capabilities are issued by the backend; this map only supports optimistic UI
 * checks for roles the account already holds. Every mutation is still verified
 * server-side, so a wrong client guess can never grant access.
 */
const ROLE_CAPABILITIES: Record<Role, string[]> = {
  administrator: [
    'platform:manage',
    'users:read',
    'users:manage',
    'datasets:read',
    'datasets:manage',
    'datasets:upload',
    'ingestion:read',
    'ingestion:run',
    'processing:read',
    'processing:run',
    'models:train',
    'models:evaluate',
    'predictions:run',
    'alerts:read',
    'alerts:manage',
    'reports:read',
    'reports:manage',
    'support:read',
    'support:manage',
    'feedback:read',
    'feedback:manage',
    'audit:read',
    'system:read',
    'system:manage',
    'analytics:export',
    'climate:read',
    'analytics:read',
  ],
  climate_analyst: [
    'datasets:read',
    'datasets:manage',
    'datasets:upload',
    'ingestion:read',
    'ingestion:run',
    'processing:read',
    'processing:run',
    'models:train',
    'models:evaluate',
    'predictions:run',
    'alerts:read',
    'alerts:manage',
    'reports:read',
    'reports:manage',
    'support:read',
    'feedback:create',
    'analytics:export',
    'analytics:read',
    'system:read',
    'climate:read',
  ],
  viewer: [
    'climate:read',
    'datasets:read',
    'datasets:upload',
    'analytics:read',
    'alerts:read',
    'reports:read',
    'feedback:create',
    'support:read',
  ],
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>('loading')
  const [account, setAccountState] = useState<Account | null>(null)
  const [capabilities, setCapabilities] = useState<string[]>([])
  const [configuration, setConfiguration] = useState<AuthConfiguration | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const clearSession = useCallback(() => {
    tokenStore.clear()
    if (!mounted.current) return
    setAccountState(null)
    setCapabilities([])
    setStatus('anonymous')
  }, [])

  useEffect(() => onUnauthorized(clearSession), [clearSession])

  const loadProfile = useCallback(async () => {
    if (!tokenStore.hydrate() && !tokenStore.access) {
      setStatus('anonymous')
      return
    }
    if (!(await ensureAccessToken())) {
      clearSession()
      return
    }
    try {
      const response = await api.auth.me()
      if (!mounted.current) return
      setAccountState(response.account)
      setCapabilities(response.capabilities)
      setStatus('authenticated')
    } catch {
      clearSession()
    }
  }, [clearSession])

  useEffect(() => {
    void loadProfile()
  }, [loadProfile])

  const refreshConfiguration = useCallback(async () => {
    try {
      const next = await api.auth.configuration()
      if (mounted.current) setConfiguration(next)
    } catch {
      if (mounted.current) setConfiguration(FALLBACK_CONFIGURATION)
    }
  }, [])

  useEffect(() => {
    void refreshConfiguration()
  }, [refreshConfiguration])

  const login = useCallback(async (email: string, password: string) => {
    setError(null)
    try {
      const pair = await api.auth.login(email, password)
      tokenStore.save(pair)
      const response = await api.auth.me()
      if (!mounted.current) return
      setAccountState(response.account)
      setCapabilities(response.capabilities)
      setStatus('authenticated')
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Sign in failed.'
      setError(message)
      throw cause
    }
  }, [])

  const register = useCallback(
    async (payload: { full_name: string; email: string; password: string; organisation?: string }) => {
      setError(null)
      try {
        const pair = await api.auth.register(payload)
        tokenStore.save(pair)
        const response = await api.auth.me()
        if (!mounted.current) return
        setAccountState(response.account)
        setCapabilities(response.capabilities)
        setStatus('authenticated')
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : 'Registration failed.'
        setError(message)
        throw cause
      }
    },
    [],
  )

  const logout = useCallback(async () => {
    const refreshToken = tokenStore.refresh
    try {
      await api.auth.logout(refreshToken)
    } catch {
      /* the token is discarded locally regardless of the server response */
    }
    clearSession()
  }, [clearSession])

  const value = useMemo<AuthState>(
    () => ({
      status,
      account,
      capabilities,
      configuration,
      error,
      login,
      register,
      logout,
      reload: loadProfile,
      refreshConfiguration,
      setAccount: (next: Account) => setAccountState(next),
      can: (capability: string) => {
        if (capabilities.includes('*')) return true
        if (capabilities.includes(capability)) return true
        if (!account) return false
        return (ROLE_CAPABILITIES[account.role] ?? []).includes(capability)
      },
      hasRole: (...roles: Role[]) => Boolean(account && roles.includes(account.role)),
    }),
    [status, account, capabilities, configuration, error, login, register, logout, loadProfile, refreshConfiguration],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}

export { FALLBACK_CONFIGURATION }
