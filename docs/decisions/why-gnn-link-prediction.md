# Why GNN link prediction as optional, torch-guarded infrastructure

**Decision date:** 2026-06-10
**Status:** active

## Context

Candidate links between operational entities are currently scored by the deterministic heuristic in [candidate_linking.py](../../backend/candidate_linking.py) (spatial + compatibility + confidence + history). A learned graph model can surface *non-obvious* missing links from the graph structure itself — the kind of suggestion a hand-tuned scorer cannot express. city2graph already provides a GeoDataFrame→PyTorch-Geometric bridge for exactly this.

## Decision

Inherit the GNN capability from the open-source **city2graph** library (BSD-3) by **vendoring** its `gdf_to_pyg` bridge into [backend/graph_pyg.py](../../backend/graph_pyg.py), and add a minimal 2-layer GraphSAGE auto-encoder that trains on observed edges (with negative sampling) and ranks currently-unconnected operational-entity pairs by predicted link probability. Surfaced as `POST /api/graph/gnn/suggest-links` and the beat task `worker.tick_gnn_link_prediction`, which MERGEs the top predictions as **advisory** `GNN_SUGGESTED_LINK` edges (never promoted automatically — analyst review only).

The hard constraint is air-gap + image weight: PyTorch is large and the backend image did not originally ship it. So the module is **torch-guarded** — graph assembly is pure numpy and always importable, but the training path imports torch lazily and raises `GNNUnavailable` when it is absent. The request path then returns an honest **503**, mirroring `dem_available` / `osrm_available`, and `/api/graph/gnn/status` reports availability. The guard remains so the module stays importable on any image; it is no longer the active state in the shipped stack (see below).

**Update (2026-06-11): CPU torch is now baked.** `torch==2.8.0+cpu` is pinned in [backend/requirements.txt](../../backend/requirements.txt) via the PyTorch CPU `--extra-index-url`, so the GNN path is live in the default backend + worker image (`/api/graph/gnn/status` → `ready:true`). The CPU wheel (~190 MB) is used deliberately, not the ~2 GB CUDA build: the backend snapshots are small (≤1500 nodes, 3-feature, 60-epoch GraphSAGE), so GPU gives no meaningful speedup, and the partitioned GPUs belong to `inference-sam3`. `torch_geometric` is *not* required — the encoder is pure torch. The 503/skip path is retained for stripped images that omit torch.

Vendoring vs. depending on city2graph: same reasoning as the sibling proximity/OD decisions — copying the small bridge core (attributed in the docstring) avoids the heavy geo + online-loader dependency tree and preserves the offline guarantee.

## Consequences

**Positive**
- A learned link suggester is wired end-to-end, gated behind one optional dependency.
- Zero impact on the default air-gapped image (torch not bundled); feature is opt-in.
- Suggestions are advisory edges, so they never corrupt analyst-asserted graph state.

**Negative / accepted**
- CPU torch adds ~190 MB to the backend + worker image (now ~3.3 GB). Accepted to make the feature live by default; the CUDA wheel was rejected as ~10× heavier for no GPU benefit at this scale.
- We own the vendored bridge; upstream city2graph fixes do not flow in automatically.

## Related

- [backend/graph-pyg.md](../backend/graph-pyg.md) — module reference
- [backend/graph-writes.md](../backend/graph-writes.md) — `project_gnn_suggested_links_batch`
- [backend-routers/graph-router.md](../backend-routers/graph-router.md) — `GET /api/graph/gnn/status`, `POST /api/graph/gnn/suggest-links`
- [backend/candidate-linking.md](../backend/candidate-linking.md) — the heuristic scorer this complements
- [decisions/why-llm-proposed-entities.md](why-llm-proposed-entities.md) — same "propose for review, never auto-assert" principle
