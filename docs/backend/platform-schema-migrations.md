# `backend/platform_schema.py` — Idempotent Migrations

**Path:** [backend/platform_schema.py](../../backend/platform_schema.py)
**Lines:** ~1010
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
- [`ensure_platform_tables`](../../backend/platform_schema.py#L92) — umbrella call; safe to repeat. Owns these tables (in addition to the per-feature blocks above):
  - `detection_target_candidates` — Phase 1.B candidate-link rows.
  - `aois`, `documents`, `transcripts`, `prompt_profiles` and the ontology cluster.
  - `operational_entities` + CHECK on `kind` — Phase 4 operational entity rows (Vessel/Aircraft/Vehicle/Facility/Unit/Asset). Phase 5.J adds `re_id_embedding JSONB`, `re_id_dim INT`, `re_id_updated_at TIMESTAMPTZ` via `ALTER … IF NOT EXISTS` for the DINOv3 centroid.
  - `entity_candidates` — Phase 4.F LLM/heuristic-proposed entities awaiting analyst review (mirrors `detection_target_candidates` shape).
  - `near_builder_state` — Phase 4.C cursor table for `worker.tick_near_builder` (one row per `site_id` with `last_detection_id` + `last_run_at`).
  - `repeat_detector_thresholds` — Phase 5.B per-kind admin-editable config (window_days, min_count, near_radius_m, `current` flag). Unique partial index on `(kind) WHERE current = TRUE`.
  - `operational_entity_tracks` — Phase 5.J association table linking entities to detection_tracks for embedding aggregation.
- [`auto_seed_ontology_if_empty`](../../backend/platform_schema.py#L537).
- [`ensure_tile_sources`](../../backend/platform_schema.py#L677) — Martin MVT function source `detections_mvt(z,x,y,query_params)` (one `detections` polygon layer; `geom_mode=obb|hbb|mask` is the only query param; empty tiles COALESCE to 0-byte MVT so Martin serves a cacheable 204, not 404), the `detection_obb_geom` OBB-from-`metadata.geo_polygon` helper, and the `tile_version` singleton + `bump_tile_version`/`get_tile_version`. The version is bumped on every detection mutation **and once at startup**, so tiles baked by an older function shape are never served to a new frontend bundle (VectorGrid paints unstyled tile layers with default Leaflet path options). See [decisions/why-detection-mvt-tiles.md](../decisions/why-detection-mvt-tiles.md) and [decisions/removed-legacy-detection-geojson-path.md](../decisions/removed-legacy-detection-geojson-path.md).

## Failure modes

- DB not reachable at startup → backend retries (FastAPI lifespan level).
- Schema lock contention → second caller blocks ≤ a few seconds; never fails.

## Cross-references

- [backend/database-connections.md](database-connections.md)
- [backend/main-app-entrypoint.md](main-app-entrypoint.md)
- [scripts/backend-scripts-train-and-seed.md](../scripts/backend-scripts-train-and-seed.md)
