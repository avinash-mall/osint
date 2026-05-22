# Imagery Router (`/api/imagery/*`, `/api/basemap/countries`)

**Path:** [backend/routers/imagery.py](../../backend/routers/imagery.py)
**Lines:** ~166
**Depends on:** [backend/change_detection.py](../../backend/change_detection.py), [backend/database.py](../../backend/database.py), [backend/geometry.py](../../backend/geometry.py), [backend/imagery_metadata.py](../../backend/imagery_metadata.py)

## Purpose

Read endpoints for satellite passes, COG tile URLs, per-pass band statistics, plus a basemap endpoint for the Natural-Earth country layer.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/imagery/change` | [imagery.py#L26](../../backend/routers/imagery.py#L26) | Two-pass change detection (delegates to `change_detection.compute_change`) |
| `GET` | `/api/imagery` | [imagery.py#L46](../../backend/routers/imagery.py#L46) | Satellite passes; filters `bbox`, `start_time`, `end_time`, `sensor_type` |
| `GET` | `/api/imagery/{pass_id}/tiles` | [imagery.py#L96](../../backend/routers/imagery.py#L96) | TiTiler tile-URL template for this pass |
| `GET` | `/api/imagery/{pass_id}/bands` | [imagery.py#L110](../../backend/routers/imagery.py#L110) | Per-band min/max/mean/stddev |
| `GET` | `/api/basemap/countries` | [imagery.py#L146](../../backend/routers/imagery.py#L146) | Natural Earth country polygons (cached) |

## Why this design

- **Tile URL generated, not the tile** — endpoint returns a string the client gives to Leaflet; tiles flow direct nginx → titiler with 24h cache.
- **Per-band stats precomputed at ingest** — UI's "Adjust contrast" tool doesn't re-read the COG.
- **`/api/imagery/change` here, not `analytics`** — strictly a two-raster diff; the analytics router's change endpoint is the general AOI-bounded version.
- **`native_max_zoom` computed per row** in `GET /api/imagery` by `imagery_metadata.native_max_zoom` from each pass's stored COG `metadata` (GSD via `width`/`bounds`/`crs`). Frontend SAT `TileLayer` feeds it into Leaflet's `maxNativeZoom` → high-GSD passes inspected tight without TiTiler upsampling above the COG's true resolution. Computed on read (not stored) → passes ingested before the field existed get it too. See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend/imagery-metadata-hashing.md](../backend/imagery-metadata-hashing.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
