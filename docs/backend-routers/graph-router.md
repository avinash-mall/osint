# Graph Router (`/api/graph`, `/api/geotime/features`)

**Path:** [backend/routers/graph.py](../../backend/routers/graph.py)
**Lines:** ~163
**Depends on:** [backend/database.py](../../backend/database.py) (`db` for Neo4j + `postgis_db`), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Read-only Neo4j entity graph for the Link Graph workspace + geotime feature queries for the map. No mutations — node/edge creation done by the worker during ingest and by the candidate-link approval workflow.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/graph` | [graph.py#L13](../../backend/routers/graph.py#L13) | Up to 1000 nodes + edges (limit hard-coded) |
| `POST` | `/api/graph/neighborhood` | [graph.py#L79](../../backend/routers/graph.py#L79) | Subgraph around a seed node, configurable depth |
| `GET` | `/api/geotime/features` | [graph.py#L110](../../backend/routers/graph.py#L110) | Static features (Bases, LaunchPoints) + asset track history within a bbox/time range |

## Why this design

- **1000-node cap** keeps the Link Graph navigable in `react-force-graph` — past that it's unreadable. Neighborhood queries let the operator drill in.
- **Each link carries `predicate`** (the Neo4j relationship type) alongside `type` → Link Graph labels and filters edges by semantic (UX-AUDIT F22).
- **`/api/geotime/features` overlaps PostGIS** — `Base`/`LaunchPoint` nodes live in Neo4j but their footprint geometry is in PostGIS. Router pulls both, merges server-side → single round-trip for the map.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
