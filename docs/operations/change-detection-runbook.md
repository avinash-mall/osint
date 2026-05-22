# Operations — Change Detection Runbook

## Two entry points

| Surface | When to use |
|---|---|
| **UI:** Geoint workspace → "Change detection" → [map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md) | Interactive: pick before/after passes from a list |
| **API:** `POST /api/imagery/change` or `POST /api/analytics/change` | Scripted, AOI-bounded |

## Single-pair vs AOI

- `POST /api/imagery/change` — bounded to the intersection of the two passes. Returns polygons in pixel/geographic space.
- `POST /api/analytics/change` — accepts an AOI polygon; backend selects the most recent two passes overlapping the AOI.

## How the result is rendered

Result polygons returned as GeoJSON, overlaid as a new ephemeral map layer. Click a polygon for:

- The two pass IDs.
- Change magnitude (mean diff in the polygon).
- Quick links to the two pass tile URLs for side-by-side comparison.

## When the result is empty

Common causes:

- **Bbox doesn't intersect** → operator picked passes not covering the same area. UI hides "Submit" when this happens.
- **Threshold too high** → diff didn't exceed `mean + N*stddev`. Set `CHANGE_DET_THRESHOLD_STDDEVS` lower.
- **Sensor mismatch** → mixing optical and SAR produces mostly noise. Restrict pass selection to same-sensor pairs.

## Cross-references

- [backend/change-detection-raster.md](../backend/change-detection-raster.md)
- [backend-routers/imagery-router.md](../backend-routers/imagery-router.md)
- [backend-routers/analytics-router.md](../backend-routers/analytics-router.md)
- [frontend/map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md)
