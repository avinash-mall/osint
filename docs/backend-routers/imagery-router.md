# Imagery Router (`/api/imagery/*`, `/api/basemap/countries`, `/api/dossier`)

**Path:** [backend/routers/imagery.py](../../backend/routers/imagery.py)
**Lines:** ~260
**Depends on:** [backend/change_detection.py](../../backend/change_detection.py), [backend/database.py](../../backend/database.py) (`db` Neo4j + `postgis_db`), [backend/auth.py](../../backend/auth.py) (`require_admin`), [backend/geometry.py](../../backend/geometry.py), [backend/imagery_metadata.py](../../backend/imagery_metadata.py)

## Purpose

Read endpoints for satellite passes, COG tile URLs, per-pass band statistics, an admin hard-delete for a pass, plus a basemap endpoint for the Natural-Earth country layer.

## Endpoints

| Method | Path | Source | Behavior |
|---|---|---|---|
| `POST` | `/api/imagery/change` | [imagery.py#L27](../../backend/routers/imagery.py#L27) | Two-pass change detection (delegates to `change_detection.compute_change`) |
| `GET` | `/api/imagery` | [imagery.py#L47](../../backend/routers/imagery.py#L47) | Satellite passes; filters `bbox`, `start_time`, `end_time`, `sensor_type` |
| `GET` | `/api/imagery/{pass_id}/tiles` | [imagery.py#L97](../../backend/routers/imagery.py#L97) | TiTiler tile-URL template for this pass |
| `DELETE` | `/api/imagery/{pass_id}` | [imagery.py#L110](../../backend/routers/imagery.py#L110) | **Admin.** Hard-delete: drops `detections` + the `satellite_passes` row, removes the COG file, `DETACH DELETE`s the Neo4j `SatellitePass`+`Detection` nodes, and purges the no-FK orphans (`object_details`, now-empty `detection_tracks` + their `operational_entity_tracks` links) via [cascade_delete.py](../../backend/cascade_delete.py). File/graph cleanup best-effort; 404 if missing. See [decisions/why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md) and [backend/cascade-delete.md](../backend/cascade-delete.md). |
| `GET` | `/api/imagery/{pass_id}/bands` | [imagery.py#L155](../../backend/routers/imagery.py#L155) | Per-band min/max/mean/stddev |
| `GET` | `/api/basemap/countries` | [imagery.py#L190](../../backend/routers/imagery.py#L190) | Natural Earth country polygons (cached) |
| `GET` | `/api/dossier` | [imagery.py#L215](../../backend/routers/imagery.py#L215) | **Offline area dossier (Tier C).** `lat`/`lon` → country via point-in-polygon over baked `ne_countries` (name/admin/ISO3/pop/GDP) + detection count within 25 km. Backs the map right-click popup. |

## Why this design

- **Tile URL generated, not the tile** — endpoint returns a string the client gives to Leaflet; tiles flow direct nginx → titiler with 24h cache.
- **Dossier is fully offline** — country facts come from the locally-baked `ne_countries` table (loaded by [init_postgis.sql](../../backend/init_postgis.sql)), not Wikipedia/Wikidata; works air-gapped. The deliberate offline analogue of ShadowBroker's online country dossier. See [decisions/why-offline-area-dossier.md](../decisions/why-offline-area-dossier.md).
- **Per-band stats precomputed at ingest** — UI's "Adjust contrast" tool doesn't re-read the COG.
- **`/api/imagery/change` here, not `analytics`** — strictly a two-raster diff; the analytics router's change endpoint is the general AOI-bounded version.
- **`native_max_zoom` computed per row** in `GET /api/imagery` by `imagery_metadata.native_max_zoom` from each pass's stored COG `metadata` (GSD via `width`/`bounds`/`crs`). Frontend SAT `TileLayer` feeds it into Leaflet's `maxNativeZoom` → high-GSD passes inspected tight without TiTiler upsampling above the COG's true resolution. Computed on read (not stored) → passes ingested before the field existed get it too. See [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md).

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend/imagery-metadata-hashing.md](../backend/imagery-metadata-hashing.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [decisions/why-sat-tiles-cap-at-native-zoom.md](../decisions/why-sat-tiles-cap-at-native-zoom.md)
