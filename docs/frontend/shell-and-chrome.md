# `Shell.tsx` — Chrome (Rail + Topbar + Status Bar)

**Path:** [frontend/src/components/Shell.tsx](../../frontend/src/components/Shell.tsx)
**Lines:** ~922
**Depends on:** [frontend/src/hooks/useAuth.ts](../../frontend/src/hooks/useAuth.ts), [frontend/src/hooks/usePreferences.tsx](../../frontend/src/hooks/usePreferences.tsx), [frontend/src/components/atoms.tsx](../../frontend/src/components/atoms.tsx), `lucide-react`, `axios`

## Purpose

The persistent chrome: 64 px icon rail (expands to 224 px on hover), topbar (current workspace title + cursor readout + status indicators), command palette, footer status bar. Every workspace renders inside the Shell. `canUseAdmin` filters the Admin rail item, admin command-palette actions, and health-alert bell for analyst sessions.

## What it does

- **Rail** lists the primary workspaces. Admin is included only when `canUseAdmin` is true. Active workspace highlighted; rail expands on hover, on keyboard focus into a nav button, or when **pinned** via the rail-header chevron — persisted to `localStorage['shell:railPinned']` (UX-AUDIT F6). Map workspace labelled `Map` (F7).
- **Topbar bell** carries a `BellBadge` unread-count with crit/warn tone, derived from degraded health + failed uploads (F9), and appears only for admin sessions because it navigates to Admin → Alerts.
- **Analyst menu** exposes theme / density / clock-TZ toggles backed by `usePreferences`, replacing the former one-item dropdown (F10/F18).
- **Status bar** renders the imagery job as a slim `ImageryJobPill` (filename · bar · percent) with a hover popover for the full stage/ETA breakdown (F11); the context line carries live ingest/admin counts instead of decorative copy (F8).
- **Mobile rail** — below `46rem` (`@media` in `index.css`) hover-to-expand is unavailable → rail becomes an off-canvas overlay sheet. A `.shell-menu-btn` hamburger in the topbar toggles `railOpen`, which adds `.shell-rail.is-open` and slides the aside in over a tap-to-dismiss `.shell-rail-backdrop`. Selecting a workspace or `Esc` closes it. On desktop the hamburger is `display: none`; rail keeps hover-to-expand.
- **Topbar** shows: active workspace title, lat/lng under cursor (from `MapEventHandlers`), session user, environment-banner (when not production).
- **Status bar** fed by `/api/health` polling (5 s interval). Shows: backend up/down, inference profile loaded, LLM available, last ingest event.
- **Command palette** (`Ctrl/Cmd-K`) — workspace navigation plus DET-id jumps. Admin and health-alert commands are filtered out for analyst sessions.

## Why this design

- **Single chrome = single source of state** — cursor lat/lng, active workspace, session all live in Shell → workspaces don't rederive.
- **Hover-to-expand rail** keeps screen real estate for the map.
- **Hamburger fallback on touch** — hover doesn't fire on touch devices, so below `46rem` the rail is hidden off-canvas, topbar hamburger drives it; otherwise the collapsed 64 px rail would permanently occlude the workspace with no way to expand.
- **Role-filtered Admin entry points** mirror backend authorization so analysts do not see controls that can only fail with 403.

## Failure modes

- Missing health/upload poll response marks the shell degraded but leaves navigation usable.
- The adaptive health poll (`useSystemStatus`) re-schedules its interval after each awaited tick; `reschedule()` checks the effect's `cancelled` flag first so a tick that resolves after unmount cannot resurrect the interval (it previously leaked a permanent poller per Shell mount).
- If an analyst somehow has `active="admin"`, Shell falls back the title to the first visible workspace and App does not render `AdminScreen`.

## Cross-references

- [app-and-routing.md](app-and-routing.md)
- [auth-hook.md](auth-hook.md)
- [event-stream-hook.md](event-stream-hook.md)
- [atoms.tsx](../../frontend/src/components/atoms.tsx) — `CursorReadout`, `SentinelMark`, `BellBadge`, primitives used here
- [decisions/why-admin-mutators-require-admin.md](../decisions/why-admin-mutators-require-admin.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md) — rail pin, bell badge, analyst-menu preferences, status pill
