# Reference-Embedding DB Schema and Helpers

**Path:** [backend/platform_schema.py](../../backend/platform_schema.py), [backend/reference_platform_db.py](../../backend/reference_platform_db.py)
**Lines:** ~1012 + ~440
**Depends on:** PostGIS, pgvector >= 0.8, `database.postgis_db`, existing `detections` + `ontology_objects` + `object_details` tables.

## Purpose
Defines the Reference Embedding Vector Database schema:
- `reference_platforms` — one row per identifiable platform (F-35, Arleigh Burke DDG-51, T-90, …). Stores name, family, FK to `ontology_objects`, free-form attributes JSONB, and per-view-domain centroid vectors.
- `reference_chips` — many per platform. Each chip carries provenance (`source_dataset`, `source_url`, `license_spdx`, `attribution`), optional GSD + sensor for overhead, and the actual embedding vector in the column matching its `view_domain`.
- `platform_identification_candidates` — per-detection top-k queue mirroring the existing `detection_target_candidates` approve/reject pattern.
- Adds `platform_name`, `platform_family`, `platform_confidence`, `platform_source` columns to `object_details` so approved/auto-applied identifications land on the analyst-asserted metadata row.

## Why this design
- **pgvector + HNSW** instead of FAISS/hnswlib because the reference DB sits next to detections in the same Postgres; co-locating it removes a network hop, removes a Python dep from `inference-sam3`, and lets the existing `psycopg2` pool serve queries. See [why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md).
- **Two embedding columns per row**, not one — DINOv3-SAT (1024 d) for overhead chips, plus a 512-d `ground` slot for future/offline ground or side-photo embeddings. Each row uses exactly one, gated by `view_domain`; partial HNSW indexes keep each index dense. No active RemoteCLIP runtime verifier feeds this path.
- **Per-chip + centroid** because a centroid alone hides which reference example drove a match. Auto-identify uses centroid HNSW for top-K filter, then re-ranks against per-chip vectors of the K winners.
- **`detection_id INTEGER`** in the candidates table because `detections.id` is `SERIAL`. UUIDs everywhere else because the reference DB is identity-stable across rebuilds; SERIAL would change on a `pg_dump` round-trip.

## Key symbols
- [`ensure_reference_platform_tables()`](../../backend/platform_schema.py#L659-L764) — idempotent migration; called from `ensure_platform_tables()` and (transitively) from the FastAPI lifespan + any router that uses `_ensure_*` guards.
- [`find_similar_platforms()`](../../backend/reference_platform_db.py#L188-L293) — two-stage read-path helper: centroid HNSW top-K (`candidate_pool`) → re-rank by best per-chip cosine. Returns `[{platform_id, platform_name, platform_family, score, matched_chip_ids}, ...]` ordered by descending score. Caller compares `score` against `REFERENCE_ID_AUTO_THRESHOLD` to decide auto-apply vs queue-for-review. The query embedding is rendered as a pgvector **text literal** and every distance term casts `%s::vector` — it must NOT rely on the pool's lazy pgvector adapter, which can be unregistered on a given connection and would bind the list as `numeric[]` (→ `operator does not exist: vector <=> numeric[]`, which silently failed auto-identify for every detection until fixed). See [database-connections.md](database-connections.md) Failure modes.
- [`_upsert_platform_identification()`](../../backend/reference_platform_db.py#L301-L350) — shared writer for the four `object_details.platform_*` columns. Called by both the worker auto-path (`platform_source='auto'`, `updated_by='reference-db-auto-identify'`) and the Plan D analyst-approve router (`platform_source='analyst'`, `updated_by=<session-username>`). `ON CONFLICT DO UPDATE SET` only touches the four platform_* columns + housekeeping, so analyst-asserted columns (threat_level, affiliation, designation, notes) survive untouched.
- [`attach_identification_candidates()`](../../backend/reference_platform_db.py#L353-L435) — Plan C write-path wrapper called by `store_detections` immediately after the detection INSERT. Calls `find_similar_platforms`, replaces any prior `platform_identification_candidates` rows for the detection, inserts the top-k as `pending`, and if top-1 ≥ `auto_threshold` (default 0.85) marks that row `auto_applied` and UPSERTs `platform_name` / `platform_family` / `platform_confidence` / `platform_source='auto'` into `object_details`. Returns the candidate count; returns 0 (and leaves `object_details` untouched) when no candidates were found. See [why-auto-write-with-threshold.md](../decisions/why-auto-write-with-threshold.md) and [why-auto-identify-in-backend-not-inference.md](../decisions/why-auto-identify-in-backend-not-inference.md).

## Inputs / Outputs
- Inputs: none beyond an open Postgres connection from `database.postgis_db`.
- Outputs: three tables, the four new `object_details` columns, four HNSW indexes (two per view domain, on both centroids and chips), six regular B-tree indexes, plus a unique index `uq_reference_chips_platform_path` on `reference_chips(platform_id, chip_path)` that backs the `ON CONFLICT` upsert used by `backend.reference_platform_db.insert_reference_chip`; plus a CHECK constraint `object_details_platform_confidence_check` keeping `platform_confidence` in [0, 1].

## Failure modes
- pgvector missing → `CREATE EXTENSION vector` raises `ERROR: could not open extension control file`. Fix: ensure the Postgres container is the derived image with `postgresql-18-pgvector` installed ([postgis/Dockerfile](../../postgis/Dockerfile)).
- Concurrent migrations → blocked by `pg_advisory_xact_lock` keyed on `sentinel_reference_platform_schema`.

## Cross-references
- Historical Plan A summary: [archive/superpowers-summary.md](../archive/superpowers-summary.md)
- Decision: [why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md)
- Existing approve/reject pattern this mirrors: [docs/operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- Object details helpers that will eventually write `platform_*`: [backend/detection_helpers.py](../../backend/detection_helpers.py) (helpers will be extended when the auto-identify path lands — not yet wired in this task).
