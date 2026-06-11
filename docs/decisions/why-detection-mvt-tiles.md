# Why a Martin MVT detection layer (now DEFAULT ON)

**Status:** shipped and **DEFAULT ON** (`VITE_DETECTION_TILES`, default `1`).
Set `VITE_DETECTION_TILES=0` at build time to restore the legacy fat-geojson
path. Phase 2 landed it behind a flag (default OFF); the cutover (Option A —
MVT boxes + lite-fed markers/dots + `/enriched` selection) flipped the default.

## Context

The Map workspace renders persisted detections as **one react-leaflet
`<Polygon>` per feature** (`MapStage.tsx`, fed by `filteredDetectionsGeoJSON`
→ `geomDisplayedDetectionsGeoJSON`). This is correct and the default, but a
dense pass can return thousands of features (tens of MB of GeoJSON with full
ontology/metadata) and paints thousands of SVG paths — heavy on both the wire
and the DOM.

The backend MVT source: `GET /maps/detections_mvt/{z}/{x}/{y}?v=<version>&geom_mode=<obb|hbb|mask>`
serves **two** layers — `detections` (Polygons, props `id, class, confidence,
branch_id, parent_class, original_class, icon_key, label, threat_level,
allegiance, review_status, pass_id`) and `detection_points` (centroid Points).
`geom_mode` (default `obb`) picks the polygon geometry. Selection uses the
per-id `GET /api/detections/{id}/enriched`; cache-bust is `GET
/api/detections/tile-version`. A NEW **lite feed** `GET
/api/detections/geojson-lite` returns small centroid-Point features (light
props, no polygon geometry, no fat metadata) — 2.7 MB/0.6 s for 6 441 vs
57 MB/8 s for the fat `/geojson`.

## Decision

Cutover (Option A) — DEFAULT ON:

- **Boxes** render from MVT (`DetectionTileLayer.tsx`, `L.vectorGrid.protobuf`
  via `leaflet.vectorgrid`), gated by
  `USE_DETECTION_TILES = import.meta.env.VITE_DETECTION_TILES !== '0'` in
  `MapStage.tsx`. `geomMode` (GaiaMap's `bboxMode`) is appended to the tile URL,
  so the OBB/HBB/mask toggle drives the served polygon (full geom-mode parity).
- **Markers/dots** render from the **lite feed**, kept in the existing
  `detectionsGeoJSON` state (now centroid Points). The lucide icon markers (≤800)
  and dots (>800) are PRESERVED. The MVT `detection_points` sublayer is **hidden**
  (`detection_points: () => ({ stroke:false, fill:false })`) so dots aren't doubled.
- **Selection** routes through a single `selectDetectionById(id, fallback)` in
  GaiaMap → `/api/detections/{id}/enriched` (fat shape for the SelectionPanel),
  with a fallback to the in-memory feature on 404. ALL clicks use it: MVT polygon,
  icon marker, dot, and live previews.
- **Live previews** (`detections_partial`, Polygon features) still append to
  `detectionsGeoJSON` and render via the per-feature `<Polygon>` layer (the
  `!USE_DETECTION_TILES` gate was removed — lite centroid Points yield `null` from
  `geojsonToLatLngs` so only preview polygons draw). `detections_updated`
  re-fetches the lite feed and bumps `detectionTileVersion`.

`VITE_DETECTION_TILES=0` restores the exact legacy fat-`/geojson` behavior
(per-feature `<Polygon>` boxes + markers/dots/halos from the full feed).

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
- **Selection via `/enriched` (one helper).** Tile/marker/dot features carry only
  light props, so every click hands the `id` to `selectDetectionById`, which
  fetches the fully-enriched GeoJSON Feature (same ~39-prop shape as the old
  `/api/detections/geojson` feature) for the SelectionPanel. A 404 (e.g. an
  unpersisted live preview) falls back to the in-memory feature so a click never
  throws.
- **Lite feed for the bulk fetch.** When tiles are on, GaiaMap's bulk
  `fetchDetectionFeatures` calls `/api/detections/geojson-lite` (same
  bbox/time/class params, `limit=100000`, all-at-once — no cursor pagination)
  instead of the fat `/geojson`. It drives counts, the class filter, framing, and
  the marker/dot layers.
- **Live streaming stays on GeoJSON.** Tiles are static; `detections_partial`
  previews keep rendering via the per-feature `<Polygon>` path. On the
  authoritative `detections_updated`, `GaiaMap` re-fetches the lite feed and
  `tile-version` and bumps `detectionTileVersion` so persisted tiles refresh.

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
- **Toggle via build arg, not a runtime env.** `VITE_DETECTION_TILES` is a
  frontend Docker build arg (default `1`, wired in docker-compose); Vite inlines
  `import.meta.env` at build, so reverting to the legacy path needs `docker
  compose build frontend` with `VITE_DETECTION_TILES=0`, not a container env
  change.

## Build status

Both production builds GREEN with Node 20 / Vite 8: default (tiles ON) and
`VITE_DETECTION_TILES=0` (legacy fat path). `tsc` clean for both.

## Known parity gaps / accepted casualties

- **Box geometry modes**: now at parity — `geom_mode=<obb|hbb|mask>` follows
  GaiaMap's `bboxMode` toggle and the backend serves the matching polygon.
- **Density / icon markers / dots**: PRESERVED — they render from the lite feed,
  not from tiles (the MVT `detection_points` sublayer is hidden).
- **Position-uncertainty halos**: DROPPED for persisted detections. The halo
  block reads `position_uncertainty_ellipse` / `position_uncertainty_m`, which the
  lite feed omits, so it no-ops. Accepted casualty; the code is left in place
  (renders again only on the legacy fat path).
- **Live `detections_partial`** previews stay on the per-feature `<Polygon>`
  path; they enter tiles only after `detections_updated` bumps the version.

## Consequences

- MVT is the default; reverting to the legacy fat path is a one-line build arg.
- `det_class` / `pass_id` server-side tile filters are intentionally **not** used
  by the map — filtering is client-side to match the box layer's filter
  semantics without re-fetching tiles (the params exist for other callers).

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/why-detection-boxes-use-polygon-map.md](why-detection-boxes-use-polygon-map.md)
- [decisions/why-live-streaming-detections.md](why-live-streaming-detections.md)
