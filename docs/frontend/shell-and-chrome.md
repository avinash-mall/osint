# `Shell.tsx` — Chrome (Rail + Topbar + Status Bar)

**Path:** [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx)
**Lines:** ~735

## Purpose

The persistent chrome: 64 px icon rail (expands to 224 px on hover), topbar (current workspace title + cursor readout + status indicators), command palette, and footer status bar. Every workspace renders inside the Shell.

## What it does

- **Rail** lists the five primary workspaces. Active workspace is highlighted; hover expands to show labels.
- **Mobile rail** — below `46rem` (`@media` in `index.css`) hover-to-expand is unavailable, so the rail becomes an off-canvas overlay sheet. A `.shell-menu-btn` hamburger in the topbar toggles `railOpen` state, which adds `.shell-rail.is-open` and slides the aside in over a tap-to-dismiss `.shell-rail-backdrop`. Selecting a workspace or pressing `Esc` closes it. On desktop the hamburger is `display: none` and the rail keeps hover-to-expand.
- **Topbar** shows: active workspace title, lat/lng under cursor (from `MapEventHandlers`), session user, environment-banner (when not production).
- **Status bar** is fed by `/api/health` polling (5 s interval). Shows: backend up/down, inference profile loaded, LLM available, last ingest event.
- **Command palette** (`Ctrl/Cmd-K`) — fuzzy-search across detections, targets, FMV clips. Calls `/api/detections` + `/api/graph` for results.

## Why this design

- **Single chrome means single source of state.** Cursor lat/lng, active workspace, session — all live in Shell so workspaces don't have to rederive.
- **Hover-to-expand rail** keeps screen real estate for the map.
- **Hamburger fallback on touch.** Hover does not fire on touch devices, so below `46rem` the rail is hidden off-canvas and the topbar hamburger drives it — otherwise the collapsed 64 px rail would permanently occlude the workspace with no way to expand it.

## Cross-references

- [app-and-routing.md](app-and-routing.md)
- [auth-hook.md](auth-hook.md)
- [event-stream-hook.md](event-stream-hook.md)
- [atoms.tsx](../../frontend/src/components/atoms.tsx) — `CursorReadout`, `ContainerCard`, primitives used here
