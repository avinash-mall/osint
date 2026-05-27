# Reference Embedding DB — Plan A: pgvector Foundation & Schema

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the database foundation for the Reference Embedding Vector Database — install `pgvector` in the PostGIS container, create the three new tables (`reference_platforms`, `reference_chips`, `platform_identification_candidates`) with HNSW indexes, and extend `object_details` with platform columns. After this plan, the schema is queryable; no data yet.

**Architecture:** Build a small derived Postgres image (`postgis/postgis:18-3.6` + `postgresql-18-pgvector` apt package) so we keep proven PostGIS bits and add pgvector cleanly. Wire `CREATE EXTENSION vector` into both the init-script (new installs) and the runtime migration helper (existing installs). Add an idempotent `ensure_reference_platform_tables()` to `backend/platform_schema.py`, chained from the existing `ensure_platform_tables()`, and call it from the FastAPI lifespan path that already exists.

**Tech Stack:**
- Postgres 18 + PostGIS 3.6 (existing) + pgvector ≥ 0.8 (new apt package)
- `pgvector` Python package (new, registers the `vector` adapter for psycopg2)
- psycopg2-binary (existing)
- pytest + pytest-mark integration (existing pattern, e.g. `backend/tests/test_object_details.py`)

**Parent spec:** `/home/avinash/.claude/plans/i-want-to-build-breezy-snail.md`

---

## File Structure

**Created:**
- `postgis/Dockerfile` — derived Postgres image baking in pgvector.
- `backend/tests/test_reference_platform_schema.py` — integration test asserting the extension, tables, columns, vector roundtrip, HNSW index work.
- `docs/backend/reference-platform-db.md` — module doc per the six-section template.
- `docs/decisions/why-pgvector-for-reference-db.md` — decision record.

**Modified:**
- `docker-compose.yml` — switch the `postgis` service from a pinned image to a build with a stable local image tag.
- `backend/init_postgis.sql` — add `CREATE EXTENSION IF NOT EXISTS vector;` near the top.
- `backend/platform_schema.py` — add `ensure_reference_platform_tables()`; chain it from `ensure_platform_tables()`.
- `backend/requirements.txt` — add `pgvector` (the Python adapter).
- `docs/INDEX.txt` — add sorted entries for the two new doc files.

**Untouched in Plan A (deferred to later plans):**
- `inference-sam3/*` — Plan C.
- Any router or worker code — Plan C / D.
- Any frontend code — Plan D.
- Seed data, bake scripts, datasets — Plan B.

---

## Task 1 — Build the Postgres image with pgvector

**Files:**
- Create: `postgis/Dockerfile`
- Modify: `docker-compose.yml` (the `postgis:` service block, currently lines ~16–27)

The `postgresql-18-pgvector` package is in the PGDG apt repo that the `postgis/postgis` image already ships with — one apt install is enough.

- [ ] **Step 1: Create the Dockerfile**

Create `postgis/Dockerfile` with this exact content:

```dockerfile
# Derived from the upstream PostGIS image. Adds pgvector so the Reference
# Embedding DB schema can use `vector(1024)` / `vector(512)` columns and
# HNSW indexes. The PGDG apt repo is already configured in the base image.
FROM postgis/postgis:18-3.6

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18-pgvector \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Switch the compose service to a build**

In `docker-compose.yml`, replace the `image: postgis/postgis:18-3.6` line inside the `postgis:` service with a `build` block and a stable local image tag. The full updated service block looks like this (only the `image` line is replaced — keep everything else):

```yaml
  postgis:
    build:
      context: ./postgis
    image: sentinel-postgis:18-3.6-pgvector
    environment:
      - POSTGRES_USER=sentinel
      - POSTGRES_PASSWORD=sentinel
      - POSTGRES_DB=sentinel
    volumes:
      - pg_data:/var/lib/postgresql
      - ./backend/init_postgis.sql:/docker-entrypoint-initdb.d/init_postgis.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U sentinel -d sentinel"]
      interval: 10s
      timeout: 5s
