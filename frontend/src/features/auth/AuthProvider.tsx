import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from "react";
import * as authApi from "../../lib/api/auth";
import { ApiError, registerAuthHooks, clearTokens } from "../../lib/api/client";
import { getRefreshToken } from "../../lib/api/client";
import type { Capability, User } from "../../lib/api/types";

export type AuthStatus = "bootstrapping" | "authenticated" | "unauthenticated";

interface AuthState {
  status: AuthStatus;
  user: User | null;
  capabilities: Capability[];
  error: ApiError | null;
}

type Action =
  | { type: "bootstrap-start" }
  | { type: "authenticated"; user: User; capabilities: Capability[] }
  | { type: "user"; user: User }
  | { type: "capabilities"; capabilities: Capability[] }
  | { type: "unauthenticated"; error?: ApiError | null };

function reducer(state: AuthState, action: Action): AuthState {
  switch (action.type) {
    case "bootstrap-start":
      return { ...state, status: "bootstrapping" };
    case "authenticated":
      return {
        status: "authenticated",
        user: action.user,
        capabilities: action.capabilities,
        error: null,
      };
    case "user":
      return { ...state, user: action.user };
    case "capabilities":
      return { ...state, capabilities: action.capabilities };
    case "unauthenticated":
      return {
        status: "unauthenticated",
        user: null,
        capabilities: [],
        error: action.error ?? null,
      };
    default:
      return state;
  }
}

export interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<User>;
  register: (input: authApi.RegisterInput) => Promise<User>;
  logout: () => Promise<void>;
  refreshMe: () => Promise<void>;
  updateProfile: (changes: Parameters<typeof authApi.updateProfile>[0]) => Promise<User>;
  changePassword: (current: string, next: string) => Promise<void>;
  requestPasswordReset: (email: string) => Promise<void>;
  confirmPasswordReset: (token: string, next: string) => Promise<void>;
  /** true when the account still carries its initial password and must rotate it. */
  requiresPasswordChange: boolean;
  can: (capability: Capability) => boolean;
  canAny: (capabilities: Capability[]) => boolean;
  hasRole: (role: User["role"]) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, {
    status: "bootstrapping",
    user: null,
    capabilities: [],
    error: null,
  });

  // Keep a ref so the rotation hook (stable identity) can read the latest status.
  const statusRef = useRef(state.status);
  statusRef.current = state.status;

  useEffect(() => {
    registerAuthHooks({
      onRotated: (user) => {
        if (user) dispatch({ type: "user", user });
      },
      onSignedOut: () => {
        if (statusRef.current === "authenticated") {
          dispatch({ type: "unauthenticated" });
        }
      },
    });
  }, []);

  // Cold boot: if a refresh token survives in localStorage, re-mint silently.
  useEffect(() => {
    let cancelled = false;
    async function run() {
      if (!getRefreshToken()) {
        dispatch({ type: "unauthenticated" });
        return;
      }
      dispatch({ type: "bootstrap-start" });
      try {
        const me = await authApi.bootstrap();
        if (cancelled) return;
        if (me) {
          dispatch({ type: "authenticated", user: me.user, capabilities: me.capabilities });
        } else {
          clearTokens();
          dispatch({ type: "unauthenticated" });
        }
      } catch (err) {
        if (cancelled) return;
        clearTokens();
        dispatch({
          type: "unauthenticated",
          error: err instanceof ApiError ? err : null,
        });
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const { user } = await authApi.login(email, password);
    // The login payload omits capabilities; /auth/me is the source of truth.
    const me = await authApi.fetchMe();
    dispatch({ type: "authenticated", user: me.user, capabilities: me.capabilities });
    return user;
  }, []);

  const register = useCallback(async (input: authApi.RegisterInput) => {
    const user = await authApi.register(input);
    // Registration does not mint a session; sign the new account straight in.
    await authApi.login(input.email, input.password);
    const me = await authApi.fetchMe();
    dispatch({ type: "authenticated", user: me.user, capabilities: me.capabilities });
    return user;
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      clearTokens();
      dispatch({ type: "unauthenticated" });
    }
  }, []);

  const refreshMe = useCallback(async () => {
    const me = await authApi.fetchMe();
    dispatch({ type: "authenticated", user: me.user, capabilities: me.capabilities });
  }, []);

  const updateProfile = useCallback(
    async (changes: Parameters<typeof authApi.updateProfile>[0]) => {
      const user = await authApi.updateProfile(changes);
      dispatch({ type: "user", user });
      return user;
    },
    [],
  );

  const changePassword = useCallback(async (current: string, next: string) => {
    await authApi.changePassword(current, next);
    const me = await authApi.fetchMe();
    dispatch({ type: "authenticated", user: me.user, capabilities: me.capabilities });
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      login,
      register,
      logout,
      refreshMe,
      updateProfile,
      changePassword,
      requestPasswordReset: authApi.requestPasswordReset,
      confirmPasswordReset: authApi.confirmPasswordReset,
      requiresPasswordChange: state.user?.must_change_password ?? false,
      can: (capability) => state.capabilities.includes(capability),
      canAny: (capabilities) => capabilities.some((c) => state.capabilities.includes(c)),
      hasRole: (role) => state.user?.role === role,
    }),
    [state, login, register, logout, refreshMe, updateProfile, changePassword],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
