# Detections Router (`/api/detections/*`)

**Path:** [backend/routers/detections.py](../../backend/routers/detections.py)
**Lines:** ~226
**Depends on:** [backend/detection_helpers.py](../../backend/detection_helpers.py), [backend/detection_policy.py](../../backend/detection_policy.py), [backend/auth.py](../../backend/auth.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

**Review surface** for satellite-imagery detections. Reading is in [backend/main.py](../../backend/main.py); this router owns detail edits, manual detections, deletion, the per-detection workflow.

Bulk read endpoints (`GET /api/detections`, `/api/detections/geojson`, `/api/detections/classes`, `/api/detections/{id}/similar`, `/api/detections/queue`, `/api/detections/resolve`, `/api/detections/prithvi-overlays`, `/api/detections/{id}/candidate-links`, `/api/detection-target-candidates/{id}/approve`, `/api/detections/{id}/tag`, `/api/detections/{id}/review`) live in [backend/main.py](../../backend/main.py). `/api/detections/classes?llm=true` may return `display_label` / `label_source` for all-YOLOE-PF imagery AMG class rows; raw `class` remains the filter key.

## Endpoints in this router

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/detections/{id}/details` | [detections.py#L34](../../backend/routers/detections.py#L34) | Operator-editable object_details row |
| `PUT` | `/api/detections/{id}/details` | [detections.py#L53](../../backend/routers/detections.py#L53) | Update threat/affiliation/notes via [`ObjectDetailsBody`](../../backend/schemas.py) |
| `POST` | `/api/detections/manual` | [detections.py#L101](../../backend/routers/detections.py#L101) | Operator-drawn detection with `ManualDetectionBody` |
| `DELETE` | `/api/detections/{id}` | [detections.py#L197](../../backend/routers/detections.py#L197) | Soft-delete (sets a flag, keeps row) |

## Why this design

- **Details split from detection row** — `detections` stores model output; `object_details` stores operator edits. Splitting keeps detector-pipeline writes idempotent, prevents overwriting operator work on the next chip re-process.
- **Soft delete** — detections are evidence, never hard-deleted. Flag lets re-processing skip them, UI hide them; admin can restore.
- **`require_admin` not used here** — any logged-in operator can edit details. Mutating verbs gated by session middleware at [backend/main.py#L84](../../backend/main.py#L84).

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md) — shared validators (threat levels, affiliations)
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- [decisions/why-amg-detection-classes-use-llm-labels.md](../decisions/why-amg-detection-classes-use-llm-labels.md)
- [frontend/object-details-form.md](../frontend/object-details-form.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
