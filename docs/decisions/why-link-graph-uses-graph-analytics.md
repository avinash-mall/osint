# Why the Link Graph wires in the Phase 6 graph-analytics endpoints

**Status:** Implemented.
**Scope:** [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx) (Investigation mode).

## Context

Phase 6 added five backend graph/analytics capabilities (proximity co-location, GNN link prediction, isochrone, OD-flows, rustworkx metrics — vendored/adapted from the open-source city2graph library, BSD-3). Three of them are *graph-native* and belong on the Link Graph surface; the analyst was previously looking at an un-ranked node-link view with no notion of which entities are structurally important, no spatial-cluster lens, and no learned link suggestions.

## Decision

Surface exactly the three graph-native features in Investigation mode, each reusing an existing UI idiom rather than inventing a new surface:

- **A — Metrics + centrality sizing** (`GET /api/graph/metrics`): extend the existing top-left stats card and scale node radius by PageRank in the existing `nodeCanvasObject`. No new panel chrome. Hubs/brokers become visible at a glance.
- **B — Co-location** (`GET /api/graph/colocation` + `COLOCATED_WITH` default-hidden): the persisted proximity edges ride the existing predicate-chip filter (auto-surfaced, opt-in), and a one-button lens renders the *live* proximity graph as a `filteredData` view — the same mechanism "Find path" / "Expand node" already use.
- **C — GNN suggestions** (`/api/graph/gnn/status` + `/suggest-links` + `GNN_SUGGESTED_LINK`): a status-gated toolbar button overlays dashed advisory edges and a score-ranked detail panel, mirroring the path/site-rollup panels.

## Why this design

- **Reuse over new surfaces.** Each feature maps onto an idiom already in the workspace (stats card, predicate chips, `filteredData` lens, side panel), so the analyst's mental model and selection context are preserved. See [decisions/why-three-graph-modes.md](why-three-graph-modes.md).
- **Honest capability gating.** The GNN runtime (torch) is optional infra — not shipped in the backend image — so the button self-disables with an explanatory tooltip exactly like the map's DEM/OSRM capability chip ([decisions/why-gnn-link-prediction.md](why-gnn-link-prediction.md)). No dead control, no silent failure.
- **Two id spaces, handled explicitly.** Metrics and GNN suggestions key by Neo4j `elementId` and join straight into the entity feed. Co-location returns PostGIS `detections.id`, a different id space, so its lens renders standalone `det-<id>` nodes instead of being forced to merge — avoids fabricating phantom joins.
- **Dense/advisory edges start hidden.** `COLOCATED_WITH` (dense) and `GNN_SUGGESTED_LINK` (advisory) are seeded into the default-hidden predicate set so they never clutter the default view; the lens / suggest action re-enables the relevant predicate on demand.

## Consequences

- The metrics card shows `backend: fallback` until rustworkx is installed in the image; correctness is identical, only speed differs.
- GNN suggestions are advisory — they persist as `GNN_SUGGESTED_LINK` for review and are never auto-promoted to real relationships; promotion stays a deliberate analyst action through the existing candidate/approval paths.

## Related

- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md) — the workspace surface.
- [backend/graph-metrics.md](../backend/graph-metrics.md), [backend/graph-proximity.md](../backend/graph-proximity.md), [backend/graph-pyg.md](../backend/graph-pyg.md) — backing modules.
- [decisions/why-proximity-colocation-graph.md](why-proximity-colocation-graph.md), [decisions/why-rustworkx-graph-metrics.md](why-rustworkx-graph-metrics.md), [decisions/why-gnn-link-prediction.md](why-gnn-link-prediction.md).
