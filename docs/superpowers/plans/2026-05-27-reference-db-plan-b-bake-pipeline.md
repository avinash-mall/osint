# Reference Embedding DB — Plan B: Bake Pipeline (DOTA Proof-of-Life)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate the Plan A schema with real reference data from DOTA-v1.0 (already on disk). End state: ~18 platforms (one per DOTA class) with ~10–20 chips each, each chip carrying a DINOv3-SAT 1024-d embedding; per-platform centroids computed; `EXPLAIN ORDER BY centroid_overhead <=> '[...]'` picks the HNSW index. No router or worker code yet — Plan C wires the auto-identify path.

**Architecture:**
- **Where embeddings come from:** A new lightweight `POST /embed` endpoint on `inference-sam3` wraps `embedding.dinov3_pool()` ([inference-sam3/embedding.py#L44-L57](../../inference-sam3/embedding.py)). Reusable by Plan D's analyst lookup. Avoids running the full detection pipeline per chip.
- **Where rows are written:** A new `backend/scripts/bake_reference_index.py` reads chip paths from a seed manifest, POSTs each chip to `inference-sam3:8001/embed`, INSERTs `reference_chips` rows, then recomputes per-platform centroids with one `UPDATE … FROM (SELECT AVG(embedding) …)` query.
- **The pgvector adapter foundation:** Plan A flagged that `pgvector.psycopg2.register_vector()` was never wired into the connection pool — without it, Python `Vector` objects fail to render on INSERT. This plan registers the adapter on the pool inside `database.PostGISDB` as Task 1.
- **DOTA proof-of-life scope:** DOTA has 18 classes (plane, ship, harbor, …) — those are *categories*, not specific platforms (F-35, Arleigh Burke). We seed one row per DOTA class as a placeholder platform whose `platform_family` matches the DOTA class verbatim and whose `ontology_object_id` links to the existing ontology object if one exists. Later plans (xView/RarePlanes) deepen platforms to actual model identity.

**Tech Stack:**
- pgvector 0.4.x Python adapter (already in `backend/requirements.txt`, Plan A Task 2)
- `httpx` (already in `backend/requirements.txt`) for backend→inference-sam3 HTTP
- DINOv3-SAT via the existing `embedding.dinov3_pool()` ([inference-sam3/embedding.py#L44-L57](../../inference-sam3/embedding.py))
- PIL for image decode on the inference side
- DOTA-v1.0 chip + label files at `/nvme/osint/inference-sam3/eval/datasets/dota_val/` (per scripts/eval_datasets/dota.py exploration)
- pytest+integration tests, same convention as Plan A

**Parent spec:** `/nvme/osint/docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md` (this file); broader design `/home/avinash/.claude/plans/i-want-to-build-breezy-snail.md` (Plan A is checked-in at `docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md`).

---

## File Structure

**Created:**
- `inference-sam3/main.py` — new `POST /embed` route (modification, ~20 lines).
- `backend/database.py` — register pgvector adapter on the connection factory (modification, ~6 lines).
- `backend/reference_platform_db.py` *(new module)* — pure helpers: `upsert_reference_platform()`, `insert_reference_chip()`, `recompute_platform_centroids()`. Keeps SQL out of the bake script.
- `backend/scripts/seeds/reference_platforms.seed.json` *(new)* — DOTA-class manifest: 18 entries mapping class → `{platform_name, platform_family, ontology_object_id?, country_of_origin?, role?, attributes?, source_terms_per_dataset}`.
- `backend/scripts/bake_reference_index.py` *(new)* — top-level CLI: `python -m scripts.bake_reference_index --dataset dota --max-chips-per-class 20`.
- `backend/tests/test_reference_platform_baker.py` *(new)* — integration test (mocks the inference-sam3 `/embed` HTTP call) covering: seed-load + upsert idempotency, chip insert with embedding roundtrip, centroid recompute against per-chip vectors.
- `docs/backend/reference-platform-baker.md` *(new)* — module doc per six-section template.
- `docs/inference/embed-endpoint.md` *(new)* — short doc for the new `/embed` route.
- `docs/decisions/why-standalone-embed-endpoint.md` *(new)* — decision record.

**Modified:**
- `docs/INDEX.txt` — three new entries (one per new doc), tags from canonical vocabulary.

**Untouched in Plan B:**
- `backend/routers/*` — Plan D.
- `backend/worker_legacy.py` — Plan C.
- Any frontend code.
- Any other dataset adapter (xView/RarePlanes/etc) — separate follow-ups under `docs/conventions/adding-a-reference-dataset.md` (drafted in Plan B, formalised later).

---

## Task 1 — Register pgvector adapter on the connection pool

**Files:**
- Modify: `backend/database.py` (the PostGIS connection-pool initialisation)

Plan A's final review flagged that nothing registers the pgvector adapter on pooled connections, so any Python writer outside the test fixture will fail. Fix this once, in the connection factory, so every consumer downstream gets it automatically.

- [ ] **Step 1: Read the existing connection-pool code**

Read `backend/database.py:1-100` to locate the `psycopg2.pool.SimpleConnectionPool` (or equivalent) constructor and the function that hands out connections. Identify the precise spot where a new connection enters the pool.

- [ ] **Step 2: Wire the adapter at connection time**

Use the `connection_factory` pattern (psycopg2 supports passing a custom factory). Approach:

```python
from psycopg2.extensions import connection as Psycopg2Connection

class _VectorAwareConnection(Psycopg2Connection):
    """psycopg2 connection that registers pgvector's vector adapter on first use."""
    _vector_registered = False
    def cursor(self, *args, **kwargs):
        if not self._vector_registered:
            try:
                from pgvector.psycopg2 import register_vector
                register_vector(self)
            except Exception:
                # pgvector extension not yet installed in the target DB; harmless
                # for non-reference-DB callers. Will retry on next cursor() call.
                pass
            else:
                self._vector_registered = True
        return super().cursor(*args, **kwargs)
```

Pass `connection_factory=_VectorAwareConnection` to whatever `SimpleConnectionPool`/`ThreadedConnectionPool` constructor is in use today. Place the class definition near the existing `PostGISDB` / pool-creation code so it's discoverable.

(If the file uses a `psycopg2.connect(..., connection_factory=...)` call rather than a pool, set the factory there.)

- [ ] **Step 3: Add a unit test of the registration**

Create `backend/tests/test_pgvector_pool_registration.py`:

```python
"""Verify pgvector adapter is registered on connections from the postgis pool.

Touches PostGIS; safe to skip if no DB available.
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


def test_pool_connections_can_insert_vector_via_list():
    """A bare Python list must round-trip into a vector column once
    the pool's connection factory has registered the adapter."""
    from database import postgis_db
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()

    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO reference_platforms (platform_name, platform_family, centroid_overhead, view_domains) "
            "VALUES (%s, %s, %s, %s) RETURNING centroid_overhead",
            ('pytest-pool-reg', 'PoolRegFamily', [0.5] * 1024, ['overhead']),
        )
        row = cur.fetchone()
    assert row["centroid_overhead"] is not None

    # Teardown
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM reference_platforms WHERE platform_name = %s", ('pytest-pool-reg',))
```

- [ ] **Step 4: Run the test — must pass**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_pgvector_pool_registration.py tests/test_reference_platform_schema.py -v"
```

Expected: 9 passed (1 new + 8 existing).

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/tests/test_pgvector_pool_registration.py
git commit -m "deps(database): register pgvector adapter on pooled connections"
```

---

## Task 2 — Add `POST /embed` endpoint to inference-sam3

**Files:**
- Modify: `inference-sam3/main.py` (add a new route, ~25 lines)

Today there is no standalone embedding endpoint; the only way to extract a DINOv3-SAT vector is through the full `/detect` pipeline. A lightweight `/embed` route lets bake scripts (Plan B) and analyst lookup (Plan D) compute embeddings cheaply.

- [ ] **Step 1: Locate the routes block**

Read `inference-sam3/main.py` looking for where existing `@app.post("/detect")` or `@app.post("/detect_raw")` routes are defined. The new route should land next to them.

Also find the import line `from embedding import dinov3_pool` (or add it if missing) and the `_bundle()` helper or wherever the DINOv3-SAT model bundle is fetched. Note: bundle keys typically include `"dinov3_sat"`.

- [ ] **Step 2: Add the endpoint**

Insert the following route definition next to the existing `/detect` route. Imports go at the top of the file alongside the existing FastAPI/PIL/io imports — only add what's not already imported.

```python
@app.post("/embed")
async def embed_endpoint(image: UploadFile = File(...)):
    """Compute a DINOv3-SAT 1024-d embedding of the supplied image.

    Lightweight alternative to /detect for bake scripts and analyst lookup
    that only need the embedding, not the full detection pipeline.

    Returns:
        {"model": str, "dim": 1024, "fp16_b64": str}
    """
    bundle = _bundle().get("dinov3_sat")
    if bundle is None:
        raise HTTPException(status_code=503, detail="dinov3_sat layer not loaded")
    try:
        img_bytes = await image.read()
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not decode image: {e}")
    result = embedding.dinov3_pool(bundle, pil_img)
    if not result.get("fp16_b64"):
        raise HTTPException(status_code=500, detail="embedding computation returned empty result")
    return result
```

Adjust the `_bundle()` call to match how other routes in this file fetch the active model bundle (some codebases use a global, some a dependency, etc.).

- [ ] **Step 3: Restart inference-sam3 + smoke-test**

```bash
docker compose up -d --build inference-sam3
# wait for /health to go green (model preload is eager)
sleep 30
# Smoke-test with a synthetic 256x256 image
python3 -c "
import io
from PIL import Image
import requests
img = Image.new('RGB', (256, 256), color=(120, 50, 80))
buf = io.BytesIO(); img.save(buf, format='JPEG'); buf.seek(0)
r = requests.post('http://localhost:8001/embed', files={'image': ('test.jpg', buf, 'image/jpeg')})
print(r.status_code, r.json().get('dim'))
"
```

Expected: `200 1024`.

If the call returns 503 ("dinov3_sat layer not loaded"), the imagery profile is not active — first POST `{"profile": "imagery"}` to `/load`, then retry. Document the requirement in the doc (Task 8).

- [ ] **Step 4: Commit**

```bash
git add inference-sam3/main.py
git commit -m "feat(inference-sam3): POST /embed for standalone DINOv3-SAT embeddings"
```

---

## Task 3 — Seed manifest for DOTA platforms

**Files:**
- Create: `backend/scripts/seeds/reference_platforms.seed.json`

The seed manifest is the human-curated bridge between source-dataset class names (DOTA "plane") and canonical platform identities (later: F-35, B-2, etc.). For DOTA proof-of-life, one row per class with the class name as `platform_family`.

- [ ] **Step 1: Check the existing ontology objects**

Before writing the seed file, list ontology objects that might cover the 18 DOTA classes, so we can populate `ontology_object_id` where it makes sense:

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT id, label FROM ontology_objects WHERE LOWER(label) IN ('aircraft','plane','ship','vessel','tank','vehicle','harbor','bridge','helicopter') ORDER BY label"
```

Record whichever IDs return rows; the rest of the 18 entries will leave `ontology_object_id` as `null`.

- [ ] **Step 2: Write the seed file**

Create `backend/scripts/seeds/reference_platforms.seed.json` with this content. Replace `null` for `ontology_object_id` with the actual IDs from Step 1 where a match exists. Keep all entries (every DOTA class gets a row, even with null ontology link):

```json
{
  "version": "2026-05-27-plan-b",
  "platforms": [
    {
      "platform_name": "DOTA::plane",
      "platform_family": "Plane",
      "ontology_object_id": null,
      "country_of_origin": null,
      "role": "Fixed-wing aircraft (DOTA class, unspecified model)",
      "attributes": {},
      "source_terms_per_dataset": {"dota": ["plane"]}
    },
    {"platform_name": "DOTA::ship", "platform_family": "Ship", "ontology_object_id": null, "role": "Naval/civil vessel (DOTA class, unspecified hull)", "attributes": {}, "source_terms_per_dataset": {"dota": ["ship"]}},
    {"platform_name": "DOTA::storage-tank", "platform_family": "StorageTank", "ontology_object_id": null, "role": "Industrial storage tank", "attributes": {}, "source_terms_per_dataset": {"dota": ["storage-tank"]}},
    {"platform_name": "DOTA::baseball-diamond", "platform_family": "BaseballDiamond", "ontology_object_id": null, "role": "Sports facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["baseball-diamond"]}},
    {"platform_name": "DOTA::tennis-court", "platform_family": "TennisCourt", "ontology_object_id": null, "role": "Sports facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["tennis-court"]}},
    {"platform_name": "DOTA::basketball-court", "platform_family": "BasketballCourt", "ontology_object_id": null, "role": "Sports facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["basketball-court"]}},
    {"platform_name": "DOTA::ground-track-field", "platform_family": "GroundTrackField", "ontology_object_id": null, "role": "Sports facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["ground-track-field"]}},
    {"platform_name": "DOTA::harbor", "platform_family": "Harbor", "ontology_object_id": null, "role": "Port infrastructure", "attributes": {}, "source_terms_per_dataset": {"dota": ["harbor"]}},
    {"platform_name": "DOTA::bridge", "platform_family": "Bridge", "ontology_object_id": null, "role": "Bridge / span", "attributes": {}, "source_terms_per_dataset": {"dota": ["bridge"]}},
    {"platform_name": "DOTA::large-vehicle", "platform_family": "LargeVehicle", "ontology_object_id": null, "role": "Truck or other large ground vehicle", "attributes": {}, "source_terms_per_dataset": {"dota": ["large-vehicle"]}},
    {"platform_name": "DOTA::small-vehicle", "platform_family": "SmallVehicle", "ontology_object_id": null, "role": "Car / small ground vehicle", "attributes": {}, "source_terms_per_dataset": {"dota": ["small-vehicle"]}},
    {"platform_name": "DOTA::helicopter", "platform_family": "Helicopter", "ontology_object_id": null, "role": "Rotary-wing aircraft", "attributes": {}, "source_terms_per_dataset": {"dota": ["helicopter"]}},
    {"platform_name": "DOTA::roundabout", "platform_family": "Roundabout", "ontology_object_id": null, "role": "Road intersection", "attributes": {}, "source_terms_per_dataset": {"dota": ["roundabout"]}},
    {"platform_name": "DOTA::soccer-ball-field", "platform_family": "SoccerBallField", "ontology_object_id": null, "role": "Sports facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["soccer-ball-field"]}},
    {"platform_name": "DOTA::swimming-pool", "platform_family": "SwimmingPool", "ontology_object_id": null, "role": "Recreational facility", "attributes": {}, "source_terms_per_dataset": {"dota": ["swimming-pool"]}},
    {"platform_name": "DOTA::container-crane", "platform_family": "ContainerCrane", "ontology_object_id": null, "role": "Port crane", "attributes": {}, "source_terms_per_dataset": {"dota": ["container-crane"]}},
    {"platform_name": "DOTA::airport", "platform_family": "Airport", "ontology_object_id": null, "role": "Airfield infrastructure", "attributes": {}, "source_terms_per_dataset": {"dota": ["airport"]}},
    {"platform_name": "DOTA::helipad", "platform_family": "Helipad", "ontology_object_id": null, "role": "Helicopter landing pad", "attributes": {}, "source_terms_per_dataset": {"dota": ["helipad"]}}
  ]
}
```

The `DOTA::` prefix in `platform_name` keeps these clearly distinguishable from later real platforms (e.g. `F-35A Lightning II`) added by xView/RarePlanes seeds.

- [ ] **Step 3: Commit**

```bash
git add backend/scripts/seeds/reference_platforms.seed.json
git commit -m "data(reference-db): DOTA-class seed manifest (18 placeholder platforms)"
```

---

## Task 4 — `backend/reference_platform_db.py` helper module

**Files:**
- Create: `backend/reference_platform_db.py`

Pure helpers that the bake script (and Plans C/D) will reuse. No I/O orchestration here — just SQL wrapped in functions with clean signatures.

- [ ] **Step 1: Write the module**

Create `backend/reference_platform_db.py`:

```python
"""Helpers for reading and writing rows in the Reference Embedding DB.

Pure SQL wrappers; no HTTP, no file I/O. See:
- docs/backend/reference-platform-db.md for the schema this module manipulates
- docs/backend/reference-platform-baker.md for the bake script that drives it

All functions expect callers to hold a live cursor from
`database.postgis_db.get_cursor(commit=True)` or to manage the connection
explicitly. Idempotent on the natural keys (platform_name; chip_path).
"""

from __future__ import annotations

from typing import Optional, Sequence
import json


def upsert_reference_platform(
    cursor,
    *,
    platform_name: str,
    platform_family: str,
    ontology_object_id: Optional[str] = None,
    country_of_origin: Optional[str] = None,
    role: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> str:
    """Upsert one platform by `platform_name` (UNIQUE). Returns the row id (UUID).

    Updates platform_family / role / attributes if the row already exists; does
    NOT touch centroids or view_domains (recompute_platform_centroids handles those).
    """
    cursor.execute(
        """
        INSERT INTO reference_platforms
            (platform_name, platform_family, ontology_object_id,
             country_of_origin, role, attributes)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (platform_name) DO UPDATE SET
            platform_family    = EXCLUDED.platform_family,
            ontology_object_id = EXCLUDED.ontology_object_id,
            country_of_origin  = EXCLUDED.country_of_origin,
            role               = EXCLUDED.role,
            attributes         = EXCLUDED.attributes,
            updated_at         = NOW()
        RETURNING id
        """,
        (
            platform_name,
            platform_family,
            ontology_object_id,
            country_of_origin,
            role,
            json.dumps(attributes or {}),
        ),
    )
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def insert_reference_chip(
    cursor,
    *,
    platform_id: str,
    view_domain: str,
    source_dataset: str,
    chip_path: str,
    embedding: Sequence[float],
    license_spdx: str,
    source_url: Optional[str] = None,
    attribution: Optional[str] = None,
    gsd_meters: Optional[float] = None,
    sensor: Optional[str] = None,
    bbox_in_source: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert one chip and its embedding. Idempotent on (platform_id, chip_path).

    `view_domain` is either 'overhead' (embedding lands in embedding_overhead)
    or 'ground' (embedding_ground). `embedding` must be length 1024 for
    overhead, 512 for ground.
    """
    if view_domain not in ("overhead", "ground"):
        raise ValueError(f"view_domain must be 'overhead' or 'ground', got {view_domain!r}")

    expected_dim = 1024 if view_domain == "overhead" else 512
    if len(embedding) != expected_dim:
        raise ValueError(
            f"{view_domain} embedding must be {expected_dim}-d; got {len(embedding)}"
        )

    overhead_col = list(embedding) if view_domain == "overhead" else None
    ground_col = list(embedding) if view_domain == "ground" else None

    cursor.execute(
        """
        INSERT INTO reference_chips
            (platform_id, view_domain, source_dataset, source_url, license_spdx,
             attribution, gsd_meters, sensor, chip_path, bbox_in_source, metadata,
             embedding_overhead, embedding_ground)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (platform_id, chip_path) DO UPDATE SET
            source_dataset     = EXCLUDED.source_dataset,
            source_url         = EXCLUDED.source_url,
            license_spdx       = EXCLUDED.license_spdx,
            attribution        = EXCLUDED.attribution,
            gsd_meters         = EXCLUDED.gsd_meters,
            sensor             = EXCLUDED.sensor,
            bbox_in_source     = EXCLUDED.bbox_in_source,
            metadata           = EXCLUDED.metadata,
            embedding_overhead = EXCLUDED.embedding_overhead,
            embedding_ground   = EXCLUDED.embedding_ground
        RETURNING id
        """,
        (
            platform_id,
            view_domain,
            source_dataset,
            source_url,
            license_spdx,
            attribution,
            gsd_meters,
            sensor,
            chip_path,
            json.dumps(bbox_in_source) if bbox_in_source is not None else None,
            json.dumps(metadata or {}),
            overhead_col,
            ground_col,
        ),
    )
    row = cursor.fetchone()
    return row["id"] if isinstance(row, dict) else row[0]


def recompute_platform_centroids(cursor, *, platform_id: Optional[str] = None) -> int:
    """Recompute `reference_platforms.centroid_overhead` / `centroid_ground`
    as the per-domain mean of their chips' embeddings.

    Updates `view_domains` to reflect which centroids became non-null. Returns
    the number of platform rows updated.

    If `platform_id` is given, only that platform is recomputed; otherwise all
    platforms with at least one chip are recomputed.
    """
    where_clause = "WHERE p.id = %s" if platform_id else ""
    params = (platform_id,) if platform_id else ()
    cursor.execute(
        f"""
        WITH agg AS (
            SELECT
                c.platform_id,
                AVG(c.embedding_overhead) FILTER (WHERE c.view_domain = 'overhead' AND c.embedding_overhead IS NOT NULL) AS centroid_overhead,
                AVG(c.embedding_ground)   FILTER (WHERE c.view_domain = 'ground'   AND c.embedding_ground   IS NOT NULL) AS centroid_ground
            FROM reference_chips c
            GROUP BY c.platform_id
        )
        UPDATE reference_platforms p
           SET centroid_overhead = agg.centroid_overhead,
               centroid_ground   = agg.centroid_ground,
               view_domains      = (
                   CASE WHEN agg.centroid_overhead IS NOT NULL THEN ARRAY['overhead']::text[] ELSE '{{}}'::text[] END
                 ||CASE WHEN agg.centroid_ground   IS NOT NULL THEN ARRAY['ground']::text[]   ELSE '{{}}'::text[] END
               ),
               updated_at = NOW()
          FROM agg
         WHERE p.id = agg.platform_id
           {where_clause}
        """,
        params,
    )
    return cursor.rowcount
```

NOTE: the `(platform_id, chip_path)` ON CONFLICT requires a unique index on that pair. Add it to `ensure_reference_platform_tables()` — go back into `backend/platform_schema.py` and add this line in the `reference_chips` section, just after the existing `CREATE INDEX idx_reference_chips_dataset ...` line:

```python
cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_reference_chips_platform_path ON reference_chips(platform_id, chip_path)")
```

The unique index is idempotent; if the bake runs against a DB that has chips inserted previously without this index, the index creation will fail loudly — that case shouldn't exist in practice because Plan A's tables shipped empty, but verify before running.

- [ ] **Step 2: Verify `pg_aggregate` supports AVG on `vector`**

pgvector 0.5+ defines `avg(vector) → vector`. Plan A shipped pgvector 0.8.2 so this is available. Sanity-check:

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT pg_get_function_result((SELECT oid FROM pg_proc WHERE proname='avg' AND prorettype = 'vector'::regtype LIMIT 1))"
```

Expected: returns `vector` (one row). If empty, pgvector is older than expected — escalate as BLOCKED.

- [ ] **Step 3: Commit**

```bash
git add backend/reference_platform_db.py backend/platform_schema.py
git commit -m "feat(reference-db): platform/chip helpers + (platform_id, chip_path) unique index"
```

---

## Task 5 — Failing integration test for the helpers + bake

**Files:**
- Create: `backend/tests/test_reference_platform_baker.py`

TDD: write the test first. The test mocks the HTTP call to `/embed` (so it can run without inference-sam3 up) and exercises the seed-loader + helpers + centroid recompute end-to-end against a small fixture chip list.

- [ ] **Step 1: Write the test**

Create `backend/tests/test_reference_platform_baker.py`:

```python
"""Integration tests for backend/scripts/bake_reference_index.py and helpers.

The DOTA pipeline is exercised against a small synthetic chip set; the
inference-sam3 /embed HTTP call is mocked so the test is self-contained.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _cleanup_pytest_rows():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM reference_chips
             WHERE source_dataset = 'pytest-bake-dota'
        """)
        cur.execute("""
            DELETE FROM reference_platforms
             WHERE platform_name LIKE 'pytest-bake-%'
        """)


@pytest.fixture(scope="module")
def ensured_schema():
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()
    _cleanup_pytest_rows()
    yield
    _cleanup_pytest_rows()


def test_upsert_reference_platform_is_idempotent(ensured_schema):
    from database import postgis_db
    from reference_platform_db import upsert_reference_platform
    with postgis_db.get_cursor(commit=True) as cur:
        a = upsert_reference_platform(
            cur,
            platform_name="pytest-bake-A",
            platform_family="Fighter Aircraft",
            role="initial role",
        )
        b = upsert_reference_platform(
            cur,
            platform_name="pytest-bake-A",
            platform_family="Fighter Aircraft",
            role="updated role",
        )
    assert a == b, "same platform_name must return same id"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("SELECT role FROM reference_platforms WHERE id = %s", (a,))
        row = cur.fetchone()
    assert row["role"] == "updated role"


def test_insert_reference_chip_roundtrips_embedding(ensured_schema):
    from database import postgis_db
    from reference_platform_db import insert_reference_chip, upsert_reference_platform

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-B", platform_family="ShipCategory"
        )
        v = [0.1] * 1024
        chip_id = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-bake-dota",
            chip_path="/tmp/pytest-bake-chip-1.png",
            embedding=v,
            license_spdx="CC-BY-4.0",
        )
    assert chip_id is not None

    # Second insert with same (platform_id, chip_path) must upsert, not duplicate
    with postgis_db.get_cursor(commit=True) as cur:
        chip_id_2 = insert_reference_chip(
            cur,
            platform_id=pid,
            view_domain="overhead",
            source_dataset="pytest-bake-dota",
            chip_path="/tmp/pytest-bake-chip-1.png",
            embedding=[0.2] * 1024,
            license_spdx="CC-BY-4.0",
        )
    assert chip_id_2 == chip_id, "second insert on same (platform_id, chip_path) must upsert"


def test_insert_reference_chip_rejects_wrong_dimension(ensured_schema):
    from database import postgis_db
    from reference_platform_db import insert_reference_chip, upsert_reference_platform

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-dim", platform_family="DimCategory"
        )
        with pytest.raises(ValueError, match="must be 1024-d"):
            insert_reference_chip(
                cur,
                platform_id=pid,
                view_domain="overhead",
                source_dataset="pytest-bake-dota",
                chip_path="/tmp/pytest-bake-dim.png",
                embedding=[0.0] * 512,  # wrong dim for overhead
                license_spdx="CC-BY-4.0",
            )


def test_recompute_platform_centroids_averages_chips(ensured_schema):
    from database import postgis_db
    from reference_platform_db import (
        insert_reference_chip,
        recompute_platform_centroids,
        upsert_reference_platform,
    )

    with postgis_db.get_cursor(commit=True) as cur:
        pid = upsert_reference_platform(
            cur, platform_name="pytest-bake-C", platform_family="CentroidCategory"
        )
        for i, val in enumerate([0.1, 0.2, 0.3]):
            insert_reference_chip(
                cur,
                platform_id=pid,
                view_domain="overhead",
                source_dataset="pytest-bake-dota",
                chip_path=f"/tmp/pytest-bake-C-{i}.png",
                embedding=[val] * 1024,
                license_spdx="CC-BY-4.0",
            )
        n = recompute_platform_centroids(cur, platform_id=pid)
    assert n == 1, f"expected 1 platform updated, got {n}"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT centroid_overhead, view_domains FROM reference_platforms WHERE id = %s",
            (pid,),
        )
        row = cur.fetchone()
    # AVG of [0.1, 0.2, 0.3] is 0.2 per dim
    centroid = row["centroid_overhead"]
    assert centroid is not None
    arr = np.asarray(centroid, dtype=np.float32)
    assert arr.shape == (1024,)
    assert pytest.approx(float(arr.mean()), abs=1e-5) == 0.2
    assert "overhead" in row["view_domains"]


def test_bake_script_seeds_dota_platforms_and_inserts_chips(ensured_schema, tmp_path, monkeypatch):
    """End-to-end small-fixture run of the bake script with a mocked /embed.

    The script must:
      1. Read the seed manifest.
      2. Upsert each manifest platform.
      3. For each chip file under the fixture root, POST to /embed (mocked).
      4. Insert each chip with the returned embedding.
      5. Recompute centroids.
    """
    import io, base64, struct
    from PIL import Image

    # Build a 2-platform, 3-chip-each fixture seed
    fixture_seed = {
        "version": "pytest-fixture",
        "platforms": [
            {"platform_name": "pytest-bake-plane",
             "platform_family": "Plane",
             "source_terms_per_dataset": {"dota": ["plane"]}},
            {"platform_name": "pytest-bake-ship",
             "platform_family": "Ship",
             "source_terms_per_dataset": {"dota": ["ship"]}},
        ],
    }
    seed_path = tmp_path / "reference_platforms.seed.json"
    seed_path.write_text(json.dumps(fixture_seed))

    # Build a fixture chip tree with 3 small PNGs per class
    chips_root = tmp_path / "chips"
    for cls in ("plane", "ship"):
        (chips_root / cls).mkdir(parents=True)
        for i in range(3):
            img = Image.new("RGB", (32, 32), color=(i * 10, 50, 80))
            img.save(chips_root / cls / f"chip_{i}.png")

    # Mock the /embed HTTP call to return a deterministic fp16-encoded vector
    def _fake_post(url, files, timeout):
        cls_name = Path(files["image"][0]).parent.name
        # Vector that differs per class; identical across chips of the same class
        v = np.full(1024, 0.1 if cls_name == "plane" else 0.5, dtype=np.float16)
        b64 = base64.b64encode(v.tobytes()).decode("ascii")
        class _R:
            status_code = 200
            def json(self):
                return {"model": "facebook/dinov3-vitl16-pretrain-sat493m",
                        "dim": 1024, "fp16_b64": b64}
        return _R()

    from scripts import bake_reference_index as bake
    monkeypatch.setattr(bake, "_post_embed", _fake_post)

    rows_written = bake.run(
        seed_path=str(seed_path),
        dataset="dota",
        dataset_root=str(chips_root),
        license_spdx="CC-BY-4.0",
        max_chips_per_class=10,
    )
    assert rows_written["platforms"] == 2
    assert rows_written["chips"] == 6
    assert rows_written["centroids"] == 2

    # Confirm centroids are non-null and differ between platforms
    with postgis_db_cursor() as cur:
        cur.execute(
            "SELECT platform_name, centroid_overhead FROM reference_platforms "
            "WHERE platform_name IN ('pytest-bake-plane', 'pytest-bake-ship')"
        )
        rows = {r["platform_name"]: np.asarray(r["centroid_overhead"], dtype=np.float32)
                for r in cur.fetchall()}
    assert rows["pytest-bake-plane"].mean() == pytest.approx(0.1, abs=5e-4)
    assert rows["pytest-bake-ship"].mean() == pytest.approx(0.5, abs=5e-4)


def postgis_db_cursor():
    """Small adapter so the test above can use a plain `with … as cur:` block."""
    from database import postgis_db
    return postgis_db.get_cursor(commit=False)
```

- [ ] **Step 2: Run the test — it must fail because helpers and bake script don't exist yet**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_baker.py -v"
```

Expected: `ImportError: No module named 'reference_platform_db'` (or similar) — confirms tests are RED for the right reason.

After Task 4 (helpers module) lands but before Task 6 (bake script) lands, expected state: 4 of 5 tests pass (helpers tests), 1 fails (end-to-end test, because `scripts.bake_reference_index` doesn't exist yet).

- [ ] **Step 3: Commit the failing test**

```bash
git add backend/tests/test_reference_platform_baker.py
git commit -m "test(reference-db): failing tests for platform helpers + DOTA bake script"
```

---

## Task 6 — `backend/scripts/bake_reference_index.py`

**Files:**
- Create: `backend/scripts/bake_reference_index.py`
- Create: `backend/scripts/__init__.py` *(empty, only if it doesn't already exist — required for `from scripts import bake_reference_index` to work)*

- [ ] **Step 1: Confirm or create the package init file**

```bash
ls /nvme/osint/backend/scripts/__init__.py
```

If missing, create it as an empty file (`Write` with content `""`). Otherwise leave it alone.

- [ ] **Step 2: Write the bake script**

Create `backend/scripts/bake_reference_index.py`:

```python
"""Bake the Reference Embedding DB from a curated seed manifest + chip tree.

Usage (inside the backend container):
    python -m scripts.bake_reference_index \
        --seed /app/scripts/seeds/reference_platforms.seed.json \
        --dataset dota \
        --dataset-root /data/datasets/reference-chips/dota \
        --license CC-BY-4.0 \
        --max-chips-per-class 20

Reads the seed manifest, upserts every listed platform, walks the dataset's chip
tree (one subdirectory per source-class), posts each chip image to
inference-sam3 :8001/embed, and inserts a reference_chips row carrying the
returned 1024-d DINOv3-SAT embedding. After all rows are inserted,
per-platform centroids are recomputed.

Idempotent: re-runnable; reuses existing rows by (platform_id, chip_path).

See docs/backend/reference-platform-baker.md for the full module doc.
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import numpy as np
import requests

# Make `backend/` importable when running via `python -m scripts...` inside the container
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from database import postgis_db
from platform_schema import ensure_reference_platform_tables
from reference_platform_db import (
    insert_reference_chip,
    recompute_platform_centroids,
    upsert_reference_platform,
)

log = logging.getLogger("bake_reference_index")

INFERENCE_BASE = os.environ.get("INFERENCE_SAM3_URI", "http://inference-sam3:8001")
EMBED_TIMEOUT_SEC = float(os.environ.get("REFERENCE_EMBED_TIMEOUT", "60"))


def _post_embed(url: str, files: dict, timeout: float):
    """Single seam for HTTP. Tests monkey-patch this."""
    return requests.post(url, files=files, timeout=timeout)


def _decode_fp16_embedding(payload: dict) -> list[float]:
    fp16_b64 = payload.get("fp16_b64", "")
    if not fp16_b64:
        raise RuntimeError(f"embedding payload missing fp16_b64: {payload!r}")
    raw = base64.b64decode(fp16_b64)
    arr = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    if arr.shape != (1024,):
        raise RuntimeError(f"expected 1024-d embedding, got {arr.shape}")
    return arr.tolist()


def _chip_paths_for_class(dataset_root: Path, source_terms: list[str], max_per_class: int) -> list[Path]:
    """Collect chip files. The default convention is one subdirectory per
    source class under `dataset_root` (e.g. `dota_root/plane/*.png`)."""
    results: list[Path] = []
    for term in source_terms:
        cls_dir = dataset_root / term
        if not cls_dir.is_dir():
            log.warning("no chip directory found for class %r at %s", term, cls_dir)
            continue
        files = sorted(p for p in cls_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        results.extend(files[:max_per_class])
    return results


def run(
    *,
    seed_path: str,
    dataset: str,
    dataset_root: str,
    license_spdx: str,
    max_chips_per_class: int,
    inference_base: Optional[str] = None,
) -> dict[str, int]:
    """Programmatic entry point for tests; see __main__ for CLI."""
    ensure_reference_platform_tables()

    inference_base = inference_base or INFERENCE_BASE
    seed = json.loads(Path(seed_path).read_text())
    platforms_in_seed = seed.get("platforms", [])
    dataset_root_path = Path(dataset_root)

    chips_written = 0
    platforms_written = 0

    with postgis_db.get_cursor(commit=True) as cur:
        for entry in platforms_in_seed:
            source_terms = (entry.get("source_terms_per_dataset", {}) or {}).get(dataset, [])
            if not source_terms:
                continue  # platform isn't in this dataset; skip
            platform_id = upsert_reference_platform(
                cur,
                platform_name=entry["platform_name"],
                platform_family=entry["platform_family"],
                ontology_object_id=entry.get("ontology_object_id"),
                country_of_origin=entry.get("country_of_origin"),
                role=entry.get("role"),
                attributes=entry.get("attributes") or {},
            )
            platforms_written += 1

            for chip_path in _chip_paths_for_class(dataset_root_path, source_terms, max_chips_per_class):
                with chip_path.open("rb") as f:
                    resp = _post_embed(
                        f"{inference_base}/embed",
                        files={"image": (chip_path.name, f, "image/png")},
                        timeout=EMBED_TIMEOUT_SEC,
                    )
                if getattr(resp, "status_code", None) != 200:
                    log.warning("embed failed for %s: %s", chip_path, getattr(resp, "text", "?"))
                    continue
                emb = _decode_fp16_embedding(resp.json())
                insert_reference_chip(
                    cur,
                    platform_id=platform_id,
                    view_domain="overhead",
                    source_dataset=dataset,
                    chip_path=str(chip_path),
                    embedding=emb,
                    license_spdx=license_spdx,
                )
                chips_written += 1

    # Centroid recompute (separate transaction so any partial chip insert is durable)
    with postgis_db.get_cursor(commit=True) as cur:
        centroids_updated = recompute_platform_centroids(cur)

    log.info(
        "bake done: platforms=%d, chips=%d, centroids_updated=%d",
        platforms_written, chips_written, centroids_updated,
    )
    return {
        "platforms": platforms_written,
        "chips": chips_written,
        "centroids": centroids_updated,
    }


def _main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Bake the Reference Embedding DB from a seed + chip tree")
    p.add_argument("--seed", required=True, help="Path to reference_platforms.seed.json")
    p.add_argument("--dataset", required=True, help="Dataset key in source_terms_per_dataset (e.g. 'dota')")
    p.add_argument("--dataset-root", required=True, help="Root directory of chips (one subdir per source class)")
    p.add_argument("--license", required=True, help="SPDX license identifier for the source dataset")
    p.add_argument("--max-chips-per-class", type=int, default=20)
    p.add_argument("--inference-base", default=None, help="Override INFERENCE_SAM3_URI")
    args = p.parse_args(argv)
    stats = run(
        seed_path=args.seed,
        dataset=args.dataset,
        dataset_root=args.dataset_root,
        license_spdx=args.license,
        max_chips_per_class=args.max_chips_per_class,
        inference_base=args.inference_base,
    )
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
```

- [ ] **Step 3: Run the integration test — must now show 5 passed**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_baker.py -v"
```

Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/scripts/bake_reference_index.py backend/scripts/__init__.py
git commit -m "feat(reference-db): bake_reference_index CLI populates DB from seed + chip tree"
```

---

## Task 7 — Stage DOTA chips and run the bake end-to-end

**Files:**
- Modify (or create-by-copy): chips already exist at `/nvme/osint/inference-sam3/eval/datasets/dota_val/`, but the bake expects one-subdir-per-class layout under `/data/datasets/reference-chips/dota/`. This task stages them.

CLAUDE.md hard-rule 1 says treat `/data/*` as read-only on dev host: "populated at build time or by long-running pipelines". The bake IS such a long-running pipeline — it's the legitimate writer for `/data/datasets/reference-chips/`. Use absolute paths and write only to the reference-chips subtree.

- [ ] **Step 1: Inspect the source layout**

```bash
ls /nvme/osint/inference-sam3/eval/datasets/dota_val/ | head -20
docker compose exec -T inference-sam3 ls /app/eval/datasets/dota_val/ 2>/dev/null | head -20 || echo "not in inference container"
docker compose exec -T backend ls /data/datasets/ 2>/dev/null | head -20 || echo "no /data/datasets in backend"
```

The labels.json format (per `scripts/eval_datasets/dota.py:38-57`) is `[{"chip_file": "chip_*.png", "annotations": [{"label": str, "bbox_xyxy": [...]}]}]`.

- [ ] **Step 2: Write a one-shot staging helper inside the bake script's tree**

Create `backend/scripts/stage_dota_chips.py`:

```python
"""Stage DOTA chips into the per-class layout the baker expects.

For each entry in labels.json:
- Pick the annotation with the largest bbox area (tie-break: first listed).
- Crop the chip to that bbox + 8 px margin (clipped to image bounds).
- Save the crop as <out_root>/<class>/<chip-stem>__<idx>.png.

This makes every chip carry one canonical class assignment without re-extracting
from the full DOTA training rasters. The result is one subdirectory per class
under <out_root>, ready for `bake_reference_index --dataset dota`.

Idempotent: re-runs overwrite existing files of the same name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def stage(labels_json: Path, chips_dir: Path, out_root: Path, margin_px: int = 8) -> dict[str, int]:
    rows = json.loads(labels_json.read_text())
    counts: dict[str, int] = {}
    for row in rows:
        anns = row.get("annotations") or []
        if not anns:
            continue
        chip_path = chips_dir / row["chip_file"]
        if not chip_path.is_file():
            continue
        # Pick largest-area annotation
        def _area(a):
            x1, y1, x2, y2 = a["bbox_xyxy"]
            return max(0.0, x2 - x1) * max(0.0, y2 - y1)
        best = max(anns, key=_area)
        cls = best["label"]
        x1, y1, x2, y2 = best["bbox_xyxy"]
        with Image.open(chip_path) as img:
            w, h = img.size
            l = max(0, int(x1) - margin_px)
            t = max(0, int(y1) - margin_px)
            r = min(w, int(x2) + margin_px)
            b = min(h, int(y2) + margin_px)
            if r - l < 8 or b - t < 8:
                continue
            crop = img.crop((l, t, r, b))
            (out_root / cls).mkdir(parents=True, exist_ok=True)
            crop.save(out_root / cls / f"{chip_path.stem}__{cls}.png")
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def _main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--labels", required=True)
    p.add_argument("--chips-dir", required=True)
    p.add_argument("--out-root", required=True)
    p.add_argument("--margin-px", type=int, default=8)
    args = p.parse_args(argv)
    counts = stage(Path(args.labels), Path(args.chips_dir), Path(args.out_root), margin_px=args.margin_px)
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
```

- [ ] **Step 3: Stage the DOTA chips**

Run staging inside the backend container so it sees `/data` correctly:

```bash
docker compose exec -T backend bash -lc "mkdir -p /data/datasets/reference-chips/dota && cd /app && python scripts/stage_dota_chips.py --labels /app/eval/datasets/dota_val/labels.json --chips-dir /app/eval/datasets/dota_val --out-root /data/datasets/reference-chips/dota --margin-px 8"
```

(Adjust the `--labels` and `--chips-dir` paths to wherever DOTA actually lives in the backend container's filesystem — Step 1 told you where it is. If the dataset is only accessible from the inference-sam3 container, run the staging there: `docker compose exec inference-sam3 python /app/scripts/stage_dota_chips.py …` with `--out-root` pointing at a path that backend ALSO sees, typically the shared `/data` volume.)

Expected stdout: a JSON like `{"plane": 47, "ship": 22, "small-vehicle": 89, …}`. Total chips across all classes should be tens to hundreds depending on the source dataset size.

- [ ] **Step 4: Confirm inference-sam3 is up with the imagery profile loaded**

```bash
docker compose exec -T inference-sam3 bash -lc 'curl -s http://localhost:8001/capabilities | head'
# If dinov3_sat is not in capabilities, load the imagery profile:
docker compose exec -T inference-sam3 bash -lc 'curl -s -X POST http://localhost:8001/load -H "Content-Type: application/json" -d "{\"profile\":\"imagery\"}"'
```

- [ ] **Step 5: Run the bake**

```bash
docker compose exec -T backend bash -lc "cd /app && python -m scripts.bake_reference_index --seed /app/scripts/seeds/reference_platforms.seed.json --dataset dota --dataset-root /data/datasets/reference-chips/dota --license CC-BY-4.0 --max-chips-per-class 20"
```

Expected stdout: a JSON like `{"platforms": <N>, "chips": <M>, "centroids": <N>}`. `<N>` should be between 1 and 18 (depending on how many DOTA classes have chips after staging); `<M>` should be 1–20× `<N>`.

- [ ] **Step 6: Verify rows landed**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT platform_family, count(*) AS chips FROM reference_chips c JOIN reference_platforms p ON c.platform_id = p.id WHERE c.source_dataset = 'dota' GROUP BY platform_family ORDER BY chips DESC"
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT platform_name, (centroid_overhead IS NOT NULL) AS has_overhead_centroid FROM reference_platforms WHERE platform_name LIKE 'DOTA::%' ORDER BY platform_name"
```

Expected: at least one DOTA class with non-null centroid; chip counts ≤ max-chips-per-class.

- [ ] **Step 7: HNSW planner sanity check at scale**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "EXPLAIN SELECT id, platform_id FROM reference_chips WHERE view_domain = 'overhead' AND embedding_overhead IS NOT NULL ORDER BY embedding_overhead <=> (SELECT centroid_overhead FROM reference_platforms WHERE platform_name = 'DOTA::plane') LIMIT 5"
```

Expected: `Index Scan using reference_chips_overhead_hnsw`. If the planner still picks Seq Scan because the row count is small, retry with `SET enable_seqscan = off;` before the EXPLAIN and confirm the HNSW plan is at least *available*.

- [ ] **Step 8: Commit the staging script**

```bash
git add backend/scripts/stage_dota_chips.py
git commit -m "feat(reference-db): DOTA chip staging into per-class subdirs for baker"
```

(The bake itself writes only to the DB and `/data/datasets/reference-chips/dota/` — neither is checked in.)

---

## Task 8 — Documentation

**Files:**
- Create: `docs/backend/reference-platform-baker.md`
- Create: `docs/inference/embed-endpoint.md`
- Create: `docs/decisions/why-standalone-embed-endpoint.md`
- Modify: `docs/INDEX.txt` (three new entries, canonical tags only)

- [ ] **Step 1: Write the baker module doc**

Create `docs/backend/reference-platform-baker.md`:

```markdown
# `backend/scripts/bake_reference_index.py` — Reference DB Baker

**Path:** [backend/scripts/bake_reference_index.py](../../backend/scripts/bake_reference_index.py)
**Lines:** ~150
**Depends on:** `backend/reference_platform_db.py`, `backend/platform_schema.py`, `requests`, inference-sam3 `:8001/embed` route.

## Purpose
Populates `reference_platforms` and `reference_chips` from a curated seed manifest plus a per-class chip tree on disk. For each chip image, posts to inference-sam3's `/embed`, decodes the fp16 vector, INSERTs it into `reference_chips.embedding_overhead`, and (at the end) recomputes per-platform centroids.

Designed for build-time / long-running-pipeline use — not request-path.

## Why this design
- **HTTP to inference-sam3** instead of importing `embedding` directly: keeps the bake script in the backend container (where `psycopg2` and the connection pool live) and reuses the already-loaded DINOv3-SAT model in GPU VRAM. See [why-standalone-embed-endpoint.md](../decisions/why-standalone-embed-endpoint.md).
- **Idempotent on `(platform_id, chip_path)`** via the unique index added to `reference_chips`. Re-runs upsert in place rather than duplicate.
- **Centroid recompute as a separate transaction**: any partial chip insert is durable even if the centroid AVG step rolls back.
- **Seam for tests**: `_post_embed` is the only place HTTP touches the network — tests monkey-patch it.

## Key symbols
- [`run()`](../../backend/scripts/bake_reference_index.py) — programmatic entry point; called from `__main__` and from `test_reference_platform_baker.py`.
- [`_chip_paths_for_class()`](../../backend/scripts/bake_reference_index.py) — convention: one subdirectory per source class under `dataset_root`.
- [`_decode_fp16_embedding()`](../../backend/scripts/bake_reference_index.py) — handles the inference response's `fp16_b64` field; raises if dim != 1024.

## Inputs / Outputs
- **Inputs:** seed JSON (one entry per platform with `source_terms_per_dataset`), a dataset root with one subdir per source class, an SPDX license identifier, a max-chips-per-class cap.
- **Outputs:** new/updated rows in `reference_platforms` and `reference_chips`; per-platform centroids; a stdout JSON with `{platforms, chips, centroids}` counts.

## Failure modes
- inference-sam3 returns 503 ("dinov3_sat layer not loaded") → load the `imagery` profile first via `POST /load`.
- Network timeout → tunable via `REFERENCE_EMBED_TIMEOUT` env (default 60 s).
- Seed file references a dataset key absent from `source_terms_per_dataset` → entry silently skipped (intentional: lets one manifest cover many datasets).
- Chip directory missing for a listed class → log a warning, skip that class.

## Cross-references
- [reference-platform-db.md](reference-platform-db.md) — the schema this baker writes into.
- [embed-endpoint.md](../inference/embed-endpoint.md) — the inference route consumed.
- Plan A spec (in-repo): [docs/superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md](../superpowers/plans/2026-05-26-reference-db-plan-a-pgvector-schema.md)
- Plan B spec (in-repo): [docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md](../superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md)
```

- [ ] **Step 2: Write the inference endpoint doc**

Create `docs/inference/embed-endpoint.md`:

```markdown
# `inference-sam3` — `POST /embed`

**Path:** [inference-sam3/main.py](../../inference-sam3/main.py)
**Lines:** ~20 (the route handler)
**Depends on:** `embedding.dinov3_pool()`, the `dinov3_sat` layer in the active profile (typically loaded via `POST /load {"profile":"imagery"}`).

## Purpose
Compute a DINOv3-SAT 1024-d embedding of a single image. Lightweight alternative to `POST /detect` for callers that only need the embedding, not the full SAM3/DOTA/GDINO/YOLOE pipeline.

## Why this design
The bake script (Plan B) and the analyst lookup (Plan D) both want fast embeddings of arbitrary images without paying the full detection-pipeline cost. The shared `dinov3_pool()` is already in the inference image; this route is a thin wrapper.

## Request
```
POST /embed
Content-Type: multipart/form-data
Form field:
  image: <PNG | JPEG bytes>
```

## Response
```json
{
  "model": "facebook/dinov3-vitl16-pretrain-sat493m",
  "dim": 1024,
  "fp16_b64": "<base64-encoded fp16 vector>"
}
```

Decode with:
```python
import base64, numpy as np
arr = np.frombuffer(base64.b64decode(resp["fp16_b64"]), dtype=np.float16).astype(np.float32)
```

## Failure modes
- `503` "dinov3_sat layer not loaded" → load the `imagery` profile via `POST /load`.
- `400` "could not decode image" → image bytes are not a valid PNG/JPEG.
- `500` "embedding computation returned empty result" → crop too small for DINOv3-SAT (see `embed_crop` reject path at [inference-sam3/embedding.py](../../inference-sam3/embedding.py)).

## Cross-references
- [reference-platform-baker.md](../backend/reference-platform-baker.md) — the primary consumer in Plan B.
- [dinov3-embeddings.md](dinov3-embeddings.md) — the model bundle this route uses.
```

- [ ] **Step 3: Write the decision doc**

Create `docs/decisions/why-standalone-embed-endpoint.md`:

```markdown
**Decision:** Add a lightweight `POST /embed` endpoint to inference-sam3 instead of computing embeddings inside the backend container or reusing `POST /detect`.

## Why
- **Reuse the loaded model.** DINOv3-SAT is preloaded into GPU VRAM at inference-sam3 startup (eager lifespan, see `preload_models_on_startup`). The bake script gets to amortise that startup cost across thousands of chips without paying it per-call.
- **Don't pay the full detection cost.** `POST /detect` runs SAM3 + DOTA-OBB + GDINO + YOLOE + fusion + RemoteCLIP verification — 100–500× the compute the bake actually needs. Calling `/detect` to harvest one embedding field would burn GPU time and add latency for no gain.
- **Avoid putting psycopg2 / DB credentials inside inference-sam3.** The inference container is GPU-heavy and pinned to specific PyTorch/CUDA versions. Adding the backend's DB dep there couples lifecycles unnecessarily. A thin HTTP boundary keeps each container focused on what it owns.
- **Reusable beyond Plan B.** Plan D's analyst-side "what is this object?" lookup needs ad-hoc embeddings on operator-uploaded reference photos. Same endpoint serves both flows.

## What we rejected
- **Bake script inside `inference-sam3`**: would require giving the inference container access to the backend DB. Cross-cuts service boundaries.
- **Calling `/detect` and reading the `embedding` field**: 100×+ unnecessary compute per chip.
- **A new dedicated embedding service**: adds an extra container, an extra port, an extra Docker image, and another piece of state to manage. Wrong scale of solution for a side door that's logically the same model anyway.

## Consequences
- inference-sam3 gains one tiny route, ~25 lines.
- The bake script lives in `backend/scripts/`, alongside its peers.
- The `dinov3_sat` layer must be loaded for `/embed` to return 200 — operators / startup config must keep the `imagery` profile active (it is by default).
```

- [ ] **Step 4: Update INDEX.txt**

Open `docs/INDEX.txt`. Add the three new entries in within-section alphabetical position. Use only the canonical tag vocabulary (`backend | inference | decision`, never invented).

Find `backend/reference-platform-db.md|...` (added in Plan A). Insert immediately AFTER (since `reference-platform-baker.md` sorts after `reference-platform-db.md`? No — `baker` comes before `db` alphabetically). Re-read the file and place this line in correct alphabetical position relative to its neighbours:

```
backend/reference-platform-baker.md|backend|reads seed + chip tree, POSTs to inference-sam3 /embed, writes reference_chips + centroids
```

Find `inference/dinov3-embeddings.md|...` (existing). Insert immediately AFTER (since `embed-endpoint` sorts before `dinov3-embeddings`? `embed` < `dinov3`? `d` < `e`, so `dinov3` < `embed-endpoint`). Place this line in correct position:

```
inference/embed-endpoint.md|inference|POST /embed for standalone DINOv3-SAT vectors; used by reference DB baker
```

For the decision doc, place between `decisions/why-sar-confidence-cap.md` and `decisions/why-sat-tiles-cap-at-native-zoom.md` (since `why-standalone-...` sorts there alphabetically):

```
decisions/why-standalone-embed-endpoint.md|decision|standalone /embed avoids /detect cost and keeps DB dep in backend
```

Verify line lengths each <= ~150 chars (project's de facto cap, matching Plan A's polish). Verify within-section alphabetical order:

```bash
grep -n "reference-platform-\|embed-endpoint\|why-standalone-embed" docs/INDEX.txt
```

- [ ] **Step 5: Commit the docs**

```bash
git add docs/backend/reference-platform-baker.md docs/inference/embed-endpoint.md docs/decisions/why-standalone-embed-endpoint.md docs/INDEX.txt
git commit -m "docs(reference-db): baker module, embed-endpoint, decision record"
```

---

## Task 9 — Final end-to-end verification

**Files:** none modified.

- [ ] **Step 1: Full test run on touched suites**

```bash
docker compose exec -T backend bash -lc "cd /app && POSTGIS_URI=postgresql://sentinel:sentinel@postgis:5432/sentinel python -m pytest tests/test_reference_platform_schema.py tests/test_reference_platform_baker.py tests/test_pgvector_pool_registration.py tests/test_object_details.py -v"
```

Expected: all suites green, count totals to ~17 passed (8 schema + 5 baker + 1 pool reg + 4 object details, possibly higher if the suites grew).

- [ ] **Step 2: Re-run the bake idempotently — second run must NOT duplicate rows**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT count(*) FROM reference_chips WHERE source_dataset='dota'" > /tmp/count_before.txt
docker compose exec -T backend bash -lc "cd /app && python -m scripts.bake_reference_index --seed /app/scripts/seeds/reference_platforms.seed.json --dataset dota --dataset-root /data/datasets/reference-chips/dota --license CC-BY-4.0 --max-chips-per-class 20"
docker compose exec -T postgis psql -U sentinel -d sentinel -c "SELECT count(*) FROM reference_chips WHERE source_dataset='dota'" > /tmp/count_after.txt
diff /tmp/count_before.txt /tmp/count_after.txt
```

Expected: no diff. Row counts identical after second run = idempotent.

- [ ] **Step 3: Spot-check a KNN query**

```bash
docker compose exec -T postgis psql -U sentinel -d sentinel -c "WITH q AS (SELECT centroid_overhead FROM reference_platforms WHERE platform_name = 'DOTA::plane') SELECT p.platform_name, c.chip_path FROM reference_chips c JOIN reference_platforms p ON c.platform_id = p.id, q WHERE c.view_domain = 'overhead' ORDER BY c.embedding_overhead <=> q.centroid_overhead LIMIT 5"
```

Expected: at least one of the top-5 results is a `DOTA::plane` chip — sanity that the centroid actually matches its own chips best.

- [ ] **Step 4: No-scope-creep check**

```bash
git diff --name-only $(git log --oneline | grep -m1 'docs(reference-db): refresh Lines header' | cut -d' ' -f1)..HEAD
```

Expected set (15 files, give or take INDEX.txt):
- `backend/database.py`
- `backend/platform_schema.py`
- `backend/reference_platform_db.py`
- `backend/scripts/__init__.py` (if newly created)
- `backend/scripts/bake_reference_index.py`
- `backend/scripts/seeds/reference_platforms.seed.json`
- `backend/scripts/stage_dota_chips.py`
- `backend/tests/test_pgvector_pool_registration.py`
- `backend/tests/test_reference_platform_baker.py`
- `inference-sam3/main.py`
- `docs/backend/reference-platform-baker.md`
- `docs/inference/embed-endpoint.md`
- `docs/decisions/why-standalone-embed-endpoint.md`
- `docs/INDEX.txt`
- `docs/superpowers/plans/2026-05-27-reference-db-plan-b-bake-pipeline.md`

NOTHING in `backend/routers/`, NOTHING in `frontend/`, NOTHING in `backend/worker_legacy.py`.

---

## Definition of Done

- `inference-sam3` exposes `POST /embed` returning a `{model, dim:1024, fp16_b64}` body for a sample image.
- `backend/database.py` wires `pgvector.psycopg2.register_vector` on the connection pool; `test_pgvector_pool_registration.py` passes.
- `reference_platform_db.py` exposes `upsert_reference_platform`, `insert_reference_chip`, `recompute_platform_centroids`; all unit/integration tests pass (5 in `test_reference_platform_baker.py`).
- `bake_reference_index.py` runs end-to-end against the DOTA chip tree and produces at least one platform with a non-null `centroid_overhead`; idempotent on re-run.
- An `EXPLAIN ORDER BY embedding_overhead <=> ...` against the populated `reference_chips` shows the HNSW index plan when `enable_seqscan` is off (and may show it when on, depending on row count).
- Module doc + endpoint doc + decision doc are committed; `docs/INDEX.txt` updated; canonical tags only.
- No router, worker, or frontend code modified in this plan.

## What this plan does NOT do

- Wire auto-identify into the detection write path (Plan C).
- Expose any API surface for analyst lookup (Plan D).
- Add xView / RarePlanes / ShipRSImageNet / HRSC2016 / DVIDS / Wikimedia / NARA / NASA datasets (separate per-dataset PRs under a future `docs/conventions/adding-a-reference-dataset.md`).
- Modify the ontology or seed platforms beyond the DOTA proof-of-life.

Hand back to the user when "Definition of Done" is fully checked.