```

- [ ] **Step 3: Build and verify pgvector is present**

Run:

```bash
docker compose build postgis
docker compose up -d postgis
# wait for healthcheck to go green (~10s)
docker compose exec -T postgis bash -lc "psql -U sentinel -d sentinel -c \"SHOW server_version_num;\" -c \"CREATE EXTENSION IF NOT EXISTS vector;\" -c \"SELECT extname, extversion FROM pg_extension WHERE extname IN ('postgis','vector') ORDER BY extname;\""
```

Expected output includes:

```
 extname | extversion
---------+------------
 postgis | 3.6.x
 vector  | 0.8.x
```

If `vector` is missing, the apt install failed inside the build — rebuild with `docker compose build --no-cache postgis` and re-check.

- [ ] **Step 4: Commit**

```bash
git add postgis/Dockerfile docker-compose.yml
git commit -m "infra(postgis): derived image with pgvector for reference DB"
```

---

## Task 2 — Add the pgvector Python adapter

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add the dependency**

Append a single line to `backend/requirements.txt`:

```
pgvector
```

(Pinning is unnecessary — the package is small, ABI-stable against psycopg2, and the airgap bake will resolve to a concrete version.)

- [ ] **Step 2: Rebuild the backend image so the package lands**

```bash
docker compose build backend
```

Expected: build completes; the layer that runs `pip install -r requirements.txt` includes `Successfully installed pgvector-...`.

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "deps(backend): pgvector adapter for reference DB schema"
```

---

## Task 3 — Wire `CREATE EXTENSION vector` into the init script

**Files:**
- Modify: `backend/init_postgis.sql:1-2`

The init script runs only on first-init of a fresh data volume, but it's the canonical declaration of expected extensions. Add the line right after the existing `CREATE EXTENSION postgis;`.

- [ ] **Step 1: Edit the init script**

Replace lines 1–2 of `backend/init_postgis.sql`:

```sql
-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;
```

with:

```sql
-- Enable PostGIS extension
CREATE EXTENSION IF NOT EXISTS postgis;

-- Enable pgvector extension (Reference Embedding DB — see
-- docs/decisions/why-pgvector-for-reference-db.md)
CREATE EXTENSION IF NOT EXISTS vector;
```

- [ ] **Step 2: Commit**

```bash
git add backend/init_postgis.sql
git commit -m "schema(postgis): enable pgvector extension in init script"
```

---

## Task 4 — Write the failing integration test

**Files:**
- Create: `backend/tests/test_reference_platform_schema.py`

We assert the post-migration shape end-to-end: extension installed, tables present, vector columns typed correctly, idempotent re-run, a vector roundtrip insert/select, and an HNSW-indexed ORDER BY query.

- [ ] **Step 1: Write the test file**

Create `backend/tests/test_reference_platform_schema.py` with this exact content:

