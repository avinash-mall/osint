# Decision: reset the PostGIS pool in every Celery prefork child

## Context

The Celery worker runs the prefork pool (`celery -A worker.celery_app worker`,
`concurrency: 96`). Celery imports `worker.celery_app` once in the MainProcess,
then `fork()`s N child workers from it.

Importing `worker_legacy` runs module-level code, including
`DETECTION_POLICY = active_detection_policy()`
([worker_legacy.py#L221](../../backend/worker_legacy.py#L221)).
`active_detection_policy()` calls `_load_db_overrides()`, which issues
`SELECT config FROM inference_config` — a real PostGIS query. That query builds
`postgis_db`'s `ThreadedConnectionPool` (and, with `POSTGIS_POOL_MIN=1`, opens
one live socket) **inside the MainProcess, before the fork**.

Every forked child therefore inherits a pool object whose connections are
sockets opened by the parent. libpq connections are not fork-safe — sharing one
TCP connection across processes desyncs the wire protocol. The first DB-touching
task in a child (`worker.tick_feed_poll` → `ensure_feed_tables()`) crashed with:

```
psycopg2.DatabaseError: error with status PGRES_TUPLES_OK and no message from the libpq
```

## Decision

Register a `worker_process_init` signal handler
([worker_legacy.py](../../backend/worker_legacy.py), just after `celery_app`)
that calls `postgis_db.reset_after_fork()` in each child. The handler fires once
per child immediately after the fork, before the child accepts any task.

`PostGISConnection.reset_after_fork()`
([database.py#L105](../../backend/database.py#L105)) just nulls `_pool` under
the pool lock. The next `get_connection()` in that process lazily rebuilds a
fresh pool owning its own connections.

It deliberately does **not** call `closeall()`: a forked fd points at the same
TCP connection as the parent's, so closing inherited connections from a child
could tear down the parent's connection. The parent's one stray import-time
connection is left for the parent to close at shutdown (`postgis_db.close()`).

## Alternatives considered

- **Make `DETECTION_POLICY` lazy** so no DB query runs at import time. Removes
  *this* trigger but not the class of bug — any future pre-fork DB touch
  (another import-time query, an eager `worker_process_init` that runs before
  ours) reintroduces it. The signal handler is robust regardless of trigger, so
  it is the primary fix. Making the policy lazy remains a reasonable cleanup but
  is not required for correctness.
- **`closeall()` in the child** — unsafe, see above.
- **`POSTGIS_POOL_MIN=0`** — would avoid the eager socket but the pool object
  and its bookkeeping are still inherited and still corrupt on first reuse.

## Scope

Neo4j (`neo4j_db`) is not reset here: `GraphDatabase.driver()` is lazy and the
import-time policy path touches PostGIS only, so no Neo4j connection is opened
pre-fork. If a future import-time path opens a Neo4j session, extend the handler.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [backend/worker-legacy-monolith.md](../backend/worker-legacy-monolith.md)
- [backend/detection-policy.md](../backend/detection-policy.md)
- [backend/platform-schema-migrations.md](../backend/platform-schema-migrations.md)
