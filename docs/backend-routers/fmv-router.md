# FMV Router (`/api/fmv/detections/*`)

**Path:** [backend/routers/fmv.py](../../backend/routers/fmv.py)
**Lines:** ~117
**Depends on:** [backend/detection_helpers.py](../../backend/detection_helpers.py), [backend/schemas.py](../../backend/schemas.py)

## Purpose

Operator-edit + delete on **FMV detections** (per-frame, per-track rows in `fmv_detections`). Bulk FMV reads — `GET /api/fmv/clips`, `/api/fmv/clips/{id}`, `/api/fmv/clips/{id}/klv`, `/api/fmv/clips/{id}/detections`, `/api/fmv/detections/{id}/similar` — live in [backend/main.py](../../backend/main.py). `POST /api/fmv/clips` (clip upload + processing kickoff) lives in [backend/routers/ingest.py](../../backend/routers/ingest.py).

## Endpoints in this router

| Method | Path | Source | Behavior |
|---|---|---|---|
| `GET` | `/api/fmv/detections/{id}/details` | [fmv.py#L29](../../backend/routers/fmv.py#L29) | Operator-edit row for a single FMV detection |
| `PUT` | `/api/fmv/detections/{id}/details` | [fmv.py#L48](../../backend/routers/fmv.py#L48) | Update via [`ObjectDetailsBody`](../../backend/schemas.py) |
| `DELETE` | `/api/fmv/detections/{id}` | [fmv.py#L94](../../backend/routers/fmv.py#L94) | Soft-delete |

## Why this design

Mirrors [detections-router.md](detections-router.md) — same edit/delete surface, separate table. Reusing `ObjectDetailsBody` + `detection_helpers` → FMV review UI inherits the threat/affiliation/notes validators for free.

Track-level operations (pin/unpin, reprocess) live elsewhere — see [`/api/tracks/detections/*`](../backend/api-routes-reference.md#graph--tracks) in main.py.

## Cross-references

- [backend/detection-helpers.md](../backend/detection-helpers.md)
- [backend/fmv-track-consolidation.md](../backend/fmv-track-consolidation.md)
- [frontend/workspace-fmv-player.md](../frontend/workspace-fmv-player.md)
- [architecture/data-flow-fmv.md](../architecture/data-flow-fmv.md)
