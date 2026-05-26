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