```python
"""Integration tests for the Reference Embedding DB schema.

Run with:
    POSTGIS_URI=postgresql://sentinel:sentinel@localhost:5432/sentinel \
      python -m pytest backend/tests/test_reference_platform_schema.py -v

Touches PostGIS. Idempotent — cleans up its own rows on teardown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="module")
def ensured_schema():
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()
    yield
    # Module-scope teardown: drop our test rows. The tables themselves stay
    # so other tests can use them.
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM platform_identification_candidates WHERE created_at > NOW() - INTERVAL '1 hour'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-fixture'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-%'")


def _fetch_one(sql, params=()):
    from database import postgis_db
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def test_vector_extension_installed(ensured_schema):
    row = _fetch_one("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    assert row is not None, "pgvector extension is not installed"


def test_reference_platforms_table_shape(ensured_schema):
    row = _fetch_one("""
        SELECT column_name, udt_name
          FROM information_schema.columns
         WHERE table_name = 'reference_platforms'
           AND column_name = 'centroid_overhead'
    """)
    assert row is not None
    assert row[1] == 'vector', f"expected centroid_overhead udt='vector', got {row[1]}"


def test_reference_chips_table_shape(ensured_schema):
    row = _fetch_one("""
        SELECT column_name, udt_name
          FROM information_schema.columns
         WHERE table_name = 'reference_chips'
           AND column_name = 'embedding_overhead'
    """)
    assert row is not None
    assert row[1] == 'vector'


def test_platform_identification_candidates_fk_type(ensured_schema):
    row = _fetch_one("""
        SELECT data_type
          FROM information_schema.columns
         WHERE table_name = 'platform_identification_candidates'
           AND column_name = 'detection_id'
    """)
    assert row is not None
    assert row[0] == 'integer', \
        f"detection_id must be INTEGER to match detections.id SERIAL; got {row[0]}"


def test_object_details_platform_columns_added(ensured_schema):
    from database import postgis_db
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT column_name
              FROM information_schema.columns
             WHERE table_name = 'object_details'
               AND column_name IN
                   ('platform_name','platform_family','platform_confidence','platform_source')
             ORDER BY column_name
        """)
        cols = [r[0] for r in cur.fetchall()]
    assert cols == [
        'platform_confidence', 'platform_family', 'platform_name', 'platform_source'
    ]


def test_hnsw_index_present(ensured_schema):
    row = _fetch_one("""
        SELECT indexname
          FROM pg_indexes
         WHERE tablename = 'reference_chips'
           AND indexname = 'reference_chips_overhead_hnsw'
    """)
    assert row is not None, "HNSW index on embedding_overhead missing"


def test_vector_roundtrip_and_knn_query(ensured_schema):
    from database import postgis_db
    from pgvector.psycopg2 import register_vector

    # Register the adapter on the live connection
    with postgis_db.get_cursor(commit=True) as cur:
        register_vector(cur.connection)

        # Insert a platform with a known centroid
        v = [0.1] * 1024
        cur.execute("""
            INSERT INTO reference_platforms (platform_name, platform_family, centroid_overhead, view_domains)
            VALUES (%s, %s, %s, %s)
            RETURNING id
        """, ('pytest-A', 'Fighter Aircraft', v, ['overhead']))
        platform_id = cur.fetchone()[0]

        # Insert 3 chips for it
        for i in range(3):
            chip_v = [0.1 + 0.001 * i] * 1024
            cur.execute("""
                INSERT INTO reference_chips
                    (platform_id, view_domain, source_dataset, license_spdx, chip_path, embedding_overhead)
                VALUES (%s, 'overhead', 'pytest-fixture', 'CC0-1.0', %s, %s)
            """, (platform_id, f'/tmp/pytest-{i}.jpg', chip_v))

        # Query: nearest centroid to a near-by query vector
        q = [0.105] * 1024
        cur.execute("""
            SELECT platform_name
              FROM reference_platforms
             WHERE centroid_overhead IS NOT NULL
             ORDER BY centroid_overhead <=> %s
             LIMIT 1
        """, (q,))
        assert cur.fetchone()[0] == 'pytest-A'

        # Query: top-2 chips by HNSW-indexed distance on embedding_overhead
        cur.execute("""
            SELECT chip_path
              FROM reference_chips
             WHERE view_domain = 'overhead'
             ORDER BY embedding_overhead <=> %s
             LIMIT 2
        """, (q,))
        chips = [r[0] for r in cur.fetchall()]
        assert len(chips) == 2


def test_ensure_is_idempotent(ensured_schema):
    from platform_schema import ensure_reference_platform_tables
    # Second call must not raise; tables already exist.
    ensure_reference_platform_tables()
    ensure_reference_platform_tables()
```

- [ ] **Step 2: Run the test — it must fail because the helper does not exist yet**

