"""Tests for WebSocket auth + identification event publishing.

Covers:
  - publish_event called on approve/reject/identify with expected payload.
  - WS connection without session cookie is rejected with 1008.
  - WS connection with valid session cookie is accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

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
    import main
    return TestClient(main.app)


def _login(client) -> dict:
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.cookies


def test_ws_rejects_unauthenticated_connection(client):
    """No cookie → WS handshake closes with 1008."""
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?topic=identifications"):
            pass
    # 1008 = Policy Violation per WS RFC
    assert exc_info.value.code == 1008


def test_ws_accepts_authenticated_connection(client):
    """With a valid session cookie, WS handshake succeeds."""
    _login(client)
    with client.websocket_connect("/ws?topic=identifications") as ws:
        pass


# --- publish_event call-site tests (monkeypatch) ---------------------------


@pytest.fixture(scope="module")
def populated_ref():
    import numpy as np
    from platform_schema import ensure_reference_platform_tables
    from reference_platform_db import (
        upsert_reference_platform, insert_reference_chip, recompute_platform_centroids,
    )
    from database import postgis_db
    ensure_reference_platform_tables()
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM platform_identification_candidates WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM object_details WHERE source = 'detection' AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-wsev-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-wsev'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-wsev-%'")
        pid = upsert_reference_platform(cur, platform_name="pytest-wsev-Red", platform_family="WsevFam")
        for i in range(3):
            insert_reference_chip(
                cur, platform_id=pid, view_domain="overhead",
                source_dataset="pytest-wsev",
                chip_path=f"/tmp/pytest-wsev-red-{i}.png",
                embedding=np.full(1024, 1.0, dtype=np.float32),
                license_spdx="CC0-1.0",
            )
        recompute_platform_centroids(cur, platform_id=pid)
    yield {"red_id": pid}
    with postgis_db.get_cursor(commit=True) as cur:
        cur.execute("DELETE FROM platform_identification_candidates WHERE detection_id IN (SELECT id FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM object_details WHERE source = 'detection' AND source_id IN (SELECT id::text FROM detections WHERE class LIKE 'pytest-wsev-%')")
        cur.execute("DELETE FROM detections WHERE class LIKE 'pytest-wsev-%'")
        cur.execute("DELETE FROM reference_chips WHERE source_dataset = 'pytest-wsev'")
        cur.execute("DELETE FROM reference_platforms WHERE platform_name LIKE 'pytest-wsev-%'")


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


def test_approve_publishes_identification_approved(client, populated_ref):
    """Approving a candidate must publish_event('identifications', {type='identification_approved', ...})."""
    import numpy as np
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-approve")
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/identification-candidates/{cand_id}/approve")
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_approved"
    assert payload["detection_id"] == det_id
    assert payload["candidate_id"] == cand_id
    assert payload["reviewed_by"] == os.environ["ADMIN_USERNAME"]


def test_reject_publishes_identification_rejected(client, populated_ref):
    import numpy as np
    from database import postgis_db
    from reference_platform_db import attach_identification_candidates

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-reject")
    with postgis_db.get_cursor(commit=True) as cur:
        attach_identification_candidates(
            cur, detection_id=det_id,
            embedding=np.zeros(1024, dtype=np.float32),
            view_domain="overhead", auto_threshold=0.85, top_k=3,
        )
        cur.execute(
            "SELECT id FROM platform_identification_candidates WHERE detection_id = %s ORDER BY rank LIMIT 1",
            (det_id,),
        )
        cand_id = cur.fetchone()["id"]

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/identification-candidates/{cand_id}/reject")
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_rejected"
    assert payload["detection_id"] == det_id


def test_identify_publishes_identification_refreshed(client, populated_ref):
    """POST /api/detections/{id}/identify must publish a 'identification_refreshed' event."""
    import base64
    import numpy as np
    from database import postgis_db

    _login(client)
    det_id = _insert_fake_detection("pytest-wsev-identify")
    # Plant an embedding so identify doesn't 400
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

    published = []
    with patch("routers.reference_platforms.publish_event", side_effect=lambda topic, payload: published.append((topic, payload))):
        resp = client.post(f"/api/detections/{det_id}/identify", json={"view_domain": "overhead", "top_k": 3})
        assert resp.status_code == 200, resp.text

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "identifications"
    assert payload["type"] == "identification_refreshed"
    assert payload["detection_id"] == det_id
