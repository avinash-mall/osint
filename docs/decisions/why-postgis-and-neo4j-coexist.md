# Why PostGIS and Neo4j Both Exist

## Decision

Sentinel runs **two** databases. PostGIS owns spatial data and most operational tables; Neo4j owns the entity/link graph.

## Why

The two access patterns have nothing in common:

- **Spatial joins** ("detections inside this AOI between these times, filtered by class and confidence") want PostGIS — GiST indexes, `ST_Intersects`, partial indexes per sensor. Neo4j's spatial layer is comparatively weak, not integrated with vector tiles.
- **Graph traversals** ("which Targets has this Asset been observed near, transitively through Observations") want Cypher — variable-depth `MATCH`, shortest path, neighborhood queries. PostgreSQL with recursive CTEs works but is awkward and slow at depth >2.

Forcing one workload into the other database costs at least one of: query expressiveness, indexing, operator UX. Two databases costs operations: two backups, two upgrade paths, two failure modes. We chose the latter.

## Mapping

| Concept | Database | Notes |
|---|---|---|
| Detection (with mask RLE, embedding, footprint) | PostGIS | `detections` table; vector tile via martin |
| FMV clip / frame / detection | PostGIS | `fmv_clips`, `fmv_frames`, `fmv_detections` |
| Satellite pass footprint | PostGIS **and** Neo4j | PostGIS for the `MULTIPOLYGON`; Neo4j `SatellitePass` node for graph traversal |
| Ontology branches/objects/prompts | PostGIS | canonical source; seed JSON is bootstrap-only |
| `auth_config` (LDAP settings) | PostGIS | singleton row |
| Target, Asset, Observation, Base, LaunchPoint | Neo4j | nodes |
| Edges (`OBSERVED_AT`, `OBSERVED_BY`, `WITHIN`, `LAUNCHED_FROM`, ...) | Neo4j | |

## Synchronization

The databases are **not** synchronized. A detection lives only in PostGIS until an operator (or LLM-assisted candidate linker) resolves it to a Target — that resolution creates a Neo4j `Observation` node with `:OBSERVED_BY {detection_id, source: 'postgis'}` referring back to PostGIS. The reverse pointer is the detection's `target_id` UUID column.

If an external system needs both views, it joins by `detection_id` / `target_id` at the application layer. See [backend-routers/detections-router.md](../backend-routers/detections-router.md), [operations/candidate-link-approval.md](../operations/candidate-link-approval.md).

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [backend-routers/graph-router.md](../backend-routers/graph-router.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
