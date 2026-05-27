"""Integration tests for backend/routers/reference_platforms.py.

Covers all 6 routes:
  - GET  /api/reference-platforms
  - GET  /api/reference-platforms/{platform_id}
  - POST /api/detections/{detection_id}/identify
  - GET  /api/detections/{detection_id}/identification-candidates
  - POST /api/identification-candidates/{candidate_id}/approve
  - POST /api/identification-candidates/{candidate_id}/reject

Auth: all routes require a logged-in session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="module", autouse=True)
def _setup_env():
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    os.environ.setdefault("ADMIN_USERNAME", "test-admin")
    os.environ.setdefault("ADMIN_PASSWORD", "test-admin-pass")


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import main  # noqa: WPS433
    return TestClient(main.app)


def _login(client) -> None:
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text


def _cleanup():
    from database import postgis_db
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("""
            DELETE FROM platform_identification_candidates
             WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-router-%')
        """)
        cur.execute("""
            DELETE FROM object_details
             WHERE source = 'detection'
               AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-router-%')
        """)
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-router-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-router-fixture'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-router-%'")


@pytest.fixture(scope="module")
def populated_ref():
    """Two reference platforms (Red all-ones, Blue all-neg-ones) for the tests."""
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform,
        insert_reference_chip,
        recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    _cleanup()
    with postgis_db.get_cursor(commit=True) as cur:
        pid_red = upsert_reference_platform(
            cur, platform_name="pytest-router-Red", platform_family="RouterRedFam",
            country_of_origin="USA", role="Test platform Red",
        )
        pid_blue = upsert_reference_platform(
            cur, platform_name="pytest-router-Blue", platform_family="RouterBlueFam",
        )
        for i in range(3):
            insert_reference_chip(
                cur,
                platform_id=pid_red, view_domain="overhead",
                source_dataset="pytest-router-fixture",
                chip_path=f"/tmp/pytest-router-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
            insert_reference_chip(
                cur,
                platform_id=pid_blue, view_domain="overhead",
                source_dataset="pytest-router-fixture",
                chip_path=f"/tmp/pytest-router-blue-{i}.png",
                embedding=np.full(1024, -1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid_red)
        recompute_platform_centroids(cur, platform_id=pid_blue)
    yield {"red_id": pid_red, "blue_id": pid_blue}
    _cleanup()


def _insert_fake_detection(label: str) -> int:
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
            (label,),
        )
        return cur.fetchone()["id"]


# --- Auth gate ----------------------------------------------------------------


def test_list_platforms_requires_auth(client):
    resp = client.get("/api/reference-platforms")
    assert resp.status_code == 401


def test_identify_requires_auth(client):
    det_id = _insert_fake_detection("pytest-router-noauth")
    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 401


# --- GET /api/reference-platforms ---------------------------------------------


def test_list_platforms_returns_seeded_rows(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms?limit=200")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "platforms" in body and "count" in body
    fixture_names = {p["platform_name"] for p in body["platforms"]
                     if p["platform_name"].startswith("pytest-router-")}
    assert fixture_names == {"pytest-router-Red", "pytest-router-Blue"}


def test_list_platforms_supports_family_filter(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms?family=RouterRedFam")
    assert resp.status_code == 200
    body = resp.json()
    names = [p["platform_name"] for p in body["platforms"]]
    assert "pytest-router-Red" in names
    assert "pytest-router-Blue" not in names


# --- GET /api/reference-platforms/{id} ----------------------------------------


def test_get_platform_detail_includes_chips(client, populated_ref):
    _login(client)
    pid = populated_ref["red_id"]
    resp = client.get(f"/api/reference-platforms/{pid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform_name"] == "pytest-router-Red"
    assert body["platform_family"] == "RouterRedFam"
    assert body["country_of_origin"] == "USA"
    assert len(body["chips"]) >= 3
    assert body["chips"][0]["source_dataset"] == "pytest-router-fixture"
    assert body["chips"][0]["license_spdx"] == "CC0-1.0"


def test_get_platform_detail_404_for_unknown(client, populated_ref):
    _login(client)
    resp = client.get("/api/reference-platforms/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# --- POST /api/detections/{id}/identify ---------------------------------------


def test_identify_returns_ranked_candidates(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-identify")
    # Plant an embedding on the detection (mimic what inference-sam3 sets)
    from database import postgis_db
    import base64
    v_fp16 = np.full(1024, 1.0, dtype=np.float16)
    fp16_b64 = base64.b64encode(v_fp16.tobytes()).decode("ascii")
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE detections SET metadata = %s::jsonb WHERE id = %s",
            (
                f'{{"embedding": {{"model":"test","dim":1024,"fp16_b64":"{fp16_b64}"}}}}',
                det_id,
            ),
        )

    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["detection_id"] == det_id
    assert body["candidates_written"] >= 1
    fixture_cands = [c for c in body["candidates"]
                     if c["platform_name"].startswith("pytest-router-")]
    assert any(c["platform_name"] == "pytest-router-Red" for c in fixture_cands), \
        "Red should be in returned candidates (matches the all-ones query)"


def test_identify_404_for_unknown_detection(client, populated_ref):
    _login(client)
    resp = client.post(
        "/api/detections/999999999/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 404


def test_identify_400_when_detection_has_no_embedding(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-no-emb")
    # Detection has metadata='{}' from _insert_fake_detection — no embedding key.
    resp = client.post(
        f"/api/detections/{det_id}/identify",
        json={"view_domain": "overhead", "top_k": 3},
    )
    assert resp.status_code == 400, resp.text
    assert "embedding" in resp.json().get("detail", "").lower()


# --- GET /api/detections/{id}/identification-candidates -----------------------


def test_get_candidates_returns_what_identify_wrote(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-getcands")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.full(1024, 1.0, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
    resp = client.get(f"/api/detections/{det_id}/identification-candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert body["detection_id"] == det_id
    assert body["count"] >= 1
    # Top rank should be 1 with status either pending or auto_applied
    top = next(c for c in body["candidates"] if c["rank"] == 1)
    assert top["status"] in ("pending", "auto_applied")


# --- POST .../approve ---------------------------------------------------------


def test_approve_writes_analyst_to_object_details(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-approve")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),  # cosine ~ 0 — below threshold
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates "
            "WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    resp = client.post(f"/api/identification-candidates/{cand_id}/approve")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"] == os.environ["ADMIN_USERNAME"]

    # object_details now reflects analyst approval
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT platform_name, platform_source, updated_by FROM object_details "
            "WHERE source = 'detection' AND source_id = %s",
            (str(det_id),),
        )
        od = cur.fetchone()
    assert od is not None
    assert od["platform_source"] == "analyst"
    assert od["updated_by"] == os.environ["ADMIN_USERNAME"]


def test_approve_404_for_unknown_candidate(client, populated_ref):
    _login(client)
    resp = client.post(
        "/api/identification-candidates/00000000-0000-0000-0000-000000000000/approve",
    )
    assert resp.status_code == 404


# --- POST .../reject ----------------------------------------------------------


def test_reject_sets_status_and_does_not_write_object_details(client, populated_ref):
    _login(client)
    det_id = _insert_fake_detection("pytest-router-reject")
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates "
            "WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    resp = client.post(f"/api/identification-candidates/{cand_id}/reject")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "rejected"
    assert body["reviewed_by"] == os.environ["ADMIN_USERNAME"]

    # object_details NOT written
    with postgis_db.get_cursor(commit=False) as cur:
        cur.execute(
            "SELECT platform_name FROM object_details "
            "WHERE source = 'detection' AND source_id = %s",
            (str(det_id),),
        )
        od = cur.fetchone()
    assert od is None or od.get("platform_name") is None


# --- Concurrent-analyst race: approve/reject of already-reviewed candidate ---


def _seed_candidate(det_label: str) -> str:
    """Insert a fake detection + run attach to create candidates; return rank-1 id."""
    det_id = _insert_fake_detection(det_label)
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates "
            "WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        return cur.fetchone()["id"]


def test_approve_already_approved_returns_409(client, populated_ref):
    _login(client)
    cand_id = _seed_candidate("pytest-router-race-approve")
    # First approve — 200
    r1 = client.post(f"/api/identification-candidates/{cand_id}/approve")
    assert r1.status_code == 200, r1.text
    # Second approve on the same row — must 409 with current state
    r2 = client.post(f"/api/identification-candidates/{cand_id}/approve")
    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body["detail"]["status"] == "approved"
    assert body["detail"]["reviewed_by"] == os.environ["ADMIN_USERNAME"]
    assert body["detail"]["reviewed_at"]  # ISO string


def test_reject_after_approve_returns_409(client, populated_ref):
    _login(client)
    cand_id = _seed_candidate("pytest-router-race-reject-approved")
    assert client.post(f"/api/identification-candidates/{cand_id}/approve").status_code == 200
    # Try to reject the now-approved row
    r = client.post(f"/api/identification-candidates/{cand_id}/reject")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["status"] == "approved"


def test_approve_after_reject_returns_409(client, populated_ref):
    _login(client)
    cand_id = _seed_candidate("pytest-router-race-approve-rejected")
    assert client.post(f"/api/identification-candidates/{cand_id}/reject").status_code == 200
    r = client.post(f"/api/identification-candidates/{cand_id}/approve")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["status"] == "rejected"


def test_reject_404_for_unknown_candidate(client, populated_ref):
    """Symmetry: reject also distinguishes 404 from 409."""
    _login(client)
    r = client.post("/api/identification-candidates/00000000-0000-0000-0000-000000000000/reject")
    assert r.status_code == 404
