# `backend/platform_schema.py` — Idempotent Migrations

**Path:** [backend/platform_schema.py](../../backend/platform_schema.py)
**Lines:** ~559
**Depends on:** [backend/database.py](../../backend/database.py), PostgreSQL advisory locks

## Purpose

`CREATE TABLE IF NOT EXISTS` for every operational table outside the inference output (`detections`/`fmv_detections` live in the initial PostGIS dump at [backend/init_postgis.sql](../../backend/init_postgis.sql)). Called from FastAPI startup and from every router that touches one of these tables.

## Why this design

- **Advisory lock around table creation** so concurrent startups (web + worker + scripts) don't race. `acquire_schema_xact_lock` takes a transaction-scoped lock; the second caller blocks until the first commits.
- **Per-feature `ensure_*` functions** rather than one giant DDL block. Lets each subsystem (feeds, collection tasks, reports, observations) be added without touching others.
- **Auto-seeds ontology when empty.** [`auto_seed_ontology_if_empty`](../../backend/platform_schema.py#L537) detects an empty `ontology_branches` table and runs the seed JSON. Idempotent on subsequent boots.
- **No Alembic, no migrations directory.** The repository follows a "schema is code, evolution is care" model — every new table is a `CREATE TABLE IF NOT EXISTS` here, and column additions are explicit `ALTER TABLE IF NOT EXISTS` checks. Heavyweight migrations were tried and abandoned because every air-gap re-deploy was a chore.

## Key symbols

- [`acquire_schema_xact_lock`](../../backend/platform_schema.py#L25).
- [`ensure_feed_tables`](../../backend/platform_schema.py#L31) — `feed_sources`, `feed_events`.
- [`ensure_collection_tables`](../../backend/platform_schema.py#L68) — `collection_tasks`, `reports`, `timeline_events`, `observations`.
- [`ensure_platform_tables`](../../backend/platform_schema.py#L92) — the umbrella call; safe to call repeatedly.
- [`auto_seed_ontology_if_empty`](../../backend/platform_schema.py#L537).

## Failure modes

- DB not reachable at startup → backend retries (handled at the FastAPI lifespan level).
- Schema lock contention → second caller blocks ≤ a few seconds; never fails.

## Cross-references

- [backend/database-connections.md](database-connections.md)
- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md)
