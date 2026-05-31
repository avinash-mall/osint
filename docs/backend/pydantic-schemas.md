# `backend/schemas.py` — Pydantic Request/Response Models

**Path:** [backend/schemas.py](../../backend/schemas.py)
**Lines:** ~447
**Depends on:** `pydantic`

## Purpose

Every router request body + most response shapes. Extracted from `main.py` so routers import shapes without dragging the entire app into their namespace.

## Why this module

- **Avoid circular imports** — routers need shapes; shapes don't need routers. One-way dependency.
- **No business logic** — schemas validate; the route handler does the work.
- **No DB types** — schemas describe the HTTP boundary, not rows. PostGIS rowtypes local to the helper that reads them.

## Key shapes (alphabetical, with line refs)

| Class | Line | Used by |
|---|---|---|
| `AuthTestRequest` | [#L34](../../backend/schemas.py#L34) | [auth-router.md](../backend-routers/auth-router.md) |
| `AIActionProposalRequest`, `AIAnalysisRequest` | | [ai-router.md](../backend-routers/ai-router.md) |
| `AnalyticsRequest` | | [analytics-router.md](../backend-routers/analytics-router.md), [ai-router.md](../backend-routers/ai-router.md) |
| `ConfidenceConfig` | | [inference-router.md](../backend-routers/inference-router.md) |
| `DetectionQuery` | [#L48](../../backend/schemas.py#L48) | `GET /api/detections` |
| `DetectionTagUpdate` | [#L44](../../backend/schemas.py#L44) | tag PATCH |
| `GraphActionRequest` | | [graph-router.md](../backend-routers/graph-router.md) |
| `IngestRequest`, `IngestUrlRequest` | | [ingest-router.md](../backend-routers/ingest-router.md) |
| `LoginRequest` | [#L29](../../backend/schemas.py#L29) | login |
| `ManualDetectionBody` | [#L65](../../backend/schemas.py#L65) | operator-drawn detection |
| `ObjectDetailsBody` | [#L55](../../backend/schemas.py#L55) | details PUT |
| `OntologyBranchIn`, `OntologyBranchPatch` | [#L210](../../backend/schemas.py#L210) | branch CRUD |
| `OntologyObjectIn`, `OntologyObjectPatch` | [#L233](../../backend/schemas.py#L233) | object CRUD |
| `OntologyUpdateRequest` | [#L203](../../backend/schemas.py#L203) | LLM bulk edit |
| `PinRequest` | [#L190](../../backend/schemas.py#L190) | track pin/unpin |
| `ReprocessRequest` | [#L194](../../backend/schemas.py#L194) | track reprocess |
| `ReviewUpdate` | [#L81](../../backend/schemas.py#L81) | review PATCH |
| `TrainingJobCreate` | | [models-training-router.md](../backend-routers/models-training-router.md) |

Not exhaustive — ~25 shapes total. `grep -n "^class " backend/schemas.py` for the live list. Candidate-link approve/reject, graph promotion, and graph contradict do not accept reviewer-name body schemas; they derive reviewer identity from the authenticated session.

**Reference Embedding DB (Plan D)** — section added below the Detections block: `ReferenceChipRef`, `ReferencePlatformSummary`, `ReferencePlatformDetail`, `ReferencePlatformsList`, `IdentifyRequest`, `IdentificationCandidate`, `IdentifyResponse`, `IdentificationCandidatesList`, `ApproveRejectResponse`. Consumed by [reference_platforms router](../backend-routers/reference-platforms-router.md).

## Convention

Adding a new endpoint:

1. Define the body shape here.
2. Import in the router: `from schemas import MyNewBody`.
3. Use as the route's `body: MyNewBody = Body(...)` parameter.

Don't define shapes in the router file — keeps router files navigable.

## Cross-references

- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
