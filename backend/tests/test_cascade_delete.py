"""Integration tests: every detection delete path leaves zero orphans.

PostGIS FK cascades clear candidate + track-member rows, but three classes of
data have no FK to ``detections`` and must be purged explicitly by
``backend/cascade_delete.py``:

  * ``object_details`` (analyst designation/threat, polymorphic ``source_id``)
  * empty ``detection_tracks`` (parent track left member-less by the cascade)
  * ``operational_entity_tracks`` links to those tracks

These tests seed one of each, run a delete path, and assert all three are gone.
PostGIS-backed; the suite auto-skips when the DB is unavailable (conftest.py).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TAG = "pytest-cascade"


# --------------------------------------------------------------------------
# Seeding / teardown
# --------------------------------------------------------------------------

def _cleanup():
    from database import postgis_db
    from platform_schema import ensure_platform_tables
    ensure_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "DELETE FROM object_details WHERE source_id IN "
            "(SELECT id::text FROM detections WHERE class LIKE %s) "
            "OR source_id IN (SELECT id::text FROM fmv_detections WHERE class LIKE %s)",
            (f"{_TAG}%", f"{_TAG}%"),
        )
        cur.execute("DELETE FROM operational_entity_tracks WHERE entity_id LIKE %s", (f"{_TAG}%",))
        cur.execute("DELETE FROM detection_tracks WHERE track_uid LIKE %s", (f"{_TAG}%",))
        cur.execute("DELETE FROM fmv_clips WHERE name LIKE %s", (f"{_TAG}%",))
        cur.execute("DELETE FROM detections WHERE class LIKE %s", (f"{_TAG}%",))
        cur.execute("DELETE FROM satellite_passes WHERE name LIKE %s", (f"{_TAG}%",))


@pytest.fixture(autouse=True)
def _clean():
    _cleanup()
    yield
    _cleanup()


def _seed_pass_with_detection(cur):
    """Insert a pass + one detection + object_details + a single-member track +
    an operational_entity_tracks link. Returns (pass_id, det_id, track_id)."""
    cur.execute(
        "INSERT INTO satellite_passes (name, file_path, sensor_type, acquisition_time) "
        "VALUES (%s, %s, 'Optical', NOW()) RETURNING id",
        (f"{_TAG}-pass", f"/tmp/{_TAG}-{uuid.uuid4()}.tif"),
    )
    pass_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO detections (pass_id, class, confidence, geom, centroid, metadata, source) "
        "VALUES (%s, %s, 0.9, "
        "ST_GeomFromText('POLYGON((0 0,0 1,1 1,1 0,0 0))',4326), "
        "ST_SetSRID(ST_MakePoint(0.5,0.5),4326), '{}'::jsonb, 'operator') RETURNING id",
        (pass_id, f"{_TAG}-cls"),
    )
    det_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO object_details (source, source_id, designation, updated_by) "
        "VALUES ('detection', %s, 'seeded', 'pytest')",
        (str(det_id),),
    )
    cur.execute(
        "INSERT INTO detection_tracks (track_uid, primary_class, category, status, obs_count, "
        "last_centroid) VALUES (%s, %s, 'infrastructure', 'confirmed', 1, "
        "ST_SetSRID(ST_MakePoint(0.5,0.5),4326)) RETURNING id",
        (f"{_TAG}-{uuid.uuid4()}", f"{_TAG}-cls"),
    )
    track_id = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO detection_track_members (track_id, detection_id, pass_id, observed_at, "
        "centroid, seq_index, cost) VALUES (%s, %s, %s, NOW(), "
        "ST_SetSRID(ST_MakePoint(0.5,0.5),4326), 0, 0)",
        (track_id, det_id, pass_id),
    )
    cur.execute(
        "INSERT INTO operational_entity_tracks (entity_id, track_id, attached_by) "
        "VALUES (%s, %s, 'pytest')",
        (f"{_TAG}-ent-{uuid.uuid4()}", track_id),
    )
    return pass_id, det_id, track_id


def _counts(cur, det_id, track_id):
    cur.execute("SELECT COUNT(*) c FROM object_details WHERE source='detection' AND source_id=%s",
                (str(det_id),))
    od = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM detection_tracks WHERE id=%s", (track_id,))
    tr = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM operational_entity_tracks WHERE track_id=%s", (track_id,))
    oet = cur.fetchone()["c"]
    cur.execute("SELECT COUNT(*) c FROM detections WHERE id=%s AND deleted_at IS NULL", (det_id,))
    live_det = cur.fetchone()["c"]
    return od, tr, oet, live_det


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_delete_imagery_leaves_no_orphans():
    from database import postgis_db
    from auth import SessionUser
    from routers.imagery import delete_imagery

    with postgis_db.get_cursor(commit=True) as cur:
        pass_id, det_id, track_id = _seed_pass_with_detection(cur)

    delete_imagery(pass_id, user=SessionUser(username="pytest", role="admin"))

    with postgis_db.get_cursor(commit=False) as cur:
        od, tr, oet, _ = _counts(cur, det_id, track_id)
        cur.execute("SELECT COUNT(*) c FROM detections WHERE id=%s", (det_id,))
        det = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM satellite_passes WHERE id=%s", (pass_id,))
        sp = cur.fetchone()["c"]
    assert (od, tr, oet, det, sp) == (0, 0, 0, 0, 0)


def test_clear_existing_detections_leaves_no_orphans():
    from database import postgis_db
    import worker_legacy

    with postgis_db.get_cursor(commit=True) as cur:
        pass_id, det_id, track_id = _seed_pass_with_detection(cur)

    worker_legacy.clear_existing_detections(pass_id)

    with postgis_db.get_cursor(commit=False) as cur:
        od, tr, oet, _ = _counts(cur, det_id, track_id)
        cur.execute("SELECT COUNT(*) c FROM detections WHERE id=%s", (det_id,))
        det = cur.fetchone()["c"]
    assert (od, tr, oet, det) == (0, 0, 0, 0)


def test_detection_soft_delete_purges_projections_but_keeps_tombstone():
    from database import postgis_db
    from auth import SessionUser
    from routers.detections import delete_detection

    with postgis_db.get_cursor(commit=True) as cur:
        _pass_id, det_id, track_id = _seed_pass_with_detection(cur)

    delete_detection(det_id, user=SessionUser(username="pytest", role="admin"))

    with postgis_db.get_cursor(commit=False) as cur:
        od, tr, oet, live_det = _counts(cur, det_id, track_id)
        # Row survives as a tombstone (deleted_at set), projections purged.
        cur.execute("SELECT COUNT(*) c FROM detections WHERE id=%s AND deleted_at IS NOT NULL",
                    (det_id,))
        tombstoned = cur.fetchone()["c"]
    assert (od, tr, oet) == (0, 0, 0)
    assert live_det == 0          # hidden from live reads
    assert tombstoned == 1        # but preserved for audit


def test_delete_fmv_clip_purges_object_details():
    from database import postgis_db
    from auth import SessionUser
    from main import delete_fmv_clip

    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO fmv_clips (name, file_path, status) VALUES (%s, %s, 'stored') RETURNING id",
            (f"{_TAG}-clip", f"/tmp/{_TAG}-{uuid.uuid4()}.mp4"),
        )
        clip_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO fmv_detections (clip_id, frame_index, class, confidence) "
            "VALUES (%s, 0, %s, 0.8) RETURNING id",
            (clip_id, f"{_TAG}-fmvcls"),
        )
        fmv_det_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO object_details (source, source_id, designation, updated_by) "
            "VALUES ('fmv_detection', %s, 'seeded', 'pytest')",
            (str(fmv_det_id),),
        )

    delete_fmv_clip(clip_id, user=SessionUser(username="pytest", role="admin"))

    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT COUNT(*) c FROM object_details WHERE source='fmv_detection' AND source_id=%s",
            (str(fmv_det_id),),
        )
        od = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM fmv_clips WHERE id=%s", (clip_id,))
        clip = cur.fetchone()["c"]
    assert (od, clip) == (0, 0)
