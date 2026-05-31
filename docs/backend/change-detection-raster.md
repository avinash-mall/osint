# `backend/change_detection.py` — Two-Pass Raster Diff

**Path:** [backend/change_detection.py](../../backend/change_detection.py)
**Lines:** ~250
**Depends on:** `rasterio`, `numpy`, `scipy.ndimage`, `shapely`, [backend/database.py](../../backend/database.py)

## Purpose

Change polygons between two co-registered satellite passes. Surfaces as `POST /api/imagery/change` (single pass-pair) and `POST /api/analytics/change` (AOI-bounded). Two methods share one resample → mask → polygonise spine:

- **`diff`** (default, optical) — normalised mean absolute difference over the first ≤3 bands, thresholded as a fraction of the peak (`CHANGE_DET_THRESHOLD`). `mode: "raster_diff"`.
- **`sar_logratio`** — Sentinel-1 multi-temporal change. `10·log10((after+ε)/(before+ε))` on the VV band (band 1), median-despeckled, thresholded on `|dB| ≥ CHANGE_DET_SAR_THRESHOLD_DB`. Catches brightening (new structures) and darkening (flood — water reflects radar away), through cloud and at night. `mode: "sar_logratio"`.

## Why this design

- **Resampled to common shape** — both passes read at the intersection bbox, resampled into a target shape under `CHANGE_DET_MAX_PIXELS` → prevents OOM on native-resolution diffs.
- **One polygonizer for both methods** — each method produces a `(diff_norm, mask)` pair; `_polygonize_mask` vectorises and scores by mean magnitude, so optical and SAR share the same review/overlay path. SAR is a preset, not a parallel pipeline.
- **Log-ratio for SAR** — SAR backscatter is multiplicative-speckle dominated; the ratio (log-difference) is the standard, calibration-robust change operator. Clean-room implementation — see [decisions/why-sar-logratio-change.md](../decisions/why-sar-logratio-change.md).
- **Polygonized via `rasterio.features.shapes`** — GeoJSON-friendly, directly renderable.
- **Returns `None` on overlap failure** — non-intersecting passes / load failure → `None`, not raise; caller surfaces 4xx.

## Key symbols

- [`compute_change(before_id, after_id, method="diff")`](../../backend/change_detection.py#L149) — orchestrator: load, window, resample, branch on method, polygonise.
- [`_change_map_optical`](../../backend/change_detection.py#L149) / [`_change_map_sar`](../../backend/change_detection.py#L149) — the two `(diff_norm, mask, peak)` producers.
- [`_polygonize_mask`](../../backend/change_detection.py#L67) — shared mask → scored GeoJSON Features.
- [`_load_pass`](../../backend/change_detection.py#L103) — pass row lookup → COG path + footprint.
- [`_resample_window`](../../backend/change_detection.py#L125) — common-bbox resampling.
- Env: `CHANGE_DET_THRESHOLD`, `CHANGE_DET_MAX_PIXELS`, `CHANGE_DET_MIN_AREA_PX`, `CHANGE_DET_SIMPLIFY_TOLERANCE_DEG`, `CHANGE_DET_SAR_THRESHOLD_DB` (3.0), `CHANGE_DET_SAR_DESPECKLE` (3).

## Failure modes

- Pass not found / COG missing / empty bbox intersection → `None`.
- **SAR on a non-SAR pass** — runs the log-ratio on band 1 regardless; output is meaningless on optical input. The frontend selector labels intent; the backend does not enforce sensor type.
- Threshold yields zero polygons → empty `FeatureCollection` with a `summary` (valid empty result; optical reports `peak_diff`, SAR reports `peak_diff_db`).

## Cross-references

- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
- [frontend/map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md)
