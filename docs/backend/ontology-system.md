# `backend/ontology.py` — Canonical Ontology

**Path:** [backend/ontology.py](../../backend/ontology.py)
**Lines:** ~658
**Depends on:** [backend/database.py](../../backend/database.py) (`postgis_db`)

## Purpose

DB-canonical normalize/lookup for object labels. Maps free-text labels into the ontology tree (branch → object), resolves default text prompts per sensor, routes unrecognized labels into the triage queue. Every backend module touching a label imports from here.

## Why this design

- **DB is source of truth** — seed JSON in [backend/scripts/seed_ontology.py](../../backend/scripts/seed_ontology.py) consumed **once** at bootstrap; after that edits go through UI, persist in PostGIS (`ontology_branches`, `ontology_objects`, `ontology_unknown_labels`).
- **Read-through cache** keyed by `db_version` — invalidates on version-bump detection (`_get_tree`, `_read_db_version`). The `SELECT version_id` probe is throttled (`ONTOLOGY_VERSION_CHECK_TTL_S`, default 2 s); in-process edits force an immediate rebuild via `bump_version() → invalidate_cache()`, so the throttle only affects out-of-process version changes.
- **`normalize()` is result-memoized** per `(canon, layer)` against the current tree (`_NORMALIZE_MEMO`, cleared on every rebuild / `invalidate_cache`). The matching sweep + unknown-label upsert ran on *every* call (~1.3 ms) and dominated any loop normalizing thousands of detections (e.g. `/api/detections/geojson`); the memo makes a repeat label a dict lookup. See [decisions/why-memoize-ontology-normalize.md](../decisions/why-memoize-ontology-normalize.md).
- **`normalize()` never raises** — unknown labels return a `NormalizedLabel` with `branch_id="unknown"` AND get logged to `ontology_unknown_labels` via UPSERT → appear in admin triage UI. Failure to write the unknown row also swallowed — normalization must not break the detection pipeline.
- **One object per unique prompt; UI label IS the prompt.** The seed ([defenceOntology.seed.json](../../backend/scripts/seeds/defenceOntology.seed.json)) carries no separate defence-terminology layer — each object's `label` equals the title-cased `prompt` sent to the model. This kills the old false positive where two objects shared a vague prompt and `normalize()` tie-broke to an arbitrary (often wrong) defence label. See [decisions/why-deconflicted-detection-prompts.md](../decisions/why-deconflicted-detection-prompts.md).
- **`prompts_by_branch` enables scene-scoped vocabularies** — `_build_tree` rolls every object's prompt up to its branch and ancestor branches so a caller can request a small, scene-relevant prompt set instead of the whole vocabulary (the main lever for open-vocabulary precision).

## Key symbols

- [`NormalizedLabel`](../../backend/ontology.py#L41) — dataclass `{branch_id, object_id, canonical_label, icon_key}`.
- [`_canonical`](../../backend/ontology.py#L69) — lowercase + strip + collapse whitespace.
- [`_strip_source_prefix`](../../backend/ontology.py#L88) — drops `"sam3: "`, `"yoloe: "`, etc.
- [`_build_tree`](../../backend/ontology.py#L113) — reads `ontology_branches` + `ontology_objects` into in-memory map.
- [`_get_tree`](../../backend/ontology.py#L210) — cache lookup; `_build_tree` on miss.
- [`invalidate_cache`](../../backend/ontology.py#L243) — public; used by tests and SIGHUP.
- [`_log_unknown`](../../backend/ontology.py#L299) — UPSERT into `ontology_unknown_labels` plus the two *noisy* side-effects: the `ontology.normalize: unknown label=…` WARNING and the `worker.project_unknown_labels` enqueue that refreshes the Neo4j triage mirror. Both noisy side-effects are **throttled per label** via [`_should_surface_unknown`](../../backend/ontology.py#L285) (process-local, once per label per 5-min window). Because `normalize()` is now memoized, `_log_unknown` only runs on a memo *miss* (once per label per ontology version), not once per call — so the triage row + `last_seen` are still recorded but the internal occurrence counter no longer counts every call (it is consumed by no feature). See [decisions/why-memoize-ontology-normalize.md](../decisions/why-memoize-ontology-normalize.md).
- [`normalize`](../../backend/ontology.py#L337) — main entry point; result-memoized wrapper around [`_normalize_uncached`](../../backend/ontology.py#L362) (the matching sweep + step-4 unknown fallback). On a memo hit the sweep and `_log_unknown` are skipped.
- [`default_prompts`](../../backend/ontology.py#L410) — `(sensor, branch)` → prompt list (used by inference via the router). `branch` scopes to that branch + descendants.
- [`all_prompts`](../../backend/ontology.py#L431).

## Failure modes

- DB unreachable → `_build_tree` raises; cached tree (if any) reused. `normalize()` falls back to cached tree even on DB failure to keep pipeline alive.
- Unknown label → `branch_id="unknown"`, logged to triage.
- Version cursor missing → `_read_db_version` returns `None`; cache lifetime falls back to 30 s.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)
- Tests: [backend/tests/test_ontology.py](../../backend/tests/test_ontology.py)
