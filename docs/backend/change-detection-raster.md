# `backend/change_detection.py` — Two-Pass Raster Diff

**Path:** [backend/change_detection.py](../../backend/change_detection.py)
**Lines:** ~191
**Depends on:** `rasterio`, `numpy`, [backend/database.py](../../backend/database.py)

## Purpose

Compute pixel-difference change polygons between two satellite passes. Surfaces as `POST /api/imagery/change` (single-pass-pair) and `POST /api/analytics/change` (AOI-bounded across multiple passes).

## Why this design

- **Resampled to a common shape.** Both passes are read at the intersection bbox and resampled into a target shape (small enough to fit `CHANGE_DET_MAX_PIXELS`, default 4M pixels). Prevents OOM on huge native-resolution diffs.
- **Per-pixel absolute difference with adaptive threshold.** A fixed threshold doesn't work across sensor/illumination changes; the threshold is computed as `mean(diff) + N*stddev(diff)`.
- **Polygonized via `rasterio.features.shapes`.** Output is GeoJSON-friendly so it's directly renderable in the map.
- **Returns `None` on overlap failure.** If the two passes don't intersect or one fails to load, the function returns `None` rather than raising; the caller surfaces a 400.

## Key symbols

- [`_env_float`](../../backend/change_detection.py#L31), [`_env_int`](../../backend/change_detection.py#L38) — env reads.
- [`_load_pass`](../../backend/change_detection.py#L51) — looks up the pass row, returns COG path + footprint.
- [`_resample_window`](../../backend/change_detection.py#L73) — common-bbox resampling.
- [`compute_change`](../../backend/change_detection.py#L97) — main entry.

## Failure modes

- Pass not found → returns `None`.
- COG file missing on disk → returns `None`.
- Bbox intersection empty → returns `None`.
- Threshold produces zero polygons → returns `{"polygons": [], "meta": ...}` (valid empty result).

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
- [frontend/map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md)
