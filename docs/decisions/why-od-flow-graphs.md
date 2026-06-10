# Why Origin-Destination flow graphs from track points

**Decision date:** 2026-06-10
**Status:** active

## Context

The patterns-of-life heatmap (`/api/analytics/pol`) shows *where* activity concentrates but not *where it moves between*. Analysts want movement corridors — the dominant origin→destination flows in a body of track data — as weighted directed edges. Aggregating ordered movement traces into an OD matrix and rendering it as a flow graph is a standard mobility-analysis operation with a mature open-source implementation.

## Decision

Inherit the OD-flow capability from the open-source **city2graph** library (BSD-3) by **vendoring** its `od_matrix_to_graph` into [backend/od_flows.py](../../backend/od_flows.py), plus a thin `build_od_flows_from_tracks` adapter for Sentinel's `track_points`. `POST /api/analytics/od-flows` snaps each point to a `cell_deg` grid, counts per-track consecutive cell transitions, and returns weighted LineString flows as GeoJSON.

Vendoring vs. depending, same reasoning as the sibling proximity decision: city2graph carries online loaders and a heavy geo stack; the OD core we need is a handful of functions over plain `math`. Copying it (attributed in the module docstring) keeps Sentinel air-gap clean with **zero new dependency**.

## Consequences

**Positive**
- Movement corridors are now a first-class analytics product, rendered as weighted lines on the map.
- No new runtime dependency; offline guarantee preserved.

**Negative / accepted**
- We own the vendored copy; upstream fixes do not flow in automatically.
- Grid-cell snapping discretises movement — corridor resolution is bounded by `cell_deg` (operator-tunable).

## Related

- [backend/od-flows.md](../backend/od-flows.md) — module reference
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md) — `POST /api/analytics/od-flows`
- [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md) — OD Flows tool
- [decisions/why-proximity-colocation-graph.md](why-proximity-colocation-graph.md) — sibling city2graph-inherited capability
