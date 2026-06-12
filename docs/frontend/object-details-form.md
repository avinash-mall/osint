# `ObjectDetailsForm.tsx` — Shared Metadata Editor

**Path:** [frontend/src/components/ObjectDetailsForm.tsx](../../frontend/src/components/ObjectDetailsForm.tsx)
**Lines:** ~558
**Depends on:** `axios`, [frontend/src/hooks/useAuth.ts](../../frontend/src/hooks/useAuth.ts), [frontend/src/utils/objectMetadata.ts](../../frontend/src/utils/objectMetadata.ts)

## Purpose

Single component rendering the operator's editable fields for **any** detection — used by both the map Selection Panel and the FMV workspace. Backing table: `object_details` in PostGIS. Dirty forms autosave through the normal `PUT` route; tab-close handling uses `fetch(..., { method: "PUT", keepalive: true })` at [ObjectDetailsForm.tsx#L224-L240](../../frontend/src/components/ObjectDetailsForm.tsx#L224-L240) so the browser does not downgrade the save to a `POST`.

## Fields

- **Class** — display only (model output); see also "canonical class" + "branch" derived via [backend/ontology-system.md](../backend/ontology-system.md).
- **Affiliation** — `unknown | friendly | hostile | neutral` (validated by [backend/detection-helpers.md](../backend/detection-helpers.md))
- **Threat level** — `unrated | low | medium | high | critical`
- **Notes** — free-form text
- **Size estimation** — read-only, computed by [backend/size-estimation-obb.md](../backend/size-estimation-obb.md)
- **Provenance** — chip ID, detector layer, model version (link to [map-review-similar-provenance.md](map-review-similar-provenance.md))
- **Platform identification** — read-only display of `platform_name`, `platform_family`, `platform_confidence`, `platform_source` when populated by the reference-DB pipeline. Analysts approve/reject via the Identification panel, not by typing here (see [identification-panel.md](identification-panel.md)).

## Data sources

- `GET /api/detections/{id}/details` or `GET /api/fmv/detections/{id}/details`
- `PUT` on the same paths

## Failure modes

- Save failures leave the form dirty and surface the existing inline save status (messages pass through [`apiErrorMessage`](../../frontend/src/utils/apiError.ts) so a 422 `detail` array can't be rendered raw as a React child).
- When a restored sessionStorage draft wins over the server row at hydrate time, the hydrate effect itself schedules the debounce save — previously the debounce only started in `set()`, so a restored draft silently never persisted unless the operator typed again.
- Browser unload may drop best-effort keepalive requests when the payload exceeds user-agent limits; the route/method still match backend contracts when sent.

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [backend-routers/fmv-router.md](../backend-routers/fmv-router.md)
- [map-selection-panel.md](map-selection-panel.md)
