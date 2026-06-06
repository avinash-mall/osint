# `backend/main.py` — FastAPI Entrypoint

**Path:** [backend/main.py](../../backend/main.py)
**Lines:** ~2556
**Depends on:** Every other backend module — see import block at top.

## Purpose

Holds the FastAPI application object. Mounts the 19 routers (including `reference_platforms` — the Reference Embedding DB HTTP surface at `/api/reference-platforms` and the `/api/detections/{id}/identify*` family), registers session middleware gating mutating verbs, declares lifespan startup, **and** holds bulk read endpoints never moved out (`/api/detections` GET, `/api/tracks/*`, `/api/observations`, `/api/timeline/events`, `/api/feeds/*`, `/api/sources/*`, `/api/imagery` extras, `/api/collection/tasks`).

## Why this design

- **Centralized session middleware** at [main.py#L110-L127](../../backend/main.py#L110-L127) gates every `POST`/`PUT`/`PATCH`/`DELETE` except a small whitelist (`/api/auth/login`, `/api/auth/logout`). Endpoints that need audit identity still declare `Depends(get_current_user)`.
- **Read endpoints still here** — predate the router refactor. Migration is Phase-2; same "preserve names, then migrate" pattern as [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).
- **CORS is env-scoped** — `CORS_ORIGINS` defaults to local dev origins; nginx remains the production gateway.
- **Detection-target candidate decisions use session identity** — approve/reject write `reviewed_by` from the signed cookie and reject non-pending rows with 409 so stale clients cannot overwrite the audit trail.

## Key symbols

- [`lifespan`](../../backend/main.py#L62-L72) — async contextmanager: `_auto_seed_ontology_if_empty()` on startup, `db.close()` on shutdown. Passed to `FastAPI(lifespan=...)`; replaces deprecated `@app.on_event(...)`.
- [`get_cors_origins`](../../backend/main.py#L90-L92) — parses `CORS_ORIGINS` for `CORSMiddleware`.
- [`require_session_on_mutations`](../../backend/main.py#L110-L127) — the middleware.
- [`app.include_router(...)`](../../backend/main.py#L197-L216) — router mount block; **add new routers here**.
- [`upload_fmv_clip`](../../backend/main.py#L947-L1126) — `/api/fmv/clips` upload path; HLS transcode + telemetry extraction happen before `process_fmv` dispatch.
- [`delete_fmv_clip`](../../backend/main.py#L1148-L1188) — `DELETE /api/fmv/clips/{id}`, admin-only hard delete: drops `fmv_detections`+`fmv_frames`+`fmv_clips` rows, purges the no-FK `object_details` (`source='fmv_detection'`) via [cascade_delete.py](../../backend/cascade_delete.py), `rmtree`s the clip upload dir (video+HLS+sidecars), `DETACH DELETE`s the Neo4j clip/detection nodes (file/graph best-effort). See [decisions/why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md) and [backend/cascade-delete.md](cascade-delete.md).
- [`_raise_detection_candidate_404_or_409`](../../backend/main.py#L2035-L2055) — disambiguates missing vs already-reviewed detection-target candidates.
- [`approve_detection_target_candidate`](../../backend/main.py#L2058-L2127) / [`reject_detection_target_candidate`](../../backend/main.py#L2130-L2152) — session-derived reviewer identity + pending-only updates.

## Inputs / Outputs

`POST /api/fmv/clips` accepts optional comma-separated `prompts`. Omitted in PCS mode → queues `process_fmv` with `["vehicle", "person", "building"]`, not all ontology prompts.

`GET /api/detections/classes?llm=true` aggregates PostGIS detections by raw class. The raw `class`, deterministic `label`, branch, and threat fields remain stable for filtering and audit. When requested, the first rows may include `llm_advisory` text, but `display_label` remains deterministic.

`POST /api/detection-target-candidates/{id}/approve|reject` no longer accepts a request-body analyst field. The active `SessionUser.username` is the only `reviewed_by` source.

## Failure modes

FMV prompt-mode validation rejects unknown modes and SAM3 AMG; promptless detection handled by choosing model `yolo26` with AMG mode → maps to worker's `yoloe` path. Telemetry validation failure removes the staged HLS/upload directory before returning 422 so rejected clips do not leave orphaned runtime files.

LLM unavailable during Detection Classes enrichment leaves `display_label` equal to the deterministic label and `classification_status="unavailable"`; the class list still renders.

Candidate approve/reject on an already-reviewed row returns HTTP 409 with the current status/reviewer instead of overwriting.

## Cross-references

- [backend/api-routes-reference.md](api-routes-reference.md) — complete route table
- [backend/auth-and-sessions.md](auth-and-sessions.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md)
- [decisions/why-reject-double-review-with-409.md](../decisions/why-reject-double-review-with-409.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
