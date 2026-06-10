# `backend/graph_pyg.py` — PyTorch-Geometric bridge + GNN link predictor

**Path:** [backend/graph_pyg.py](../../backend/graph_pyg.py)
**Lines:** ~219
**Depends on:** `numpy` (always). `torch` (lazy, optional — not in the backend image by default) for the GNN path; `torch_geometric` (lazy, optional) only for `to_pyg_data`.

## Purpose

Bridge node/edge records into the tensors a GNN needs and run a GraphSAGE link predictor that scores *missing* links between operational entities — the learned upgrade to the heuristic candidate scorer in [candidate_linking.py](../../backend/candidate_linking.py).

## Why this design

Vendored from city2graph's `gdf_to_pyg` (BSD-3). Two layers: graph assembly is pure numpy and always importable (unit-tested on its own); the GraphSAGE encoder + dot-product decoder imports `torch` lazily. The backend image does not ship torch, so the request path returns an honest 503 — mirroring `dem_available` / `osrm_available`. Installing `torch` (CPU) enables it with no other change. See [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md).

## Key symbols

- [`GNNUnavailable`](../../backend/graph_pyg.py#L26-L27) — raised when the GNN path is requested but torch is absent (router maps to 503).
- [`is_torch_available()`](../../backend/graph_pyg.py#L30-L35) / [`is_pyg_available()`](../../backend/graph_pyg.py#L38-L43) — lazy capability probes feeding `/api/graph/gnn/status`.
- [`assemble_graph(nodes, edges, feature_keys, normalize, add_reverse)`](../../backend/graph_pyg.py#L55-L106) — pure-numpy assembly into a stable node index, `(N, F)` z-scored feature matrix `x`, and `(2, E)` `edge_index`. No torch.
- [`to_pyg_data(assembled)`](../../backend/graph_pyg.py#L109-L119) — build a `torch_geometric.data.Data`; raises `GNNUnavailable` without PyG.
- [`suggest_links(nodes, edges, candidate_pairs, feature_keys, epochs, hidden, out, top_k, seed)`](../../backend/graph_pyg.py#L160-L219) — train a 2-layer GraphSAGE auto-encoder with negative sampling, score candidate pairs by sigmoid dot-product, return top-`k`. Raises `GNNUnavailable` if torch is missing or the graph has no edges.
- [`_build_model(in_dim, hidden, out)`](../../backend/graph_pyg.py#L126-L157) — minimal pure-torch GraphSAGE encoder (no `torch_geometric` required).

## Inputs / Outputs

**Input:** `nodes` are dicts each with an `id` + numeric features; `edges` / `candidate_pairs` are `(src_id, dst_id)`; `feature_keys` selects the numeric columns. **Output:** `suggest_links` returns `[{source, target, score}]` sorted by descending probability. `assemble_graph` returns an `AssembledGraph` (dict subclass) with `node_ids`, `x`, `edge_index`, `feature_keys`.

## Failure modes

- torch absent → `GNNUnavailable("torch is not installed in this image")`. Route → 503; beat task → `{"skipped": ...}`.
- Graph has no edges to train on → `GNNUnavailable`.
- Candidate pairs with an endpoint outside the node index are skipped.

## Cross-references

- Decision: [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md)
- Persistence: [backend/graph-writes.md](graph-writes.md) — `project_gnn_suggested_links_batch` writes `GNN_SUGGESTED_LINK`.
- Routes: [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/gnn/status`, `POST /api/graph/gnn/suggest-links`.
- Beat task: [backend/worker-legacy-monolith.md](worker-legacy-monolith.md) — `worker.tick_gnn_link_prediction`.
- [backend/candidate-linking.md](candidate-linking.md) — the heuristic scorer this complements.
