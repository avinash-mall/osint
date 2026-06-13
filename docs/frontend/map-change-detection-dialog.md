# Change Detection Dialog

**Path:** [frontend/src/components/map/ChangeDetectionDialog.tsx](../../frontend/src/components/map/ChangeDetectionDialog.tsx)
**Lines:** ~317

## Purpose

Modal for setting up a two-pass raster diff: pick a "before" pass, an "after" pass (both filterable by date and sensor), submit, render resulting polygons on the map.

## Behavior

1. **Opened** from the TimeMachineBar `CHANGE` button (shown when a compare pass is pinned), with `before`/`after` = the active and compare passes ordered by acquisition time. GaiaMap owns the `changePair` state and mounts the dialog.
2. Submission posts to `POST /api/imagery/change` for the pair (the method toggle re-runs as Optical `diff` or SAR `sar_logratio`).
3. **"Open on map"** dispatches a `sentinel:overlay-geojson` CustomEvent `{id, label, featureCollection}`. [MapStage](map-stage-and-layers.md) listens for it (and `sentinel:overlay-clear`), renders the FeatureCollection as a generic GeoJSON overlay (magenta, fill opacity scaled by `score`/`confidence`), flies to its bounds, and shows a dismissible chip to remove it. "Export GeoJSON" downloads the result.

This dialog + the MapStage overlay subsystem were wired up in [decisions/completed-deferred-items-2026-06-09.md](../decisions/completed-deferred-items-2026-06-09.md) (the dialog was previously never mounted and the `sentinel:overlay-geojson` handoff had no listener).

## Cross-references

- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [operations/change-detection-runbook.md](../operations/change-detection-runbook.md)
