# `backend/graph_metrics.py` — In-memory graph metrics

**Path:** [backend/graph_metrics.py](../../backend/graph_metrics.py)
**Lines:** ~227
**Depends on:** stdlib only on the fallback path; `rustworkx` (fast path, **baked into the backend image** as of 2026-06-11 — [backend/requirements.txt](../../backend/requirements.txt) pins `rustworkx>=0.14`, image ships 0.17.1, so `GET /api/graph/metrics` reports `backend: rustworkx`); `networkx` only for the optional interop helpers.

## Purpose

Compute graph-level metrics (density, connected components) and per-node centrality (degree, betweenness, PageRank) over a bounded Neo4j snapshot in memory, picking the fast `rustworkx` backend when available and a dependency-free pure-Python fallback otherwise.

## Why this design

Sentinel's entity/link graph lives in Neo4j; running global analytics as Cypher is awkward and slow, so a bounded snapshot is pulled into memory and analysed there — the pattern city2graph uses via its `nx_to_rx` rustworkx interop. `rustworkx` is the compiled fast path, but the backend image ships it only after a rebuild, so every metric also has a fallback (union-find components + Brandes betweenness + power-iteration PageRank) that is correct for the ≤1500-node snapshots the graph endpoint returns. See [decisions/why-rustworkx-graph-metrics.md](../decisions/why-rustworkx-graph-metrics.md).

## Key symbols

- [`compute_metrics(node_ids, edges, top_k, prefer_rustworkx)`](../../backend/graph_metrics.py#L142-L205) — public entry; returns `{backend, node_count, edge_count, density, component_count, largest_component, top_centrality}`. `backend` reports which path ran (`rustworkx` / `fallback` / `none`). The rustworkx branch builds a mirrored `PyDiGraph` for PageRank because `rustworkx.pagerank` rejects an undirected `PyGraph`, and indexes the `CentralityMapping` by node index (not `.values()`) so scores align to `node_ids`.
- [`RUSTWORKX_AVAILABLE`](../../backend/graph_metrics.py#L25-L30) — module-level flag set at import.
- [`_connected_components(n, edges)`](../../backend/graph_metrics.py#L57-L75) — union-find component sizes (fallback).
- [`_betweenness(n, adj)`](../../backend/graph_metrics.py#L78-L111) — normalised Brandes betweenness (fallback).
- [`_pagerank(n, adj, damping, iters, tol)`](../../backend/graph_metrics.py#L114-L134) — power-iteration PageRank (fallback).
- [`nx_to_rx(graph)`](../../backend/graph_metrics.py#L198-L205) / [`rx_to_nx(graph)`](../../backend/graph_metrics.py#L208-L217) — city2graph interop; require both libraries; not on the request path.

## Inputs / Outputs

**Input:** `node_ids` (opaque stable ids — Neo4j `elementId` on the request path) + `edges` referencing them. **Output:** the metrics dict above; `top_centrality` has `degree` / `betweenness` / `pagerank` buckets, each a list of `{id, score}`. The router enriches each entry with `label` + `name`.

## Failure modes

- Empty graph → `{"backend": "none", ...}` with zeroed counts.
- Edges referencing unknown node ids or self-loops are silently dropped.
- `rustworkx.pagerank` only accepts a directed graph — passing the undirected `PyGraph` raises `TypeError: 'PyGraph' object cannot be converted to 'PyDiGraph'`. Guarded by building a both-directions `PyDiGraph` for the PageRank call (regression-tested in [tests/test_graph_metrics.py](../../backend/tests/test_graph_metrics.py) `test_rustworkx_path_matches_fallback_when_available`).
- `nx_to_rx` / `rx_to_nx` raise `RuntimeError` when either library is missing (interop only — not reachable from the route).

## Cross-references

- Decision: [decisions/why-rustworkx-graph-metrics.md](../decisions/why-rustworkx-graph-metrics.md)
- Route: [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/metrics`.
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
