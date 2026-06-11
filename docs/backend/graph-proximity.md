# `backend/graph_proximity.py` — Proximity-graph builders

**Path:** [backend/graph_proximity.py](../../backend/graph_proximity.py)
**Lines:** ~303
**Depends on:** `numpy`, `scipy` (`spatial.Delaunay`, `spatial.cKDTree`, `sparse.csgraph.minimum_spanning_tree`) — both already in [backend/requirements.txt](../../backend/requirements.txt). No network, no online loaders.

## Purpose

Build undirected proximity (co-location) edges over geographic points — kNN, fixed-radius, Delaunay, Gabriel, relative-neighbourhood, and Euclidean-MST graphs, plus a heterogeneous `bridge_edges` between two point layers — from `(id, lon, lat)` records, fully offline.

## Why this design

Vendored from the proximity models in the open-source **city2graph** library (BSD-3). Only the algorithm core is copied; none of city2graph's online loaders (Overture/OSM/GTFS) are pulled in, so the module stays air-gap clean and adds no runtime dependency beyond `numpy`/`scipy`. See [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md). Distances are great-circle metres (haversine); the planar models operate on a local equirectangular projection around the point centroid — accurate to <1% at co-location scale (single AOI / city) with no CRS dependency.

## Key symbols

- [`haversine_m(lon1, lat1, lon2, lat2)`](../../backend/graph_proximity.py#L47-L53) — great-circle distance in metres.
- [`knn_edges(records, k, max_distance_m)`](../../backend/graph_proximity.py#L114-L126) — symmetric k-nearest-neighbour graph via `cKDTree`.
- [`fixed_radius_edges(records, radius_m)`](../../backend/graph_proximity.py#L129-L139) — every pair within `radius_m`; KD-tree query then exact haversine filter.
- [`delaunay_edges(records, max_distance_m)`](../../backend/graph_proximity.py#L142-L145) — Delaunay-triangulation adjacency backbone.
- [`gabriel_edges(records, max_distance_m)`](../../backend/graph_proximity.py#L148-L163) — Gabriel subgraph of Delaunay (empty-circle test).
- [`relative_neighborhood_edges(records, max_distance_m)`](../../backend/graph_proximity.py#L166-L182) — RNG, empty-lune test; sparsest backbone.
- [`euclidean_mst_edges(records)`](../../backend/graph_proximity.py#L185-L202) — Euclidean MST over the Delaunay edge set (which provably contains it).
- [`build_proximity_edges(records, method, **kw)`](../../backend/graph_proximity.py#L215-L222) — dispatch by name; unknown method → `ValueError`.
- [`bridge_edges(source, target, method, k, radius_m)`](../../backend/graph_proximity.py#L225-L267) — heterogeneous edges from each source node to nearby target nodes (city2graph `bridge_nodes` semantics).
- [`build_colocation_edges(records, method, k, radius_m, max_distance_m)`](../../backend/graph_proximity.py#L270-L303) — high-level helper; returns persistence-ready `{a_id, b_id, distance_m, method}` rows for `project_colocation_edges_batch`. Applies a default 5 km cap. **Remaps the stringified ids back to the caller's original int type** and value-orders the direction — `build_proximity_edges` stringifies node ids internally, but the persistence MATCH compares against Neo4j's integer `postgis_id`, so emitting `"31915"` instead of `31915` silently wrote zero edges (caught by the San Diego airport real-imagery test, 2026-06-11).

## Inputs / Outputs

**Input:** `Sequence[(node_id, lon, lat)]`. **Output:** list of `(id_a, id_b, distance_m)` edges with `id_a < id_b`, deduped (`build_colocation_edges` returns dict rows instead). `bridge_edges` returns directed `(source_id, target_id, distance_m)`.

## Failure modes

- Unknown `method` → `ValueError` (the `/api/graph/colocation` route maps it to HTTP 400; the builder task returns `{"error": "bad_method"}`).
- `< 2` records → empty edge list.
- Collinear/degenerate input to Delaunay → falls back to the complete graph.
- **Id-type mismatch (fixed):** `build_colocation_edges` must return the caller's original int ids; if rows leak the internal string ids, `project_colocation_edges_batch`'s `MATCH (:Detection {postgis_id: ...})` matches nothing and persists zero edges with no error (it swallows misses). Regression-guarded in [tests/test_graph_proximity.py](../../backend/tests/test_graph_proximity.py) `test_colocation_rows_preserve_int_ids`.

## Cross-references

- Decision: [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md)
- Persistence: [backend/graph-writes.md](graph-writes.md) — `project_colocation_edges_batch` writes `COLOCATED_WITH`.
- Builder task: [backend/worker-legacy-monolith.md](worker-legacy-monolith.md) — `worker.tick_colocation_builder`.
- Route: [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/colocation`.
