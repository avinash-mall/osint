"""Integration tests for the worker-side auto-identify path.

Exercises:
  - find_similar_platforms returns ranked candidates
  - attach_identification_candidates inserts platform_identification_candidates rows
  - Auto-apply writes object_details.platform_* when score >= threshold
  - Below-threshold candidates land as 'pending' (no object_details write)
  - Idempotent re-run on the same detection_id replaces rows (not duplicates)
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


def _cleanup_pytest_rows():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM platform_identification_candidates
             WHERE detection_id IN (
                 SELECT id FROM detections WHERE class LIKE 'pytest-autoid-%'
             )
        """)
        cur.execute("""
            DELETE FROM object_details
             WHERE source = 'detection'
               AND source_id IN (
                   SELECT id::text FROM detections WHERE class LIKE 'pytest-autoid-%'
               )
        """)
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-autoid-%'")
        cur.execute("""
            DELETE FROM reference_chips
             WHERE source_dataset = 'pytest-autoid-fixture'
        """)
        cur.execute("""
            DELETE FROM reference_platforms
             WHERE platform_name LIKE 'pytest-autoid-%'
        """)


@pytest.fixture(scope="module")
def populated_ref_db():
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform,
        insert_reference_chip,
        recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup_pytest_rows()
    # Build two platforms whose centroids are far apart so a query close to
    # one is unambiguously the right answer.
    with postgis_db.get_cursor(commit=True) as cur:
        pid_red = upsert_reference_platform(
            cur, platform_name="pytest-autoid-Red", platform_family="RedFam"
        )
        pid_blue = upsert_reference_platform(
            cur, platform_name="pytest-autoid-Blue", platform_family="BlueFam"
        )
        for i in range(3):
            insert_reference_chip(
                cur,
                platform_id=pid_red,
                view_domain="overhead",
                source_dataset="pytest-autoid-fixture",
                chip_path=f"/tmp/pytest-autoid-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),  # all-ones
                license_spdx="CC0-1.0",
            )
            insert_reference_chip(
                cur,
                platform_id=pid_blue,
                view_domain="overhead",
                source_dataset="pytest-autoid-fixture",
                chip_path=f"/tmp/pytest-autoid-blue-{i}.png",
                embedding=np.full(1024, -1.0, dtype=np.float32),  # all-neg-ones
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid_red)
        recompute_platform_centroids(cur, platform_id=pid_blue)
    yield {"red_id": pid_red, "blue_id": pid_blue}
    _cleanup_pytest_rows()


def test_find_similar_platforms_ranks_correctly(populated_ref_db):
    """A query embedding identical to Red's centroid must rank Red top-1
    among the test fixture's platforms. Real DBs may contain other
    populated platforms (e.g. DOTA platforms from earlier bakes); the
    test isolates by filtering results to the pytest-autoid-* fixtures.
    """
    from database import postgis_db
    from reference_platform_db import find_similar_platforms
    q = np.full(1024, 1.0, dtype=np.float32)
    # Use a wider candidate_pool so both fixture platforms (Red, Blue) survive
    # past any other platforms in the DB and into the re-rank stage.
    with postgis_db.get_cursor(commit=False) as cur:
        results = find_similar_platforms(
            cur, embedding=q, view_domain="overhead",
            top_k=50, candidate_pool=50,
        )
    fixture_results = [r for r in results if r["platform_name"].startswith("pytest-autoid-")]
    assert len(fixture_results) == 2, \
        f"expected 2 fixture platforms in results, got {[r['platform_name'] for r in fixture_results]}"
    red = next(r for r in fixture_results if r["platform_name"] == "pytest-autoid-Red")
    blue = next(r for r in fixture_results if r["platform_name"] == "pytest-autoid-Blue")
    assert red["score"] == pytest.approx(1.0, abs=1e-4), f"Red score: {red['score']}"
    assert blue["score"] == pytest.approx(-1.0, abs=1e-4), f"Blue score: {blue['score']}"
    # Red must outrank Blue
    red_rank = fixture_results.index(red)
    blue_rank = fixture_results.index(blue)
    assert red_rank < blue_rank, f"Red (idx {red_rank}) must outrank Blue (idx {blue_rank})"
    assert len(red["matched_chip_ids"]) > 0


def _insert_fake_detection(class_label: str = "pytest-autoid-test") -> int:
    """Insert a minimal detection row so we have a real FK target."""
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO detections (class, confidence, geom, centroid, metadata)
            VALUES (%s, 0.5,
                    ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326),
                    ST_GeomFromText('POINT(0.5 0.5)', 4326),
                    '{}'::jsonb)
            RETURNING id
            """,
            (class_label,),
        )
        return cur.fetchone()["id"]


def test_attach_writes_candidates_and_auto_applies_above_threshold(populated_ref_db):
    """Top-1 score >= threshold must (a) write a 'auto_applied' candidate row
    and (b) populate object_details.platform_name/family/confidence/source."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-high")
    q = np.full(1024, 1.0, dtype=np.float32)  # exactly matches Red centroid
    with postgis_db.get_cursor(commit=True) as cur:
        n = attach_identification_candidates(
            cur,
            detection_id=det_id,
            embedding=q,
            view_domain="overhead",
            auto_threshold=0.85,
            top_k=3,
        )
    assert n >= 1, "expected at least one candidate inserted"

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT p.platform_name, c.score, c.rank, c.status
              FROM platform_identification_candidates c
              JOIN reference_platforms p ON c.platform_id = p.id
             WHERE c.detection_id = %s
             ORDER BY c.rank
        """, (det_id,))
        cands = cur.fetchall()
    assert len(cands) >= 1
    top = cands[0]
    assert top["platform_name"] == "pytest-autoid-Red"
    assert top["status"] == "auto_applied"
    assert top["score"] >= 0.85

    # object_details auto-populated
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT platform_name, platform_family, platform_confidence, platform_source
              FROM object_details
             WHERE source = 'detection' AND source_id = %s
        """, (str(det_id),))
        od = cur.fetchone()
    assert od is not None
    assert od["platform_name"] == "pytest-autoid-Red"
    assert od["platform_family"] == "RedFam"
    assert od["platform_source"] == "auto"
    assert od["platform_confidence"] >= 0.85
    assert od["platform_confidence"] <= 1.0


