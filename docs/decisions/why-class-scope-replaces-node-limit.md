# Why class scope replaces the fixed node limit on the graph endpoints

**Decision date:** 2026-06-11
**Status:** active
**Scope:** [backend/routers/graph.py](../../backend/routers/graph.py) (`/api/graph`, `/api/graph/metrics`, `/api/graph/colocation`, `/api/graph/classes`) + [frontend/src/components/GraphExplorer.tsx](../../frontend/src/components/GraphExplorer.tsx).

## Context

`/api/graph` and (as first shipped) `/api/graph/metrics` / `/api/graph/colocation` carried a hard `LIMIT 1500`. With the graph grown past 6000 detections, that bound silently truncated results: the San Diego airport ingest produced 4157 detections + 10911 `COLOCATED_WITH` edges, but the bounded snapshot returned an arbitrary 1238-node slice that excluded most of them. The limit answered "how many nodes" but never "*which* nodes" — so metrics described an unrepresentative sample and the analyst couldn't reliably see a class they cared about.

## Decision

Remove the fixed node-count limits and replace them with **semantic scoping by two combinable dropdowns — detection class and source image (pass)**:

- New `GET /api/graph/classes` returns distinct detection classes + counts; new `GET /api/graph/passes` returns imagery scenes (satellite passes) that have detections, with counts + acquisition date. They populate the **Class scope** and **Image** dropdowns.
- `/api/graph`, `/api/graph/metrics`, `/api/graph/colocation` gain optional `det_class` **and `pass_id`** params (combinable, ANDed) and make `limit` **optional / unbounded** (a safety cap, not a default truncation). The Neo4j scope Cypher is built by the shared `_scoped_graph_cypher` helper: `pass_id` matches `(SatellitePass {postgis_id})-[:CONTAINS_DETECTION]->(Detection)`, `det_class` matches `Detection {class}`, and they intersect.
- Selecting a class and/or an image fetches *every* matching detection plus its 1-hop neighbourhood (parent `SatellitePass`, `COLOCATED_WITH` peers, `NEAR` sites, candidate links) — unbounded, but naturally bounded by the selection. Metrics and the co-location lens follow the same scope.
- "All images" + "All entities" returns the bounded operational overview for the force graph, and **whole-graph** (unbounded) metrics — so the metrics card finally describes the entire graph, not a slice.

The `Detection(class)` filter is index-backed by the existing `idx_detection_class_created` index, and `pass_id` traverses the `CONTAINS_DETECTION` edge keyed on `SatellitePass.postgis_id`, so both scopes are cheap.

## Why this design

- **The bound should be meaningful, not arbitrary.** "All ships" is a scope an analyst reasons about; "the first 1500 nodes Neo4j happens to return" is not. Class scope makes the returned set both complete (for that class) and intelligible.
- **Honesty of metrics.** Centrality/density over a truncated slice is misleading. Unbounded-by-default (or class-scoped) metrics describe a real, nameable graph.
- **Performance moves to the analyst's control.** Whole-graph metrics on 6600 nodes take ~7 s (betweenness dominates); a class scope (e.g. 979 ship-nodes) is sub-second. The dropdown is the escape hatch — pick a class, get fast, focused metrics — instead of a one-size cap that's both too small to be complete and too big to be fast.

## Consequences

**Positive**
- No silent truncation; the data you ask for is the data you get.
- Class dropdown is a first-class scoping control shared by graph view, metrics, and co-location.

**Negative / accepted**
- Unbounded whole-graph metrics are O(V·E) on betweenness — ~7 s at 6600 nodes and worse as the graph grows. Accepted because (a) class scope is the fast path, and (b) the metrics card fetches async and never blocks the canvas. If the whole-graph case becomes painful at much larger scale, the follow-up is approximate/threshold-gated betweenness, not re-introducing a data-truncating node cap.
- A force graph rendered over a very populous class (e.g. building ×2014) will be dense; that is the analyst's explicit choice, surfaced by the count shown in the dropdown.

## Related

- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — the endpoints.
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md) — the Class scope dropdown.
- [decisions/why-rustworkx-graph-metrics.md](why-rustworkx-graph-metrics.md) — why metrics can afford to run unbounded (compiled fast path).
- [decisions/why-link-graph-uses-graph-analytics.md](why-link-graph-uses-graph-analytics.md) — the Phase 6 metrics/co-location/GNN integration this extends.
