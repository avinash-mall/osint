# Why a Martin MVT detection layer (now the ONLY path)

**Status:** shipped and **the only path** — the `VITE_DETECTION_TILES` build
flag and the legacy fat-`/geojson` path were **removed** (2026-06). History:
Phase 2 landed MVT behind a flag (default OFF); the cutover (Option A — MVT
boxes + lite-fed markers/dots + `/enriched` selection) flipped the default;
the flag-removal pass then deleted the legacy branch entirely (frontend flag,
`USE_DETECTION_TILES` export, position-uncertainty halo block, Docker build
arg) alongside the backend's fat `/api/detections/geojson` endpoint.

## Context

The Map workspace renders persisted detections as **one react-leaflet
`<Polygon>` per feature** (`MapStage.tsx`, fed by `filteredDetectionsGeoJSON`
→ `geomDisplayedDetectionsGeoJSON`). This is correct and the default, but a
dense pass can return thousands of features (tens of MB of GeoJSON with full
ontology/metadata) and paints thousands of SVG paths — heavy on both the wire
and the DOM.

The backend MVT source: `GET /maps/detections_mvt/{z}/{x}/{y}?v=<version>&geom_mode=<obb|hbb|mask>`
serves the `detections` layer (Polygons, props `id, class, confidence,
branch_id, parent_class, original_class, icon_key, label, threat_level,
allegiance, review_status, pass_id`); the `detection_points` centroid sublayer
was dropped with the legacy path. `geom_mode` (default `obb`) picks the polygon
geometry. Selection uses the per-id `GET /api/detections/{id}/enriched`;
cache-bust is `GET /api/detections/tile-version`. The **lite feed** `GET
/api/detections/geojson-lite` returns small centroid-Point features (light
props, no polygon geometry, no fat metadata) — 2.7 MB/0.6 s for 6 441 vs
57 MB/8 s for the (removed) fat `/geojson`.

## Decision

Cutover (Option A) — DEFAULT ON:

- **Boxes** render from MVT (`DetectionTileLayer.tsx`, `L.vectorGrid.protobuf`
  via `leaflet.vectorgrid`) whenever the detections layer is on (the
  `USE_DETECTION_TILES` gate was removed with the flag). `geomMode` (GaiaMap's
  `bboxMode`) is appended to the tile URL, so the OBB/HBB/mask toggle drives
  the served polygon (full geom-mode parity).
- **Markers/dots** render from the **lite feed**, kept in the existing
  `detectionsGeoJSON` state (now centroid Points). The lucide icon markers (≤800)
  and dots (>800) are PRESERVED. The MVT `detection_points` centroid sublayer
  was dropped from the tile (it was only ever hidden client-side to avoid
  doubling the dots), and the frontend no longer styles it.
- **Selection** routes through a single `selectDetectionById(id, fallback)` in
  GaiaMap → `/api/detections/{id}/enriched` (fat shape for the SelectionPanel),
  with a fallback to the in-memory feature on 404. ALL clicks use it: MVT polygon,
  icon marker, dot, and live previews.
- **Live previews** (`detections_partial`, Polygon features) still append to
  `detectionsGeoJSON` and render via the per-feature `<Polygon>` layer (lite
  centroid Points yield `null` from `geojsonToLatLngs` so only preview polygons
  draw). `detections_updated` re-fetches the lite feed and bumps
  `detectionTileVersion`.

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
  fetches the fully-enriched GeoJSON Feature (~39-prop fat shape) for the
  SelectionPanel. A 404 (e.g. an unpersisted live preview) falls back to the
  in-memory feature so a click never throws.
- **Lite feed for the bulk fetch.** GaiaMap's bulk `fetchDetectionFeatures`
  calls `/api/detections/geojson-lite` (same bbox/time/class params,
  `limit=100000`, all-at-once — no cursor pagination). It drives counts, the
  class filter, framing, and the marker/dot layers.
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
- **VectorGrid needs an explicit zIndex above the raster stack.** VectorGrid
  is a GridLayer → it renders into the TILE pane, where GridLayer's default
  zIndex is **1**. The raster layers sit at 100 (basemap fallback) / 200 (SAT
  imagery) / 300 (reference overlay), so without `zIndex: 500` the detection
  boxes drew UNDER the imagery and were invisible wherever any tile painted
  ("the OBB is not displayed over the image"). Markers/popups live in higher
  panes and are unaffected.
- **Empty tiles return 204, not 404.** `ST_AsMVT` over zero rows is NULL, which
  Martin serves as 404 — noisy in the console and uncacheable over empty
  ocean/land at low zoom. The function does `RETURN COALESCE(mvt, ''::bytea)` so
  an empty tile is a cacheable 0-byte 204.
## Build status

Production build GREEN with Node 20 / Vite 8; `tsc` clean. (The
`VITE_DETECTION_TILES` build arg and the legacy fat-path build variant no
longer exist.)

## Known parity gaps / accepted casualties

- **Box geometry modes**: now at parity — `geom_mode=<obb|hbb|mask>` follows
  GaiaMap's `bboxMode` toggle and the backend serves the matching polygon.
- **Density / icon markers / dots**: PRESERVED — they render from the lite feed,
  not from tiles.
- **Position-uncertainty halos**: REMOVED. The halo block read
  `position_uncertainty_ellipse` / `position_uncertainty_m`, which the lite feed
  omits — permanently dead once the fat path was deleted, so the render block
  was deleted from `MapStage.tsx`. Accepted casualty.
- **Live `detections_partial`** previews stay on the per-feature `<Polygon>`
  path; they enter tiles only after `detections_updated` bumps the version.

## Consequences

- MVT is the only path; the legacy fat path (flag, fat `/geojson` feed,
  per-feature persisted-box rendering, halos) is gone — reverting means a git
  revert, not a build arg.
- The tile function takes only `geom_mode` (plus the `v` cache-bust). The unused
  `det_class` / `pass_id` server-side filters were removed with the legacy path —
  detection filtering is client-side (`styleForTileProps` hides filtered
  features) so a filter change never re-fetches tiles; callers that need
  server-side filtering use `/api/detections/geojson-lite`, which has
  bbox/time/class/pass params.

## Cross-references

- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [decisions/why-detection-boxes-use-polygon-map.md](why-detection-boxes-use-polygon-map.md)
- [decisions/why-live-streaming-detections.md](why-live-streaming-detections.md)
