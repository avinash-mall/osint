# `Shell.tsx` — Chrome (Rail + Topbar + Status Bar)

**Path:** [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx)
**Lines:** ~700

## Purpose

The persistent chrome: 64 px icon rail (expands to 224 px on hover), topbar (current workspace title + cursor readout + status indicators), command palette, and footer status bar. Every workspace renders inside the Shell.

## What it does

- **Rail** lists the five primary workspaces. Active workspace is highlighted; hover expands to show labels.
- **Topbar** shows: active workspace title, lat/lng under cursor (from `MapEventHandlers`), session user, environment-banner (when not production).
- **Status bar** is fed by `/api/health` polling (5 s interval). Shows: backend up/down, inference profile loaded, LLM available, last ingest event.
- **Command palette** (`Ctrl/Cmd-K`) — fuzzy-search across detections, targets, FMV clips. Calls `/api/detections` + `/api/graph` for results.

## Why this design

- **Single chrome means single source of state.** Cursor lat/lng, active workspace, session — all live in Shell so workspaces don't have to rederive.
- **Hover-to-expand rail** keeps screen real estate for the map.

## Cross-references

- [app-and-routing.md](app-and-routing.md)
- [auth-hook.md](auth-hook.md)
- [event-stream-hook.md](event-stream-hook.md)
- [atoms.tsx](../../frontend/src/components/atoms.tsx) — `CursorReadout`, `ContainerCard`, primitives used here
