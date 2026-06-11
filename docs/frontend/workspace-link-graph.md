# Link Graph Workspace — `GraphExplorer.tsx`

**Path:** [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx)
**Lines:** ~1530 (TSX). Companion: [frontend/src/components/graph/TimeScrubber.tsx](../../frontend/src/components/graph/TimeScrubber.tsx) (~120).
**Status:** Phases 1–5 of the [Link Graph redesign](../architecture/link-graph-redesign.md) shipped — all three modes (Investigation, Evidence, Ontology) are functional, each backed by a live endpoint.

## Purpose

Three-mode force-directed view of the Neo4j entity graph for defence analysts. Investigation surfaces operational entities (Target, Asset, Base/LaunchPoint/Facility, Vessel/Aircraft/Vehicle, Unit) and their evidence neighborhood with time and class lenses. Evidence renders a column-DAG chain of evidence per node; Ontology renders the branch/object tree with UnknownLabel orbits. Each mode owns its own fetch/render path — `fetchData` only populates the Investigation force-graph feed.

## Modes (sub-tabs in the panel header)

| Mode | Status | Backed by |
|---|---|---|
| **Investigation** (default) | Phase 1 | [`GET /api/graph/investigation`](../backend-routers/graph-router.md), [`POST /api/graph/path`](../backend-routers/graph-router.md), [`GET /api/graph/site-composition/{base_id}`](../backend-routers/graph-router.md) |
| **Evidence** | Phase 2 | `EvidenceColumnDAG` ([frontend/src/components/graph/EvidenceColumnDAG.tsx](../../frontend/src/components/graph/EvidenceColumnDAG.tsx)) fed by `/api/graph/evidence/{node_id}`. Right-click "Evidence chain" on any node in Investigation triggers it. Contradict button on Detection/OntologyCandidate leaves POSTs `/api/graph/contradict`. |
| **Ontology** | Phase 3 | `OntologyOrbit` ([frontend/src/components/graph/OntologyOrbit.tsx](../../frontend/src/components/graph/OntologyOrbit.tsx)) fed by `/api/graph/ontology`. Renders the branch/object tree + UnknownLabel orbits + their LABEL_OF supports. Clicking an UnknownLabel opens an inline triage popover (assign-to-existing or create-new), calling the same `assignUnknownLabel` API used by [OntologyAdmin](ontology-admin-ui.md). The OntologyAdmin list view stays as the bulk-edit surface. |

Sub-tabs share selection state (current node, time range, class lens) — see [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md).

## Investigation mode controls

- **Class-lens chip row** — restrict the feed to one or more node labels (`Target`, `Base`, `Detection`, …). Empty = all operational labels + 1-hop expansion. Server-side filter via `class_lens=…` query params.
- **Time scrubber** ([TimeScrubber.tsx](../../frontend/src/components/graph/TimeScrubber.tsx)) — 30-day default per the plan; presets at 1H / 24H / 7D / 30D; 24-bucket histogram fed from any `node.properties.created_at` in the current payload. Pattern is modelled after [GaiaMap](map-cop-overview.md) but lives in its own component — GaiaMap is untouched.
- **Predicate chip bar** (UX-AUDIT F22) — filter edges by Neo4j relationship type.
- **Candidates toggle** — show/hide pending `CANDIDATE_DETECTED_AS` edges. Default hidden because Phase 1.B persists them as real edges (see [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)).
- **Force-graph** — `react-force-graph-2d`, summarise-and-expand capped at 150 nodes (server enforces). Per-node "Expand Node" still uses `/api/graph/neighborhood`.
- **Co-occurrence histogram** (detail panel, when a node is selected) — 8-bucket temporal histogram of the selected node's linked neighbours by their `created_at` across the active time window. Derived from the loaded graph payload, no extra request; renders "No time-stamped links in window" when none of the neighbours carry timestamps. (Replaced the earlier seed-hash placeholder bars.)

## Right-click context menu

- **Search Around** — local 1-hop filter using already-loaded data.
- **Expand Node** — server-side 2-hop fetch via `/api/graph/neighborhood`.
- **Find path to…** — picks a second node from either the canvas or the entity list, calls `/api/graph/path` (max depth 4), renders all shortest paths in the detail panel with predicate trail, and swaps the visible graph for the union of returned paths.
- **Roll up to site** (only on Base/LaunchPoint/Facility) — calls `/api/graph/site-composition/{id}` and renders recent-detections by class, per-asset-kind buckets (vessels/vehicles/aircraft/other), **FMV clips** intersecting the AOI footprint, and **reports** linked to anchored entities — all in the detail panel (FMV + reports populated by the Phase 5.L backend buckets).
- **Evidence chain** — switches to Evidence mode focused on this node; fetches `/api/graph/evidence/{id}` and renders the column-DAG.
- **Export Selection** — JSON dump of the selected node or current view.

