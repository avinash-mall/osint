# Selection Panel — Right Rail

**Path:** [frontend/src/components/map/SelectionPanel.tsx](../../frontend/src/components/map/SelectionPanel.tsx)
**Lines:** ~27057 characters

## Purpose

Four-tab right rail that appears when a detection is selected on the map.

## Tabs

| Tab | What it shows |
|---|---|
| **Details** | `ObjectDetailsForm` — threat, affiliation, notes, size estimation, original/canonical labels, provenance link |
| **Analytics** | Buttons for viewshed/LOS/route/change-detection from this detection's location |
| **Similar** | k-NN list of detections with similar embeddings (`GET /api/detections/{id}/similar`) |
| **Actions** | Resolve-to-target, candidate-link suggestions, create target package, propose collection task |

## Data sources

- `GET /api/detections/{id}/details` + `PUT` (Details tab) — see [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- `GET /api/detections/{id}/similar` (Similar tab)
- `GET /api/detections/{id}/candidate-links` and `POST /api/detection-target-candidates/{id}/approve` (Actions tab)
- `POST /api/analytics/*` (Analytics tab)

## Cross-references

- [object-details-form.md](object-details-form.md)
- [map-analytics-tools.md](map-analytics-tools.md)
- [map-review-similar-provenance.md](map-review-similar-provenance.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
