# `frontend/src/components/map/IdentificationPanel.tsx` — Reference-DB Identification UI

**Path:** [frontend/src/components/map/IdentificationPanel.tsx](../../frontend/src/components/map/IdentificationPanel.tsx)
**Lines:** ~419
**Depends on:** `axios`, `lucide-react`, the backend routes from [backend-routers/reference-platforms-router.md](../backend-routers/reference-platforms-router.md) (GET candidates, POST approve/reject/identify, GET chip image).

## Purpose

Renders top-k reference-platform candidates for a detection inside SelectionPanel's Details tab. Analysts see rank, platform name/family, cosine-score percentage, up to 3 chip thumbnails, and approve/reject buttons per candidate. A "Re-identify" button re-runs the pgvector lookup.

## Why this design

- Single-component scope; no Redux/Context; state lives in 4 useState vars (matches project convention — see [object-details-form.md](object-details-form.md)).
- Approve/reject buttons disabled for already-approved/rejected candidates so analysts can't double-act.
- Per-action busy state gates ALL candidates' buttons (`anyBusy`) to prevent race conditions on the analyst's transaction.
- Re-identify uses Plan D's `POST /api/detections/{id}/identify` with `auto_threshold=999.0` semantics: never auto-applies, just refreshes the queue.

## Key symbols

- `IdentificationPanel({ detectionId, onChanged })` — default export. Mounted by [SelectionPanel](map-selection-panel.md).
- `load()` — fetches `GET /api/detections/{id}/identification-candidates` on mount + when detectionId changes.
- `handleApprove(id)`, `handleReject(id)` — POST to `/api/identification-candidates/{id}/{approve|reject}` and re-fetch.
- `handleReidentify()` — POST to `/api/detections/{id}/identify` with `view_domain=overhead, top_k=3`.

## Inputs / Outputs

- Inputs: `detectionId: number` prop; reads from 4 backend endpoints.
- Outputs: `onChanged?()` callback fires after approve/reject so the parent can refresh `object_details`.

## Failure modes

- 401 → analyst session expired; error chip shows "Unauthorized"-style message. SelectionPanel doesn't redirect to login (existing behaviour).
- 400 (no embedding on detection) → expected for detections inserted before Plan C's worker splice; the error chip surfaces the backend's detail.
- Broken chip URLs → browser renders the broken-image glyph. Future polish: add `<img onError>` fallback.

## Cross-references

- Backend router: [reference-platforms-router.md](../backend-routers/reference-platforms-router.md)
- Schema: [reference-platform-db.md](../backend/reference-platform-db.md)
- Threshold policy: [why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md)
- Plan E spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md](../superpowers/plans/2026-05-27-reference-db-plan-e-frontend.md)
