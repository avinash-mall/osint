# SAT Tiles Cap At The COG's Native Zoom

## Decision

The map's satellite `TileLayer` no longer fetches TiTiler tiles past the COG's real pixel resolution. Three coordinated changes:

1. **Frontend** ([MapStage.tsx](../../frontend/src/components/map/MapStage.tsx)) — the SAT `TileLayer` gained `maxNativeZoom`, `keepBuffer={6}`, `updateWhenZooming={false}`, a `.webp` tile-format extension.
2. **Nginx** ([nginx/tile-proxy.conf](../../nginx/tile-proxy.conf)) — the `/tiles/` location dropped `proxy_cache_lock_timeout` from 5 s to 2 s, added `proxy_cache_background_update on`.
3. **Backend** ([imagery_metadata.py](../../backend/imagery_metadata.py), [routers/imagery.py](../../backend/routers/imagery.py)) — `GET /api/imagery` now returns a per-pass `native_max_zoom`, computed from the COG's GSD.

## Why

The SAT `TileLayer` was configured `maxZoom={22}` with **no `maxNativeZoom`** — unlike the basemap/terrain layers in the same file, which correctly cap at `maxNativeZoom={BASEMAP_OVERLAY_MAX_ZOOM}` (their pre-baked pyramid ceiling — currently z=14; see [why-basemap-z14-cap.md](why-basemap-z14-cap.md)). With no native-zoom ceiling, every notch the analyst zoomed past the COG's real resolution triggered a fresh round-trip to TiTiler for tiles that do not exist as native pixels. TiTiler answered by re-reading its highest overview and resampling on the fly. That request storm:

- blocked behind `proxy_cache_lock` (up to 5 s) for any uncached tile;
- returned upsampled tiles no sharper than what Leaflet could produce client-side from the native-zoom tiles;
- hid the previously-loaded crisp tiles behind a slow-painting layer — what the analyst perceived as "stays blurry for several seconds".

Past `maxNativeZoom`, Leaflet upscales the already-cached native tile client-side instead — instant, visually identical to TiTiler's server-side resample.

## How

- **`native_max_zoom(metadata, default=18)`** derives ground sample distance from the stored raster `width` + `bounds` + `crs`. Projected CRSes (UTM, Web Mercator) use the bounds span directly as metres; geographic CRSes (`EPSG:4326`/`CRS84`) get a cos-latitude metres conversion. Native zoom = `round(log2(R0 / gsd))` where `R0 = 156543.034` m/px is the zoom-0 WebMercatorQuad tile resolution; result clamped to `[10, 24]`. Missing/degenerate tags fall back to `default` → the API never emits `null`.
- Computed **on read** in `GET /api/imagery`, not stored at ingest → passes ingested before the field existed still get it.
- Frontend uses `selectedImageryData.native_max_zoom ?? 18` — 18 is the conservative fallback if the API field is ever absent.

## Smaller contributors fixed alongside

- **`keepBuffer={6}`** (Leaflet default 2) — keeps a wider ring of tiles alive so the map doesn't degrade to bare tiles mid-gesture.
- **`updateWhenZooming={false}`** — Leaflet no longer fires (then discards) requests for every intermediate zoom level during a pinch/scroll.
- **`.webp` tile format** — ~5× smaller on the wire than PNG for 3-band RGB imagery, no visible quality loss. TiTiler 2.0.2 selects the encoder from the tile path extension (`{z}/{x}/{y}.webp`), **not** a `?format=` query param — a query param would be silently ignored and still serve PNG.
- **`proxy_cache_lock_timeout 2s`** — a waiting cold-cache request falls through to its own TiTiler call after 2 s instead of stalling the whole viewport behind one slow fetch.
- **`proxy_cache_background_update on`** — a re-visited pass paints the stale tile immediately, refreshes it in the background once the 24 h cache TTL lapses.

## Trade-offs accepted

- `native_max_zoom` recomputed on every `/api/imagery` call. The maths is a handful of float ops over already-fetched JSONB — negligible next to the PostGIS query, avoids a schema/backfill migration.
- A COG with missing `width`/`bounds` metadata falls back to zoom 18, which may still over-/under-fetch slightly for unusual GSDs. Acceptable: the blur/storm pathology is gone either way, and well-formed COGs (the norm from the ingest COG-translate step) get the exact value.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [backend/imagery-metadata-hashing.md](../backend/imagery-metadata-hashing.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [deployment/nginx-gateway-and-tile-cache.md](../deployment/nginx-gateway-and-tile-cache.md)
