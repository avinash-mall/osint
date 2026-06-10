# `backend/od_flows.py` — Origin-Destination flow graphs

**Path:** [backend/od_flows.py](../../backend/od_flows.py)
**Lines:** ~110
**Depends on:** stdlib `math` only. No new dependency.

## Purpose

Turn movement data (ordered track-point sequences, or a raw zone×zone OD matrix) into a spatial flow graph — weighted edges between grid cells / zones that surface movement corridors for pattern-of-life — and render the result as GeoJSON.

## Why this design

Vendored from city2graph's `od_matrix_to_graph` (BSD-3). The OD-matrix form is the direct city2graph entry point; `build_od_flows_from_tracks` adapts Sentinel's `track_points` to it by snapping each point to a grid cell and aggregating per-track consecutive cell transitions into an OD matrix. Plain `math` keeps it air-gap clean with zero new dependency. See [decisions/why-od-flow-graphs.md](../decisions/why-od-flow-graphs.md).

## Key symbols

- [`snap_cell(lon, lat, cell_deg)`](../../backend/od_flows.py#L25-L29) — snap a point to the centre of its `cell_deg` grid cell.
- [`od_matrix_to_graph(matrix, centroids, min_flow, drop_self)`](../../backend/od_flows.py#L32-L63) — direct city2graph form: zone×zone weight matrix + centroids → flow edges, sorted by descending weight.
- [`build_od_flows_from_tracks(tracks, cell_deg, min_flow)`](../../backend/od_flows.py#L66-L100) — aggregate ordered `(lon, lat)` track sequences into flow edges weighted by movement count.
- [`flows_to_geojson(edges)`](../../backend/od_flows.py#L103-L110) — render flow edges as a FeatureCollection of weighted LineStrings.

## Inputs / Outputs

**Input:** `tracks` is `Sequence[Sequence[(lon, lat)]]` time-ordered per track (the OD-matrix form takes a weight matrix + centroids instead). **Output:** list of `{origin, dest, weight, origin_cell, dest_cell}` flow edges; `flows_to_geojson` wraps them as `{type: FeatureCollection, features: [...]}` with `weight` in each feature's `properties`.

## Failure modes

- Self-loops dropped (`drop_self`) and edges below `min_flow` dropped — empty result is valid, not an error.
- A track with fewer than two distinct cells contributes no movement.

## Cross-references

- Decision: [decisions/why-od-flow-graphs.md](../decisions/why-od-flow-graphs.md)
- Route: [backend-routers/analytics-router.md](../backend-routers/analytics-router.md) — `POST /api/analytics/od-flows`.
- Frontend: [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md) — OD Flows tool.
