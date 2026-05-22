# UX-AUDIT-001 — UX/UI sweep across login, shell, map, FMV, graph, admin

## Decision

A 25-finding UX/UI audit (`#UX-AUDIT-001`) was applied across seven frontend
surfaces plus two backend endpoints. No visual redesign, no new runtime
dependencies — changes follow the existing design tokens and IBM Plex
pairing. New shared primitives, hooks, and one backend router were added.

## Why

Several UI choices implied accreditation/SLA the open-source build cannot
back (the hardcoded `UNCLASSIFIED // FOR OFFICIAL USE ONLY` banner), and
others were leftover scaffolding (theme/density CSS with no toggle, a
one-item profile menu, an overloaded 11-field status bar).

## What shipped

### New shared code
- **[frontend/src/hooks/usePreferences.tsx](../../frontend/src/hooks/usePreferences.tsx)** —
  `PreferencesProvider` + `usePreferences` (theme · density · clock TZ),
  persisted to `localStorage`, re-applies the `<html>` classes (F10/F18).
- **[frontend/src/hooks/useDeploymentMode.ts](../../frontend/src/hooks/useDeploymentMode.ts)** —
  resolves the login banner from `/api/system/deployment-mode` (F1).
- **[frontend/src/components/atoms.tsx](../../frontend/src/components/atoms.tsx)** —
  `SentinelMark` (F2), `BellBadge` (F9), `ConfirmDialog` (F27),
  `KeyboardShortcutSheet` (F21).
- **[frontend/src/components/admin/ModelLoadingView.tsx](../../frontend/src/components/admin/ModelLoadingView.tsx)** —
  new admin tab: load inference profiles + confirm-gated VRAM unload (F27),
  `disabled` models shown as a neutral `NEEDS SETUP` step, not a fault (F28).
- **[backend/routers/system.py](../../backend/routers/system.py)** —
  `GET /api/system/deployment-mode` (`SENTINEL_DEPLOYMENT_MODE`, default
  `demo`; `SENTINEL_DEPLOYMENT_LABEL`, `SENTINEL_AUTH_SUPPORT_CONTACT`).
- `index.css` `/* === UX-AUDIT-001 === */` section; `--warn` shifted to
  `#f0a020` to break the collision with `--nato-unknown` (F17).

### Finding status (25)
- **Applied (22):** F1–F12, F14, F15, F17–F23, F27–F29.
- **Already satisfied (2):** F13 — detection markers already show labels on
  hover, not always-on (the "hover labels" affordance pre-dates this PR);
  F16 — the analytics tool cards already carry text labels, there is no
  icon-only toolbar to fix.
- **Not applied — structure mismatch (3):** F24/F25/F26 target an
  IngestConnect with a failed-jobs table, a combined dropzone+URL field,
  and a flat model-toggle grid. The real `IngestConnect.tsx` is
  Tailwind-based with a single-file dropzone, a sensor-driven pipeline, and
  an ontology object tree — none of those elements exist. They need a
  proper IngestConnect redesign spec, tracked separately.

### Reinterpretations from the PR sketch
- **F22** — the PR sketched SVG `<line>`/`<text>` edges; `GraphExplorer`
  renders on a `react-force-graph-2d` canvas, so predicate labels are drawn
  via `linkCanvasObject` and edges tinted by `predicateColor`.
- **F27/F28** — the PR targeted `admin/Models.tsx` (does not exist) with an
  `Unload` button (does not exist). Built `admin/ModelLoadingView.tsx`
  against the real `/api/inference/{dashboard,load,unload}` endpoints.
- **F8** — full per-segment live state needs cross-workspace plumbing; the
  Shell now derives live counts for `ingest`/`admin` (data it already
  polls) and a live UTC clock for the rest, replacing decorative copy.

## Backwards compatibility

- `SENTINEL_DEPLOYMENT_MODE` defaults to `demo`; production deployments
  must set it to `accredited` + a label to restore a gov/mil banner.
- `WorkspaceKey` enum unchanged (`'map'`); only the `Geoint`→`Map` label
  changed (F7), so routing / command-palette IDs keep working.
- New `localStorage` keys: `shell:railPinned`, `sentinel:theme`,
  `sentinel:density`, `sentinel:clockTz`. No migration.
- Graph link objects gained a `predicate` field alongside `type`.

## Cross-references

- [frontend/shell-and-chrome.md](../frontend/shell-and-chrome.md)
- [frontend/app-and-routing.md](../frontend/app-and-routing.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [frontend/workspace-admin.md](../frontend/workspace-admin.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/map-time-machine.md](../frontend/map-time-machine.md)
- [backend-routers/system-router.md](../backend-routers/system-router.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
