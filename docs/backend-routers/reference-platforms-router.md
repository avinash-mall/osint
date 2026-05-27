# `backend/routers/reference_platforms.py` — Reference Embedding DB HTTP API

**Path:** [backend/routers/reference_platforms.py](../../backend/routers/reference_platforms.py)
**Lines:** ~400
**Depends on:** `backend/reference_platform_db.py` (helpers), `backend/schemas.py` (Pydantic models), `backend/auth.py` (`get_current_user`), `backend/database.py` (pool).

## Purpose
Exposes the Reference Embedding DB to authenticated analysts. Six routes:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/reference-platforms` | List platforms; filter by family/country/ontology_object_id; paginated. |
| GET | `/api/reference-platforms/{platform_id}` | Detail view with up to `max_chips` reference chips. 404 if unknown. |
| POST | `/api/detections/{detection_id}/identify` | Re-run lookup against pgvector for an existing detection that has an embedding. Re-writes the candidate queue idempotently; never auto-applies (analyst path). |
| GET | `/api/detections/{detection_id}/identification-candidates` | Read the current candidate queue for a detection. |
| POST | `/api/identification-candidates/{candidate_id}/approve` | Set status='approved', write `platform_*` to `object_details` with `platform_source='analyst'`, `updated_by=<session-username>`. |
| POST | `/api/identification-candidates/{candidate_id}/reject` | Set status='rejected'; leaves `object_details` untouched. |

## Why this design
- **Every endpoint explicitly takes `Depends(get_current_user)`** — even the POST ones the session middleware already gates. The explicit dep is belt-and-suspenders consistency + survives a middleware refactor + makes router-only tests still meaningful. See [why-analyst-username-from-session.md](../decisions/why-analyst-username-from-session.md).
- **`/identify` disables auto-apply** by passing `auto_threshold=999.0`. Analysts already saw the original auto-applied candidate; the re-identify is meant to surface alternatives, not silently rewrite `object_details` again. Approve/reject is the analyst's path to write.
- **Approve and the worker auto-path share `_upsert_platform_identification`** — one SQL site for both, differs only by `platform_source` and `updated_by`. Decision: [why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md).
- **`view_domain` is per-request**, defaulting to `"overhead"`. Plan C's worker splice was overhead-only by design (every satellite detection is overhead); Plan D's request body lets analysts identify FMV ground-view chips when that path lands.
- **NaN-safe approve**: a zero-vector query produces cosine 0/0 = NaN, which would violate `platform_confidence ∈ [0,1]`. The approve handler clamps via `math.isfinite` before passing to `_upsert_platform_identification`.

## Key symbols
- [`list_reference_platforms`](../../backend/routers/reference_platforms.py#L85-L134) — GET list with filters.
- [`get_reference_platform`](../../backend/routers/reference_platforms.py#L146-L199) — GET detail with chips.
- [`identify_detection`](../../backend/routers/reference_platforms.py#L211-L268) — POST analyst re-identify.
- [`get_identification_candidates`](../../backend/routers/reference_platforms.py#L280-L304) — GET queue.
- [`approve_identification_candidate`](../../backend/routers/reference_platforms.py#L316-L371) — POST analyst approve (also writes platform_* to object_details).
- [`reject_identification_candidate`](../../backend/routers/reference_platforms.py#L383-L410) — POST analyst reject.
- [`_decode_embedding_anchor`](../../backend/routers/reference_platforms.py#L41-L58) — local helper that decodes `metadata['embedding'].fp16_b64` to a numpy ndarray. Returns ndarray (NOT a list) because the pgvector adapter dispatches on `np.ndarray`.

## Inputs / Outputs
- Inputs: HTTP requests with a valid `sentinel_session` cookie. Path/query/body Pydantic-validated.
- Outputs: JSON per the Pydantic response models in `backend/schemas.py`.

## Failure modes
- 401 Unauthorized — no valid session.
- 400 — detection has no embedding (cannot identify without one).
- 404 — detection / platform / candidate not found.
- 500 — defensive; only fires on FK violations the schema should already prevent.

## Cross-references
- Schema and helpers: [reference-platform-db.md](../backend/reference-platform-db.md).
- Auto-identify worker path (sibling): worker splice documented in [worker-legacy-monolith.md](../backend/worker-legacy-monolith.md); decision in [why-auto-identify-in-backend-not-inference.md](../decisions/why-auto-identify-in-backend-not-inference.md).
- Plan D spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md](../superpowers/plans/2026-05-27-reference-db-plan-d-backend-api.md).
