# `backend/routing.py` — NetworkX Routing on OSMnx Graph

**Path:** [backend/routing.py](../../backend/routing.py)
**Lines:** ~245
**Depends on:** `networkx`, pickled graph at `${ROUTING_GRAPH_PATH:-/data/routing/graph.pkl}`

## Purpose

Compute shortest routes, optionally weighted by terrain or threat exposure, on a pre-built OSMnx road graph. Surfaces as `POST /api/analytics/routes`.

## Why this design

- **Graph is pre-built on disk.** Building from OSM at request time would be intolerably slow. Operators build the graph offline (`osmnx.graph_from_bbox(...)` then `pickle.dump`) and drop it into `/data/routing/graph.pkl`.
- **Module is lazy.** `_load_graph` is called on first use and cached. Reset with `reset_graph_cache()`.
- **Multiple weight strategies.** Fastest, shortest, and a "low-threat" mode that down-weights edges passing through high-exposure cells. The exposure data is layered onto the graph at build time.
- **Fixture fallback when missing.** `graph_available()` lets the analytics router return `mode: "fixture_no_graph"` instead of 500.

## Key symbols

- [`graph_path`](../../backend/routing.py#L38), [`graph_available`](../../backend/routing.py#L42).
- [`_load_graph`](../../backend/routing.py#L47), [`reset_graph_cache`](../../backend/routing.py#L54).
- [`_haversine`](../../backend/routing.py#L58), [`_nearest_node`](../../backend/routing.py#L67), [`_edge_length_m`](../../backend/routing.py#L82), [`_edge_attr`](../../backend/routing.py#L102).
- [`_path_coords`](../../backend/routing.py#L119), [`_path_metrics`](../../backend/routing.py#L131).
- [`_route_with_weight`](../../backend/routing.py#L150) — single weight-function dispatcher.
- [`compute_routes`](../../backend/routing.py#L157) — main entry; returns multiple strategies.
- [`_risk_label`](../../backend/routing.py#L236) — exposure → "low/medium/high" UI label.

## Failure modes

- Graph file missing → caller returns fixture response.
- Source or destination node unreachable in graph → returns empty for that strategy.

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [deployment/volume-mounts-and-paths.md](../deployment/volume-mounts-and-paths.md)
