# Detections Router (`/api/detections/*`)

**Path:** [backend/routers/detections.py](../../backend/routers/detections.py)
**Lines:** ~270
**Depends on:** [backend/detection_helpers.py](../../backend/detection_helpers.py), [backend/detection_policy.py](../../backend/detection_policy.py), [backend/auth.py](../../backend/auth.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

**Review surface** for satellite-imagery detections. Reading is in [backend/main.py](../../backend/main.py); this router owns detail edits, manual detections, deletion, the per-detection workflow.

Bulk read endpoints (`GET /api/detections`, `/api/detections/geojson-lite`, `/api/detections/classes`, `/api/detections/{id}/similar`, `/api/detections/queue`, `/api/detections/resolve`, `/api/detections/{id}/candidate-links`, `/api/detection-target-candidates/{id}/approve`, `/api/detections/{id}/tag`, `/api/detections/{id}/review`) live in [backend/main.py](../../backend/main.py). `/api/detections/classes?llm=true` may return non-authoritative `llm_advisory`; raw `class` and deterministic labels remain the filter/audit keys.

## Endpoints in this router

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/detections/{id}/details` | [detections.py#L34](../../backend/routers/detections.py#L34) | Operator-editable object_details row |
| `PUT` | `/api/detections/{id}/details` | [detections.py#L53](../../backend/routers/detections.py#L53) | Update threat/affiliation/notes via [`ObjectDetailsBody`](../../backend/schemas.py) |
| `POST` | `/api/detections/manual` | [detections.py#L118](../../backend/routers/detections.py#L118) | Operator-drawn detection with `ManualDetectionBody`. `detections.geom` is `GEOMETRY(POLYGON)`: a single-part MultiPolygon is stored as its one polygon (`ST_GeometryN(…, 1)`); multi-part MultiPolygons are rejected with 400 rather than silently dropping parts |
| `DELETE` | `/api/detections/{id}` | [detections.py#L223](../../backend/routers/detections.py#L223) | Soft-delete (sets `deleted_at`, keeps row) + purges projections (candidate links, track membership, empty tracks, `object_details`, Neo4j node) via [cascade_delete.py](../../backend/cascade_delete.py) |

## Why this design

- **Details split from detection row** — `detections` stores model output; `object_details` stores operator edits. Splitting keeps detector-pipeline writes idempotent, prevents overwriting operator work on the next chip re-process.
- **Soft delete** — detections are evidence, never hard-deleted. The `deleted_at` tombstone lets re-processing skip them and the UI hide them; admin can restore. Downstream **projections** (candidate links, track membership, empty parent tracks, `object_details`, the Neo4j `:Detection` node) are purged so nothing stale renders behind the hidden row — see [backend/cascade-delete.md](../backend/cascade-delete.md).
- **`require_admin` not used here** — any logged-in operator can edit details. Mutating verbs gated by session middleware at [backend/main.py#L84](../../backend/main.py#L84).

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md) — shared validators (threat levels, affiliations)
- [backend/pydantic-schemas.md](../backend/pydantic-schemas.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [frontend/object-details-form.md](../frontend/object-details-form.md)
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch
