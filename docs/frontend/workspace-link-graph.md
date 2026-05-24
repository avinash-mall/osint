# Link Graph Workspace — `GraphExplorer.tsx`

**Path:** [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx)
**Lines:** ~25326 characters (~650 lines TSX)

## Purpose

Force-directed visualization of the Neo4j entity graph: Targets, Assets, Observations, Satellites, Bases, LaunchPoints, and edges between them.

## Data sources

- `GET /api/graph` — initial 1000-node load
- `POST /api/graph/neighborhood` — expanding a node fetches its k-hop neighborhood
- `GET /api/ontology/updates` — proposed ontology updates for analyst review in the bottom strip

## Behavior

- Drag-pan / wheel-zoom standard graph viewer controls.
- Node colors by category (Target/Asset/Base/etc.).
- Clicking a node opens a details popover, pivots focus.
- Double-click expands the neighborhood.
- **Edge predicates** (UX-AUDIT F22) — each edge carries the Neo4j relationship type as `predicate`; edges tinted by `predicateColor`, labelled mid-edge via `linkCanvasObject` once zoomed in. A `PredicateChipBar` above the canvas filters edges by predicate.
- Workspace icon is `Share2` (UX-AUDIT F23), not the former `Crosshair`.

## Why a graph viewer

Spatial joins are PostGIS; graph traversals are Neo4j. The Link Graph workspace is the only place an operator interacts directly with Neo4j. See [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — approved 4-phase redesign turning this workspace into a defence-analyst reasoning surface (Investigation / Evidence / Ontology modes).
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/ux-audit-001.md](../decisions/ux-audit-001.md)