## Bottom strip

Reads `/api/ontology/updates` (8-item cap). Pending OntologyUpdate cards across the bottom. Unchanged from pre-redesign.

## Phase 6 additions (graph-analytics surface)

Investigation mode consumes the three new graph-analytics endpoints (see [decisions/why-link-graph-uses-graph-analytics.md](../decisions/why-link-graph-uses-graph-analytics.md)):

- **A — Metrics card + centrality node-sizing.** On load (and on the Candidates toggle), `GET /api/graph/metrics?limit=1500&top_k=50` populates the left-top "Graph metrics" card (density, component count, largest component, compute backend) plus a clickable Top-central list (by PageRank — each row selects + focuses the node). The force-graph `nodeCanvasObject` scales each node's radius by its PageRank centrality (up to +3.5 px), so hubs/brokers stand out. Scores key by Neo4j `elementId`, joining cleanly to the Investigation feed; nodes outside the top-50 just render at base size.
- **B — Co-location lens + auto-surfaced edges.** `COLOCATED_WITH` is added to the default-hidden predicate set, so the persisted proximity edges (from `worker.tick_colocation_builder`) appear as an opt-in chip rather than cluttering the view. The **Co-loc** toolbar button calls `GET /api/graph/colocation` (kNN, window derived from the time scrubber) and renders the live proximity graph of recent detections as a filtered lens — detections are keyed in the PostGIS id space (`det-<id>` synthetic nodes), so the lens replaces the canvas rather than merging into the Neo4j feed. A banner reports method/node/edge counts with an "Exit lens" action.
- **C — GNN suggested links (status-gated).** `GET /api/graph/gnn/status` is probed once on mount; the **Suggest links** button is disabled with an explanatory tooltip when the GNN runtime (torch) is absent — the same honest-capability pattern as the map's DEM/OSRM chip. When ready, it POSTs `/api/graph/gnn/suggest-links` and overlays the top predicted operational pairs as dashed advisory edges (predicate `GNN_SUGGESTED_LINK`, also default-hidden until invoked), with a "Suggested links" detail panel listing each pair + score (click to locate the source) and a clear-overlay control.

## Why this design

Three modes are the minimum that cover the six analyst workflows the redesign targets — splitting them in one workspace (vs. siblings in the icon rail) keeps the analyst's selection context across mode switches. See [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md).

Investigation mode is intentionally bounded: cap the default payload, expose drill-in via per-node expansion, and rely on the time + class lenses to scope further. Force-graph-2d degrades past ~150 useful nodes; the server-side `/investigation` endpoint enforces this so the client doesn't have to.

## Phase 5 additions (deferred-items roll-up)

- **Canvas cluster collapse** — same-class neighbour groups of ≥12 collapse into a virtual `:Cluster` node with a count badge. Clicking expands. Reduces visual noise when NEAR materialisation pushes density up. See [GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) `graphData` useMemo.
- **OntologyOrbit co-occurrence chips** — per-OntologyObject horizontal bars of the top-K co-classifying objects, fed by `/api/graph/ontology?include_cooccurrence=true`. Replaces the synthetic-bar placeholder.
- **SAME_AS review sub-panel** in the AdminScreen "Operational entities" tab — side-by-side cards for pending `POSSIBLY_SAME_AS` pairs with Approve / Merge / Reject. Merge opens a modal with per-column radio resolutions; submit calls the merge-into endpoint.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — full 5-phase roadmap; this doc covers the workspace surface in particular.
- [decisions/why-link-graph-uses-graph-analytics.md](../decisions/why-link-graph-uses-graph-analytics.md) — Phase 6 metrics/co-location/GNN integration.
- [backend/graph-metrics.md](../backend/graph-metrics.md), [backend/graph-proximity.md](../backend/graph-proximity.md), [backend/graph-pyg.md](../backend/graph-pyg.md) — the backing modules.
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — backing routes.
- [backend-routers/aois-router.md](../backend-routers/aois-router.md) — Base/LaunchPoint/Facility nodes are projected from `aois` tagged with `metadata.aoi_kind`.
- [backend/graph-writes.md](../backend/graph-writes.md) — write helpers (used by the `/promote` endpoint).
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)
- [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
