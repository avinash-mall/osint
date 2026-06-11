# Removed: legacy fat-geojson detection path and MVT transition scaffolding

**Date:** 2026-06-11
**Status:** removed
**Supersedes:** the opt-out half of [why-detection-mvt-tiles.md](why-detection-mvt-tiles.md);
the endpoint scope of [why-release-db-connection-before-enrichment.md](why-release-db-connection-before-enrichment.md).

## Context

The MVT cutover (see [why-detection-mvt-tiles.md](why-detection-mvt-tiles.md))
made Martin vector tiles the default detection renderer but kept the entire
legacy path behind `VITE_DETECTION_TILES=0` as a transition escape hatch, plus
several pieces of scaffolding that only existed to support both paths at once.
With the cutover validated (headless Chromium + smoke), the dual-path code was
dead weight: every branch doubled the test/build matrix and the tile payload
carried a sublayer nothing rendered.

## What was removed

**Frontend:**
- `USE_DETECTION_TILES` / `VITE_DETECTION_TILES` — the flag export in
  `MapStage.tsx`, every branch it gated in `GaiaMap.tsx` (endpoint, `limit`,
  `timeout`), the `frontend/Dockerfile` ARG/ENV, and the docker-compose build
  arg. The lite feed (`/api/detections/geojson-lite`, `limit=100000`, 20 s
  timeout) is now the only bulk fetch.
- The position-uncertainty **halo render block** in `MapStage.tsx`. The lite
  feed omits `position_uncertainty_ellipse`/`position_uncertainty_m`, so the
  block was permanently dead once the fat feed was gone (it was already an
  "accepted casualty" at cutover). Uncertainty values still reach the
  SelectionPanel via `/api/detections/{id}/enriched`.
- The `detection_points` style stub in `DetectionTileLayer.tsx` (the sublayer
  no longer exists in the tile).
- The visual-test mock (`frontend/tests/visual/mockApi.ts`) was stale — it
  still fulfilled the fat route the app stopped calling at cutover. It now
  fulfills `/geojson-lite`, `/tile-version`, `/maps/detections_mvt/**`
  (0-byte), and `/api/detections/1/enriched` (the click path).

**Backend:**
- `GET /api/detections/geojson` (the fat bulk feed: per-row
  `enriched_detection_metadata()` over full polygons + metadata; 57 MB/8 s for
  6 441 detections) and its cursor-pagination helpers
  `_encode_detection_cursor`/`_decode_detection_cursor` (+ their unit test).
  `_build_detection_feature` survives — `/api/detections/{id}/enriched` is its
  only caller now.
- The `detection_points` centroid sublayer in the `detections_mvt` SQL function
  (`platform_schema.py`). It was emitted in every tile but hidden client-side
  since cutover (markers/dots render from the lite feed) — pure payload.
- The `det_class`/`pass_id` query-param filters in `detections_mvt` — no caller
  ever passed them; map filtering is deliberately client-side, and server-side
  filtering belongs to `/geojson-lite` (which has bbox/time/class/pass params).
- The `"lite": true` marker property in the `/geojson-lite` response — nothing
  read it.

## What was deliberately kept

- `selectDetectionById`'s in-memory fallback on a `/enriched` 404 — live
  previews are unpersisted, so a click on one must not throw.
- The per-feature `<Polygon>` layer in `MapStage.tsx` — it is the live
  `detections_partial` preview renderer, not legacy (persisted features are
  centroid Points → `geojsonToLatLngs` → `null` → skipped).
- The basemap/terrain parent-tile fallback, the `vendor-vectorgrid` chunk
  isolation, and the VectorGrid `zIndex: 500` — documented load-bearing
  runtime fixes, unrelated to the legacy path.
- `geomDisplayedDetectionsGeoJSON` (`bboxMode` transform) — still shapes live
  previews and drives the `geom_mode` tile param.

## Consequences

- One rendering path, one build variant. Reverting is a git revert, not a
  build arg.
- `detections_mvt` is `CREATE OR REPLACE` and runs idempotently at startup —
  the slimmer function deploys with no migration. `ensure_tile_sources()` now
  also bumps `tile_version` at startup: VectorGrid renders an *unstyled* tile
  layer with default Leaflet path options, so a 24h-nginx-cached two-layer tile
  served to the new bundle would have painted `detection_points` as default
  blue blobs. The bump changes the `v=` URL param, so old-function tiles are
  never served to a new bundle (cost: tile cache invalidates on backend
  restart).
- Smoke catalog: 164 routes (the fat route removed; counts in the script
  header were also corrected from stale 152/154).

## Cross-references

- [why-detection-mvt-tiles.md](why-detection-mvt-tiles.md)
- [why-live-streaming-detections.md](why-live-streaming-detections.md)
- [why-memoize-ontology-normalize.md](why-memoize-ontology-normalize.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
- [frontend/workspace-geoint-gaiamap.md](../frontend/workspace-geoint-gaiamap.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
