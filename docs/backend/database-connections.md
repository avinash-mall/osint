# `backend/database.py` — Neo4j + PostGIS

**Path:** [backend/database.py](../../backend/database.py)
**Lines:** ~215
**Depends on:** `neo4j`, `psycopg2`, `pgvector`, env `NEO4J_URI`, `POSTGIS_URI`, `POSTGIS_POOL_MIN`, `POSTGIS_POOL_MAX`

## Purpose

Two connection objects shared by every backend module: `db` (Neo4j), `postgis_db` (PostGIS pool). Imported as `from database import db, postgis_db`.

## Why this design

- **Both clients = module-level globals**, not factories. Exactly one Neo4j driver + one PostGIS pool per process; modules import once, reuse.
- **PostGIS via psycopg v3 connection pool** — min/max from env; single-tenant dev stays `1/10`, multi-tenant can crank `POSTGIS_POOL_MAX` to 30+. The server-side ceiling must cover the *sum* of every process's pool — each Celery prefork child rebuilds its own ([reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md)), plus Martin (~20) and the backend — so `postgis` runs with `max_connections=${POSTGIS_MAX_CONNECTIONS:-300}` to survive concurrent imagery+FMV ingest. See [decisions/why-postgis-max-connections-300.md](../decisions/why-postgis-max-connections-300.md).
- **Docker DNS fallback** — `POSTGIS_URI` host `postgis` + DNS failure (common during compose startup race) → retry `127.0.0.1` once.
- **pgvector adapter registered on every pooled connection** via a `connection_factory` subclass. The Reference Embedding DB stores 1024-D vectors and every writer (bake pipeline, refresh tasks, identification scorer) inserts via Python lists / numpy arrays / `pgvector.Vector` objects. Registering once at the pool level means no callsite has to remember to call `register_vector(conn)`. Registration is lazy on first `cursor()` and silently no-ops if the `vector` extension is not yet installed (the ensure-schema step runs in lifespan startup).
- **`DatabaseManager`** wraps both for context-managed startup/shutdown.

## Key symbols

- [`_VectorAwareConnection`](../../backend/database.py#L14-L43) — `psycopg2.extensions.connection` subclass; overrides `cursor()` to lazily call `pgvector.psycopg2.register_vector(self)` once, then short-circuits. Passed as `connection_factory` to the pool.
- [`Neo4jConnection`](../../backend/database.py#L75) — wraps `neo4j.GraphDatabase.driver` with retry.
- [`PostGISConnection`](../../backend/database.py#L97) — psycopg2 `ThreadedConnectionPool` with `connection_factory=_VectorAwareConnection`, `RealDictCursor` row factory; `get_cursor(commit=...)` context manager.
- [`PostGISConnection.reset_after_fork`](../../backend/database.py#L155) — nulls `_pool` so a forked process rebuilds its own; called from worker's `worker_process_init`.
- [`DatabaseManager`](../../backend/database.py#L205) — composite startup/shutdown helper.
- [`env_int`](../../backend/database.py#L61) / [`env_float`](../../backend/database.py#L68) — pool-size + threshold env reads, used across modules.

## Connection lifecycle

```python
from database import db, postgis_db

# Neo4j
with db.session() as session:
    result = session.run("MATCH (n) RETURN count(n) AS n").single()

# PostGIS
with postgis_db.cursor() as cur:
    cur.execute("SELECT count(*) FROM detections WHERE deleted_at IS NULL")
    n = cur.fetchone()[0]
```

Worker (separate process) builds its **own** `db`/`postgis_db` from the same module. No DB connection crosses process boundaries.

## Fork safety

libpq connections are **not** fork-safe — a child reusing a parent-opened socket corrupts the wire protocol. Celery worker uses the prefork pool: imports `worker.celery_app` in MainProcess (which runs DB queries at import time, e.g. `DETECTION_POLICY = active_detection_policy()`), then `fork()`s every child. Each child must call `reset_after_fork()` → discards inherited pool, lazily builds own. Wired via `worker_process_init` signal in [worker_legacy.py](worker-legacy-monolith.md). See [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md).

## Failure modes

- PostGIS unreachable → pool throws on first `with postgis_db.cursor()`; backend catches per-request → 503.
- Neo4j down → same; `db.session()` raises.
- Schema not initialized → [`platform_schema.ensure_platform_tables()`](platform-schema-migrations.md) runs in lifespan startup.
- Pool inherited across `fork()` without `reset_after_fork()` → `DatabaseError: error with status PGRES_TUPLES_OK and no message from the libpq` on first task per child.
- **Pool exhaustion from long connection holds.** `get_cursor()` keeps the connection
  checked out (`idle in transaction`) for the *whole* `with` block. A handler that builds a
  large/slow response (e.g. per-row enrichment of thousands of rows) inside the block holds a
  connection for tens of seconds; ~`POSTGIS_POOL_MAX` such concurrent requests exhaust the pool
  → every other request gets `RuntimeError: PostGIS connection pool exhausted` (500).
  **Rule: fetch rows inside `get_cursor`, exit the block, then do the slow Python work.** See
  [decisions/why-release-db-connection-before-enrichment.md](../decisions/why-release-db-connection-before-enrichment.md).
- **pgvector adapter is best-effort** (lazy, no-ops if the `vector` extension was absent at
  first `cursor()` — see line 16). A connection whose adapter never registered binds Python
  lists as `numeric[]`, so `vector <=> %s` raises `operator does not exist: vector <=> numeric[]`.
  Vector-distance queries must cast explicitly — `<=> %s::vector` with a pgvector text literal —
  rather than rely on the adapter (this bit `find_similar_platforms`, breaking auto-identify).

## Cross-references

- [backend/platform-schema-migrations.md](platform-schema-migrations.md)
- [backend/worker-legacy-monolith.md](worker-legacy-monolith.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md)
