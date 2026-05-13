"""Tests for the /api/ontology/* endpoints — Step 6 of the ontology refactor.

Run with:
    POSTGIS_URI=postgresql://sentinel:sentinel@172.18.0.4:5432/sentinel \
      python -m pytest backend/tests/test_ontology_api.py -v

These tests touch the live PostGIS DB. They clean up after themselves by
deleting rows whose ids start with ``test_step6_``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import ontology  # noqa: E402
from database import postgis_db  # noqa: E402

TEST_PREFIX = "test_step6_"
TEST_BRANCH_A = f"{TEST_PREFIX}branch_a"
TEST_BRANCH_B = f"{TEST_PREFIX}branch_b"
TEST_OBJECT_A = f"{TEST_PREFIX}object_a"
TEST_OBJECT_B = f"{TEST_PREFIX}object_b"
TEST_OBJECT_NEW = f"{TEST_PREFIX}object_new"
TEST_UNKNOWN = f"{TEST_PREFIX}unknown_label"


def _cleanup() -> None:
    with postgis_db.get_cursor(commit=True) as cur:
        # Reassign any test-tagged detection metadata back to Other so the
        # tests don't leave dangling references when branches are deleted.
        cur.execute(
            "UPDATE detections SET metadata = "
            "  (coalesce(metadata, '{}'::jsonb) - 'icon_key' - 'canonical_label' - 'ontology_object_id') "
            "  || jsonb_build_object('branch_id', 'Other', 'icon_key', 'circle_help') "
            "WHERE metadata->>'branch_id' LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
        cur.execute(
            "DELETE FROM ontology_unknown_labels WHERE label LIKE %s",
            (f"{TEST_PREFIX}%",),
        )
        cur.execute(
            "DELETE FROM ontology_objects WHERE id LIKE %s OR branch_id LIKE %s",
            (f"{TEST_PREFIX}%", f"{TEST_PREFIX}%"),
        )
        cur.execute(
            "DELETE FROM ontology_branches WHERE id LIKE %s",
            (f"{TEST_PREFIX}%",),
        )


@pytest.fixture()
def client():
    """A pre-authenticated TestClient. The /api/auth middleware gates every
    mutating verb, and we exercise PUT/POST/DELETE throughout this suite, so
    each fresh client logs in as the env admin first."""
    import os
    os.environ.setdefault("ADMIN_USERNAME", "admin")
    os.environ.setdefault("ADMIN_PASSWORD", "sentinel-dev-admin")
    os.environ.setdefault("SESSION_SECRET", "dev-session-secret-replace-in-production-with-openssl-rand-hex-32")
    tc = TestClient(main.app)
    r = tc.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert r.status_code == 200, f"test fixture login failed: {r.text}"
    return tc


@pytest.fixture(autouse=True)
def _reset_state():
    _cleanup()
    ontology.invalidate_cache()
    yield
    _cleanup()
    ontology.invalidate_cache()


def _seed_unknown(label: str) -> None:
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO ontology_unknown_labels (label, layer) VALUES (%s, %s) "
            "ON CONFLICT (label) DO UPDATE SET count = ontology_unknown_labels.count + 1, "
            "  last_seen = now()",
            (label, "test_layer"),
        )


def _create_branch(client: TestClient, **overrides) -> dict:
    body = {
        "id": TEST_BRANCH_A,
        "label": "Step 6 Branch A",
        "color": "#abcdef",
        "short": "S6A",
        "icon_key": "tank",
        "matchers": [],
        "sensors": ["optical", "sar"],
        "order_index": 100,
    }
    body.update(overrides)
    resp = client.post("/api/ontology/branches", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_object(client: TestClient, **overrides) -> dict:
    body = {
        "id": TEST_OBJECT_A,
        "branch_id": TEST_BRANCH_A,
        "label": "Step 6 Object A",
        "prompt": "step 6 widget",
        "sensors": ["optical"],
        "icon_key": "tank",
        "order_index": 1,
    }
    body.update(overrides)
    resp = client.post("/api/ontology/objects", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. GET /api/ontology
# ---------------------------------------------------------------------------
def test_get_ontology_returns_branches_and_version(client):
    resp = client.get("/api/ontology")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body.get("version_id"), int)
    assert isinstance(body.get("branches"), list)
    assert len(body["branches"]) > 0
    # Each top-level branch should have objects + children
    for b in body["branches"]:
        assert "id" in b
        assert "objects" in b and isinstance(b["objects"], list)
        assert "children" in b and isinstance(b["children"], list)


# ---------------------------------------------------------------------------
# 2. GET /api/ontology?sensor=sar
# ---------------------------------------------------------------------------
def test_get_ontology_filtered_by_sensor(client):
    _create_branch(client, sensors=["sar"])
    _create_object(client, sensors=["sar"])

    resp = client.get("/api/ontology?sensor=sar")
    assert resp.status_code == 200
    body = resp.json()

    def find_branch(branches, bid):
        for b in branches:
            if b["id"] == bid:
                return b
            f = find_branch(b.get("children") or [], bid)
            if f:
                return f
        return None

    found = find_branch(body["branches"], TEST_BRANCH_A)
    assert found is not None
    assert any(o["id"] == TEST_OBJECT_A for o in found["objects"])

    # Now request optical-only — our object should be filtered out.
    resp2 = client.get("/api/ontology?sensor=optical")
    assert resp2.status_code == 200
    body2 = resp2.json()
    found2 = find_branch(body2["branches"], TEST_BRANCH_A)
    # Branch may be present (it has 'optical' in branch.sensors) but with no objects
    if found2:
        assert all(o["id"] != TEST_OBJECT_A for o in found2["objects"])


# ---------------------------------------------------------------------------
# 3. GET /api/ontology/version
# ---------------------------------------------------------------------------
def test_get_ontology_version(client):
    resp = client.get("/api/ontology/version")
    assert resp.status_code == 200
    assert isinstance(resp.json().get("version_id"), int)


# ---------------------------------------------------------------------------
# 4. GET /api/ontology/default-prompts
# ---------------------------------------------------------------------------
def test_get_default_prompts(client):
    _create_branch(client)
    _create_object(client, prompt="step 6 sar prompt", sensors=["sar"])
    _create_object(client, id=TEST_OBJECT_B, prompt="step 6 optical prompt", sensors=["optical"])

    all_resp = client.get("/api/ontology/default-prompts")
    assert all_resp.status_code == 200
    all_prompts = all_resp.json()["prompts"]
    assert "step 6 sar prompt" in all_prompts
    assert "step 6 optical prompt" in all_prompts

    sar_resp = client.get("/api/ontology/default-prompts?sensor=sar")
    sar_prompts = sar_resp.json()["prompts"]
    assert "step 6 sar prompt" in sar_prompts
    assert "step 6 optical prompt" not in sar_prompts
    assert set(sar_prompts).issubset(set(all_prompts))


# ---------------------------------------------------------------------------
# 5. POST /api/ontology/branches creates + bumps version; duplicate -> 409
# ---------------------------------------------------------------------------
def test_create_branch_bumps_version_and_409_on_duplicate(client):
    v0 = client.get("/api/ontology/version").json()["version_id"]
    created = _create_branch(client)
    assert created["id"] == TEST_BRANCH_A
    v1 = client.get("/api/ontology/version").json()["version_id"]
    assert v1 > v0

    # Duplicate
    dup = client.post("/api/ontology/branches", json={
        "id": TEST_BRANCH_A,
        "label": "dup",
        "icon_key": "x",
    })
    assert dup.status_code == 409


# ---------------------------------------------------------------------------
# 6. PATCH /api/ontology/branches/{id}
# ---------------------------------------------------------------------------
def test_patch_branch_updates_and_bumps(client):
    _create_branch(client)
    v0 = client.get("/api/ontology/version").json()["version_id"]
    resp = client.patch(f"/api/ontology/branches/{TEST_BRANCH_A}", json={"label": "Updated S6A"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Updated S6A"
    v1 = client.get("/api/ontology/version").json()["version_id"]
    assert v1 > v0

    # 404
    resp404 = client.patch(f"/api/ontology/branches/{TEST_PREFIX}does_not_exist", json={"label": "x"})
    assert resp404.status_code == 404


# ---------------------------------------------------------------------------
# 7. DELETE /api/ontology/branches/{id}
# ---------------------------------------------------------------------------
def test_delete_branch_with_detections_blocked_then_force(client):
    _create_branch(client)

    # Insert a detection referencing this branch via a real satellite_pass.
    import uuid as _uuid
    fp = f"/tmp/test_step6_{_uuid.uuid4().hex}.tif"
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute(
            "INSERT INTO satellite_passes (name, acquisition_time, footprint, file_path, metadata) "
            "VALUES (%s, now(), "
            "  ST_Multi(ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326)), "
            "  %s, %s::jsonb) RETURNING id",
            ("test_step6_pass", fp, "{}"),
        )
        pass_id = cur.fetchone()["id"]
        cur.execute(
            "INSERT INTO detections (pass_id, class, confidence, geom, centroid, metadata) "
            "VALUES (%s, %s, %s, "
            "  ST_GeomFromText('POLYGON((0 0, 0 1, 1 1, 1 0, 0 0))', 4326), "
            "  ST_SetSRID(ST_MakePoint(0.5, 0.5), 4326), %s::jsonb) RETURNING id",
            (
                pass_id, "test_step6_class", 0.9,
                json.dumps({"branch_id": TEST_BRANCH_A, "icon_key": "tank"}),
            ),
        )
        det_id = cur.fetchone()["id"]

    try:
        # Default (force=false) should block.
        resp = client.delete(f"/api/ontology/branches/{TEST_BRANCH_A}")
        assert resp.status_code == 409
        body = resp.json()
        # detail may be a dict
        detail = body.get("detail", body)
        if isinstance(detail, dict):
            assert detail.get("affected_detections", 0) >= 1

        # force=true should succeed.
        resp2 = client.delete(f"/api/ontology/branches/{TEST_BRANCH_A}?force=true")
        assert resp2.status_code == 200
        out = resp2.json()
        assert out["deleted"] == 1
        assert out["affected_detections"] >= 1

        # Verify reassignment.
        with postgis_db.get_cursor() as cur:
            cur.execute("SELECT metadata->>'branch_id' AS bid FROM detections WHERE id = %s", (det_id,))
            row = cur.fetchone()
            assert row["bid"] == "Other"

        # Verify the unknown-label was logged.
        with postgis_db.get_cursor() as cur:
            cur.execute(
                "SELECT count FROM ontology_unknown_labels WHERE label = %s",
                ("test_step6_class",),
            )
            row = cur.fetchone()
            assert row is not None and int(row["count"]) >= 1
    finally:
        with postgis_db.get_cursor(commit=True) as cur:
            cur.execute("DELETE FROM detections WHERE id = %s", (det_id,))
            cur.execute("DELETE FROM satellite_passes WHERE id = %s", (pass_id,))
            cur.execute("DELETE FROM ontology_unknown_labels WHERE label = %s", ("test_step6_class",))


# ---------------------------------------------------------------------------
# 8. POST /api/ontology/objects
# ---------------------------------------------------------------------------
def test_create_object_validates_branch(client):
    _create_branch(client)
    v0 = client.get("/api/ontology/version").json()["version_id"]
    obj = _create_object(client)
    assert obj["id"] == TEST_OBJECT_A
    v1 = client.get("/api/ontology/version").json()["version_id"]
    assert v1 > v0

    # Bad branch
    bad = client.post("/api/ontology/objects", json={
        "id": TEST_OBJECT_B,
        "branch_id": "test_step6_does_not_exist",
        "label": "x",
        "prompt": "x",
    })
    assert bad.status_code == 400


# ---------------------------------------------------------------------------
# 9. PATCH /api/ontology/objects/{id}
# ---------------------------------------------------------------------------
def test_patch_object_updates_and_bumps(client):
    _create_branch(client)
    _create_object(client)
    v0 = client.get("/api/ontology/version").json()["version_id"]
    resp = client.patch(f"/api/ontology/objects/{TEST_OBJECT_A}", json={"label": "Updated S6 Obj"})
    assert resp.status_code == 200
    assert resp.json()["label"] == "Updated S6 Obj"
    v1 = client.get("/api/ontology/version").json()["version_id"]
    assert v1 > v0

    resp404 = client.patch(f"/api/ontology/objects/{TEST_PREFIX}nope", json={"label": "x"})
    assert resp404.status_code == 404


# ---------------------------------------------------------------------------
# 10. DELETE /api/ontology/objects/{id}
# ---------------------------------------------------------------------------
def test_delete_object_hard_delete_bumps(client):
    _create_branch(client)
    _create_object(client)
    v0 = client.get("/api/ontology/version").json()["version_id"]
    resp = client.delete(f"/api/ontology/objects/{TEST_OBJECT_A}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 1
    v1 = client.get("/api/ontology/version").json()["version_id"]
    assert v1 > v0

    resp404 = client.delete(f"/api/ontology/objects/{TEST_PREFIX}nope")
    assert resp404.status_code == 404


# ---------------------------------------------------------------------------
# 11. GET /api/ontology/unknown-labels
# ---------------------------------------------------------------------------
def test_get_unknown_labels(client):
    _seed_unknown(TEST_UNKNOWN)
    resp = client.get("/api/ontology/unknown-labels?limit=1000")
    assert resp.status_code == 200
    labels = resp.json()["unknown_labels"]
    assert any(r["label"] == TEST_UNKNOWN for r in labels)


# ---------------------------------------------------------------------------
# 12. POST /api/ontology/unknown-labels/{label}/assign with object_id
# ---------------------------------------------------------------------------
def test_assign_unknown_with_existing_object(client):
    _create_branch(client)
    _create_object(client)
    _seed_unknown(TEST_UNKNOWN)

    # Pre-assign: row exists in ontology_unknown_labels
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM ontology_unknown_labels WHERE label = %s",
            (TEST_UNKNOWN,),
        )
        assert cur.fetchone()["c"] == 1

    resp = client.post(
        f"/api/ontology/unknown-labels/{TEST_UNKNOWN}/assign",
        json={"branch_id": TEST_BRANCH_A, "object_id": TEST_OBJECT_A},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_to_branch"] == TEST_BRANCH_A
    assert body["created_object_id"] is None
    assert body["removed_from_queue"] is True

    # Post-assign: row is gone
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM ontology_unknown_labels WHERE label = %s",
            (TEST_UNKNOWN,),
        )
        assert cur.fetchone()["c"] == 0


# ---------------------------------------------------------------------------
# 13. POST /api/ontology/unknown-labels/{label}/assign with create_object
# ---------------------------------------------------------------------------
def test_assign_unknown_with_create_object(client):
    _create_branch(client)
    _seed_unknown(TEST_UNKNOWN)

    # Pre-assign: row exists in ontology_unknown_labels
    with postgis_db.get_cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS c FROM ontology_unknown_labels WHERE label = %s",
            (TEST_UNKNOWN,),
        )
        assert cur.fetchone()["c"] == 1

    resp = client.post(
        f"/api/ontology/unknown-labels/{TEST_UNKNOWN}/assign",
        json={
            "branch_id": TEST_BRANCH_A,
            "create_object": {
                "id": TEST_OBJECT_NEW,
                "label": "New Object",
                "prompt": "new prompt",
                "icon_key": "tank",
                "sensors": ["optical"],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["assigned_to_branch"] == TEST_BRANCH_A
    assert body["created_object_id"] == TEST_OBJECT_NEW
    assert body["removed_from_queue"] is True

    with postgis_db.get_cursor() as cur:
        cur.execute("SELECT id FROM ontology_objects WHERE id = %s", (TEST_OBJECT_NEW,))
        assert cur.fetchone() is not None
        # Post-assign: row is gone from queue
        cur.execute(
            "SELECT COUNT(*) AS c FROM ontology_unknown_labels WHERE label = %s",
            (TEST_UNKNOWN,),
        )
        assert cur.fetchone()["c"] == 0


def test_assign_unknown_rejects_both_object_id_and_create_object(client):
    _create_branch(client)
    _create_object(client)
    _seed_unknown(TEST_UNKNOWN)

    resp = client.post(
        f"/api/ontology/unknown-labels/{TEST_UNKNOWN}/assign",
        json={
            "branch_id": TEST_BRANCH_A,
            "object_id": TEST_OBJECT_A,
            "create_object": {"label": "x", "prompt": "y"},
        },
    )
    assert resp.status_code == 400
