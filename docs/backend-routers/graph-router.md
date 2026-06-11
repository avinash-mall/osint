# Graph Router (`/api/graph`, `/api/geotime/features`)

**Path:** [backend/routers/graph.py](../../backend/routers/graph.py)
**Lines:** ~1345
**Depends on:** [backend/auth.py](../../backend/auth.py), [backend/database.py](../../backend/database.py) (`db` for Neo4j + `postgis_db`), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/schemas.py](../../backend/schemas.py), and (lazily) [backend/graph_proximity.py](../../backend/graph_proximity.py), [backend/graph_metrics.py](../../backend/graph_metrics.py), [backend/graph_pyg.py](../../backend/graph_pyg.py)

## Purpose

Neo4j-backed read endpoints for the Link Graph workspace + the Phase 1 redesign endpoints. Write operations limited to candidate-edge promotion; node/edge creation happens elsewhere (worker during ingest, candidate-approval in [main.py](../../backend/main.py), Phase 2+ projectors).

## Endpoints

### Back-compat (pre-redesign)

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/graph`                | [graph.py#L156](../../backend/routers/graph.py#L156) | Graph slice. **Unbounded by default** (the old hard `LIMIT 1500` is gone); two combinable scope params — `det_class` (class dropdown) and `pass_id` (image dropdown) — return the matching detections + their 1-hop neighbourhood, `limit` is an optional cap, `include_candidates` toggles `CANDIDATE_*` filtering. Cypher built by `_scoped_graph_cypher`. See [decisions/why-class-scope-replaces-node-limit.md](../decisions/why-class-scope-replaces-node-limit.md). |
| `GET`  | `/api/graph/classes`        | [graph.py#L136](../../backend/routers/graph.py#L136) | Distinct detection classes + counts (PostGIS, desc by count) — populates the Class-scope dropdown. |
| `GET`  | `/api/graph/passes`         | [graph.py#L155](../../backend/routers/graph.py#L155) | Imagery passes (scenes) that have detections, with counts + acquisition time (PostGIS, most-recent first) — populates the Image dropdown. |
| `POST` | `/api/graph/neighborhood`   | [graph.py#L120](../../backend/routers/graph.py#L120) | 1-hop neighborhood of a seed node |
| `GET`  | `/api/geotime/features`     | [graph.py#L150](../../backend/routers/graph.py#L150) | Static features (Bases, LaunchPoints) + asset track history for the map |

### Phase 1 (Link Graph redesign)

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/graph/investigation`  | [graph.py#L207](../../backend/routers/graph.py#L207) | Default Investigation feed. Operational nodes + 1-hop neighborhood, scoped by time / AOI / class lens / seed node. Capped at `limit` (default 150, max 500). |
| `POST` | `/api/graph/path`           | [graph.py#L302](../../backend/routers/graph.py#L302) | `allShortestPaths` between two nodes up to `max_depth=4` (max 8). Workflow 6 — link discovery. |
| `GET`  | `/api/graph/site-composition/{base_id}` | [graph.py#L329](../../backend/routers/graph.py#L329) | Workflow 3 — "what's at this site?" Returns recent-detections histogram (live PostGIS ST_DWithin against the AOI centroid) plus Neo4j Vessels/Vehicles/Aircraft/other Assets observed at the site. As of Phase 5.L also returns `fmv_clips` (frame-footprint ∩ AOI) and `reports` (target_id of anchored entities). |
| `GET`  | `/api/graph/evidence/{node_id}` | [graph.py#L387](../../backend/routers/graph.py#L387) | Workflow 5 — chain of evidence. Returns 2-hop Neo4j neighborhood + parallel PostGIS pull of `detections`, `satellite_passes`, `fmv_clips/frames`, `documents`/`transcripts`, `reports`, `feed_events`, `observations` keyed by `postgis_id`. As of Phase 2.B-D the FMV/Documents/Observations buckets populate; Reports/FeedEvents wait on Phase 4 projectors. |
| `POST` | `/api/graph/contradict` | [graph.py#L892](../../backend/routers/graph.py#L892) | Workflow 4/5 dissent action. Requires a session cookie and writes `(actor)-[:CONTRADICTED_BY {reason, analyst}]->(:Detection)` with `analyst` from the signed session. Both ends must exist; returns 404 otherwise. |
| `GET`  | `/api/graph/ontology` | [graph.py#L387](../../backend/routers/graph.py#L387) | Phase 3 feed: OntologyBranch + OntologyObject tree (HAS_OBJECT, HAS_CHILD), plus (optional) UnknownLabel orbits with SUGGESTED_BRANCH + LABEL_OF edges to recent supporting Detections. Query params: `include_unknown=true`, `since` (ISO), `supports_per_unknown` (≤25), and Phase 5.C `include_cooccurrence=true&cooccurrence_top_k=N` for per-OntologyObject adjacency counts powering the OntologyOrbit chips. |
| `POST` | `/api/graph/candidate-edges/{candidate_id}/promote` | [graph.py#L949](../../backend/routers/graph.py#L949) | Graph-side equivalent of `/api/detection-target-candidates/{id}/approve`. Requires a session cookie, flips only pending PostGIS rows to `approved`, and promotes the Neo4j `CANDIDATE_DETECTED_AS` edge into `DETECTED_AS`. |
| `GET`  | `/api/graph/export/stix` | [graph.py#L960](../../backend/routers/graph.py#L960) | **R3 — STIX 2.1 export.** Operational entities + FK-derived relationships → a STIX 2.1 bundle for OpenCTI/Splunk/Sentinel/QRadar. Read-only, offline (PostGIS only). Delegates to [stix_export.build_bundle](../backend/stix-export.md). |

### Phase 6 (city2graph-inherited graph analytics)

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/graph/colocation` | [graph.py#L176](../../backend/routers/graph.py#L176) | Proximity (co-location) graph over recent detection centroids. Read-only preview of the `COLOCATED_WITH` edges the `worker.tick_colocation_builder` beat task persists. Query params: `method` (`knn`/`delaunay`/`gabriel`/`relative_neighborhood`/`mst`/`fixed_radius`), `k`, `radius_m`, `window_days`, **`det_class`** (scope to one class), **`pass_id`** (scope to one image), `limit` (optional/unbounded). Proximity maths in [graph_proximity.py](../backend/graph-proximity.md). |
| `GET`  | `/api/graph/metrics` | [graph.py#L226](../../backend/routers/graph.py#L226) | Graph-level metrics + top central nodes over a Neo4j snapshot (density, connected components, degree/betweenness/PageRank). **Unbounded by default** (whole graph), or scoped with `det_class` and/or `pass_id` (combinable); `limit` optional. rustworkx fast path, pure-Python fallback — see [graph_metrics.py](../backend/graph-metrics.md). Top nodes enriched with primary label + display name. Whole-graph / whole-scene runs are O(V·E) on betweenness (~7–10 s at 4–6 k nodes); class scope is the fast path — [decisions/why-class-scope-replaces-node-limit.md](../decisions/why-class-scope-replaces-node-limit.md). |
| `GET`  | `/api/graph/gnn/status` | [graph.py#L261](../../backend/routers/graph.py#L261) | Reports `{torch_available, torch_geometric_available, ready}` — whether the GNN link-prediction path is runnable in this image (torch is optional, like DEM/OSRM). |
| `POST` | `/api/graph/gnn/suggest-links` | [graph.py#L279](../../backend/routers/graph.py#L279) | GraphSAGE link prediction over operational entities. Snapshots operational + detection nodes and their non-candidate edges, ranks unconnected operational pairs by predicted link probability. **503 when torch is not installed.** Body: `GnnSuggestRequest`. See [graph_pyg.py](../backend/graph-pyg.md). |

## Why this design

- **Synthesis removed.** The pre-redesign `/api/graph` route inlined a PostGIS query to fabricate `CANDIDATE_DETECTED_AS` edges in memory because the edge wasn't persisted. After Phase 1.B persists them ([decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)), the synthesis block is dead and was deleted — the existing Cypher loop returns candidate edges naturally.
- **Investigation mode is bounded.** `react-force-graph-2d` chokes past ~150 useful nodes ([architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)), so the default query pulls ≤80 operational nodes + their 1-hop expansion, then truncates to `limit`. Drill-in continues via `/api/graph/neighborhood`.
- **Path query uses `allShortestPaths`.** Returns up to 10 shortest paths at the target depth — analysts want to see *all* equally-short routes, not just one arbitrary one.
- **Site composition stays a live PostGIS join in Phase 1.** Precomputing `NEAR` edges is Phase 4 work; until then the ST_DWithin against the AOI centroid is fast enough for one-site queries. The endpoint accepts a `radius_m` override so analysts can probe different reaches.
- **Candidate promote runs PostGIS UPDATE first, then Neo4j.** Same ordering as `/approve` — PostGIS is source of truth, graph is derived. The UPDATE is guarded with `status='pending'`, uses the signed session username for `reviewed_by`, and returns 409 for stale review attempts. If the graph step finds no matching edge (e.g., projection lagged), the endpoint falls back to delete-by-pair so re-render is clean.

## Inputs / Outputs

Pydantic models in [schemas.py](../../backend/schemas.py): `GraphActionRequest`, `GraphPathRequest`, `GraphContradictRequest`, `GnnSuggestRequest`. The Phase 6 read endpoints (`/colocation`, `/metrics`, `/gnn/status`) take query params only.

Response shape across the new endpoints: `{nodes, links, ...meta}` where each node is `{id, label, labels, properties}` and each link is `{source, target, type, predicate, candidate, properties}`. The `predicate` field is what the frontend `PredicateChipBar` filters on ([frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)).

`POST /api/graph/contradict` and `POST /api/graph/candidate-edges/{candidate_id}/promote` take no reviewer field in the body; `SessionUser.username` is written to the graph edge or `reviewed_by`.

## Failure modes

- Seed node not found (`/investigation?seed_node_id=…`) → returns empty `{nodes:[], links:[]}` rather than 404; frontend renders an "expand to populate" hint.
- AOI scope filter dropped if no node carries `aoi_postgis_id` matching the requested AOI → returns nothing rather than the unfiltered global slice.
- PostGIS query in `/site-composition` failures are logged and the `recent_detections` bucket is empty; the Neo4j side still returns.
- `/path` with max_depth > 8 → 422 from Pydantic; >50 paths between two nodes → 10-result `LIMIT` clamps response.
- Candidate-edge promote on an already-reviewed row → HTTP 409 with current row status/reviewer; missing row remains HTTP 404.
- `/colocation` with an unknown `method` → 400 from the `ValueError` raised by `build_proximity_edges`.
- `/gnn/suggest-links` when torch is not in the image → 503 (`GNNUnavailable`); `/gnn/status` reports `ready: false`.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — approved redesign that introduced the Phase 1 endpoints.
- [backend/graph-schema.md](../backend/graph-schema.md) — uniqueness constraints these queries rely on.
- [backend/graph-writes.md](../backend/graph-writes.md) — write helpers used by `/promote`.
- [backend/database-connections.md](../backend/database-connections.md)
- [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)
- [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md)
- [decisions/why-reject-double-review-with-409.md](../decisions/why-reject-double-review-with-409.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
- [backend/graph-proximity.md](../backend/graph-proximity.md), [backend/graph-metrics.md](../backend/graph-metrics.md), [backend/graph-pyg.md](../backend/graph-pyg.md) — Phase 6 analytics modules
- [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md), [decisions/why-rustworkx-graph-metrics.md](../decisions/why-rustworkx-graph-metrics.md), [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md)
