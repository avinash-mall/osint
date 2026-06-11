# Why an opt-in Martin MVT detection layer (Phase 2)

**Status:** shipped behind a flag (`VITE_DETECTION_TILES`, default OFF).

## Context

The Map workspace renders persisted detections as **one react-leaflet
`<Polygon>` per feature** (`MapStage.tsx`, fed by `filteredDetectionsGeoJSON`
→ `geomDisplayedDetectionsGeoJSON`). This is correct and the default, but a
dense pass can return thousands of features (tens of MB of GeoJSON with full
ontology/metadata) and paints thousands of SVG paths — heavy on both the wire
and the DOM.

Backend Phase 1 added a Martin-style MVT source: `GET
/maps/detections_mvt/{z}/{x}/{y}?v=<version>` (single `detections` layer, each
feature a Polygon with `id, class, confidence, branch_id, parent_class,
original_class, icon_key, label, threat_level, allegiance, review_status,
pass_id`), a per-id `GET /api/detections/{id}/enriched` for selection, and a
`GET /api/detections/tile-version` cache-bust token.

## Decision

Add `frontend/src/components/map/DetectionTileLayer.tsx`, an **opt-in** vector-
tile layer using the `leaflet.vectorgrid` plugin (`L.vectorGrid.protobuf`),
gated by `USE_DETECTION_TILES = import.meta.env.VITE_DETECTION_TILES === '1'`
in `MapStage.tsx`. When the flag is on, the per-feature `<Polygon>` box layer is
skipped and the tile layer renders instead; everything else (icon markers,
dots, position-uncertainty halos, the live `detections_partial` preview) stays
on the existing GeoJSON path. **Default OFF — the box path is unchanged.**

### Why these specific choices

- **Pure Leaflet, no MapLibre.** The workspace is Leaflet 1.9 + react-leaflet 5.
  `leaflet.vectorgrid` is the Leaflet-native MVT renderer. No `@types` package
  ships, so a one-line ambient `declare module 'leaflet.vectorgrid'` lives in
  `src/types/leaflet-vectorgrid.d.ts`. The dep is baked into the air-gapped image
  at build time — no runtime CDN. **It needs a global `L`** (see Runtime gotchas).
- **Imperative layer, not a JSX child.** VectorGrid has no react-leaflet
  wrapper, so the component is a `useMap()` + `useEffect` attach/detach with a
  `return null`.
- **Re-create on change, not `setFeatureStyle` (Phase 2 simplicity).** The
  effect re-creates the layer whenever the cache-bust `version` or any client-
  side filter (confidence threshold, SOLO class, hidden categories) changes.
  Per-feature restyle is a later optimisation.
- **Styling parity via `branch_id`.** The tile carries `branch_id` directly —
  the exact value `branchIdForFeature` returns for the GeoJSON path — so
  `styleForTileProps` maps `branch_id → category → categoryFor().color` and
  reproduces `makeDetectionStyle` (`confidence > 0.85 ? 0.14 : 0.05` fill
  opacity, `HEAVY_OUTLINE_CATEGORIES` weight 2.4 vs 2, `Military_Forces`
  `6,3` dash). Filtered-out features return `{stroke:false,fill:false}` so
  VectorGrid hides them — the *same* predicates as `filteredDetectionsGeoJSON`.
- **Selection via `/enriched`.** Tile features carry only the tile props, so a
  click reads the feature `id` and fetches the fully-enriched GeoJSON Feature
  (same ~39-prop shape as `/api/detections/geojson`), handed to the same
  `setSelectedDetection` the boxes use — the SelectionPanel works identically.
- **Live streaming stays on GeoJSON.** Tiles are static; `detections_partial`
  previews keep rendering via the existing path. On the authoritative
  `detections_updated`, `GaiaMap` re-fetches `tile-version` and bumps
  `detectionTileVersion` so persisted tiles refresh after an ingest/delete.

## Runtime gotchas (load-bearing — found via headless validation)

- **`leaflet.vectorgrid` needs a global `L`.** It is a UMD plugin that registers
  on a global `L` at module-eval and has *no import* of leaflet for the bundler
  to order. A top-level `import 'leaflet.vectorgrid'` threw `ReferenceError: L is
  not defined` and white-screened the map. Fix, both halves required: (1)
  vite.config isolates it into its own `vendor-vectorgrid` chunk so it is **not**
  lumped into the eager `vendor-misc`; (2) `DetectionTileLayer` sets
  `window.L = L` then **dynamically** `await import('leaflet.vectorgrid')` inside
  the effect, so the plugin chunk loads lazily after the global exists.
- **Empty tiles return 204, not 404.** `ST_AsMVT` over zero rows is NULL, which
  Martin serves as 404 — noisy in the console and uncacheable over empty
  ocean/land at low zoom. The function does `RETURN COALESCE(mvt, ''::bytea)` so
  an empty tile is a cacheable 0-byte 204.
- **Enable via build arg, not a runtime env.** `VITE_DETECTION_TILES` is a
  frontend Docker build arg (default `0`, wired in docker-compose); Vite inlines
  `import.meta.env` at build, so toggling needs `docker compose build frontend`
  with `VITE_DETECTION_TILES=1`, not a container env change.

## Validation status

Backend fully tested (real tiles: 2269 features on the z14 airport tile, 12
props, `det_class`/`pass_id` filters, 204 empties). Frontend runtime-validated
headless (Chromium): flag ON → the layer instantiates and fires `detections_mvt`
tile requests (200 with data / 204 empty), no JS errors; flag OFF → zero MVT
requests, box path unchanged. **Visual render-parity vs the box layer has NOT
been eyeballed in a browser** — that, plus the cutover decision, is the
remaining work.

## Known parity gaps (vs the per-feature box layer)

- **Box geometry modes**: tiles carry the raw mask polygon; the GEOM toolbar's
  OBB/HBB rewrite (from `metadata.geo_polygon`) is not applied — mask only.
- **Density modes**: icon markers (≤800) / dots (>800) and uncertainty halos are
  not reproduced in the tile path (polygons only).
- **Live `detections_partial`** previews stay on the GeoJSON path; they enter
  tiles only after `detections_updated` bumps the version.

## Consequences

- Flag stays OFF by default until render-parity is eyeballed and cutover is
  decided; enabling is a one-line build arg.
- `det_class` / `pass_id` server-side tile filters are intentionally **not** used
  by the map — filtering is client-side to match the box layer's filter
  semantics without re-fetching tiles (the params exist for other callers).

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/why-detection-boxes-use-polygon-map.md](why-detection-boxes-use-polygon-map.md)
- [decisions/why-live-streaming-detections.md](why-live-streaming-detections.md)
