# `useAuth.ts` — Auth Context

**Path:** [frontend/src/hooks/useAuth.ts](../../frontend/src/hooks/useAuth.ts)
**Lines:** ~130

## Purpose

`AuthProvider` + `useAuth()` hook. Wraps the app, gates rendering on session boot.

## Behavior

1. On mount, calls `GET /api/auth/me`. 200 → `user` set; 401 → app shows [LoginScreen.tsx](../../frontend/src/components/LoginScreen.tsx).
2. `login(username, password)` → `POST /api/auth/login`; on success refetches `/me`.
3. `logout()` → `POST /api/auth/logout`; clears `user`, shows login screen.
4. Cookie management handled by the browser — `sentinel_session` is `HttpOnly` → JS never sees the token.

## Why this design

- **One source of truth** for `user` + `is_admin` — components read via `useAuth()`, don't refetch themselves.
- **Boot-time `/me` call** — a refresh landing on a deep link still authenticates correctly.

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [LoginScreen.tsx](../../frontend/src/components/LoginScreen.tsx)
