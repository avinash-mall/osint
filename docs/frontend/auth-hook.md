# `useAuth.ts` — Auth Context

**Path:** [frontend/src/hooks/useAuth.ts](../../frontend/src/hooks/useAuth.ts)
**Lines:** ~110

## Purpose

`AuthProvider` + `useAuth()` hook. Wraps the app and gates rendering on session boot.

## Behavior

1. On mount, calls `GET /api/auth/me`. If 200, `user` is set; if 401, the app shows [LoginScreen.tsx](../../frontend/src/components/LoginScreen.tsx).
2. `login(username, password)` calls `POST /api/auth/login`; on success refetches `/me`.
3. `logout()` calls `POST /api/auth/logout`; clears `user` and shows the login screen.
4. Cookie management is handled by the browser — `sentinel_session` is `HttpOnly`, so JS never sees the token directly.

## Why this design

- **One source of truth** for `user` and `is_admin`. Components read via `useAuth()` rather than refetching themselves.
- **Boot-time `/me` call** ensures a refresh that lands on a deep link still authenticates correctly.

## Cross-references

- [backend-routers/auth-router.md](../backend-routers/auth-router.md)
- [backend/auth-and-sessions.md](../backend/auth-and-sessions.md)
- [LoginScreen.tsx](../../frontend/src/components/LoginScreen.tsx)
