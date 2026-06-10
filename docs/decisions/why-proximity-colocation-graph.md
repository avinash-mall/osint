# Why proximity (co-location) graphs over detection centroids

**Decision date:** 2026-06-10
**Status:** active

## Context

Analysts want to see which detections cluster together spatially — co-located vessels at a quay, vehicles at a depot — as graph structure, not just dots on a map. The existing `NEAR` builder links detections only to *known sites* (Base/LaunchPoint/Facility); there was no detection↔detection adjacency. Building these graphs (kNN, Delaunay, Gabriel, RNG, MST, fixed-radius) from scratch is well-trodden computational geometry, and a mature open-source implementation already exists.

## Decision

Inherit the proximity-graph capability from the open-source **city2graph** library (BSD-3) by **vendoring** its proximity model core into [backend/graph_proximity.py](../../backend/graph_proximity.py), rather than adding `city2graph` as a dependency. A new beat task `worker.tick_colocation_builder` MERGEs the resulting edges as `COLOCATED_WITH`, and `GET /api/graph/colocation` previews them live.

Vendoring vs. depending was the key trade-off:

- city2graph pulls in online loaders (Overture/OSM/GTFS) and a heavy geospatial stack (geopandas, shapely, momepy). Sentinel ships **air-gapped** — no runtime downloads, minimal dependency surface.
- The algorithm core we need is small and depends only on `numpy`/`scipy`, both already present. Copying it (with attribution in the module docstring) keeps the image lean and the offline guarantee intact, at the cost of not tracking upstream fixes automatically.

The builder is idempotent (MERGE on a stable `a_id < b_id` direction), so unlike the `NEAR` builder it needs no per-site cursor.

## Consequences

**Positive**
- Detection↔detection adjacency is now first-class graph structure, traversable in Cypher and previewable from the API.
- Zero new runtime dependency; offline guarantee preserved.

**Negative / accepted**
- We own the vendored copy; upstream city2graph fixes do not flow in automatically.
- The planar models use a local equirectangular projection, accurate at AOI scale but not for continent-spanning point sets (acceptable — co-location is inherently local).

## Related

- [backend/graph-proximity.md](../backend/graph-proximity.md) — module reference
- [backend/graph-writes.md](../backend/graph-writes.md) — `project_colocation_edges_batch`
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/colocation`
- [decisions/why-rustworkx-graph-metrics.md](why-rustworkx-graph-metrics.md) — sibling city2graph-inherited capability