```bash
docker compose up -d postgis backend
docker compose exec -T backend bash -lc "POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest backend/tests/test_reference_platform_schema.py -v"
```

Expected: every test errors with `ImportError: cannot import name 'ensure_reference_platform_tables' from 'platform_schema'` (or fails at fixture setup).

Why inside the backend container: the `pgvector` Python adapter installed in Task 2 lives in that image. Host-side `pytest` will not import it.

- [ ] **Step 3: Commit the failing test**

```bash
git add backend/tests/test_reference_platform_schema.py
git commit -m "test(reference-db): failing integration test for pgvector schema"
```

---

## Task 5 — Implement `ensure_reference_platform_tables()`

**Files:**
- Modify: `backend/platform_schema.py` (append a new function; chain it from `ensure_platform_tables()`)

- [ ] **Step 1: Append the new function at the end of `backend/platform_schema.py`**

Append exactly this function (do NOT remove or rename any existing function). Place it AFTER the last existing function in the file:

```python
def ensure_reference_platform_tables() -> None:
    """Reference Embedding DB schema — see
    /home/avinash/.claude/plans/i-want-to-build-breezy-snail.md and
    docs/backend/reference-platform-db.md.

    Idempotent. Safe to call multiple times.
    """
    with postgis_db.get_cursor(commit=True) as cursor:
        acquire_schema_xact_lock(cursor, "sentinel_reference_platform_schema")

        # Make sure pgvector is available even on databases initialised
        # before the init_postgis.sql gained the CREATE EXTENSION line.
        cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reference_platforms (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                platform_name       TEXT NOT NULL UNIQUE,
                platform_family     TEXT NOT NULL,
                ontology_object_id  TEXT REFERENCES ontology_objects(id) ON DELETE SET NULL,
                country_of_origin   TEXT,
                role                TEXT,
                attributes          JSONB NOT NULL DEFAULT '{}'::jsonb,
                centroid_overhead   vector(1024),
                centroid_ground     vector(512),
                view_domains        TEXT[] NOT NULL DEFAULT '{}'::text[],
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reference_platforms_family ON reference_platforms(platform_family)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reference_platforms_ontology ON reference_platforms(ontology_object_id)")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reference_chips (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                platform_id         UUID NOT NULL REFERENCES reference_platforms(id) ON DELETE CASCADE,
                view_domain         TEXT NOT NULL CHECK (view_domain IN ('overhead','ground')),
                source_dataset      TEXT NOT NULL,
                source_url          TEXT,
                license_spdx        TEXT NOT NULL,
                attribution         TEXT,
                gsd_meters          REAL,
                sensor              TEXT,
                chip_path           TEXT NOT NULL,
                bbox_in_source      JSONB,
                metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
                embedding_overhead  vector(1024),
                embedding_ground    vector(512),
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reference_chips_platform ON reference_chips(platform_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reference_chips_dataset ON reference_chips(source_dataset)")
        # Partial HNSW indexes — only build over rows with a non-null embedding
        # in the relevant view domain, keeping each index dense and small.
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS reference_chips_overhead_hnsw
                ON reference_chips USING hnsw (embedding_overhead vector_cosine_ops)
             WHERE view_domain = 'overhead' AND embedding_overhead IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS reference_chips_ground_hnsw
                ON reference_chips USING hnsw (embedding_ground vector_cosine_ops)
             WHERE view_domain = 'ground' AND embedding_ground IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS reference_platforms_centroid_overhead_hnsw
                ON reference_platforms USING hnsw (centroid_overhead vector_cosine_ops)
             WHERE centroid_overhead IS NOT NULL
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS reference_platforms_centroid_ground_hnsw
                ON reference_platforms USING hnsw (centroid_ground vector_cosine_ops)
             WHERE centroid_ground IS NOT NULL
        """)

        # detection_id is INTEGER to match detections.id SERIAL.
        # platform_id has ON DELETE CASCADE so deleting a reference platform
        # cleans up its outstanding identification candidates atomically.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS platform_identification_candidates (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                detection_id      INTEGER NOT NULL REFERENCES detections(id) ON DELETE CASCADE,
                platform_id       UUID NOT NULL REFERENCES reference_platforms(id) ON DELETE CASCADE,
                score             REAL NOT NULL,
                rank              INTEGER NOT NULL,
                matched_chip_ids  UUID[] NOT NULL DEFAULT '{}'::uuid[],
                status            TEXT NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending','approved','rejected','auto_applied')),
                applied_at        TIMESTAMPTZ,
                reviewed_by       TEXT,
                reviewed_at       TIMESTAMPTZ,
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pic_detection_rank ON platform_identification_candidates(detection_id, rank)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pic_status ON platform_identification_candidates(status)")

        # Extend object_details so an approved/auto-applied identification can
        # land on the detection's existing analyst-asserted metadata row.
        cursor.execute("ALTER TABLE object_details ADD COLUMN IF NOT EXISTS platform_name       TEXT")
        cursor.execute("ALTER TABLE object_details ADD COLUMN IF NOT EXISTS platform_family     TEXT")
        cursor.execute("ALTER TABLE object_details ADD COLUMN IF NOT EXISTS platform_confidence REAL")
        cursor.execute("ALTER TABLE object_details ADD COLUMN IF NOT EXISTS platform_source     TEXT")
```

