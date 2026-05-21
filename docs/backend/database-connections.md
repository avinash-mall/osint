# `backend/database.py` — Neo4j + PostGIS

**Path:** [backend/database.py](../../backend/database.py)
**Lines:** ~170
**Depends on:** `neo4j`, `psycopg2`, env vars `NEO4J_URI`, `POSTGIS_URI`, `POSTGIS_POOL_MIN`, `POSTGIS_POOL_MAX`

## Purpose

Two connection objects shared by every backend module: `db` (Neo4j) and `postgis_db` (PostGIS pool). Imported as `from database import db, postgis_db`.

## Why this design

- **Both clients exposed as module-level globals**, not factories. There's exactly one Neo4j driver and one PostGIS pool per process. Modules import once and reuse.
- **PostGIS via psycopg v3 connection pool.** Pool min/max from env so single-tenant dev stays at `1/10` and multi-tenant deployments can crank `POSTGIS_POOL_MAX` to 30+.
- **Docker DNS fallback.** If `POSTGIS_URI` host is `postgis` and DNS fails (common during compose startup race), retry with `127.0.0.1` once — see the connection logic.
- **`DatabaseManager`** wraps both for context-managed startup/shutdown.

## Key symbols

- [`Neo4jConnection`](../../backend/database.py#L42) — wraps `neo4j.GraphDatabase.driver` with retry.
- [`PostGISConnection`](../../backend/database.py#L52) — psycopg2 `ThreadedConnectionPool` with `RealDictCursor` row factory; `get_cursor(commit=...)` context manager.
- [`PostGISConnection.reset_after_fork`](../../backend/database.py#L105) — nulls `_pool` so a forked process rebuilds its own pool; called from the worker's `worker_process_init` handler.
- [`DatabaseManager`](../../backend/database.py#L133) — composite startup/shutdown helper.
- [`env_int`](../../backend/database.py#L28) / [`env_float`](../../backend/database.py#L35) — used across modules for pool-size and threshold env reads.

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

The worker (a separate process) builds its **own** `db` and `postgis_db` from the same module. No DB connection is ever sent across process boundaries.

## Fork safety

libpq connections are **not** fork-safe — a child that reuses a socket opened by
its parent corrupts the wire protocol. The Celery worker uses the prefork pool:
it imports `worker.celery_app` in the MainProcess (which runs DB queries at
import time, e.g. `DETECTION_POLICY = active_detection_policy()`), then `fork()`s
every child from it. Each child must therefore call `reset_after_fork()` so it
discards the inherited pool and lazily builds its own. This is wired through the
`worker_process_init` signal in [worker_legacy.py](worker-legacy-monolith.md).
See [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md).

## Failure modes

- PostGIS unreachable → pool throws on first `with postgis_db.cursor()`. Backend catches at the request level and returns 503 for affected endpoints.
- Neo4j down → same pattern; `db.session()` raises.
- Schema not initialized → [`platform_schema.ensure_platform_tables()`](platform-schema-migrations.md) runs in the lifespan startup.
- Pool inherited across `fork()` without `reset_after_fork()` → `DatabaseError: error with status PGRES_TUPLES_OK and no message from the libpq` on the first task per child.

## Cross-references

- [backend/platform-schema-migrations.md](platform-schema-migrations.md)
- [backend/worker-legacy-monolith.md](worker-legacy-monolith.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md)
