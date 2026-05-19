# Detections Router (`/api/detections/*`)

**Path:** [backend/routers/detections.py](../../backend/routers/detections.py)
**Lines:** ~226
**Depends on:** [backend/detection_helpers.py](../../backend/detection_helpers.py), [backend/detection_policy.py](../../backend/detection_policy.py), [backend/auth.py](../../backend/auth.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

The **review surface** for satellite-imagery detections. Reading is in [backend/main.py](../../backend/main.py) (and other places); this router owns detail edits, manual detections, deletion, and the per-detection workflow.

The bulk read endpoints (`GET /api/detections`, `/api/detections/geojson`, `/api/detections/classes`, `/api/detections/{id}/similar`, `/api/detections/queue`, `/api/detections/resolve`, `/api/detections/prithvi-overlays`, `/api/detections/{id}/candidate-links`, `/api/detection-target-candidates/{id}/approve`, `/api/detections/{id}/tag`, `/api/detections/{id}/review`) live in [backend/main.py](../../backend/main.py).

## Endpoints in this router

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/detections/{id}/details` | [detections.py#L34](../../backend/routers/detections.py#L34) | Operator-editable object_details row |
| `PUT` | `/api/detections/{id}/details` | [detections.py#L53](../../backend/routers/detections.py#L53) | Update threat/affiliation/notes via [`ObjectDetailsBody`](../../backend/schemas.py) |
| `POST` | `/api/detections/manual` | [detections.py#L101](../../backend/routers/detections.py#L101) | Operator-drawn detection with `ManualDetectionBody` |
| `DELETE` | `/api/detections/{id}` | [detections.py#L197](../../backend/routers/detections.py#L197) | Soft-delete (sets a flag, doesn't remove row) |

## Why this design

- **Details split from detection row.** `detections` table stores the model output; `object_details` stores operator edits. Splitting keeps the detector pipeline's writes idempotent and prevents accidentally overwriting an operator's work on the next chip re-process.
- **Soft delete.** Detections are evidence — never hard-deleted. The flag lets re-processing skip them and the UI hide them; an admin can restore.
- **`require_admin`** is **not** used here; any logged-in operator can edit details. Mutating verbs are gated by the session middleware in [backend/main.py#L84](../../backend/main.py#L84).

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md) — shared validators (threat levels, affiliations) used here
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- [frontend/object-details-form.md](../frontend/object-details-form.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
