# Operations — Change Detection Runbook

## Two entry points

| Surface | When to use |
|---|---|
| **UI:** Geoint workspace → "Change detection" → [map-change-detection-dialog.md](../frontend/map-change-detection-dialog.md) | Interactive: pick before/after passes from a list |
| **API:** `POST /api/imagery/change` or `POST /api/analytics/change` | Scripted, AOI-bounded |

## Single-pair vs AOI

- `POST /api/imagery/change` — bounded to the intersection of the two passes. Returns polygons in pixel/geographic space.
- `POST /api/analytics/change` — accepts an AOI polygon; backend selects the most recent two passes overlapping the AOI.

## Method: optical vs SAR

Both endpoints accept an optional `method`:

- **`diff`** (default, optical) — normalised band difference; needs cloud-free optical passes.
- **`sar_logratio`** — Sentinel-1 dB log-ratio on VV; sees flood/damage/disturbance through cloud and at night. Use on cloudy AOIs, nocturnal events, or to corroborate an optical call.

```
POST /api/imagery/change   {"before_pass_id": 12, "after_pass_id": 34}                       # optical
POST /api/imagery/change   {"before_pass_id": 12, "after_pass_id": 34, "method": "sar_logratio"}
POST /api/analytics/change {"before_pass_id": 12, "after_pass_id": 34, "method": "sar_logratio"}
```

In the UI, the change dialog has an **Optical / SAR** METHOD toggle (switching re-runs the diff). SAR summaries report `peak_diff_db` and `mode: "sar_logratio"`; optical reports `peak_diff` and `mode: "raster_diff"`.

SAR tuning: `CHANGE_DET_SAR_THRESHOLD_DB` (default 3.0 ≈ 2× backscatter; raise to ~6 for deep change only), `CHANGE_DET_SAR_DESPECKLE` (median window px, default 3; <2 disables).

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
