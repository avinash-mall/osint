# Analytics Router (`/api/analytics/*`)

**Path:** [backend/routers/analytics.py](../../backend/routers/analytics.py)
**Lines:** ~284
**Depends on:** [backend/change_detection.py](../../backend/change_detection.py), [backend/terrain.py](../../backend/terrain.py), [backend/routing.py](../../backend/routing.py), [backend/geometry.py](../../backend/geometry.py)

## Purpose

Spatial analyses from the **Analytics Tools** panel. Each endpoint has a fixture-fallback so the UI works when DEM / routing graph files are missing — see [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md), [backend/routing-graph-osmnx.md](../backend/routing-graph-osmnx.md).

## Endpoints

| Method | Path | Source | Computes |
|---|---|---|---|
| `POST` | `/api/analytics/change` | [analytics.py#L56](../../backend/routers/analytics.py#L56) | Two-pass raster change polygons |
| `POST` | `/api/analytics/viewshed` | [analytics.py#L101](../../backend/routers/analytics.py#L101) | DEM viewshed polygon around observer |
| `POST` | `/api/analytics/los` | [analytics.py#L146](../../backend/routers/analytics.py#L146) | Line-of-sight result between two points |
| `POST` | `/api/analytics/routes` | [analytics.py#L218](../../backend/routers/analytics.py#L218) | Shortest routes on the pickled osmnx graph |
| `POST` | `/api/analytics/pol` | [analytics.py#L243](../../backend/routers/analytics.py#L243) | Patterns-of-life over a time window in an AOI |
| `GET` | `/api/analytics/capabilities` | [analytics.py#L264](../../backend/routers/analytics.py#L264) | Booleans: `dem_available`, `routing_graph_available` |
| `GET` | `/api/analytics/jobs` | [analytics.py#L274](../../backend/routers/analytics.py#L274) | Past analytics jobs |

## Why this design

- **Capabilities endpoint** — frontend disables buttons up-front when DEM/graph missing. Body returns `{mode: "fixture_no_dem"}` instead of failing → demo deployment without `/data/dem/dem.tif` stays navigable.
- **Each analysis is a `POST`** (not `GET`) — AOI body can be large (multi-polygon) and is part of the cache key.

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend/routing-graph-osmnx.md](../backend/routing-graph-osmnx.md)
- [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md)
