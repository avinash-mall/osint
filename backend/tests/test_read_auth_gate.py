"""Unit tests for the session middleware's read gate (2026-06-12 hardening).

Unauthenticated GETs on /api/* must 401 before any handler (so no DB is
needed); the public allowlist (auth, health, deployment-mode, the
inference-service prompt feed) must pass through. Offline — no DB.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    os.environ.setdefault("SESSION_SECRET", "test-session-secret-please-replace-1234567890abcdef")
    import main

    # Allowlisted handlers may legitimately fail without a live DB; the gate
    # assertions only care about 401 vs not-401.
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "path",
    [
        "/api/detections",
        "/api/detections/geojson-lite",
        "/api/tracks/detections",
        "/api/fmv/clips",
        "/api/graph",
        "/api/imagery",
        "/api/aois",
        "/api/operational-entities",
        "/api/observations",
        "/api/timeline/events",
    ],
)
def test_reads_blocked_without_session(client, path):
    resp = client.get(path)
    assert resp.status_code == 401, f"{path}: {resp.status_code}"
    assert resp.json() == {"detail": "not authenticated"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/health",
        "/api/system/deployment-mode",
        "/api/auth/me",
        "/api/ontology/default-prompts",
    ],
)
def test_public_reads_pass_the_gate(client, path):
    resp = client.get(path)
    # /api/auth/me 401s from its own handler when unauthenticated, but the
    # middleware short-circuit body is distinguishable for the others.
    if path != "/api/auth/me":
        assert resp.status_code != 401, f"{path}: {resp.status_code} {resp.text}"


def test_authenticated_read_passes_middleware(client):
    from auth import SessionUser, create_session_cookie

    client.cookies.set(
        "sentinel_session",
        create_session_cookie(SessionUser(username="alice", role="analyst")),
    )
    try:
        resp = client.get("/api/detections")
        # Without a DB the handler may 500; the middleware must not 401.
        assert resp.status_code != 401, resp.text
    finally:
        client.cookies.delete("sentinel_session")


def test_mutations_still_blocked_without_session(client):
    resp = client.post("/api/aois", json={})
    assert resp.status_code == 401
