# Decision: register the pgvector adapter at the connection-pool layer

## Context

The Reference Embedding DB (Plan A, landed in commits up to `500aca9`) stores DINOv3-SAT overhead embeddings as PostGIS `vector(1024)` columns (plus a reserved 512-d ground-view slot) on `reference_platforms`, `reference_chips`, and `platform_identification_candidates`. Plans B/C/D add multiple writers — the offline bake pipeline, the nightly centroid refresh task, the inference-time identification scorer — all of which INSERT vectors using bare Python lists, numpy arrays, or `pgvector.Vector` objects.

psycopg2 has no built-in adapter for the `vector` SQL type. `pgvector.psycopg2.register_vector(conn)` patches the connection's type-adapter map so Python iterables become `vector` literals on INSERT and `vector` results come back as numpy arrays on SELECT. Without it, every INSERT fails with `psycopg2.ProgrammingError: can't adapt type 'list'`.

Plan A's final review flagged that the adapter was only being called inside one test fixture (`test_reference_platform_schema.py::test_vector_roundtrip_and_knn_query`). Every other writer — bake task, refresh task, identification scorer — would have silently crashed on first insert.

## Decision

Register the adapter once, at the connection-pool layer, via psycopg2's `connection_factory` hook. A new `_VectorAwareConnection` subclass ([database.py#L14-L43](../../backend/database.py#L14-L43)) overrides `cursor()` to lazily call `register_vector(self)` on first use, then short-circuits via a `_vector_registered` flag. The `ThreadedConnectionPool` constructor receives `connection_factory=_VectorAwareConnection` ([database.py#L107-L114](../../backend/database.py#L107-L114)) so every pooled connection is vector-aware before any caller touches it.

Registration is lazy (deferred to first `cursor()`) rather than eager (in `__init__`) because the adapter requires the `vector` extension to exist in the target DB, and `ensure_reference_platform_tables()` only runs during lifespan startup. A connection handed out before the extension exists is still usable for everything except pgvector — we silently swallow the registration error and retry on the next `cursor()` call so the adapter activates once the extension is installed.

## Alternatives considered

- **Call `register_vector(conn)` at each writer callsite.** Brittle — every new writer must remember to do this, and forgetting fails silently in code review (the error only surfaces at runtime on first insert).
- **Register globally via `pgvector.psycopg2.register_vector(psycopg2)` at import time.** Not supported by the pgvector API — registration is per-connection. Even if it were, the extension-not-yet-installed race would still need handling somewhere.
- **Eager registration in `_VectorAwareConnection.__init__`.** Fails on every connection opened before `ensure_reference_platform_tables()` runs (e.g. import-time policy queries in the worker). Lazy `cursor()`-time registration sidesteps this without losing correctness.

## Scope

Only the PostGIS `ThreadedConnectionPool` is wrapped. The lone `psycopg2.connect()` outside the pool — `tests/conftest.py::_postgis_available` — only opens a connection to check that the DB is reachable; it never executes a vector INSERT, so wrapping it would be redundant. Future code that bypasses the pool (currently none) would need to opt in by passing `connection_factory=_VectorAwareConnection` itself.

## Cross-references

- [backend/database-connections.md](../backend/database-connections.md)
- [decisions/reset-db-pool-after-fork.md](reset-db-pool-after-fork.md)
- [archive/superpowers-summary.md](../archive/superpowers-summary.md)