def test_attach_below_threshold_leaves_candidates_pending(populated_ref_db):
    """A query that matches the top platform with score < threshold must
    (a) still write candidate rows but (b) NOT populate object_details."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-low")
    # An embedding orthogonal-ish to both centroids — cosine ~ 0
    q = np.zeros(1024, dtype=np.float32)
    q[0] = 1.0
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur,
            detection_id=det_id,
            embedding=q,
            view_domain="overhead",
            auto_threshold=0.85,
            top_k=3,
        )

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT status FROM platform_identification_candidates
             WHERE detection_id = %s
        """, (det_id,))
        statuses = [r["status"] for r in cur.fetchall()]
        cur.execute("""
            SELECT platform_name FROM object_details
             WHERE source = 'detection' AND source_id = %s
        """, (str(det_id),))
        od = cur.fetchone()

    assert all(s == "pending" for s in statuses), f"expected all 'pending', got {statuses}"
    assert od is None or od.get("platform_name") is None, \
        "object_details.platform_name must NOT be auto-populated below threshold"


def test_attach_is_idempotent_on_rerun(populated_ref_db):
    """A second attach call on the same detection_id replaces, not duplicates."""
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-idem")
    q = np.full(1024, 1.0, dtype=np.float32)
    with postgis_db.get_cursor(commit=True) as cur:
        n1 = attach_identification_candidates(
            cur, detection_id=det_id, embedding=q, view_domain="overhead",
            auto_threshold=0.85, top_k=3,
        )
    with postgis_db.get_cursor(commit=True) as cur:
        n2 = attach_identification_candidates(
            cur, detection_id=det_id, embedding=q, view_domain="overhead",
            auto_threshold=0.85, top_k=3,
        )
    assert n1 == n2
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT count(*) AS n FROM platform_identification_candidates
             WHERE detection_id = %s
        """, (det_id,))
        row = cur.fetchone()
    # After two calls with identical inputs, the count should equal one
    # run's worth — NOT 2× (which would mean we duplicated instead of replacing).
    assert row["n"] == n1, f"expected {n1} rows, got {row['n']}"


def test_auto_apply_overwrites_analyst_asserted_platform(populated_ref_db):
    """Auto-identify run REPLACES an analyst-asserted platform_name when
    top-1 score >= threshold. Intentional contract — Plan D's UI surfaces
    the conflict via platform_source='auto'.
    """
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    det_id = _insert_fake_detection("pytest-autoid-overwrite")

    # Step 1: analyst manually asserts platform_name = 'Manually-Asserted'
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            """
            INSERT INTO object_details
                (source, source_id, platform_name, platform_family,
                 platform_source, updated_by)
            VALUES ('detection', %s, 'Manually-Asserted', 'ManualFam',
                    'analyst', 'pytest-analyst')
            """,
            (str(det_id),),
        )

    # Step 2: auto-identify fires with a Red-pointing query (above threshold)
    q = np.full(1024, 1.0, dtype=np.float32)
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur,
            detection_id=det_id,
            embedding=q,
            view_domain="overhead",
            auto_threshold=0.85,
            top_k=3,
        )

    # Step 3: object_details.platform_name is now Red (auto overwrote analyst)
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute("""
            SELECT platform_name, platform_family, platform_source
              FROM object_details
             WHERE source = 'detection' AND source_id = %s
        """, (str(det_id),))
        od = cur.fetchone()
    assert od["platform_name"] == "pytest-autoid-Red", \
        f"auto should have overwritten 'Manually-Asserted'; got {od['platform_name']}"
    assert od["platform_family"] == "RedFam"
    assert od["platform_source"] == "auto", \
        f"platform_source should be 'auto' (signalling the overwrite); got {od['platform_source']}"


def test_platform_confidence_check_rejects_out_of_range(populated_ref_db):
    """The CHECK constraint added in Task 1 must reject confidence > 1.0."""
    import psycopg2
    from database import postgis_db
    det_id = _insert_fake_detection("pytest-autoid-checkfail")
    with pytest.raises(psycopg2.errors.CheckViolation):
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO object_details
                    (source, source_id, platform_name, platform_confidence)
                VALUES ('detection', %s, 'pytest-autoid-Red', 1.5)
                """,
                (str(det_id),),
            )
