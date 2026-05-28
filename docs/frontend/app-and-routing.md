# `frontend/src/App.tsx` + `main.tsx` — Top Level

**Paths:** [frontend/src/App.tsx](../../frontend/src/App.tsx), [frontend/src/main.tsx](../../frontend/src/main.tsx)
**Lines:** [App.tsx](../../frontend/src/App.tsx) ~132; [main.tsx](../../frontend/src/main.tsx) ~11
**Depends on:** [frontend/src/hooks/useAuth.ts](../../frontend/src/hooks/useAuth.ts), [frontend/src/hooks/usePreferences.tsx](../../frontend/src/hooks/usePreferences.tsx), [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx)
**Stack:** React 19, Vite 8, TypeScript, react-leaflet

## Purpose

`main.tsx` renders the React root. `App.tsx` mounts five workspaces under a `PreferencesProvider` → `AuthProvider` pair, threads global state (cursor lat/lng, selected detection, current workspace) across them, and derives `canUseAdmin` from the authenticated session role at [App.tsx#L67-L88](../../frontend/src/App.tsx#L67-L88). `PreferencesProvider` ([hooks/usePreferences.tsx](../../frontend/src/hooks/usePreferences.tsx)) owns theme/density/clock-TZ, applies the `<html>` classes (UX-AUDIT F18). Shell derives its own context line → `App.tsx` no longer passes a static `CONTEXT_LINE` (F8).

## Workspaces

| Workspace | Component file |
|---|---|
| **Ingest** | [IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx) |
| **Map** (key `'map'`) | [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) |
| **Drone Video** | [FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx) |
| **Link Graph** | [GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) |
| **Admin** | [AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx) — rendered only for `role === "admin"` |

## Failure modes

- Unauthenticated boot stays on `LoginScreen` until `/api/auth/me` succeeds.
- Analyst sessions cannot navigate into Admin through the rail, command palette, health bell, or cross-navigation guard.

## Cross-references

- [shell-and-chrome.md](shell-and-chrome.md) — the persistent rail/topbar/status framing every workspace
- [auth-hook.md](auth-hook.md) — `AuthProvider` lives here
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md) — `PreferencesProvider`, workspace rename
- Per-workspace docs: [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md), [workspace-fmv-player.md](workspace-fmv-player.md), [workspace-link-graph.md](workspace-link-graph.md), [workspace-admin.md](workspace-admin.md), [workspace-ingest.md](workspace-ingest.md)
