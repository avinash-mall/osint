# Link Graph Workspace — `GraphExplorer.tsx`

**Path:** [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx)
**Lines:** ~1000 (TSX). Companion: [frontend/src/components/graph/TimeScrubber.tsx](../../frontend/src/components/graph/TimeScrubber.tsx) (~120).
**Status:** Phase 1 of the [Link Graph redesign](../architecture/link-graph-redesign.md) shipped — Investigation mode is functional, Evidence and Ontology are stubs that point to Phase 2/3.

## Purpose

Three-mode force-directed view of the Neo4j entity graph for defence analysts. Investigation surfaces operational entities (Target, Asset, Base/LaunchPoint/Facility, Vessel/Aircraft/Vehicle, Unit) and their evidence neighborhood with time and class lenses. Evidence and Ontology are scoped placeholders until Phase 2 and Phase 3 ship.

## Modes (sub-tabs in the panel header)

| Mode | Status | Backed by |
|---|---|---|
| **Investigation** (default) | Phase 1 | [`GET /api/graph/investigation`](../backend-routers/graph-router.md), [`POST /api/graph/path`](../backend-routers/graph-router.md), [`GET /api/graph/site-composition/{base_id}`](../backend-routers/graph-router.md) |
| **Evidence** | Phase 2 stub | placeholder card; right-click "Evidence chain" from Investigation will land here |
| **Ontology** | Phase 3 stub | placeholder card; reuses [OntologyAdmin's](ontology-admin-ui.md) unknown-label form as a popover when shipped |

Sub-tabs share selection state (current node, time range, class lens) — see [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md).

## Investigation mode controls

- **Class-lens chip row** — restrict the feed to one or more node labels (`Target`, `Base`, `Detection`, …). Empty = all operational labels + 1-hop expansion. Server-side filter via `class_lens=…` query params.
- **Time scrubber** ([TimeScrubber.tsx](../../frontend/src/components/graph/TimeScrubber.tsx)) — 30-day default per the plan; presets at 1H / 24H / 7D / 30D; 24-bucket histogram fed from any `node.properties.created_at` in the current payload. Pattern is modelled after [GaiaMap](map-cop-overview.md) but lives in its own component — GaiaMap is untouched.
- **Predicate chip bar** (UX-AUDIT F22) — filter edges by Neo4j relationship type.
- **Candidates toggle** — show/hide pending `CANDIDATE_DETECTED_AS` edges. Default hidden because Phase 1.B persists them as real edges (see [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)).
- **Force-graph** — `react-force-graph-2d`, summarise-and-expand capped at 150 nodes (server enforces). Per-node "Expand Node" still uses `/api/graph/neighborhood`.

## Right-click context menu

- **Search Around** — local 1-hop filter using already-loaded data.
- **Expand Node** — server-side 2-hop fetch via `/api/graph/neighborhood`.
- **Find path to…** — picks a second node from either the canvas or the entity list, calls `/api/graph/path` (max depth 4), renders all shortest paths in the detail panel with predicate trail, and swaps the visible graph for the union of returned paths.
- **Roll up to site** (only on Base/LaunchPoint/Facility) — calls `/api/graph/site-composition/{id}` and renders recent-detections by class + per-asset-kind buckets in the detail panel. FMV clips + reports panels are Phase 2 placeholders.
- **Export Selection** — JSON dump of the selected node or current view.

## Bottom strip

Reads `/api/ontology/updates` (8-item cap). Pending OntologyUpdate cards across the bottom. Unchanged from pre-redesign.

## Why this design

Three modes are the minimum that cover the six analyst workflows the redesign targets — splitting them in one workspace (vs. siblings in the icon rail) keeps the analyst's selection context across mode switches. See [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md).

Investigation mode is intentionally bounded: cap the default payload, expose drill-in via per-node expansion, and rely on the time + class lenses to scope further. Force-graph-2d degrades past ~150 useful nodes; the server-side `/investigation` endpoint enforces this so the client doesn't have to.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — full 4-phase roadmap; this doc is the Phase 1 status snapshot.
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — backing routes.
- [backend-routers/aois-router.md](../backend-routers/aois-router.md) — Base/LaunchPoint/Facility nodes are projected from `aois` tagged with `metadata.aoi_kind`.
- [backend/graph-writes.md](../backend/graph-writes.md) — write helpers (used by the `/promote` endpoint).
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)
- [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
