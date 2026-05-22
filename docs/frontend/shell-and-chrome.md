# `Shell.tsx` — Chrome (Rail + Topbar + Status Bar)

**Path:** [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx)
**Lines:** ~830

## Purpose

The persistent chrome: 64 px icon rail (expands to 224 px on hover), topbar (current workspace title + cursor readout + status indicators), command palette, footer status bar. Every workspace renders inside the Shell.

## What it does

- **Rail** lists the five primary workspaces. Active workspace highlighted; rail expands on hover, on keyboard focus into a nav button, or when **pinned** via the rail-header chevron — persisted to `localStorage['shell:railPinned']` (UX-AUDIT F6). Map workspace labelled `Map` (F7).
- **Topbar bell** carries a `BellBadge` unread-count with crit/warn tone, derived from degraded health + failed uploads (F9).
- **Analyst menu** exposes theme / density / clock-TZ toggles backed by `usePreferences`, replacing the former one-item dropdown (F10/F18).
- **Status bar** renders the imagery job as a slim `ImageryJobPill` (filename · bar · percent) with a hover popover for the full stage/ETA breakdown (F11); the context line carries live ingest/admin counts instead of decorative copy (F8).
- **Mobile rail** — below `46rem` (`@media` in `index.css`) hover-to-expand is unavailable → rail becomes an off-canvas overlay sheet. A `.shell-menu-btn` hamburger in the topbar toggles `railOpen`, which adds `.shell-rail.is-open` and slides the aside in over a tap-to-dismiss `.shell-rail-backdrop`. Selecting a workspace or `Esc` closes it. On desktop the hamburger is `display: none`; rail keeps hover-to-expand.
- **Topbar** shows: active workspace title, lat/lng under cursor (from `MapEventHandlers`), session user, environment-banner (when not production).
- **Status bar** fed by `/api/health` polling (5 s interval). Shows: backend up/down, inference profile loaded, LLM available, last ingest event.
- **Command palette** (`Ctrl/Cmd-K`) — fuzzy-search across detections, targets, FMV clips. Calls `/api/detections` + `/api/graph`.

## Why this design

- **Single chrome = single source of state** — cursor lat/lng, active workspace, session all live in Shell → workspaces don't rederive.
- **Hover-to-expand rail** keeps screen real estate for the map.
- **Hamburger fallback on touch** — hover doesn't fire on touch devices, so below `46rem` the rail is hidden off-canvas, topbar hamburger drives it; otherwise the collapsed 64 px rail would permanently occlude the workspace with no way to expand.

## Cross-references

- [app-and-routing.md](app-and-routing.md)
- [auth-hook.md](auth-hook.md)
- [event-stream-hook.md](event-stream-hook.md)
- [atoms.tsx](../../frontend/src/components/atoms.tsx) — `CursorReadout`, `SentinelMark`, `BellBadge`, primitives used here
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md) — rail pin, bell badge, analyst-menu preferences, status pill
