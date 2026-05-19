"""Integration tests for object_details + manual detections + soft-delete.

Run with:
    POSTGIS_URI=postgresql://sentinel:sentinel@localhost:5432/sentinel \
      SESSION_SECRET=test-secret-1234567890abcdef \
      ADMIN_USERNAME=test-admin ADMIN_PASSWORD=test-admin-pass \
      python -m pytest backend/tests/test_object_details.py -v

Touches PostGIS. Cleans up its rows on teardown.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.fixture(scope="module", autouse=True)
def _setup_env() -> None:
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


def _square_polygon(lon: float, lat: float, size: float = 0.001) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [[
            [lon - size, lat - size],
            [lon + size, lat - size],
            [lon + size, lat + size],
            [lon - size, lat + size],
            [lon - size, lat - size],
        ]],
    }


def test_manual_detection_create_edit_delete(client):
    _login(client)

    # 1. Create
    create = client.post(
        "/api/detections/manual",
        json={
            "geometry": _square_polygon(32.1, 34.7),
            "object_class": "test_destroyer",
            "designation": "Test-DDG-001",
            "military_classification": "AAW · Type-052D",
            "threat_level": "high",
            "affiliation": "hostile",
            "notes": "operator-drawn under integration test",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    det_id = body["id"]
    assert body["source"] == "operator"
    assert body["threat_level"] == "high"
    assert body["affiliation"] == "hostile"
    # Centroid round-trips.
    assert abs(body["lat"] - 34.7) < 1e-6
    assert abs(body["lon"] - 32.1) < 1e-6

    # 2. Read details — auto-seeded from the create payload.
    read = client.get(f"/api/detections/{det_id}/details")
    assert read.status_code == 200
    details = read.json()["details"]
    assert details["designation"] == "Test-DDG-001"
    assert details["threat_level"] == "high"
    assert details["affiliation"] == "hostile"

    # 3. Update — change threat to critical.
    upd = client.put(
        f"/api/detections/{det_id}/details",
        json={"threat_level": "critical", "notes": "escalated"},
    )
    assert upd.status_code == 200
    updated = upd.json()["details"]
    assert updated["threat_level"] == "critical"
    # Existing fields preserved by COALESCE on update.
    assert updated["designation"] == "Test-DDG-001"
    assert updated["notes"] == "escalated"

    # 4. Delete — admin can delete operator boxes.
    delete = client.delete(f"/api/detections/{det_id}")
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True

    # 5. Subsequent reads 404.
    miss = client.get(f"/api/detections/{det_id}/details")
    assert miss.status_code == 404


def test_threat_validation_rejects_garbage(client):
    _login(client)
    bad = client.post(
        "/api/detections/manual",
        json={
            "geometry": _square_polygon(0, 0),
            "object_class": "test",
            "threat_level": "bogus-value",
        },
    )
    assert bad.status_code == 400, bad.text


def test_manual_detection_requires_polygon(client):
    _login(client)
    bad = client.post(
        "/api/detections/manual",
        json={"geometry": {"type": "Point", "coordinates": [0, 0]}, "object_class": "test"},
    )
    assert bad.status_code == 400, bad.text


def test_details_404_for_unknown_detection(client):
    _login(client)
    resp = client.get("/api/detections/9999999/details")
    assert resp.status_code == 404
