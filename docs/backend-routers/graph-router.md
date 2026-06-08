# Graph Router (`/api/graph`, `/api/geotime/features`)

**Path:** [backend/routers/graph.py](../../backend/routers/graph.py)
**Lines:** ~1037
**Depends on:** [backend/auth.py](../../backend/auth.py), [backend/database.py](../../backend/database.py) (`db` for Neo4j + `postgis_db`), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Neo4j-backed read endpoints for the Link Graph workspace + the Phase 1 redesign endpoints. Write operations limited to candidate-edge promotion; node/edge creation happens elsewhere (worker during ingest, candidate-approval in [main.py](../../backend/main.py), Phase 2+ projectors).

## Endpoints

### Back-compat (pre-redesign)

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/graph`                | [graph.py#L84](../../backend/routers/graph.py#L84) | Up to 1500 nodes + edges; `include_candidates` flag toggles `CANDIDATE_*` filtering |
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

## Why this design

- **Synthesis removed.** The pre-redesign `/api/graph` route inlined a PostGIS query to fabricate `CANDIDATE_DETECTED_AS` edges in memory because the edge wasn't persisted. After Phase 1.B persists them ([decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)), the synthesis block is dead and was deleted — the existing Cypher loop returns candidate edges naturally.
- **Investigation mode is bounded.** `react-force-graph-2d` chokes past ~150 useful nodes ([architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)), so the default query pulls ≤80 operational nodes + their 1-hop expansion, then truncates to `limit`. Drill-in continues via `/api/graph/neighborhood`.
- **Path query uses `allShortestPaths`.** Returns up to 10 shortest paths at the target depth — analysts want to see *all* equally-short routes, not just one arbitrary one.
- **Site composition stays a live PostGIS join in Phase 1.** Precomputing `NEAR` edges is Phase 4 work; until then the ST_DWithin against the AOI centroid is fast enough for one-site queries. The endpoint accepts a `radius_m` override so analysts can probe different reaches.
- **Candidate promote runs PostGIS UPDATE first, then Neo4j.** Same ordering as `/approve` — PostGIS is source of truth, graph is derived. The UPDATE is guarded with `status='pending'`, uses the signed session username for `reviewed_by`, and returns 409 for stale review attempts. If the graph step finds no matching edge (e.g., projection lagged), the endpoint falls back to delete-by-pair so re-render is clean.

## Inputs / Outputs

Pydantic models in [schemas.py](../../backend/schemas.py): `GraphActionRequest`, `GraphPathRequest`, `GraphContradictRequest`.

Response shape across the new endpoints: `{nodes, links, ...meta}` where each node is `{id, label, labels, properties}` and each link is `{source, target, type, predicate, candidate, properties}`. The `predicate` field is what the frontend `PredicateChipBar` filters on ([frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)).

`POST /api/graph/contradict` and `POST /api/graph/candidate-edges/{candidate_id}/promote` take no reviewer field in the body; `SessionUser.username` is written to the graph edge or `reviewed_by`.

## Failure modes

- Seed node not found (`/investigation?seed_node_id=…`) → returns empty `{nodes:[], links:[]}` rather than 404; frontend renders an "expand to populate" hint.
- AOI scope filter dropped if no node carries `aoi_postgis_id` matching the requested AOI → returns nothing rather than the unfiltered global slice.
- PostGIS query in `/site-composition` failures are logged and the `recent_detections` bucket is empty; the Neo4j side still returns.
- `/path` with max_depth > 8 → 422 from Pydantic; >50 paths between two nodes → 10-result `LIMIT` clamps response.
- Candidate-edge promote on an already-reviewed row → HTTP 409 with current row status/reviewer; missing row remains HTTP 404.

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
