# `backend/ontology.py` — Canonical Ontology

**Path:** [backend/ontology.py](../../backend/ontology.py)
**Lines:** ~523
**Depends on:** [backend/database.py](../../backend/database.py) (`postgis_db`)

## Purpose

DB-canonical normalize/lookup for object labels. Maps free-text labels into the ontology tree (branch → object), resolves default text prompts per sensor, and routes unrecognized labels into the triage queue. Every backend module that touches a label imports from here.

## Why this design

- **DB is the source of truth.** The seed JSON in [backend/scripts/seed_ontology.py](../../backend/scripts/seed_ontology.py) is consumed **once** at bootstrap. After that, edits go through the UI and persist in PostGIS (`ontology_branches`, `ontology_objects`, `ontology_unknown_labels`).
- **Read-through cache** keyed by `db_version`. Cache invalidates when a version bump is detected — see `_get_tree` and `_read_db_version`. Cache hit avoids a round-trip per detection.
- **`normalize()` never raises.** Unknown labels return a `NormalizedLabel` with `branch_id="unknown"` AND get logged to `ontology_unknown_labels` via UPSERT so they appear in the admin triage UI. Failure to write the unknown row is also swallowed — normalization itself must not break the detection pipeline.

## Key symbols

- [`NormalizedLabel`](../../backend/ontology.py#L41) — dataclass: `{branch_id, object_id, canonical_label, icon_key}`.
- [`_canonical`](../../backend/ontology.py#L69) — lowercase + strip + collapse whitespace.
- [`_strip_source_prefix`](../../backend/ontology.py#L88) — drops `"sam3: "`, `"yoloe: "`, etc.
- [`_build_tree`](../../backend/ontology.py#L113) — reads `ontology_branches` + `ontology_objects` into the in-memory map.
- [`_get_tree`](../../backend/ontology.py#L210) — cache lookup; calls `_build_tree` on miss.
- [`invalidate_cache`](../../backend/ontology.py#L243) — public; used by tests and SIGHUP.
- [`_log_unknown`](../../backend/ontology.py#L250) — UPSERT into `ontology_unknown_labels`.
- [`normalize`](../../backend/ontology.py#L271) — the main entry point.
- [`default_prompts`](../../backend/ontology.py#L391) — sensor → prompt list (used by inference via the router).
- [`all_prompts`](../../backend/ontology.py#L403).

## Failure modes

- DB unreachable → `_build_tree` raises; cached tree (if any) is reused. `normalize()` falls back to the cached tree even on DB failure to keep the pipeline alive.
- Unknown label → `branch_id="unknown"`, logged to triage.
- Version cursor missing → `_read_db_version` returns `None`; cache lifetime falls back to 30 seconds.

## Cross-references

- [backend-routers/ontology-router.md](../backend-routers/ontology-router.md)
- [decisions/why-open-vocabulary.md](../decisions/why-open-vocabulary.md)
- [operations/ontology-edit-workflow.md](../operations/ontology-edit-workflow.md)
- [operations/unknown-label-triage.md](../operations/unknown-label-triage.md)
- [conventions/adding-a-new-ontology-object.md](../conventions/adding-a-new-ontology-object.md)
- Tests: [backend/tests/test_ontology.py](../../backend/tests/test_ontology.py)
