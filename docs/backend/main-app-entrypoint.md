# `backend/main.py` — FastAPI Entrypoint

**Path:** [backend/main.py](../../backend/main.py)
**Lines:** ~2405
**Depends on:** Every other backend module — see import block at top.

## Purpose

The FastAPI application object lives here. Mounts the 13 routers, registers the session middleware that gates mutating verbs, declares the lifespan startup, and **also** holds the bulk read endpoints that were never moved out (`/api/detections` GET, `/api/tracks/*`, `/api/observations`, `/api/timeline/events`, `/api/feeds/*`, `/api/sources/*`, `/api/imagery` extras, `/api/collection/tasks`, `/api/ontology/updates`).

## Why this design

- **Centralized session middleware** at [main.py#L84-L107](../../backend/main.py#L84-L107) gates every `POST`/`PUT`/`PATCH`/`DELETE` except a small whitelist (`/api/auth/login`, `/api/auth/logout`). New routers inherit this for free — no per-endpoint `Depends(get_current_user)` needed.
- **Read endpoints still here** because they predate the router refactor. Migrating them is a Phase-2 task — see [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md) for the same "preserve names, then migrate" pattern.
- **CORS allows `*` origins** because nginx is the production gateway and CORS is enforced at the edge. The backend's permissive setting is for development with `npm run dev`.

## Key symbols

- [`app = FastAPI(...)`](../../backend/main.py#L56) — the application object.
- [`require_session_on_mutations`](../../backend/main.py#L84-L107) — the middleware.
- [`app.include_router(...)`](../../backend/main.py#L170-L182) — router mount block; **add new routers here**.
- [`FMV_FALLBACK_PROMPTS`](../../backend/main.py#L909) — precision-first fallback for FMV PCS uploads without explicit prompts.

## Inputs / Outputs

`POST /api/fmv/clips` accepts optional comma-separated `prompts`. If omitted in PCS mode, it queues `process_fmv` with `["vehicle", "person", "building"]` rather than expanding all ontology prompts.

## Failure modes

FMV prompt-mode validation rejects unknown modes and rejects SAM3 AMG; promptless detection is handled by choosing model `yolo26` with AMG mode, which maps to the worker's `yoloe` path.

## Cross-references

- [backend/api-routes-reference.md](api-routes-reference.md) — complete route table
- [backend/auth-and-sessions.md](auth-and-sessions.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
