# `backend/database.py` — Neo4j + PostGIS

**Path:** [backend/database.py](../../backend/database.py)
**Lines:** ~170
**Depends on:** `neo4j`, `psycopg2`, env `NEO4J_URI`, `POSTGIS_URI`, `POSTGIS_POOL_MIN`, `POSTGIS_POOL_MAX`

## Purpose

Two connection objects shared by every backend module: `db` (Neo4j), `postgis_db` (PostGIS pool). Imported as `from database import db, postgis_db`.

## Why this design

- **Both clients = module-level globals**, not factories. Exactly one Neo4j driver + one PostGIS pool per process; modules import once, reuse.
- **PostGIS via psycopg v3 connection pool** — min/max from env; single-tenant dev stays `1/10`, multi-tenant can crank `POSTGIS_POOL_MAX` to 30+.
- **Docker DNS fallback** — `POSTGIS_URI` host `postgis` + DNS failure (common during compose startup race) → retry `127.0.0.1` once.
- **`DatabaseManager`** wraps both for context-managed startup/shutdown.

## Key symbols

- [`Neo4jConnection`](../../backend/database.py#L42) — wraps `neo4j.GraphDatabase.driver` with retry.
- [`PostGISConnection`](../../backend/database.py#L52) — psycopg2 `ThreadedConnectionPool`, `RealDictCursor` row factory; `get_cursor(commit=...)` context manager.
- [`PostGISConnection.reset_after_fork`](../../backend/database.py#L105) — nulls `_pool` so a forked process rebuilds its own; called from worker's `worker_process_init`.
- [`DatabaseManager`](../../backend/database.py#L133) — composite startup/shutdown helper.
- [`env_int`](../../backend/database.py#L28) / [`env_float`](../../backend/database.py#L35) — pool-size + threshold env reads, used across modules.

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

## Cross-references

- [backend/platform-schema-migrations.md](platform-schema-migrations.md)
- [backend/worker-legacy-monolith.md](worker-legacy-monolith.md)
- [decisions/why-postgis-and-neo4j-coexist.md](../decisions/why-postgis-and-neo4j-coexist.md)
- [decisions/reset-db-pool-after-fork.md](../decisions/reset-db-pool-after-fork.md)
