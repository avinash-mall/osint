# Imagery Router (`/api/imagery/*`, `/api/basemap/countries`)

**Path:** [backend/routers/imagery.py](../../backend/routers/imagery.py)
**Lines:** ~155
**Depends on:** [backend/change_detection.py](../../backend/change_detection.py), [backend/database.py](../../backend/database.py), [backend/geometry.py](../../backend/geometry.py)

## Purpose

Read endpoints for satellite passes, COG tile URLs, and per-pass band statistics, plus a small basemap endpoint for the Natural-Earth country layer.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/imagery/change` | [imagery.py#L25](../../backend/routers/imagery.py#L25) | Two-pass change detection (delegate to `change_detection.compute_change`) |
| `GET` | `/api/imagery` | [imagery.py#L45](../../backend/routers/imagery.py#L45) | Satellite passes; filters `bbox`, `start_time`, `end_time`, `sensor_type` |
| `GET` | `/api/imagery/{pass_id}/tiles` | [imagery.py#L85](../../backend/routers/imagery.py#L85) | TiTiler tile-URL template for this pass |
| `GET` | `/api/imagery/{pass_id}/bands` | [imagery.py#L99](../../backend/routers/imagery.py#L99) | Per-band min/max/mean/stddev |
| `GET` | `/api/basemap/countries` | [imagery.py#L135](../../backend/routers/imagery.py#L135) | Natural Earth country polygons (cached) |

## Why this design

- **Tile URL is generated, not the tile itself.** The endpoint returns a string the client gives to Leaflet; tiles flow direct from nginx → titiler with a 24h cache.
- **Per-band stats** are precomputed at ingest time so the UI's "Adjust contrast" tool doesn't have to re-read the COG.
- **`/api/imagery/change`** is here (not in `analytics`) because it's strictly a two-raster diff — the analytics router's change endpoint is the more general AOI-bounded version.

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
