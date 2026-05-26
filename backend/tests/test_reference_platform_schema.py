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
