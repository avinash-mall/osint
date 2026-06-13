# AOI Router (`/api/aois`)

**Path:** [backend/routers/aois.py](../../backend/routers/aois.py)
**Lines:** ~268
**Depends on:** [backend/database.py](../../backend/database.py), [backend/graph_writes.py](../../backend/graph_writes.py), [backend/platform_schema.py](../../backend/platform_schema.py)

## Purpose

CRUD for the PostGIS `aois` table plus on-write projection into Neo4j: when an AOI carries `metadata.aoi_kind ∈ {base, launchpoint, launch_point, facility}`, a matching `:Base` / `:LaunchPoint` / `:Facility` node is MERGEd with `aoi_postgis_id` carrying the PostGIS row id. This is the first instance of the projector pattern that Phase 2 generalises.

## Why this design

Before Phase 1.D the `aois` table existed (defined at [platform_schema.py#L201-L215](../../backend/platform_schema.py#L201-L215)) but had no HTTP write path — only the satellite worker read `default_allegiance` from it. The Link Graph redesign needs `Base`/`LaunchPoint`/`Facility` to be analyst-visible nodes; rather than introduce a fully new `static_features` table for them, we re-use AOIs and tag the meaningful ones via `metadata.aoi_kind`. Bonus: the AOI polygon is already in PostGIS, which is what the Phase 1 `/api/graph/site-composition` ST_DWithin query needs.

The Neo4j mirror carries only identity + centroid lat/lon — the spatial source of truth stays in PostGIS, per [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md).

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET`  | `/api/aois`             | [aois.py#L131](../../backend/routers/aois.py#L131) | List up to 200 most-recent AOIs with geometry as GeoJSON |
| `GET`  | `/api/aois/{aoi_id}`    | [aois.py#L154](../../backend/routers/aois.py#L154) | Single AOI, geometry as GeoJSON |
| `POST` | `/api/aois`             | [aois.py#L181](../../backend/routers/aois.py#L181) | Insert AOI; project to Neo4j if `aoi_kind` set |
| `PATCH`| `/api/aois/{aoi_id}`    | [aois.py#L210](../../backend/routers/aois.py#L210) | Partial update; re-project on `aoi_kind` change; remove mirror when cleared |
| `DELETE` | `/api/aois/{aoi_id}`  | [aois.py#L249](../../backend/routers/aois.py#L249) | Cascade: PostGIS DELETE + Neo4j mirror removal |

## Inputs / Outputs

Local Pydantic models (not in [schemas.py](../../backend/schemas.py) since they're only used here): `AOICreate`, `AOIUpdate`. Geometry is required to be a GeoJSON `Polygon` on create.

Response always wraps the row in `{success, aoi, ...}` for POST/PATCH/DELETE. List/GET return the raw payload + parsed `geometry` GeoJSON.

## Failure modes

- Non-Polygon `geometry` on create → 400.
- Patch with no fields → 400.
- Missing AOI on GET/PATCH/DELETE → 404.
- Neo4j unreachable during projection → logged via [graph_writes](../backend/graph-writes.md); the PostGIS write still succeeds and [scripts/backfill_base_launchpoint_from_aois.py](../../backend/scripts/backfill_base_launchpoint_from_aois.py) reconciles.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 1.D section.
- [backend/graph-writes.md](../backend/graph-writes.md) — `merge_site_from_aoi`, `delete_site_for_aoi`.
- [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md) — `aois` table definition.
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [backend-routers/graph-router.md](graph-router.md) — `/api/graph/site-composition/{base_id}` consumes the mirrors built here.
