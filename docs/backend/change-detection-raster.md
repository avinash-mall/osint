# `backend/change_detection.py` — Two-Pass Raster Diff

**Path:** [backend/change_detection.py](../../backend/change_detection.py)
**Lines:** ~191
**Depends on:** `rasterio`, `numpy`, [backend/database.py](../../backend/database.py)

## Purpose

Pixel-difference change polygons between two satellite passes. Surfaces as `POST /api/imagery/change` (single pass-pair) and `POST /api/analytics/change` (AOI-bounded across multiple passes).

## Why this design

- **Resampled to common shape** — both passes read at intersection bbox, resampled into a target shape under `CHANGE_DET_MAX_PIXELS` (default 4M px) → prevents OOM on native-resolution diffs.
- **Per-pixel absolute difference, adaptive threshold** — fixed threshold fails across sensor/illumination changes; threshold = `mean(diff) + N*stddev(diff)`.
- **Polygonized via `rasterio.features.shapes`** — GeoJSON-friendly, directly renderable.
- **Returns `None` on overlap failure** — non-intersecting passes / load failure → `None`, not raise; caller surfaces 400.

## Key symbols

- [`_env_float`](../../backend/change_detection.py#L31), [`_env_int`](../../backend/change_detection.py#L38).
- [`_load_pass`](../../backend/change_detection.py#L51) — pass row lookup → COG path + footprint.
- [`_resample_window`](../../backend/change_detection.py#L73) — common-bbox resampling.
- [`compute_change`](../../backend/change_detection.py#L97) — main entry.

## Failure modes

- Pass not found → `None`.
- COG file missing → `None`.
- Empty bbox intersection → `None`.
- Threshold yields zero polygons → `{"polygons": [], "meta": ...}` (valid empty result).

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
- [frontend/map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md)
