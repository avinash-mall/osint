# Graph Router (`/api/graph`, `/api/geotime/features`)

**Path:** [backend/routers/graph.py](../../backend/routers/graph.py)
**Lines:** ~380
**Depends on:** [backend/database.py](../../backend/database.py) (`db` for Neo4j + `postgis_db`), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/schemas.py](../../backend/schemas.py)

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
| `GET`  | `/api/graph/site-composition/{base_id}` | [graph.py#L329](../../backend/routers/graph.py#L329) | Workflow 3 — "what's at this site?" Returns recent-detections histogram (live PostGIS ST_DWithin against the AOI centroid) plus Neo4j Vessels/Vehicles/Aircraft/other Assets observed at the site. FMV/Reports placeholders empty until Phase 2 projectors. |
| `POST` | `/api/graph/candidate-edges/{candidate_id}/promote` | [graph.py#L387](../../backend/routers/graph.py#L387) | Graph-side equivalent of `/api/detection-target-candidates/{id}/approve`. Flips PostGIS row to `approved` AND promotes the Neo4j `CANDIDATE_DETECTED_AS` edge into `DETECTED_AS`. |

## Why this design

- **Synthesis removed.** The pre-redesign `/api/graph` route inlined a PostGIS query to fabricate `CANDIDATE_DETECTED_AS` edges in memory because the edge wasn't persisted. After Phase 1.B persists them ([decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)), the synthesis block is dead and was deleted — the existing Cypher loop returns candidate edges naturally.
- **Investigation mode is bounded.** `react-force-graph-2d` chokes past ~150 useful nodes ([architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)), so the default query pulls ≤80 operational nodes + their 1-hop expansion, then truncates to `limit`. Drill-in continues via `/api/graph/neighborhood`.
- **Path query uses `allShortestPaths`.** Returns up to 10 shortest paths at the target depth — analysts want to see *all* equally-short routes, not just one arbitrary one.
- **Site composition stays a live PostGIS join in Phase 1.** Precomputing `NEAR` edges is Phase 4 work; until then the ST_DWithin against the AOI centroid is fast enough for one-site queries. The endpoint accepts a `radius_m` override so analysts can probe different reaches.
- **Candidate promote runs PostGIS UPDATE first, then Neo4j.** Same ordering as `/approve` — PostGIS is source of truth, graph is derived. If the graph step finds no matching edge (e.g., projection lagged), the endpoint falls back to delete-by-pair so re-render is clean.

## Inputs / Outputs

Pydantic models in [schemas.py](../../backend/schemas.py): `GraphActionRequest`, `GraphPathRequest`, `GraphPromoteRequest`.

Response shape across the new endpoints: `{nodes, links, ...meta}` where each node is `{id, label, labels, properties}` and each link is `{source, target, type, predicate, candidate, properties}`. The `predicate` field is what the frontend `PredicateChipBar` filters on ([frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)).

## Failure modes

- Seed node not found (`/investigation?seed_node_id=…`) → returns empty `{nodes:[], links:[]}` rather than 404; frontend renders an "expand to populate" hint.
- AOI scope filter dropped if no node carries `aoi_postgis_id` matching the requested AOI → returns nothing rather than the unfiltered global slice.
- PostGIS query in `/site-composition` failures are logged and the `recent_detections` bucket is empty; the Neo4j side still returns.
- `/path` with max_depth > 8 → 422 from Pydantic; >50 paths between two nodes → 10-result `LIMIT` clamps response.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — approved redesign that introduced the Phase 1 endpoints.
- [backend/graph-schema.md](../backend/graph-schema.md) — uniqueness constraints these queries rely on.
- [backend/graph-writes.md](../backend/graph-writes.md) — write helpers used by `/promote`.
- [backend/database-connections.md](../backend/database-connections.md)
- [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)
- [decisions/why-three-graph-modes.md](../decisions/why-three-graph-modes.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
