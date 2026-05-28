# Selection Panel — Right Rail

**Path:** [frontend/src/components/map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx)
**Lines:** ~671
**Depends on:** [ObjectDetailsForm.tsx](../../frontend/src/components/ObjectDetailsForm.tsx), [IdentificationPanel.tsx](../../frontend/src/components/map/IdentificationPanel.tsx), [services/analytics.ts](../../frontend/src/services/analytics.ts), backend `/api/detections`, `/api/analytics`, and `/api/reports`

## Purpose

Four-tab right rail that appears when a detection is selected on the map.

## Tabs

| Tab | What it shows |
|---|---|
| **Details** | `ObjectDetailsForm` — threat, affiliation, notes, size estimation, original/canonical labels, provenance link. Identification subsection — see [identification-panel.md](identification-panel.md) — renders between Taxonomy and the cross-nav buttons. |
| **Analytics** | Buttons for viewshed/LOS/route/change-detection from this detection's location |
| **Similar** | k-NN list of detections with similar embeddings (`GET /api/detections/{id}/similar`) |
| **Actions** | Resolve-to-target, candidate-link suggestions, create target package, propose collection task |

## Data sources

- `GET /api/detections/{id}/details` + `PUT` (Details tab) — see [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- `GET /api/detections/{id}/similar` (Similar tab)
- `GET /api/detections/{id}/candidate-links` and `POST /api/detection-target-candidates/{id}/approve` (Actions tab)
- `POST /api/analytics/*` (Analytics tab)
- `GET /api/analytics/elevation?lat=&lon=` (Details tab — populates the `ELEV` row in the Geolocation section using the DEM at the detection centroid; falls back to `—` when the DEM is not configured). Requests use `VITE_API_URL` plus `credentials: "include"` at [SelectionPanel.tsx#L185](../../frontend/src/components/map/SelectionPanel.tsx#L185).
- `POST /api/reports/target-package/{id}` (Details tab — the "Generate Target Package" button streams a PDF compiled from already-persisted detection state; see [backend-routers/reports-router.md](../backend-routers/reports-router.md)). Requests use `VITE_API_URL` plus `credentials: "include"` at [SelectionPanel.tsx#L207](../../frontend/src/components/map/SelectionPanel.tsx#L207).

## Failure modes

- Elevation errors are non-blocking and render `--`/unavailable state in the Geolocation section.
- Target-package generation failures keep the user in the panel and surface the existing error path rather than navigating away.

## Cross-references

- [identification-panel.md](identification-panel.md)
- [object-details-form.md](object-details-form.md)
- [map-analytics-tools.md](map-analytics-tools.md)
- [map-review-similar-provenance.md](map-review-similar-provenance.md)
- [product-tour.md](product-tour.md) — the SelectionPanel header chip, collapse button, four tabs, and the Track Object button are first-class Product Tour anchors (`selection-header-chip`, `selection-collapse`, `tab-details` / `tab-analytics` / `tab-similar` / `tab-tracks`, `tracks-track-object`).
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
