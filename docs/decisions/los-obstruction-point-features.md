# LOS obstruction features: one Point per obstruction

## What changed

The `/api/analytics/los` endpoint used to roll up all DEM-blocked sample points into a single `MultiPoint` feature whose properties carried only `role: "obstruction"` and `count: N`. The frontend rendered them as a `GeoJSON` layer with a `Obstructions · N pts` tooltip — no per-point detail.

Now: the backend emits **one `Point` feature per obstruction**, each carrying:

| Property | Meaning |
|---|---|
| `elevation_m` | DEM ground elevation at that sample point |
| `los_m` | Line-of-sight ray height at that point |
| `clearance_m` | `los_m - (ground + curvature_drop_m)` — negative means blocked |
| `distance_m` | Along-path distance from the observer |

The frontend renders each obstruction as a `CircleMarker` with a per-point sticky tooltip: `OBSTRUCTION · ELEV 312m · BLOCKED -8.4m · 1.2km out`.

## Why

`terrain.line_of_sight` already computes all four numbers for every blocked sample (`terrain.py#L146-153`), but the router was discarding them when building the GeoJSON response. Without per-point detail an obstruction dot was purely qualitative — analysts couldn't tell which obstruction to engage, how tall it was, or how badly it blocked the path. For artillery / line-of-sight planning, those numbers matter.

Emitting `Point` features (rather than retaining the `MultiPoint` and stuffing the array into properties) means each obstruction is a first-class Leaflet feature: a `CircleMarker` per obstruction binds tooltips natively, can be styled per-point, and aligns with how the frontend renders other point overlays.

## Implementation

- [backend/routers/analytics.py](../../backend/routers/analytics.py) `run_los`: replaced the `MultiPoint` aggregation with a per-blocking-point loop emitting individual `Point` features.
- [frontend/src/components/map/MapStage.tsx](../../frontend/src/components/map/MapStage.tsx): the LOS render block separates obstruction Points from line features, renders the line via `GeoJSON` and the obstructions via mapped `CircleMarker`s with per-point tooltips.

## Cross-references

- [backend/terrain-viewshed-los.md](../backend/terrain-viewshed-los.md)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [frontend/map-stage-and-layers.md](../frontend/map-stage-and-layers.md)
