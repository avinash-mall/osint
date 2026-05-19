# `ObjectDetailsForm.tsx` — Shared Metadata Editor

**Path:** [frontend/src/components/ObjectDetailsForm.tsx](../../frontend/src/components/ObjectDetailsForm.tsx)
**Lines:** ~17559 characters

## Purpose

A single component that renders the operator's editable fields for **any** detection — used by both the map Selection Panel and the FMV workspace. Backing tables: `object_details` in PostGIS.

## Fields

- **Class** — display only (the model output); see also "canonical class" and "branch" derived via [backend/ontology-system.md](../backend/ontology-system.md).
- **Affiliation** — `unknown | friendly | hostile | neutral` (validated by [backend/detection-helpers.md](../backend/detection-helpers.md))
- **Threat level** — `unrated | low | medium | high | critical`
- **Notes** — free-form text
- **Size estimation** — read-only, computed by [backend/size-estimation-obb.md](../backend/size-estimation-obb.md)
- **Provenance** — chip ID, detector layer, model version (link to [map-review-similar-provenance.md](map-review-similar-provenance.md))

## Data sources

- `GET /api/detections/{id}/details` or `GET /api/fmv/detections/{id}/details`
- `PUT` on the same paths

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [backend-routers/fmv-router.md](../backend-routers/fmv-router.md)
- [map-selection-panel.md](map-selection-panel.md)
