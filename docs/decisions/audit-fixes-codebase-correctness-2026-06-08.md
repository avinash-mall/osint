# Codebase-wide correctness audit — fixes (2026-06-08)

**Date:** 2026-06-08
**Status:** adopted

## Context

A whole-codebase sweep (6 parallel reviewers over backend core, routers,
geo/sensor modules, the inference service, scripts, and the remaining frontend
logic) hunting stubs/mocks/unimplemented/bugs. Every finding was verified
against the actual code/backend before fixing. **Risky detection-sensitivity
changes were deliberately NOT applied** (they need benchmarking, not blind
edits) and the larger unwired features were left for a dedicated effort — both
listed under "Deferred" below.

## Fixed

### Backend — correctness

- **`delete_fmv_clip` orphaned every FMV clip's graph nodes** ([main.py](../../backend/main.py)). The Neo4j cleanup matched `:FmvDetection`/`:FmvClip` (wrong case — projected as `:FMVDetection`/`:FMVClip`) and keyed detections on a non-existent `postgis_id` (identity is `(clip_id, track_uid)`). Now deletes the clip and its `CONTAINS_DETECTION` children via the edge.
- **FMV footprint swapped the lat/lon metres-per-degree divisors** ([video_metadata.py `_footprint_wkt`](../../backend/video_metadata.py)). The East-West span (added to longitude) was divided by plain 111320 and the North-South span (added to latitude) by `111320·cos(lat)` — backwards, so every footprint was wrong by a `cos(lat)` factor (~2× off at 60°). Swapped.
- **KLV relative-timestamp fallback collapsed samples into ~1 s** ([video_metadata.py](../../backend/video_metadata.py)). When KLV lacks absolute timestamps, samples were spread over `idx/n` ∈ [0,1)s regardless of clip length, so the per-frame dedup discarded most. Threaded `duration_s` into `_extract_klv` and spread across the real duration.
- **Optical change-detection crashed / silently broadcast on mismatched band counts** ([change_detection.py `_change_map_optical`](../../backend/change_detection.py)). `_resample_window` caps bands per file independently, so a 2-band vs 3-band pair raised a broadcasting error and a 1-vs-3 pair silently broadcast mismatched spectra. Now differences only the common leading bands.
- **`store_detections` could lose a whole detection batch on a Neo4j blip** ([worker_legacy.py](../../backend/worker_legacy.py)). The graph write ran inside the committing PostGIS cursor, so a Neo4j exception rolled back the source-of-truth INSERTs. Wrapped it best-effort like every other graph write.
- **`local_utm_crs` could emit an invalid UTM zone 61** ([size_estimation.py](../../backend/size_estimation.py)) at `lon == 180` (or an upstream antimeridian wrap). Clamped to zones 1..60.

### Backend — routers

- **`POST /api/ingest/url` queued work no worker runs** ([ingest.py](../../backend/routers/ingest.py)): it inserted `status='queued'` for a non-existent `workers.url.process` task (and the build is air-gapped, so runtime URL fetch is impossible). Now records the URL as a manual OSINT reference (`status='manual'`) with honest copy instead of a phantom queue.
- **`POST /api/analytics/pol` GROUP BY defeated its own clustering** ([analytics.py](../../backend/routers/analytics.py)): grouping by `ST_SnapToGrid(...), lon, lat` (the raw coords) made every point its own group. Now groups by the grid cell and returns the cell centroid.
- **AI action-approve recorded a fake approver** ([ai.py](../../backend/routers/ai.py)): hardcoded `approved_by='local_user'` with no user dependency. Now records `SessionUser.username`.

### Inference

- **`/embed` ran a GPU forward on the event loop with no device pinning** ([main.py](../../inference-sam3/main.py) + [embedding.py `dinov3_pool`](../../inference-sam3/embedding.py)): now `run_in_threadpool` + `device_ctx(device)`, matching `embed_crops_batched` (multi-GPU cross-device hazard + event-loop block).
- **Stale code-line comment** in `mask_aware_nms` ([fusion.py](../../inference-sam3/fusion.py)) referenced a non-existent "line 154"; corrected to describe the actual class gate.

### Scripts

- **`backfill_ontology_to_graph.py` projected ZERO `LABEL_OF` edges** — it read `getattr(norm, "object_id")` but `NormalizedLabel`'s field is `ontology_object_id`, so the value was always `None` and every row was skipped. Fixed the attribute name.
- **`fetch_real_datasets.py` fabricated burn-scar ground truth from the flood mask** — Sen1Floods11 has no burn GT; assigning the water mask to `burn_scar` made any burn eval actually measure flood. Dropped the bogus key.

### Frontend (SAFE-surgical from the deferred list + audit)

- **OBB box mode now renders the oriented box** ([GaiaMap.tsx](../../frontend/src/components/GaiaMap.tsx)): it read `metadata.obb` (pixel-space, nested-pairs assumption) which never matched; the backend ships the geo box as a flat `metadata.geo_polygon` — now used.
- **Viewshed/LOS height controls** ([AnalyticsToolsPanel.tsx](../../frontend/src/components/map/AnalyticsToolsPanel.tsx)): the `targetHeight`/`observerHeight` state, service params, and backend were already wired — only the sliders were missing. Added a viewshed target-height and LOS observer+target-height sliders.
- **OntologyAdmin "Recent instances"** ([OntologyAdmin.tsx](../../frontend/src/components/OntologyAdmin.tsx)): it queried `det_class` with underscores replaced by spaces, never matching the stored `lowercase_underscore` class. Now lowercases but keeps underscores.
- **Map "Recenter" no longer always jumps to the Gulf** ([MapStage.tsx](../../frontend/src/components/map/MapStage.tsx)): fits the selected imagery footprint, then the current detections, then the default view.

## Deferred (verified, not patched — need benchmarking, design, or larger features)

- **Inference presence-ratio gate asymmetry** (batched/cached path applies the SegEarth gate to post-thresholded scores → over-drops): a real behavioural concern but changing detection gating must be benchmarked, not blind-edited.
- **CFAR variance uses the guard-inclusive mean**: statistical refinement that changes detector sensitivity — needs an eval pass.
- **OBB orientation 180°-ambiguity** in `size_estimation` (mod-180 vs directed bearing) — a spec choice.
- **Time-machine / event-timeline playback** wiring, and **ChangeDetectionDialog** mounting + a generic `sentinel:overlay-geojson` overlay layer in MapStage — genuine larger features.
- **`execute_action_proposal` viewshed ignores `target_id`** (uses default coords) — in the UI-less AI suite; needs target→coords resolution.
- Script robustness (basemap/terrain unbounded future submission OOM at z14; per-dataset idempotency gaps) — bake-tool hardening.

## Validation

`ast.parse` clean on every changed backend/inference/script file; frontend `tsc`
clean + `vite build` succeeds (2930 modules). Backend unit tests were not run
(pytest/rasterio/pyproj absent in the dev env); each fix was verified by reading
the surrounding code and the backend it depends on.

## Cross-references

- [backend/video-metadata-klv.md](../backend/video-metadata-klv.md)
- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend/size-estimation-obb.md](../backend/size-estimation-obb.md)
- [backend-routers/ingest-router.md](../backend-routers/ingest-router.md)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [frontend/map-analytics-tools.md](../frontend/map-analytics-tools.md)
- [decisions/audit-fixes-ui-correctness-2026-06-08.md](audit-fixes-ui-correctness-2026-06-08.md)
