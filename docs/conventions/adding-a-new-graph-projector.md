# Adding a New PostGIS → Neo4j Graph Projector

A **projector** mirrors a PostGIS row into a Neo4j node + edges so the Link
Graph can traverse it in Cypher without a cross-DB join at query time. The
canonical examples are in [backend/worker_legacy.py](../../backend/worker_legacy.py)
(`worker.project_fmv_to_graph`, `worker.project_documents_to_graph`,
`worker.project_observations_to_graph`) and the helpers they call in
[backend/graph_writes.py](../../backend/graph_writes.py).

Follow this recipe for every new projector.

## 1. Pick the Neo4j node label + identity

- Add the label to [`_NODE_CONSTRAINTS`](../../backend/graph_schema.py#L24)
  if it isn't there. Identity is `postgis_id` for mirrors of PostGIS rows;
  `id` for analyst-asserted entities; a composite tuple for per-component
  identities (`(clip_id, track_uid)` for FMVDetection).
- Re-run the unit test for `graph_schema` to confirm the constraint is
  in the list.

## 2. Add a helper to `backend/graph_writes.py`

- Use `MERGE` keyed on the identity property; never `CREATE`.
- Use a batch shape (`UNWIND $rows AS row MERGE …`) when multiple rows
  share the same projector call — saves round-trips.
- Carry only **identity + headline properties** (`title`, `class`,
  `confidence`, `created_at`). Do not project full bodies, large JSONB,
  or spatial geometry. The PostGIS row remains the source of truth.
- Wrap the Cypher in `try / except` that **logs and swallows** Neo4j
  failures, returning a zero-count payload. PostGIS is the source of
  truth; a projection failure must never propagate.
- Add a unit test in [backend/tests/test_graph_writes.py](../../backend/tests/test_graph_writes.py)
  using the existing db stub pattern. Assert the Cypher shape and
  parameters, not the Neo4j semantics.

## 3. Add the Celery task wrapper in `worker_legacy.py`

- Decorator: `@celery_app.task(name="worker.project_<thing>_to_graph", queue="default")`.
  The `worker.xxx` name is the routing identity per
  [decisions/why-worker-legacy-monolith-kept.md](../decisions/why-worker-legacy-monolith-kept.md)
  — do not rename it across refactors.
- The task reads the PostGIS row(s), shapes them for the helper, calls the
  helper, returns a small status dict. Keep it under 60 lines.
- Re-export from [backend/worker/__init__.py](../../backend/worker/__init__.py)
  so callers can `from worker import project_<thing>_to_graph`.

## 4. Wire the trigger

Pick the writer site that produces the new PostGIS row and queue the task
via `.delay()` after the COMMIT:

```python
try:
    project_thing_to_graph.delay(new_id)
except Exception:
    logger.exception("failed to queue worker.project_thing_to_graph for thing %s", new_id)
```

Lazy-import the task inside the writer if there's any risk of a worker ←→
writer module cycle (see [backend/events.py](../../backend/events.py) for an
example).

## 5. Add a backfill path

The on-write trigger covers new rows; pre-existing rows need a one-pass
backfill. Add a function to
[backend/scripts/backfill_evidence_from_postgis.py](../../backend/scripts/backfill_evidence_from_postgis.py)
(or a sibling script) that walks the table and calls the helper directly
(don't `.delay()` from a script — Celery may not be running).

## 6. Surface the new bucket in `/api/graph/evidence/{node_id}`

[`backend/routers/graph.py`](../../backend/routers/graph.py) builds the
`evidence_records` payload by grouping `postgis_id`s by label and querying
PostGIS once per bucket. Add a new bucket and PostGIS query for the new
label. The frontend `EvidenceColumnDAG.tsx` already iterates the bucket
map; if the bucket should render as its own column, add an entry to
`COLUMNS` in
[frontend/src/components/graph/EvidenceColumnDAG.tsx](../../frontend/src/components/graph/EvidenceColumnDAG.tsx).

## 7. Document

- Update [backend/graph-writes.md](../backend/graph-writes.md) with the
  new helper signature and shape.
- Update [backend/main-app-entrypoint.md](../backend/main-app-entrypoint.md)
  if the writer trigger is in `main.py`; otherwise the relevant module doc.
- If the projector embodies a new architectural decision (e.g. introducing
  a new operational entity type), add a `docs/decisions/why-X.md`. The
  existing [`why-postgis-to-neo4j-projectors.md`](../decisions/why-postgis-to-neo4j-projectors.md)
  covers the pattern itself.

## Cross-references

- [decisions/why-postgis-to-neo4j-projectors.md](../decisions/why-postgis-to-neo4j-projectors.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [backend/graph-schema.md](../backend/graph-schema.md)
- [backend/graph-writes.md](../backend/graph-writes.md)
- [backend/worker-package-facade.md](../backend/worker-package-facade.md) — naming + facade rules.
