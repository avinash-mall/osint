# Graph Schema Bootstrap

**Path:** [backend/graph_schema.py](../../backend/graph_schema.py)
**Lines:** ~117
**Depends on:** [backend/database.py](../../backend/database.py) (`db`), `neo4j` driver

## Purpose

Idempotent bootstrap of Neo4j uniqueness constraints + indexes used by the Link Graph. Called once from the FastAPI lifespan at [main.py#L57-L66](../../backend/main.py#L57-L66).

## Why this design

The redesigned Link Graph ([architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md)) needs `MERGE` on stable keys (`postgis_id` for projected nodes, `id` for analyst-asserted entities) to prevent duplicates when projectors re-run. Cypher `MERGE` without a uniqueness constraint silently creates duplicates under concurrency. We enforce them at schema bootstrap.

Pattern mirrors [`platform_schema.ensure_platform_tables`](platform-schema-migrations.md): one process-level lock, `IF NOT EXISTS` clauses, best-effort with logged failures so the API still starts when Neo4j is briefly unreachable. The cache flag (`_graph_schema_ready`) only flips on a fully successful pass — partial failures retry on next call.

## Key symbols

- [`ensure_graph_schema()`](../../backend/graph_schema.py#L68-L107) — one-shot bootstrap; safe to call from lifespan + scripts.
- [`_NODE_CONSTRAINTS`](../../backend/graph_schema.py#L24-L43) — `(label, prop)` registry. Single property as `str`, composite as `tuple[str, ...]` (used by `FMVDetection (clip_id, track_uid)`).
- [`_NODE_INDEXES`](../../backend/graph_schema.py#L57-L60) — composite indexes; currently `Detection (class, created_at)` for the timeline + class-lens query.
- [`reset_cache_for_tests()`](../../backend/graph_schema.py#L110-L114) — flips the ready flag back so tests against a fresh Neo4j re-bootstrap.

## Inputs / Outputs

Inputs: none. Outputs: side effects on the Neo4j schema. Constraints created (one per node label):

| Label | Unique key |
|---|---|
| Target, Asset, Base, LaunchPoint, Facility, Unit, OntologyBranch, OntologyObject | `id` |
| Detection, SatellitePass, FMVClip, Document, Report, FeedEvent, Observation | `postgis_id` |
| FMVDetection | `(clip_id, track_uid)` composite |
| OntologyCandidate | `key` |
| UnknownLabel | `label` |

Indexes: `Detection (class, created_at)`, `NEAR(distance_m)` (relationship-property; harmless until Phase 4 populates `NEAR` edges).

## Failure modes

- Neo4j unreachable on startup → all statements log a warning, function returns without setting the ready flag, next caller retries. The API still serves read-only routes that don't depend on writes.
- A single constraint statement fails (e.g. a pre-existing duplicate violates uniqueness) → that statement is logged and skipped; the rest are still attempted. Operator must clean the duplicate manually then re-call.
- Tests can re-bootstrap via [`reset_cache_for_tests`](../../backend/graph_schema.py#L110-L114).

## Cross-references

- [architecture/link-graph-redesign.md](../architecture/link-graph-redesign.md) — the redesign that introduced this schema.
- [backend/platform-schema-migrations.md](platform-schema-migrations.md) — the PostGIS counterpart pattern.
- [backend/database-connections.md](database-connections.md) — Neo4j driver wiring.
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
