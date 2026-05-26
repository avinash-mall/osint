# `backend/main.py` — FastAPI Entrypoint

**Path:** [backend/main.py](../../backend/main.py)
**Lines:** ~2477
**Depends on:** Every other backend module — see import block at top.

## Purpose

Holds the FastAPI application object. Mounts the 13 routers, registers session middleware gating mutating verbs, declares lifespan startup, **and** holds bulk read endpoints never moved out (`/api/detections` GET, `/api/tracks/*`, `/api/observations`, `/api/timeline/events`, `/api/feeds/*`, `/api/sources/*`, `/api/imagery` extras, `/api/collection/tasks`).

## Why this design

- **Centralized session middleware** at [main.py#L97-L114](../../backend/main.py#L97-L114) gates every `POST`/`PUT`/`PATCH`/`DELETE` except a small whitelist (`/api/auth/login`, `/api/auth/logout`). New routers inherit free — no per-endpoint `Depends(get_current_user)`.
- **Read endpoints still here** — predate the router refactor. Migration is Phase-2; same "preserve names, then migrate" pattern as [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).
- **CORS allows `*` origins** — nginx is the production gateway, CORS enforced at the edge. Permissive backend setting is for dev with `npm run dev`.

## Key symbols

- [`lifespan`](../../backend/main.py#L57-L67) — async contextmanager: `_auto_seed_ontology_if_empty()` on startup, `db.close()` on shutdown. Passed to `FastAPI(lifespan=...)`; replaces deprecated `@app.on_event(...)`.
- [`app = FastAPI(...)`](../../backend/main.py#L69) — application object.
- [`require_session_on_mutations`](../../backend/main.py#L97-L114) — the middleware.
- [`app.include_router(...)`](../../backend/main.py#L183-L195) — router mount block; **add new routers here**.
- [`get_detection_classes`](../../backend/main.py#L1228-L1392) — Detection Classes summary for the map panel; returns deterministic `label` plus optional `display_label` / `label_source` when YOLOE-PF imagery AMG rows can safely promote an LLM advisory.
- [`FMV_FALLBACK_PROMPTS`](../../backend/main.py#L915) — precision-first fallback for FMV PCS uploads without explicit prompts.

## Inputs / Outputs

`POST /api/fmv/clips` accepts optional comma-separated `prompts`. Omitted in PCS mode → queues `process_fmv` with `["vehicle", "person", "building"]`, not all ontology prompts.

`GET /api/detections/classes?llm=true` aggregates PostGIS detections by raw class. The raw `class` and deterministic `label` remain stable for filtering and audit. Rows where every detection came from image `model=yolo26 + prompt_mode=amg` / `enabled_layers=["yoloe_pf"]` also return `display_label` from the LLM advisory, `label_source="llm_advisory"`, and `amg_image_count`; mixed or non-AMG rows keep `label_source="deterministic"`.

## Failure modes

FMV prompt-mode validation rejects unknown modes and SAM3 AMG; promptless detection handled by choosing model `yolo26` with AMG mode → maps to worker's `yoloe` path.

LLM unavailable during Detection Classes enrichment leaves `display_label` equal to the deterministic label and `classification_status="unavailable"`; the class list still renders.

## Cross-references

- [backend/api-routes-reference.md](api-routes-reference.md) — complete route table
- [backend/auth-and-sessions.md](auth-and-sessions.md)
- [decisions/why-amg-detection-classes-use-llm-labels.md](../decisions/why-amg-detection-classes-use-llm-labels.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
