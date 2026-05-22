# Change Detection Dialog

**Path:** [frontend/src/components/map/ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx)
**Lines:** ~10066 characters

## Purpose

Modal for setting up a two-pass raster diff: pick a "before" pass, an "after" pass (both filterable by date and sensor), submit, render resulting polygons on the map.

## Behavior

1. Lists candidate satellite passes from `GET /api/imagery`.
2. Submission posts to `POST /api/imagery/change` (single pair) or `POST /api/analytics/change` (AOI-bounded).
3. Result polygons render as a new map overlay; user can click each to inspect.

## Cross-references

- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
