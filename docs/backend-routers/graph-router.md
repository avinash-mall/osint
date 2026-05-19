# Graph Router (`/api/graph`, `/api/geotime/features`)

**Path:** [backend/routers/graph.py](../../backend/routers/graph.py)
**Lines:** ~163
**Depends on:** [backend/database.py](../../backend/database.py) (`db` for Neo4j + `postgis_db`), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Read-only Neo4j entity graph for the Link Graph workspace and geotime feature queries used by the map. No mutations — node and edge creation is done by the worker during ingest and by the candidate-link approval workflow.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/graph` | [graph.py#L13](../../backend/routers/graph.py#L13) | Returns up to 1000 nodes + edges (limit hard-coded) |
| `POST` | `/api/graph/neighborhood` | [graph.py#L79](../../backend/routers/graph.py#L79) | Subgraph around a seed node with configurable depth |
| `GET` | `/api/geotime/features` | [graph.py#L110](../../backend/routers/graph.py#L110) | Static features (Bases, LaunchPoints) + asset track history within a bbox/time range |

## Why this design

- **1000-node cap** keeps the Link Graph workspace navigable in `react-force-graph` — past that it becomes unreadable. Neighborhood queries let the operator drill in.
- **`/api/geotime/features` overlaps with PostGIS** (`Base`, `LaunchPoint` nodes live in Neo4j but their footprint geometry is in PostGIS). This router pulls both and merges them server-side to keep the map call a single round-trip.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [frontend/workspace-link-graph.md](../frontend/workspace-link-graph.md)
