# Audit fixes — API layer (2026-06-11)

Batch of verified bug fixes in the FastAPI routing layer from the 2026-06-11 audit. One decision doc for the batch; per-file details live in the module docs.

## What changed

**Broken reads (wrong column / key / status vocabulary):**

- `/api/ai/brief-area` 500'd on every call: `_detections_within` selected the non-existent `object_class` column. Now `class AS object_class`, and soft-deleted rows are excluded ([routers/ai.py](../../backend/routers/ai.py)).
- Both `/similar` endpoints always returned empty: `_detection_embedding` only accepted a plain list, but the worker stores the packed `{model, dim, fp16_b64}` dict. Added `_decode_packed_embedding` (mirrors `_parse_embedding_anchor` in worker_legacy.py); the legacy list branch is kept ([main.py](../../backend/main.py)).
- Candidate-link history term and `execute_action_proposal`'s target resolution filtered `status IN ('accepted','confirmed')`, but the only writers set `'approved'`. Filters are now `IN ('approved','accepted','confirmed')` — `approved` is canonical, the legacy values are tolerated for old rows.
- Target Package PDF read `metadata.threat` / `metadata.affiliation`, keys nothing writes. It now reads `metadata.threat_level` / `metadata.allegiance` with the `detections.threat_level` / `affiliation` columns as fallback ([routers/reports.py](../../backend/routers/reports.py)).
- Detection list/classes/geojson-lite/enriched/candidate-row queries `JOIN satellite_passes`, silently hiding operator-drawn detections (NULL `pass_id`). Switched to `LEFT JOIN` with NULL-tolerant pass fields. Same fix applied to `pin_detection`'s lookup (`COALESCE(sp.acquisition_time, d.created_at)` seeds the track timestamps), so manual detections can be pinned.

**Vocabulary unification:** `PATCH /tag` stored `friendly` while `PUT /details` normalized to `friend` on the same `metadata.allegiance` key. `/tag` now runs the shared `_normalize_affiliation` (friendly → friend), still accepts both spellings, and also writes the `affiliation` column. Canonical stored value is `friend`.

**Race / atomicity fixes:**

- `execute_action_proposal` claim-first: `UPDATE … SET status='executing' WHERE status='approved' RETURNING` before side effects (409 when not claimable), finalize to `executed` after; the claim is released back to `approved` on a side-effect exception so a downstream outage doesn't strand the proposal.
- `pin_detection` is one transaction; the member `INSERT … ON CONFLICT (detection_id) DO NOTHING RETURNING` decides winner/loser, the loser deletes its just-created track and pins the winner's — no more orphan zero-member pinned tracks on double-click.
- `merge_entity_into` re-homes A's `operational_entity_tracks` attachments (INSERT … ON CONFLICT DO NOTHING + DELETE), `unit_id` / `operates_from_base_id` soft references, and `entity_candidates.approved_entity_id` to B inside the merge transaction before deleting A.
- `approve_detection_target_candidate`'s Neo4j `DETECTED_AS` write is now best-effort (try/except → log + `graph_written: false` in the response), matching the graph_writes.py pattern. Previously a Neo4j outage 500'd after the PostGIS approval committed, and the retry hit the double-review 409.

**HTTP-boundary validation (400, not 500 / not silent):**

- New `parse_iso_datetime` helper in main.py; malformed `start_time`/`end_time`/`start`/`end`/`since` now 400 on `/api/tracks/detections`, `/api/observations`, `/api/timeline/events`, `/api/detections`, `/api/detections/geojson-lite`, `/api/ontology/unknown-labels`.
- `/api/tracks/detections` bbox goes through `geometry.parse_bbox` (400) instead of being silently dropped (which returned unfiltered data as if filtered).
- `ingest_feed_event` coerces `confidence`/`speed`/`heading` before the transaction — a garbage value previously raised after the feed_event commit, so the client retry duplicated the event.
- Manual detections: `detections.geom` is `GEOMETRY(POLYGON)`; a single-part MultiPolygon is stored as its one polygon (`ST_GeometryN(…, 1)`), a multi-part MultiPolygon is rejected with 400 rather than 500ing or silently dropping parts.

**Honest proposal confidence:** `ai_propose_actions` no longer hardcodes 0.62 unconditionally against the schema's fail-loudly intent. It uses `payload.confidence` (clamped 0..1) when supplied and records `confidence_source: proposer|default` in the stored payload.

**SOLO filter parity:** `det_class` on `/api/detections` and `/geojson-lite` matches `d.class OR metadata.original_class`, since the frontend sends the displayed label (which prefers `original_class`). `geojson-lite` already exposed `calibrated_confidence`; no change needed there.

**Doc/dead-code drift:** removed the phantom `POST /api/ontology/update` row from [api-routes-reference.md](../backend/api-routes-reference.md) and the unused `OntologyUpdateRequest` import in main.py (the schema class remains for history).

## Why

All were verified against the code before fixing; each fix is the minimal change that makes the read match the writer (or makes the writer atomic). Read-route authentication gating was explicitly left out of this batch — separate posture decision.

## Cross-references

- [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
- [backend-routers/ai-router.md](../backend-routers/ai-router.md)
- [backend-routers/detections-router.md](../backend-routers/detections-router.md)
- [backend-routers/reports-router.md](../backend-routers/reports-router.md)
- [backend-routers/operational-entities-router.md](../backend-routers/operational-entities-router.md)
- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [why-reject-double-review-with-409.md](why-reject-double-review-with-409.md)
- Tests: [backend/tests/test_detection_embedding_decode.py](../../backend/tests/test_detection_embedding_decode.py)
