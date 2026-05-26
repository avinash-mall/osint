# `backend/platform_schema.py` — Reference-Embedding DB Schema

**Path:** [backend/platform_schema.py](../../backend/platform_schema.py)
**Lines:** ~80 (the `ensure_reference_platform_tables` function plus the three CREATE TABLE blocks at the tail of the file)
**Depends on:** PostGIS, pgvector ≥ 0.8, `database.postgis_db`, existing `detections` + `ontology_objects` + `object_details` tables.

## Purpose
Defines the Reference Embedding Vector Database schema:
- `reference_platforms` — one row per identifiable platform (F-35, Arleigh Burke DDG-51, T-90, …). Stores name, family, FK to `ontology_objects`, free-form attributes JSONB, and per-view-domain centroid vectors.
- `reference_chips` — many per platform. Each chip carries provenance (`source_dataset`, `source_url`, `license_spdx`, `attribution`), optional GSD + sensor for overhead, and the actual embedding vector in the column matching its `view_domain`.
- `platform_identification_candidates` — per-detection top-k queue mirroring the existing `detection_target_candidates` approve/reject pattern.
- Adds `platform_name`, `platform_family`, `platform_confidence`, `platform_source` columns to `object_details` so approved/auto-applied identifications land on the analyst-asserted metadata row.

## Why this design
- **pgvector + HNSW** instead of FAISS/hnswlib because the reference DB sits next to detections in the same Postgres; co-locating it removes a network hop, removes a Python dep from `inference-sam3`, and lets the existing `psycopg2` pool serve queries. See [why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md).
- **Two embedding columns per row**, not one — DINOv3-SAT (1024 d) for overhead chips, RemoteCLIP (512 d) for ground/side photos. Each row uses exactly one, gated by `view_domain`; partial HNSW indexes keep each index dense.
- **Per-chip + centroid** because a centroid alone hides which reference example drove a match. Auto-identify uses centroid HNSW for top-K filter, then re-ranks against per-chip vectors of the K winners.
- **`detection_id INTEGER`** in the candidates table because `detections.id` is `SERIAL`. UUIDs everywhere else because the reference DB is identity-stable across rebuilds; SERIAL would change on a `pg_dump` round-trip.

## Key symbols
- [`ensure_reference_platform_tables()`](../../backend/platform_schema.py#L659-L763) — idempotent migration; called from `ensure_platform_tables()` and (transitively) from the FastAPI lifespan + any router that uses `_ensure_*` guards.

## Inputs / Outputs
- Inputs: none beyond an open Postgres connection from `database.postgis_db`.
- Outputs: three tables, the four new `object_details` columns, four HNSW indexes (two per view domain, on both centroids and chips), six regular B-tree indexes.

## Failure modes
- pgvector missing → `CREATE EXTENSION vector` raises `ERROR: could not open extension control file`. Fix: ensure the Postgres container is the derived image with `postgresql-18-pgvector` installed ([postgis/Dockerfile](../../postgis/Dockerfile)).
- Concurrent migrations → blocked by `pg_advisory_xact_lock` keyed on `sentinel_reference_platform_schema`.

## Cross-references
- Plan A spec (in-repo): [docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md](../superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md)
- Decision: [why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md)
- Existing approve/reject pattern this mirrors: [docs/operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- Object details helpers that will eventually write `platform_*`: [backend/detection_helpers.py](../../backend/detection_helpers.py) (helpers will be extended when the auto-identify path lands — not yet wired in this task).
