# Graph Write Helpers

**Path:** [backend/graph_writes.py](../../backend/graph_writes.py)
**Lines:** ~160
**Depends on:** [backend/database.py](../../backend/database.py) (`db`)

## Purpose

Owns the shared Neo4j `MERGE`/`DELETE` Cypher used by multiple writer sites — candidate-link creation in [main.py](../../backend/main.py) and [worker_legacy.py](../../backend/worker_legacy.py), plus the new graph routes. One source of truth for each edge predicate so the analyst-facing predicate chip filter stays in sync.

## Why this design

Before Phase 1.B the `CANDIDATE_DETECTED_AS` edge was *synthesised in-memory* on every `/api/graph?include_candidates=true` request ([graph.py#L46-L79](../../backend/routers/graph.py#L46-L79) historical) by joining PostGIS `detection_target_candidates` against existing Neo4j Targets. That made the edge invisible to Cypher traversals — workflow 6 (transitive `MATCH p=(a)-[*1..4]-(b)`) couldn't see pending candidates, and the route paid a PostGIS round-trip per request. Persisting the edge on candidate creation makes it traversable and removes the synthesis path.

Pure-Python scoring stays in [candidate_linking.py](../../backend/candidate_linking.py) — DB writes belong in this module, not there. See [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md) for the broader rationale.

## Key symbols

- [`merge_candidate_detected_as`](../../backend/graph_writes.py#L24-L94) — MERGE Detection (lazy) + MERGE `(t)-[rel:CANDIDATE_DETECTED_AS]->(d)` with `candidate_id, score, reason, status='pending'`. Returns `False` if the Target isn't in Neo4j.
- [`delete_candidate_detected_as`](../../backend/graph_writes.py#L97-L121) — removes the edge for one `(target, detection)` pair. Used by approve/reject endpoints.
- [`promote_candidate_to_detected_as`](../../backend/graph_writes.py#L124-L162) — atomic transition: locates the pending edge by `candidate_id`, MERGE-creates `:DETECTED_AS`, deletes the candidate edge. Used by `/api/graph/candidate-edges/{id}/promote` in Phase 1.C.

## Inputs / Outputs

All helpers take keyword args (no positional) — the candidate creation flow has eight properties to thread through, positional args would be a footgun.

Outputs are side effects on Neo4j plus a small return value (`bool` for merge/delete; `dict | None` for promote). Helpers never raise: Neo4j blips log a warning and return a falsy value. The PostGIS row is the source of truth for candidate state; the Neo4j edge is a derived view.

## Failure modes

- Neo4j unreachable → warning logged, helper returns `False`/`0`/`None`. Caller continues. Backfill ([scripts/backfill_candidate_edges.py](../../backend/scripts/backfill_candidate_edges.py)) re-runs to fill the gap.
- Target not found in Neo4j → `merge_candidate_detected_as` returns `False` (the Target may not yet exist if the candidate was generated against a Target that was later renamed; PostGIS row is still valid).
- Multiple pending edges for one `(target, detection)` → impossible by construction (MERGE on the edge type collapses them; PostGIS has `UNIQUE(detection_id, target_id)` on `detection_target_candidates`).

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)
- [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md)
- [backend/candidate-linking.md](candidate-linking.md) — pure scorer (no DB).
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md) — approval flow.
- [backend/graph-schema.md](graph-schema.md) — uniqueness constraints these helpers depend on.
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md) — backfill scripts including `backfill_candidate_edges`.
