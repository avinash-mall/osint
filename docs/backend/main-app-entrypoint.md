# `backend/main.py` — FastAPI Entrypoint

**Path:** [backend/main.py](../../backend/main.py)
**Lines:** ~2470
**Depends on:** Every other backend module — see import block at top.

## Purpose

Holds the FastAPI application object. Mounts the 19 routers (including `reference_platforms` — the Reference Embedding DB HTTP surface at `/api/reference-platforms` and the `/api/detections/{id}/identify*` family), registers session middleware gating mutating verbs, declares lifespan startup, **and** holds bulk read endpoints never moved out (`/api/detections` GET, `/api/tracks/*`, `/api/observations`, `/api/timeline/events`, `/api/feeds/*`, `/api/sources/*`, `/api/imagery` extras, `/api/collection/tasks`).

## Why this design

- **Centralized session middleware** at [main.py#L106-L123](../../backend/main.py#L106-L123) gates every `POST`/`PUT`/`PATCH`/`DELETE` except a small whitelist (`/api/auth/login`, `/api/auth/logout`). New routers inherit free — no per-endpoint `Depends(get_current_user)`.
- **Read endpoints still here** — predate the router refactor. Migration is Phase-2; same "preserve names, then migrate" pattern as [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).
- **CORS allows `*` origins** — nginx is the production gateway, CORS enforced at the edge. Permissive backend setting is for dev with `npm run dev`.

## Key symbols

- [`lifespan`](../../backend/main.py#L62-L72) — async contextmanager: `_auto_seed_ontology_if_empty()` on startup, `db.close()` on shutdown. Passed to `FastAPI(lifespan=...)`; replaces deprecated `@app.on_event(...)`.
- [`app = FastAPI(...)`](../../backend/main.py#L74) — application object.
- [`require_session_on_mutations`](../../backend/main.py#L106-L123) — the middleware.
- [`app.include_router(...)`](../../backend/main.py#L197-L215) — router mount block; **add new routers here**.
- [`upload_fmv_clip`](../../backend/main.py#L939) — `/api/fmv/clips` upload path; HLS transcode + telemetry extraction happen before `process_fmv` dispatch.
- [`delete_fmv_clip`](../../backend/main.py#L1145) — `DELETE /api/fmv/clips/{id}`, admin-only hard delete: drops `fmv_detections`+`fmv_frames`+`fmv_clips` rows, `rmtree`s the clip upload dir (video+HLS+sidecars), `DETACH DELETE`s the Neo4j clip/detection nodes (file/graph best-effort). See [decisions/why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md).
- [`get_detection_classes`](../../backend/main.py#L1247) — Detection Classes summary for the map panel; returns deterministic labels, ontology rollups, branch breakdowns, and optional non-authoritative LLM advisory metadata.
- [`FMV_FALLBACK_PROMPTS`](../../backend/main.py#L935) — precision-first fallback for FMV PCS uploads without explicit prompts.

## Inputs / Outputs

`POST /api/fmv/clips` accepts optional comma-separated `prompts`. Omitted in PCS mode → queues `process_fmv` with `["vehicle", "person", "building"]`, not all ontology prompts.

`GET /api/detections/classes?llm=true` aggregates PostGIS detections by raw class. The raw `class`, deterministic `label`, branch, and threat fields remain stable for filtering and audit. When requested, the first rows may include `llm_advisory` text, but `display_label` remains deterministic.

## Failure modes

FMV prompt-mode validation rejects unknown modes and SAM3 AMG; promptless detection handled by choosing model `yolo26` with AMG mode → maps to worker's `yoloe` path. Telemetry validation failure removes the staged HLS/upload directory before returning 422 so rejected clips do not leave orphaned runtime files.

LLM unavailable during Detection Classes enrichment leaves `display_label` equal to the deterministic label and `classification_status="unavailable"`; the class list still renders.

## Cross-references

- [backend/api-routes-reference.md](api-routes-reference.md) — complete route table
- [backend/auth-and-sessions.md](auth-and-sessions.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
