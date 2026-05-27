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


# --- WS session heartbeat (TTL re-validation) -----------------------------


def test_ws_closes_when_session_invalidates_at_heartbeat(client, monkeypatch):
    """The WS loop re-decodes the cached session cookie every
    _HEARTBEAT_SECONDS. If the cookie no longer decodes, the WS closes
    with 1008 instead of continuing to leak events to an expired session."""
    import routers.ws as ws_module
    from starlette.websockets import WebSocketDisconnect

    _login(client)
    # 1s heartbeat so the test doesn't sit for the production 60s.
    monkeypatch.setattr(ws_module, "_HEARTBEAT_SECONDS", 1)

    # Patch decode_session_cookie *in the module that owns the call site* so
    # the first call (at handshake) succeeds and the second call (at the
    # heartbeat tick) returns None — simulating TTL expiry mid-connection.
    real_decode = ws_module.decode_session_cookie
    calls = {"n": 0}

    def fake_decode(token):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_decode(token)
        return None

    monkeypatch.setattr(ws_module, "decode_session_cookie", fake_decode)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws?topic=identifications") as ws:
            ws.receive_json()  # initial "connected" message
            # Wait for the heartbeat to tick and close us. The loop polls
            # pubsub with timeout=1.0 + sleep(0.1) + heartbeat=1s, so we
            # need at most ~2-3s to observe the close.
            import time
            deadline = time.time() + 8
            while time.time() < deadline:
                ws.receive_text()  # will raise WebSocketDisconnect on close
    assert exc_info.value.code == 1008
    assert calls["n"] >= 2  # handshake + at least one heartbeat


def test_ws_stays_open_while_session_valid(client, monkeypatch):
    """Sanity check: as long as the cached cookie keeps decoding, the WS
    survives multiple heartbeats and a fresh event still arrives."""
    import json
    import time
    import routers.ws as ws_module
    from events import publish_event

    _login(client)
    monkeypatch.setattr(ws_module, "_HEARTBEAT_SECONDS", 1)

    with client.websocket_connect("/ws?topic=identifications") as ws:
        first = ws.receive_json()
        assert first["type"] == "connected"
        # Sleep across two heartbeat ticks
        time.sleep(2.5)
        # Publish a synthetic event — it must still reach the still-open WS
        publish_event("identifications", {"type": "test_ping", "n": 1})
        # The loop polls pubsub every ~1s, give it a beat
        deadline = time.time() + 5
        received = None
        while time.time() < deadline:
            try:
                msg = ws.receive_text(timeout=1.0)
            except TypeError:
                # starlette TestClient receive_text doesn't take timeout on
                # all versions — fall back to blocking receive with overall
                # deadline guard.
                msg = ws.receive_text()
            if msg:
                payload = json.loads(msg)
                if payload.get("type") == "test_ping":
                    received = payload
                    break
        assert received is not None and received["n"] == 1
