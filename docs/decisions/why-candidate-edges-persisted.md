# Why Candidate Edges Are Persisted in Neo4j

## Decision

When a `detection_target_candidates` row is inserted in PostGIS, a matching
`(:Target)-[:CANDIDATE_DETECTED_AS {candidate_id, score, reason, status='pending'}]->(:Detection)`
edge is also MERGEd in Neo4j. Approve/reject deletes the candidate edge;
approve also creates `:DETECTED_AS`.

Before Phase 1.B the same edge was *synthesised in memory* by the graph
router every time `/api/graph?include_candidates=true` was called.

## Why

Three things wrong with the synthesis approach:

1. **Cypher traversals couldn't see pending candidates.** Workflow 6 of the
   redesign — transitive `MATCH p=(a)-[*1..4]-(b)` queries used for supply
   chains, command structure, and link discovery — would skip the candidate
   edge entirely. The graph appeared sparser than the data warranted.
2. **Every request paid a PostGIS round-trip** to load up to 300 candidate
   rows and a parallel Cypher query to MATCH the corresponding nodes. With
   persistence the existing `MATCH (n) OPTIONAL MATCH (n)-[r]->(m)` loop in
   the graph router returns candidates for free.
3. **Two code paths for the same conceptual edge** invited drift. The
   synthesis path constructed a fake `link` dict with `score`, `status`, and
   `candidate_id` properties; nothing else in the codebase wrote those onto
   Neo4j-stored edges. Future analysts editing edge properties would have
   to remember to fix both places.

## Why not just keep synthesis

- Synthesis was cheap in dev (a few hundred candidates) but scales with
  PostGIS row count. A theatre with 50k pending candidates would have made
  the route unusable.
- It hid candidate semantics from any reader of the graph that isn't this
  one HTTP route — workers, scripts, the path-query endpoint added in
  Phase 1.C.

## Trade-offs accepted

- **Write amplification:** every candidate INSERT now also does a Neo4j MERGE
  (and a `Detection` MERGE if the detection wasn't already in the graph).
  This is the cheapest possible Cypher (single MERGE on indexed property);
  measured cost is well under the PostGIS INSERT it shadows.
- **Two-DB consistency window:** if the Neo4j MERGE fails after the PostGIS
  INSERT commits, the candidate exists in PostGIS without a graph edge. We
  accept this — PostGIS is the source of truth, and [`scripts/backfill_candidate_edges.py`](../../backend/scripts/backfill_candidate_edges.py)
  reconciles it. Same one-way-PostGIS-to-Neo4j philosophy that
  [why-postgis-and-neo4j-coexist.md](why-postgis-and-neo4j-coexist.md) starts
  with; the projector pattern is formalised more broadly in Phase 2.

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — Phase 1.B section.
- [backend/graph-writes.md](../backend/graph-writes.md) — helper module.
- [backend/candidate-linking.md](../backend/candidate-linking.md) — pure scorer that feeds these edges.
- [operations/candidate-link-approval.md](../operations/candidate-link-approval.md) — analyst flow.
- [decisions/why-postgis-and-neo4j-coexist.md](why-postgis-and-neo4j-coexist.md)