- [ ] **Step 2: Chain it from `ensure_platform_tables()`**

In the same file, find the last statement inside the `with _platform_schema_lock:` block of `ensure_platform_tables()` — just before `_platform_schema_ready = True` is set. Add a call to the new function so the migration runs alongside the others.

Find the line:

```python
        _platform_schema_ready = True
```

Replace it with:

```python
        ensure_reference_platform_tables()
        _platform_schema_ready = True
```

(If your search returns multiple `_platform_schema_ready = True` matches, use the one that follows the `with _platform_schema_lock:` block — there should only be one assignment of `True`.)

- [ ] **Step 3: Run the test — it must now pass**

```bash
docker compose exec -T backend bash -lc "POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest backend/tests/test_reference_platform_schema.py -v"
```

Expected: 8 passed.

If `test_vector_roundtrip_and_knn_query` fails with `psycopg2.errors.UndefinedFunction: operator does not exist: vector <=> vector`, pgvector did not install correctly in Task 1 — rebuild the postgis image.

- [ ] **Step 4: Commit**

```bash
git add backend/platform_schema.py
git commit -m "schema(reference-db): reference_platforms / chips / candidates tables + HNSW"
```

---

## Task 6 — Verify against a fresh stack (regression catch)

**Files:** none modified. This is an integration-level smoke check.

- [ ] **Step 1: Tear down, rebuild, restart the postgis container, run all backend tests**

```bash
docker compose down postgis
docker volume rm "$(basename "$PWD")_pg_data" || true
docker compose up -d postgis
# wait for healthcheck
sleep 8
docker compose exec -T backend bash -lc "POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest backend/tests/test_reference_platform_schema.py backend/tests/test_object_details.py -v"
```

Expected: all selected tests pass. The `test_object_details` suite confirms the ALTER TABLE additions didn't regress existing object_details behaviour.

⚠️ If you are running on a developer machine with valuable data in `pg_data`, **do not** run `docker volume rm`. Skip it and run only the test command — a fresh-volume regression test will instead happen in CI.

- [ ] **Step 2: Confirm no warning chatter from the lifespan**

```bash
docker compose logs --tail=200 backend | grep -iE "error|exception|warning" | head -20
```

Expected: no new errors. Pre-existing warnings (Neo4j notifications, ontology cache messages) are OK.

---

## Task 7 — Documentation

