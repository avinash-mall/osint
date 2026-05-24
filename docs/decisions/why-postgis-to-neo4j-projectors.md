# Why PostGIS → Neo4j Projectors

## Decision

Phase 2 of the Link Graph redesign introduces a family of one-way projector
tasks (`worker.project_fmv_to_graph`, `worker.project_documents_to_graph`,
`worker.project_observations_to_graph`, `worker.project_site_from_aoi`) that
mirror identity + a few headline properties from PostGIS rows into Neo4j
nodes + edges. Each projector triggers either on-write via `.delay()` from
the PostGIS writer (FMV consolidation, document extraction completion,
observation insertion, AOI create/patch) or as an idempotent batch backfill
([`scripts/backfill_evidence_from_postgis.py`](../../backend/scripts/backfill_evidence_from_postgis.py)).

## Why

[`why-postgis-and-neo4j-coexist.md`](why-postgis-and-neo4j-coexist.md) says
"the databases are **not** synchronized." That is still true for *content*,
but the Link Graph redesign needs Evidence-mode column DAGs and traversal
queries that join across both stores in real time. Walking PostGIS at query
time would either be slow (one PostGIS round-trip per Cypher hop) or
fragile (synthesising fake edges in-memory, the way pre-Phase-1
`/api/graph?include_candidates=true` did — see
[`why-candidate-edges-persisted.md`](why-candidate-edges-persisted.md)).
Projectors take the latency hit *once at write time* so traversals stay
single-DB.

## What projectors carry vs. don't carry

| Carried in Neo4j | Stays PostGIS-only |
|---|---|
| `postgis_id` (uniqueness anchor) | full row body |
| `title` / `name` / `summary` | original document/transcript text |
| `class`, `confidence` headline | per-frame bboxes, full payload JSONB |
| Edge endpoints + predicate | spatial geometry (polygons, point streams) |

A projector that puts the *body* of a document or the per-frame bboxes of
an FMV clip into Neo4j would double the storage cost without buying any
traversal value — the Evidence-mode UI fetches the row on click anyway.

## Properties of the projector pattern

1. **One-way**: PostGIS is the source of truth. If the Neo4j mirror is
   wrong, fix PostGIS and re-run the projector; never edit the mirror
   directly.
2. **Idempotent**: every projector uses `MERGE` keyed on `postgis_id`
   (or composite `(clip_id, track_uid)` for FMV tracks). Re-running
   inserts zero new nodes.
3. **Best-effort**: a projector failure logs a warning and returns
   zeros. PostGIS write completes. The backfill script reconciles later.
4. **Identity-only**: no body, no large JSONB, no geometry.

## Trade-offs accepted

- **Write amplification**: every PostGIS row that's projectable now does a
  small Neo4j MERGE on insert. Measured cost is well under the PostGIS
  INSERT.
- **Two-DB consistency window**: if Neo4j is unreachable when the projector
  fires, the PostGIS row exists without the graph mirror until the next
  backfill run. Workflow 5 (chain of evidence) degrades gracefully —
  buckets for unprojected types appear empty until reconciled.
- **Mirror staleness**: if a PostGIS row is updated *after* the projector
  fired, the mirror is stale until the row is re-projected. Today no
  Phase-2 path updates the projected fields after the first write; if that
  changes, the writer site must call `.delay()` again.

## Cross-references

- [why-postgis-and-neo4j-coexist.md](why-postgis-and-neo4j-coexist.md) — the
  pre-existing decision this softens.
- [why-candidate-edges-persisted.md](why-candidate-edges-persisted.md) —
  the first projector pattern (Phase 1.B).
- [conventions/adding-a-new-graph-projector.md](../conventions/adding-a-new-graph-projector.md) — recipe.
- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)
  — Phase 2 section.
