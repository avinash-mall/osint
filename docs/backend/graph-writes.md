# Graph Write Helpers

**Path:** [backend/graph_writes.py](../../backend/graph_writes.py)
**Lines:** ~1191
**Depends on:** [backend/database.py](../../backend/database.py) (`db`)

## Purpose

Owns the shared Neo4j `MERGE`/`DELETE` Cypher used by multiple writer sites — candidate-link creation in [main.py](../../backend/main.py) and [worker_legacy.py](../../backend/worker_legacy.py), plus the new graph routes. One source of truth for each edge predicate so the analyst-facing predicate chip filter stays in sync.

## Why this design

Before Phase 1.B the `CANDIDATE_DETECTED_AS` edge was *synthesised in-memory* on every `/api/graph?include_candidates=true` request ([graph.py#L46-L79](../../backend/routers/graph.py#L46-L79) historical) by joining PostGIS `detection_target_candidates` against existing Neo4j Targets. That made the edge invisible to Cypher traversals — workflow 6 (transitive `MATCH p=(a)-[*1..4]-(b)`) couldn't see pending candidates, and the route paid a PostGIS round-trip per request. Persisting the edge on candidate creation makes it traversable and removes the synthesis path.

Pure-Python scoring stays in [candidate_linking.py](../../backend/candidate_linking.py) — DB writes belong in this module, not there. See [decisions/why-candidate-edges-persisted.md](../decisions/why-candidate-edges-persisted.md) for the broader rationale.

## Key symbols

Candidate edges (Phase 1.B):
- [`merge_candidate_detected_as`](../../backend/graph_writes.py#L24-L95) — MERGE Detection (lazy) + MERGE `(t)-[rel:CANDIDATE_DETECTED_AS]->(d)` with `candidate_id, score, reason, status='pending'`. Returns `False` if the Target isn't in Neo4j. Single-edge path — used by [main.py](../../backend/main.py) candidate creation and `backfill_candidate_edges`.
- [`merge_candidate_detected_as_batch`](../../backend/graph_writes.py#L98-L141) — same MERGE, UNWIND-batched over many edges in one session. The satellite worker's `generate_candidate_links_for_pass` accumulates every pending edge and writes them here in 1000-row chunks **after** the PostGIS commit: a dense pass can emit tens of thousands of candidate edges and one `session.run` per edge was the dominant cost (it stalled large-scene ingest). Returns the edge count.
- [`delete_candidate_detected_as`](../../backend/graph_writes.py#L143-L178) — removes the edge for one `(target, detection)` pair. Used by approve/reject endpoints.
- [`promote_candidate_to_detected_as`](../../backend/graph_writes.py#L1077-L1119) — atomic transition: locates the pending edge by `candidate_id`, MERGE-creates `:DETECTED_AS`, deletes the candidate edge. Used by `/api/graph/candidate-edges/{id}/promote`.

AOI projection (Phase 1.D):
- `merge_site_from_aoi` — MERGE `:Base` / `:LaunchPoint` / `:Facility` from an AOI tagged with `metadata.aoi_kind`. Identity is `id = f"aoi-{postgis_id}"`.
- `delete_site_for_aoi` — removes the mirror when the AOI is deleted or `aoi_kind` is cleared.

Phase 2 projector helpers:
- `project_fmv_clip_and_tracks` — MERGE `:FMVClip` + per-track `:FMVDetection` nodes + `CONTAINS_DETECTION` edges from a single UNWIND batch.
- `project_document_with_mentions` — MERGE `:Document` stub + (when a label index is supplied) `:MENTIONS` edges to operational entities resolved by case-insensitive substring match.
- `load_entity_label_index` — builds the lowercase-name index used by the document projector. Called once per projector invocation; results are not cached (analyst can edit entity names between runs).
- `project_observation_batch` — single UNWIND-MERGE batch creating `:Observation` nodes and OPTIONAL-MATCH-then-FOREACH-MERGE `OBSERVED_AT` edges only when an operational entity resolves.
- `merge_contradicted_by` — analyst-driven dissent edge: `(actor)-[:CONTRADICTED_BY {reason, analyst}]->(:Detection)`. Used by `/api/graph/contradict`; the router supplies `analyst` from the signed session, not from the request body.

Phase 3 projector helpers (ontology):
- `project_ontology_branches_and_objects` — UNWIND-MERGE the full taxonomy (branches + objects + HAS_CHILD + HAS_OBJECT). Three Cypher statements per call, single transaction.
- `project_unknown_label` — MERGE `:UnknownLabel` + optional `SUGGESTED_BRANCH` and `LABEL_OF` orbit. Detections that aren't in Neo4j are silently skipped (orbit shrinks naturally).
- `project_label_of_for_detection_class` — batch MERGE `(d:Detection)-[:LABEL_OF]->(o:OntologyObject)` for one normalized class.

Phase 4 helpers (operational entities + NEAR + SAME_AS):
- `merge_operational_entity` — MERGE Vessel/Aircraft/Vehicle/Facility/Unit (Vessel/Aircraft/Vehicle gain the secondary `:Asset` label).
- `delete_operational_entity` — DETACH DELETE by id.
- `merge_part_of_edge`, `merge_operates_from_edge`, `merge_observed_at_for_asset` — convenience edges.
- `merge_same_as` — analyst-approved `:SAME_AS`; also deletes the matching `:POSSIBLY_SAME_AS` if present.
- `merge_possibly_same_as_batch` — UNWIND-MERGE candidate identity edges from [`worker.tick_entity_resimilarity`](../../backend/worker_legacy.py).
- `project_near_edges_batch` — UNWIND-MERGE `:NEAR {distance_m, computed_at}` edges from [`worker.tick_near_builder`](../../backend/worker_legacy.py).
- `project_repeated_at_batch` — UNWIND-MERGE representative `:REPEATED_AT` edges from [`worker.tick_repeat_detector`](../../backend/worker_legacy.py).

Phase 5 helpers:
- `delete_possibly_same_as` — remove a pending POSSIBLY_SAME_AS edge between two entities (direction-agnostic); used by the SAME_AS review-screen reject action.
- `cosine_similarity` — pure-Python cosine over two equal-length float vectors. Returns `None` for missing / zero-vec / length-mismatch. Used by the DINOv3 embedding branch of `worker.tick_entity_resimilarity`.

Phase 6 helpers (city2graph-inherited analytics):
- [`project_colocation_edges_batch`](../../backend/graph_writes.py#L1039-L1073) — UNWIND-MERGE `(a:Detection)-[:COLOCATED_WITH {distance_m, method, computed_at}]->(b:Detection)` between detection pairs a proximity graph linked. `a_id < b_id` by construction in [graph_proximity.py](graph-proximity.md), so the stored direction is stable and re-running is idempotent. Both endpoints must already be projected as `:Detection` nodes. Written by [`worker.tick_colocation_builder`](worker-legacy-monolith.md). See [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md).
- [`project_gnn_suggested_links_batch`](../../backend/graph_writes.py#L1076-L1107) — UNWIND-MERGE `(a)-[:GNN_SUGGESTED_LINK {score, model='graphsage', computed_at}]->(b)` advisory edges (keyed by Neo4j `elementId`). Non-authoritative GNN predictions for analyst review — an overlay, never a promoted relationship. Written by [`worker.tick_gnn_link_prediction`](worker-legacy-monolith.md). See [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md).

**New edge predicates:** `COLOCATED_WITH` (Detection ↔ Detection, `distance_m, method`) and `GNN_SUGGESTED_LINK` (operational entity ↔ entity, `score, model`). Both are derived/advisory — never analyst-asserted.

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
- [backend/graph-proximity.md](graph-proximity.md), [backend/graph-pyg.md](graph-pyg.md) — Phase 6 edge producers.
- [decisions/why-proximity-colocation-graph.md](../decisions/why-proximity-colocation-graph.md), [decisions/why-gnn-link-prediction.md](../decisions/why-gnn-link-prediction.md).