**Files:**
- Create: `docs/backend/reference-platform-db.md`
- Create: `docs/decisions/why-pgvector-for-reference-db.md`
- Modify: `docs/INDEX.txt` (add two entries, sorted by path)

- [ ] **Step 1: Write the module doc**

Create `docs/backend/reference-platform-db.md` with this content:

```markdown
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
- `ensure_reference_platform_tables()` — idempotent migration; called from `ensure_platform_tables()` and (transitively) from the FastAPI lifespan + any router that uses `_ensure_*` guards. [backend/platform_schema.py](../../backend/platform_schema.py).

## Inputs / Outputs
- Inputs: none beyond an open Postgres connection from `database.postgis_db`.
- Outputs: three tables, the four new `object_details` columns, six HNSW indexes, two regular indexes.

## Failure modes
- pgvector missing → `CREATE EXTENSION vector` raises `ERROR: could not open extension control file`. Fix: ensure the Postgres container is the derived image with `postgresql-18-pgvector` installed ([postgis/Dockerfile](../../postgis/Dockerfile)).
- Concurrent migrations → blocked by `pg_advisory_xact_lock` keyed on `sentinel_reference_platform_schema`.

## Cross-references
- Parent spec: `/home/avinash/.claude/plans/i-want-to-build-breezy-snail.md`
- Decision: [why-pgvector-for-reference-db.md](../decisions/why-pgvector-for-reference-db.md)
- Existing approve/reject pattern this mirrors: [docs/operations/candidate-link-approval.md](../operations/candidate-link-approval.md)
- Object details helpers that will eventually write `platform_*`: [backend/detection_helpers.py](../../backend/detection_helpers.py) (extended in Plan C).
```

- [ ] **Step 2: Write the decision doc**

Create `docs/decisions/why-pgvector-for-reference-db.md`:

```markdown
**Decision:** Use `pgvector` with HNSW indexes inside the existing PostGIS container as the vector index for the Reference Embedding Vector Database. Do not add `faiss-cpu`, `hnswlib`, `usearch`, `qdrant`, or any external vector store.

## Why
- **Co-location with detections.** The reference DB is queried whenever a detection is persisted (auto-identify) and on demand from the SelectionPanel (analyst lookup). Both call sites already hold a Postgres connection. Keeping vectors in Postgres means one round-trip per query and one consistent transaction boundary.
- **No new dependency in inference-sam3.** The inference container is GPU-heavy and already pinned to specific PyTorch / CUDA versions. Adding `faiss-cpu` or `hnswlib` widens that surface for no gain — the inference service can issue a `psycopg2` query just like the backend does.
- **HNSW handles our scale.** Phase-1 chip count is in the tens of thousands; full corpus (with xView + DVIDS/Wikimedia/NARA) is bounded around the 500 k mark. pgvector's HNSW comfortably handles this with per-query latency well below the 15 ms budget set in the parent spec.
- **Airgap compatibility.** pgvector ships as a Debian package (`postgresql-18-pgvector`) from the PGDG apt repo that the upstream `postgis/postgis` image already configures. No HTTP-time downloads, no model files, no licensing surprises.
- **Schema lives next to detections.** When pruning or rebuilding, `pg_dump` of the reference tables is one command; no separate "index file" to keep in sync.

## What we rejected
- **Pure-Python cosine over JSONB** (current approach for detection-vs-detection similar). Fine for ≤ 5 k rows. With 50 k–500 k reference vectors per query, the O(N) scan would dominate the detection write path. The parent spec's 15 ms latency target rules this out.
- **FAISS in inference-sam3.** Heavy native dep, large image footprint, ABI sensitivity. Wins nothing over pgvector at our scale.
- **External vector store (Qdrant, Weaviate).** Adds a service to the compose stack, a network hop, a separate persistence story, and (for most options) telemetry phone-home behaviours that violate the airgap rule.

## Consequences
- Postgres container becomes a small derived image ([postgis/Dockerfile](../../postgis/Dockerfile)).
- Backend gains a `pgvector` Python dependency for adapter registration.
- Reference embeddings are dumped/restored via standard `pg_dump`, simplifying the airgap bundle workflow.
```

