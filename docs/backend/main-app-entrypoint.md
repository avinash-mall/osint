# `backend/main.py` — FastAPI Entrypoint

**Path:** [backend/main.py](../../backend/main.py)
**Lines:** ~2668
**Depends on:** Every other backend module — see import block at top.

## Purpose

Holds the FastAPI application object. Mounts the 19 routers (including `reference_platforms` — the Reference Embedding DB HTTP surface at `/api/reference-platforms` and the `/api/detections/{id}/identify*` family), registers session middleware gating every `/api` verb — mutations and reads, declares lifespan startup, **and** holds bulk read endpoints never moved out (`/api/detections` GET, `/api/tracks/*`, `/api/observations`, `/api/timeline/events`, `/api/feeds/*`, `/api/sources/*`, `/api/imagery` extras, `/api/collection/tasks`).

## Why this design

- **Centralized session middleware** at [main.py#L118-L142](../../backend/main.py#L118-L142) gates every `POST`/`PUT`/`PATCH`/`DELETE` except `/api/auth/login` + `/api/auth/logout`, **and** every `GET`/`HEAD` under `/api/` except the public-read allowlist (`/api/auth/*`, `/api/health`, `/api/system/deployment-mode`, `/api/ontology/default-prompts` — the last is fetched service-to-service by inference-sam3). Endpoints that need audit identity still declare `Depends(get_current_user)`. See [decisions/why-read-routes-require-session.md](../decisions/why-read-routes-require-session.md).
- **Read endpoints still here** — predate the router refactor. Migration is Phase-2; same "preserve names, then migrate" pattern as [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md).
- **CORS is env-scoped** — `CORS_ORIGINS` defaults to local dev origins; nginx remains the production gateway.
- **Detection-target candidate decisions use session identity** — approve/reject write `reviewed_by` from the signed cookie and reject non-pending rows with 409 so stale clients cannot overwrite the audit trail.

## Key symbols

- [`lifespan`](../../backend/main.py#L62-L94) — async contextmanager. On startup, in order: `ensure_platform_tables()` (eager schema materialisation — creates the detection/platform tables, `reference_platforms`, and the `detections_mvt` tile-source function **before** the backend reports healthy, so Martin's one-shot tile-source scan finds `detections_mvt` and `_auto_enqueue_reference_seed_if_empty()` finds `reference_platforms`; see [decisions/obb-render-fix.md](../decisions/obb-render-fix.md)), then `_auto_seed_ontology_if_empty()`, `_auto_enqueue_reference_seed_if_empty()`, `ensure_graph_schema()`. `db.close()` on shutdown. Passed to `FastAPI(lifespan=...)`; replaces deprecated `@app.on_event(...)`.
- [`get_cors_origins`](../../backend/main.py#L90-L92) — parses `CORS_ORIGINS` for `CORSMiddleware`.
- [`require_session_on_requests`](../../backend/main.py#L121-L142) — the middleware (mutation gate + read gate with public allowlist).
- [`app.include_router(...)`](../../backend/main.py#L197-L216) — router mount block; **add new routers here**.
- [`upload_fmv_clip`](../../backend/main.py#L947-L1126) — `/api/fmv/clips` upload path; HLS transcode + telemetry extraction happen before `process_fmv` dispatch.
- [`delete_fmv_clip`](../../backend/main.py#L1148-L1188) — `DELETE /api/fmv/clips/{id}`, admin-only hard delete: drops `fmv_detections`+`fmv_frames`+`fmv_clips` rows, purges the no-FK `object_details` (`source='fmv_detection'`) via [cascade_delete.py](../../backend/cascade_delete.py), `rmtree`s the clip upload dir (video+HLS+sidecars), `DETACH DELETE`s the Neo4j clip/detection nodes (file/graph best-effort). See [decisions/why-deletable-imagery-and-clips.md](../decisions/why-deletable-imagery-and-clips.md) and [backend/cascade-delete.md](cascade-delete.md).
- [`parse_iso_datetime`](../../backend/main.py#L682-L688) — HTTP-boundary ISO-8601 validator (400, not 500); used by `/api/observations`, `/api/timeline/events`, `/api/detections[?start_time=]`, `/api/detections/geojson-lite`, `/api/tracks/detections`.
- [`_decode_packed_embedding`](../../backend/main.py#L1800-L1813) / [`_detection_embedding`](../../backend/main.py#L1816-L1827) — decode the worker's packed `{model, dim, fp16_b64}` embedding (or the legacy plain list) for both `/similar` endpoints; mirrors `_parse_embedding_anchor` in worker_legacy.py.
- [`_raise_detection_candidate_404_or_409`](../../backend/main.py#L2124-L2145) — disambiguates missing vs already-reviewed detection-target candidates.
- [`approve_detection_target_candidate`](../../backend/main.py#L2148-L2232) / [`reject_detection_target_candidate`](../../backend/main.py#L2235-L2257) — session-derived reviewer identity + pending-only updates. The approve-side Neo4j `DETECTED_AS` write is best-effort (`graph_written` flag in the response) — a graph outage must not 500 an already-committed approval.
- [`pin_detection`](../../backend/main.py#L2514-L2603) — single-transaction pin; the member `INSERT … ON CONFLICT (detection_id) DO NOTHING RETURNING` arbitrates concurrent pins so a double-click can't orphan an empty pinned track.

## Inputs / Outputs

`POST /api/fmv/clips` accepts optional comma-separated `prompts`. Omitted in PCS mode → queues `process_fmv` with `["vehicle", "person", "building"]`, not all ontology prompts.

`GET /api/fmv/clips` and `GET /api/fmv/clips/{id}` enrich each clip via [`fmv_public_url`](../../backend/fmv_helpers.py): `stream_url` (HLS playlist if transcoded, else the raw file) for playback, and `source_url` = `fmv_public_url(None, file_path)` — the original file's `/fmv/<rel>` URL the FMV player's **Export clip** button downloads.

`GET /api/detections/classes?llm=true` aggregates PostGIS detections by raw class. The raw `class`, deterministic `label`, branch, and threat fields remain stable for filtering and audit. When requested, the first rows may include `llm_advisory` text, but `display_label` remains deterministic.

`POST /api/detection-target-candidates/{id}/approve|reject` no longer accepts a request-body analyst field. The active `SessionUser.username` is the only `reviewed_by` source.

## Failure modes

FMV prompt-mode validation rejects unknown modes and SAM3 AMG; promptless detection handled by choosing model `yolo26` with AMG mode → maps to worker's `yoloe` path. Telemetry validation failure removes the staged HLS/upload directory before returning 422 so rejected clips do not leave orphaned runtime files.

LLM unavailable during Detection Classes enrichment leaves `display_label` equal to the deterministic label and `classification_status="unavailable"`; the class list still renders.

Candidate approve/reject on an already-reviewed row returns HTTP 409 with the current status/reviewer instead of overwriting. Approve commits the PostGIS row first; a Neo4j failure afterwards is logged and reported as `graph_written: false` rather than raised.

Malformed `start_time` / `end_time` / `start` / `end` query params return 400 via `parse_iso_datetime`; malformed `bbox` returns 400 via `parse_bbox` (including `/api/tracks/detections`, which previously dropped a bad bbox silently). `POST /api/feeds/{id}/events` coerces payload `confidence`/`speed`/`heading` to floats (default/None on garbage) before the transaction so a bad value can't 500 after the event row committed.

Detection list/geojson-lite reads `LEFT JOIN satellite_passes` so operator-drawn detections (NULL `pass_id`) appear; their `pass_name`/`acquisition_time` are NULL and active time filters exclude them. `det_class` filters match `d.class` OR `metadata.original_class` (SOLO parity with the displayed label). `PATCH /api/detections/{id}/tag` normalizes allegiance through `_normalize_affiliation` (friendly → friend) and writes both `metadata.allegiance` and the `affiliation` column, matching PUT `/details`.

## Cross-references

- [backend/api-routes-reference.md](api-routes-reference.md) — complete route table
- [backend/auth-and-sessions.md](auth-and-sessions.md)
- [decisions/why-precision-first-inference-defaults.md](../decisions/why-precision-first-inference-defaults.md)
- [decisions/removed-yoloe-imagery.md](../decisions/removed-yoloe-imagery.md)
- [decisions/why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md)
- [decisions/why-reject-double-review-with-409.md](../decisions/why-reject-double-review-with-409.md)
- [decisions/why-security-hardening-2026-05-31.md](../decisions/why-security-hardening-2026-05-31.md)
- [conventions/adding-a-new-router.md](../conventions/adding-a-new-router.md)
- [decisions/audit-fixes-api-layer-2026-06-11.md](../decisions/audit-fixes-api-layer-2026-06-11.md) — the 2026-06-11 API-layer audit batch
