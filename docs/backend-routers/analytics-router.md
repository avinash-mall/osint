# Analytics Router (`/api/analytics/*`)

**Path:** [backend/routers/analytics.py](../../backend/routers/analytics.py)
**Lines:** ~285
**Depends on:** [backend/change_detection.py](../../backend/change_detection.py), [backend/terrain.py](../../backend/terrain.py), [backend/routing.py](../../backend/routing.py), [backend/geometry.py](../../backend/geometry.py)

## Purpose

Spatial analyses from the **Analytics Tools** panel. Each endpoint is deliberately honest about resource availability: 503 when the DEM mosaic or OSRM sidecar is missing. The legacy canned shapes are only returned when `ANALYTICS_ALLOW_FIXTURES=1` is set for an explicit demo environment — see [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md), [backend/routing-osrm.md](../backend/routing-osrm.md).

## Endpoints

| Method | Path | Source | Computes |
|---|---|---|---|
| `POST` | `/api/analytics/change` | [analytics.py#L56](../../backend/routers/analytics.py#L56) | Two-pass raster change polygons |
| `POST` | `/api/analytics/viewshed` | [analytics.py#L101](../../backend/routers/analytics.py#L101) | DEM viewshed polygon around observer |
| `POST` | `/api/analytics/los` | [analytics.py#L146](../../backend/routers/analytics.py#L146) | Line-of-sight result between two points — each obstruction is its own `Point` feature with `elevation_m` / `clearance_m` / `distance_m`; see [decisions/los-obstruction-point-features.md](../decisions/los-obstruction-point-features.md) |
| `GET`  | `/api/analytics/elevation` | [analytics.py#L210](../../backend/routers/analytics.py#L210) | DEM elevation at a single (lat, lon); used by the SelectionPanel ELEV row |
| `POST` | `/api/analytics/routes` | [analytics.py#L245](../../backend/routers/analytics.py#L245) | Up to three driving routes via the OSRM sidecar |
| `POST` | `/api/analytics/pol` | [analytics.py#L270](../../backend/routers/analytics.py#L270) | Patterns-of-life heatmap: clusters `track_points` into ~0.02° grid cells (`ST_SnapToGrid`) and returns each cell centroid + count. (Previously also grouped by raw lon/lat, which defeated the clustering — every point became its own cell.) |
| `GET` | `/api/analytics/capabilities` | [analytics.py#L291](../../backend/routers/analytics.py#L291) | Booleans: `dem`, `routing`, `demo_fixtures` |
| `GET` | `/api/analytics/jobs` | [analytics.py#L301](../../backend/routers/analytics.py#L301) | Past analytics jobs |

## Why this design

- **Capabilities endpoint** — frontend uses it to disable buttons up-front and surface a `ROUTING · NONE` chip when OSRM is unreachable, so the analyst sees the system state before clicking. Renamed `routing_graph` → `routing` when the implementation moved from a pickled networkx graph to OSRM.
- **Each analysis is a `POST`** (not `GET`) — AOI body can be large (multi-polygon) and is part of the cache key.
- **Routes mode literal is `"osrm"`** — replaces the older `"graph"` mode since the route now comes from the OSRM sidecar over HTTP.

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-osrm.md](../backend/routing-osrm.md)
- [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md)
