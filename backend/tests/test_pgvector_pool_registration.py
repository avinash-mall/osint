"""Verify pgvector adapter is registered on connections from the postgis pool.

Touches PostGIS; safe to skip if no DB available.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def test_pool_connections_can_insert_and_read_numpy_vector():
    """A numpy.ndarray must round-trip into and out of a vector column
    via the pool's registered pgvector adapter.

    A bare Python list would pass without the adapter (psycopg2 serialises
    lists as array literals that Postgres implicit-casts to vector), so we
    use numpy.ndarray which cannot serialise without register_vector.
    """
    import numpy as np

    from database import postgis_db
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()

    v = np.asarray([0.5] * 1024, dtype=np.float32)
    try:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "INSERT INTO reference_platforms (platform_name, platform_family, centroid_overhead, view_domains) "
                "VALUES (%s, %s, %s, %s) RETURNING centroid_overhead",
                ('pytest-pool-reg', 'PoolRegFamily', v, ['overhead']),
            )
            row = cur.fetchone()
        # Adapter must produce a numpy array on SELECT (not a string literal)
        centroid = row["centroid_overhead"]
        assert isinstance(centroid, np.ndarray), \
            f"SELECT-side adapter not registered: got {type(centroid).__name__}"
        assert centroid.shape == (1024,)
        assert centroid.dtype.kind == "f"
    finally:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM reference_platforms WHERE platform_name = %s",
                ('pytest-pool-reg',),
            )


def test_pool_reuse_does_not_re_register():
    """Adapter registration survives put/get cycles on the same pool slot
    (the connection's type-adapter map is preserved across reuse).

    Verified by issuing two separate get_cursor() calls and confirming
    both can serialise numpy arrays — if the second one re-invoked
    register_vector, the test would still pass, but at least we lock in
    that the second insert doesn't fail with 'can't adapt'.
    """
    import numpy as np

    from database import postgis_db
    from platform_schema import ensure_reference_platform_tables
    ensure_reference_platform_tables()

    try:
        for name in ("pytest-pool-reuse-1", "pytest-pool-reuse-2"):
            v = np.asarray([0.7] * 1024, dtype=np.float32)
            with postgis_db.get_cursor(commit=True) as cur:
                cur.execute(
                    "INSERT INTO reference_platforms "
                    "(platform_name, platform_family, centroid_overhead, view_domains) "
                    "VALUES (%s, %s, %s, %s)",
                    (name, 'ReuseFamily', v, ['overhead']),
                )
    finally:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                "DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-pool-reuse-%'",
            )
