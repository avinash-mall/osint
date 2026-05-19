"""Tests for the auth module: env admin login, session cookies, gating, admin config endpoints.

These hit a real PostGIS DB to exercise the ``auth_config`` row and the
session cookie roundtrip. Run with:

    POSTGIS_URI=postgresql://sentinel:sentinel@localhost:5432/sentinel \
      SESSION_SECRET=test-secret-1234567890abcdef \
      ADMIN_USERNAME=admin ADMIN_PASSWORD=test-admin-pass \
      python -m pytest backend/tests/test_auth.py -v
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


def test_login_with_bad_credentials_returns_401(client):
    resp = client.post("/api/auth/login", json={"username": "nope", "password": "wrong"})
    assert resp.status_code == 401, resp.text


def test_login_with_admin_env_credentials_sets_cookie(client):
    resp = client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "admin"
    assert body["user"]["username"] == os.environ["ADMIN_USERNAME"]
    # FastAPI's TestClient stores the cookie on the client itself.
    assert "sentinel_session" in client.cookies


def test_me_returns_user_when_authenticated(client):
    client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_me_returns_401_without_cookie(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_logout_clears_cookie(client):
    client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    assert "sentinel_session" in client.cookies
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    # The Set-Cookie header for deletion should appear.
    set_cookie = resp.headers.get("set-cookie", "")
    assert "sentinel_session" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires" in set_cookie.lower()


def test_mutating_endpoint_blocked_without_session(client):
    resp = client.post(
        "/api/detections/manual",
        json={"geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]}, "object_class": "test"},
    )
    assert resp.status_code == 401, resp.text


def test_admin_auth_config_requires_admin(client):
    client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    resp = client.get("/api/admin/auth/config")
    assert resp.status_code == 200, resp.text
    config = resp.json()
    # Password is always masked on read.
    assert config.get("bind_password") in (None, "", "********")


def test_admin_auth_put_roundtrip(client):
    client.post(
        "/api/auth/login",
        json={"username": os.environ["ADMIN_USERNAME"], "password": os.environ["ADMIN_PASSWORD"]},
    )
    payload = {
        "enabled": False,
        "host": "ldap.example.com",
        "port": 636,
        "use_tls": True,
        "bind_dn": "cn=svc,dc=example,dc=com",
        "bind_password": "secret-123",
        "user_base_dn": "ou=People,dc=example,dc=com",
        "user_search_filter": "(uid={username})",
        "attr_username": "uid",
        "attr_displayname": "cn",
        "attr_email": "mail",
        "admin_group_dn": "",
    }
    resp = client.put("/api/admin/auth/config", json=payload)
    assert resp.status_code == 200, resp.text
    out = resp.json()
    assert out["config"]["host"] == "ldap.example.com"
    assert out["config"]["bind_password"] == "********"

    # Sending the masked password back should preserve the stored one.
    payload["host"] = "ldap-2.example.com"
    payload["bind_password"] = "********"
    resp = client.put("/api/admin/auth/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["config"]["host"] == "ldap-2.example.com"


def test_session_cookie_roundtrip_directly():
    """Sanity test the signing layer in isolation."""
    from auth import SessionUser, create_session_cookie, decode_session_cookie

    user = SessionUser(username="x", role="analyst", display_name="X")
    token = create_session_cookie(user)
    decoded = decode_session_cookie(token)
    assert decoded is not None
    assert decoded.username == "x"
    assert decoded.role == "analyst"


def test_session_cookie_rejects_tampered_token():
    from auth import decode_session_cookie

    assert decode_session_cookie("not-a-real-token") is None
    assert decode_session_cookie("") is None
