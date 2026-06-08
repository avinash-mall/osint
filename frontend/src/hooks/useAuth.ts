/**
 * Authentication context: cookie-backed sessions against /api/auth/*.
 *
 * Boot:       calls GET /api/auth/me; if 401 → user=null and the app should
 *             render <LoginScreen/>; otherwise the session is restored.
 * login():    POST /api/auth/login with credentials; sets the cookie and the
 *             in-memory user.
 * logout():   POST /api/auth/logout, then clears the user.
 *
 * axios is configured with `withCredentials: true` so the cookie travels on
 * every API call automatically. We do this here rather than per-call so
 * downstream code never needs to think about it.
 */

import axios from 'axios';
import {
  createContext,
  createElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from 'react';

const API_URL = (import.meta as any).env?.VITE_API_URL || '';

// Make the cookie travel on cross-origin XHRs in dev too.
axios.defaults.withCredentials = true;

export type Role = 'admin' | 'analyst';

export type AuthUser = {
  username: string;
  display_name: string;
  email: string;
  role: Role;
};

export type AuthState = {
  status: 'loading' | 'authenticated' | 'anonymous';
  user: AuthUser | null;
  error: string | null;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | undefined>(undefined);

type MeResponse = { user: Omit<AuthUser, 'role'>; role: Role };

async function fetchMe(): Promise<AuthUser | null> {
  try {
    const { data } = await axios.get<MeResponse>(`${API_URL}/api/auth/me`);
    return { ...data.user, role: data.role };
  } catch (err: any) {
    if (err?.response?.status === 401) return null;
    throw err;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<AuthState['status']>('loading');
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await fetchMe();
      setUser(me);
      setStatus(me ? 'authenticated' : 'anonymous');
    } catch {
      // A transient network/5xx on the boot session probe shouldn't surface as
      // a hostile "login failed" banner — the user never attempted a login.
      // Drop to the login screen silently; a real login attempt will report its
      // own error.
      setUser(null);
      setStatus('anonymous');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      const { data } = await axios.post<MeResponse>(`${API_URL}/api/auth/login`, {
        username,
        password,
      });
      setUser({ ...data.user, role: data.role });
      setStatus('authenticated');
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || 'login failed';
      setError(String(detail));
      throw err;
    }
  }, []);

  const logout = useCallback(async () => {
    try {
      await axios.post(`${API_URL}/api/auth/logout`);
    } catch {
      // Ignore — even if the server call fails, the local cookie is harmless
      // once we mark the user anonymous and force a fresh login.
    }
    setUser(null);
    setStatus('anonymous');
    setError(null);
  }, []);

  const value = useMemo<AuthState>(
    () => ({ status, user, error, login, logout, refresh }),
    [status, user, error, login, logout, refresh],
  );

  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside <AuthProvider>');
  }
  return ctx;
}
