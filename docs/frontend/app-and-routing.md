# `frontend/src/App.tsx` + `main.tsx` — Top Level

**Paths:** [frontend/src/App.tsx](../../frontend/src/App.tsx), [frontend/src/main.tsx](../../frontend/src/main.tsx)
**Stack:** React 19, Vite 8, TypeScript, react-leaflet

## Purpose

`main.tsx` renders the React root. `App.tsx` mounts five workspaces under an `AuthProvider` and threads global state (cursor lat/lng, selected detection, current workspace) across them.

## Workspaces

| Workspace | Component file |
|---|---|
| **Ingest** | [IngestConnect.tsx](../../frontend/src/components/IngestConnect.tsx) |
| **Geoint (map)** | [GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx) |
| **Drone Video** | [FmvPlayer.tsx](../../frontend/src/components/FmvPlayer.tsx) |
| **Link Graph** | [GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) |
| **Admin** | [AdminScreen.tsx](../../frontend/src/components/AdminScreen.tsx) |

## Cross-references

- [shell-and-chrome.md](shell-and-chrome.md) — the persistent rail/topbar/status that frames every workspace
- [auth-hook.md](auth-hook.md) — `AuthProvider` lives here
- Per-workspace docs: [workspace-geoint-gaiamap.md](workspace-geoint-gaiamap.md), [workspace-fmv-player.md](workspace-fmv-player.md), [workspace-link-graph.md](workspace-link-graph.md), [workspace-admin.md](workspace-admin.md), [workspace-ingest.md](workspace-ingest.md)
