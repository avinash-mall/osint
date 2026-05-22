# `backend/platform_schema.py` — Idempotent Migrations

**Path:** [backend/platform_schema.py](../../backend/platform_schema.py)
**Lines:** ~559
**Depends on:** [backend/database.py](../../backend/database.py), PostgreSQL advisory locks

## Purpose

`CREATE TABLE IF NOT EXISTS` for every operational table outside inference output (`detections`/`fmv_detections` live in the initial PostGIS dump [backend/init_postgis.sql](../../backend/init_postgis.sql)). Called from FastAPI startup and from every router touching these tables.

## Why this design

- **Advisory lock around table creation** — concurrent startups (web + worker + scripts) don't race. `acquire_schema_xact_lock` takes a transaction-scoped lock; second caller blocks until first commits.
- **Per-feature `ensure_*` functions**, not one giant DDL block — each subsystem (feeds, collection tasks, reports, observations) added without touching others.
- **Auto-seeds ontology when empty** — [`auto_seed_ontology_if_empty`](../../backend/platform_schema.py#L537) detects empty `ontology_branches`, runs seed JSON. Idempotent on later boots.
- **No Alembic, no migrations directory** — "schema is code, evolution is care": every new table = `CREATE TABLE IF NOT EXISTS` here, column additions = explicit `ALTER TABLE IF NOT EXISTS` checks. Heavyweight migrations were tried and abandoned — every air-gap re-deploy became a chore.

## Key symbols

- [`acquire_schema_xact_lock`](../../backend/platform_schema.py#L25).
- [`ensure_feed_tables`](../../backend/platform_schema.py#L31) — `feed_sources`, `feed_events`.
- [`ensure_collection_tables`](../../backend/platform_schema.py#L68) — `collection_tasks`, `reports`, `timeline_events`, `observations`.
- [`ensure_platform_tables`](../../backend/platform_schema.py#L92) — umbrella call; safe to repeat.
- [`auto_seed_ontology_if_empty`](../../backend/platform_schema.py#L537).

## Failure modes

- DB not reachable at startup → backend retries (FastAPI lifespan level).
- Schema lock contention → second caller blocks ≤ a few seconds; never fails.

## Cross-references

- [backend/database-connections.md](database-connections.md)
- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md)