- [ ] **Step 3: Add entries to docs/INDEX.txt**

Open `docs/INDEX.txt`. Add the two lines below in sorted order (alphabetically by path):

- Under the `backend/` group, after `backend/reports-and-collections.md`:

```
backend/reference-platform-db.md|backend,reference-db|reference_platforms / reference_chips / platform_identification_candidates + object_details platform_* columns
```

- Under the `decisions/` group, alphabetical insertion (between `why-open-vocabulary.md` and `why-postgis-and-neo4j-coexist.md`):

```
decisions/why-pgvector-for-reference-db.md|decision,reference-db|pgvector + HNSW in PostGIS; no faiss/hnswlib in inference-sam3
```

Keep lines ≤ 100 chars; tags from the existing vocabulary plus the new `reference-db` tag.

- [ ] **Step 4: Commit docs**

```bash
git add docs/backend/reference-platform-db.md docs/decisions/why-pgvector-for-reference-db.md docs/INDEX.txt
git commit -m "docs(reference-db): module + decision for pgvector schema"
```

---

## Task 8 — Final end-to-end verification

**Files:** none modified.

- [ ] **Step 1: Full backend test run targeted at schema-touching suites**

```bash
docker compose up -d postgis backend
docker compose exec -T backend bash -lc "POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest backend/tests/test_reference_platform_schema.py backend/tests/test_object_details.py backend/tests/test_graph_schema.py -v"
```

Expected: all three suites pass. If `test_graph_schema` reports unrelated Neo4j drift, that's pre-existing — file it but do not block this plan.

- [ ] **Step 2: Visual sanity-check the populated schema**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "\d reference_platforms" -c "\d reference_chips" -c "\d platform_identification_candidates" -c "\d object_details"
```

Expected: each `\d` lists the columns from Task 5's CREATE TABLEs; `object_details` shows `platform_name`, `platform_family`, `platform_confidence`, `platform_source`.

- [ ] **Step 3: Verify HNSW indexes are usable**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "EXPLAIN SELECT id FROM reference_chips WHERE view_domain='overhead' AND embedding_overhead IS NOT NULL ORDER BY embedding_overhead <=> '[0.1$(printf ',0.1%.0s' {1..1023})]' LIMIT 5"
```

Expected: the EXPLAIN plan shows `Index Scan using reference_chips_overhead_hnsw`. (The query won't return rows yet — Plan B populates them — but the planner should pick the HNSW index, not a Seq Scan.)

- [ ] **Step 4: Stage and commit the green test output, if any**

(Nothing to commit at this step if Steps 1–3 are clean — Plan A is done.)

---

## Definition of Done

- `docker compose exec postgis psql ... -c "SELECT extversion FROM pg_extension WHERE extname='vector'"` returns a row.
- `\d reference_platforms`, `\d reference_chips`, `\d platform_identification_candidates` each list the columns described above.
- `object_details` has `platform_name`, `platform_family`, `platform_confidence`, `platform_source`.
- `backend/tests/test_reference_platform_schema.py` has 8 tests, all passing.
- `EXPLAIN` on a vector ORDER BY against `reference_chips` picks the HNSW index.
- `docs/backend/reference-platform-db.md`, `docs/decisions/why-pgvector-for-reference-db.md`, and `docs/INDEX.txt` are in `git status` clean (committed).
- No `inference-sam3`, no router, no frontend, no worker code modified in this plan.

## What this plan does NOT do

- Populate any reference data (Plan B).
- Call the schema from inference-sam3 or the worker (Plan C).
- Add any API endpoint or UI (Plan D).
- Bake an offline `pg_dump` of reference data (Plan B's final task).

Hand back to the user when "Definition of Done" is fully checked.
