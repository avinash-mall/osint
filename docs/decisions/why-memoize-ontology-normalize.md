# Why ontology.normalize() is result-memoized

**Decision date:** 2026-06-11
**Status:** active
**Scope:** [backend/ontology.py](../../backend/ontology.py) `normalize`, `_get_tree`, `_invalidate_cache`.

## Context

`/api/detections/geojson` timed out (~30 s) over a dense scene (6441 detections after the San Diego airport ingest). Profiling showed the SQL query was ~1 s and the response build was the rest: `enriched_detection_metadata()` ran **24 s** in a per-row Python loop. Drilling in, the cost was `ontology.normalize()` — called several times per detection (via `parent_class_for_label`, `detection_decision`, `conservative_detection_ontology`) at **~1.3 ms/call** over only **26 distinct labels**.

`normalize()` was doing, on *every* call:
1. a `SELECT version_id` DB round-trip (the per-call freshness probe), and
2. the full label-matching sweep — exact-match dict lookups, then for unmatched labels a regex branch-matcher sweep, then an unknown-label **DB upsert** (`ontology_unknown_labels`, occurrence counter).

So a read endpoint was running thousands of regex sweeps and thousands of DB writes for the same handful of labels.

## Decision

Result-memoize `normalize()` keyed by `(canon, layer)` against the current ontology tree:

- A process-global `_NORMALIZE_MEMO` caches each `NormalizedLabel`. It is cleared whenever the tree is rebuilt (`_get_tree`) or invalidated (`_invalidate_cache`) — and **every** ontology mutation already routes through `bump_version() → invalidate_cache()`, so edits are picked up immediately (the tests rely on this too).
- The per-call `SELECT version_id` probe is throttled (`ONTOLOGY_VERSION_CHECK_TTL_S`, default 2 s) — it is only the *secondary* freshness path; in-process edits force an immediate rebuild by emptying the cache, independent of the throttle.

## Why this design

- **The result is a pure function of the ontology tree.** Memoizing it is correct as long as the memo dies with the tree — which it does. The throttle and memo together take `normalize()` from ~1.3 ms to a dict lookup.
- **Systemic, not endpoint-local.** Every hot caller benefits — detection persistence in the worker calls `normalize()` several times per detection, plus the AI context snapshot, threat assessment, etc. Fixing it at the source beats special-casing geojson.
- **The skipped side-effect is a no-op nobody reads.** On a memo hit the unknown-label upsert is skipped, so `ontology_unknown_labels.count` increments once per (label, version-window) instead of once per call. That column is **consumed by no backend or frontend code** (only a unit test asserted it), and it was already inflated by every map render (a read path mutating the DB). The triage *row* — which is what surfaces a label for ontology curation — is still recorded on first encounter, and `last_seen` still advances on each version window. The noisy warning + Neo4j projection were already throttled to once per label per 300 s.

## Consequences

- `/api/detections/geojson` enrichment: **24 s → 1.2 s** (0.18 ms/row); the endpoint dropped from a 30 s timeout to ~8 s for the unbounded all-detections case and ~5 s bbox-scoped — the remainder is response serialization (≈57 MB for 6441 fully-enriched features), not CPU.
- Ontology edits are reflected within ≤2 s for out-of-process changes, immediately for in-process edits (unchanged behaviour for the admin UI / worker, which go through `invalidate_cache`).
- `ontology_unknown_labels.count` is now "distinct encounters per version window," not "normalize calls." Test updated ([tests/test_ontology.py](../../backend/tests/test_ontology.py) `test_unknown_label_recorded_and_memoized`). If a true per-detection occurrence metric is ever needed, count it once at detection-persist time, not inside `normalize`.

## Related

- [backend/ontology-system.md](../backend/ontology-system.md) — the normalize/cache module.
- [decisions/why-class-scope-replaces-node-limit.md](why-class-scope-replaces-node-limit.md) — the graph endpoints whose larger datasets exposed this.
- [decisions/why-release-db-connection-before-enrichment.md](why-release-db-connection-before-enrichment.md) — the prior geojson fix (connection-holding) that this complements.
