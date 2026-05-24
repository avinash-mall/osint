"""Unit tests for /api/admin/repeat-thresholds — offline via DB stubs."""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _install_stubs(monkeypatch, *, fetchone=None, fetchall=None):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchone = MagicMock(side_effect=fetchone or [])
    cursor.fetchall = MagicMock(side_effect=fetchall or [])
    cursor_cm = MagicMock(); cursor_cm.__enter__ = MagicMock(return_value=cursor); cursor_cm.__exit__ = MagicMock(return_value=False)
    postgis = MagicMock(); postgis.get_cursor = MagicMock(return_value=cursor_cm)
    db_module = types.ModuleType("database")
    db_module.db = MagicMock()
    db_module.postgis_db = postgis
    monkeypatch.setitem(sys.modules, "database", db_module)
    ps = types.ModuleType("platform_schema")
    ps.ensure_platform_tables = MagicMock()
    monkeypatch.setitem(sys.modules, "platform_schema", ps)
    return cursor


def _client(monkeypatch, **kwargs):
    _install_stubs(monkeypatch, **kwargs)
    sys.modules.pop("routers.admin_thresholds", None)
    mod = importlib.import_module("routers.admin_thresholds")
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app), mod


def test_create_threshold_inserts_and_returns_row(monkeypatch):
    row = {"id": 1, "kind": "base", "window_days": 14, "min_count": 3,
           "near_radius_m": 7500, "current": True, "notes": None,
           "created_at": "2026-05-24T00:00:00Z", "created_by": None}
    client, _ = _client(monkeypatch, fetchone=[row])
    resp = client.post("/api/admin/repeat-thresholds",
                       json={"kind": "base", "window_days": 14, "min_count": 3, "near_radius_m": 7500})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == 1
    assert body["near_radius_m"] == 7500


def test_create_rejects_invalid_kind(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.post("/api/admin/repeat-thresholds",
                       json={"kind": "spaceport", "window_days": 30, "min_count": 5, "near_radius_m": 5000})
    assert resp.status_code == 400


def test_list_filters_by_kind(monkeypatch):
    client, _ = _client(monkeypatch, fetchall=[[]])
    resp = client.get("/api/admin/repeat-thresholds?kind=facility")
    assert resp.status_code == 200
    assert resp.json() == {"thresholds": [], "count": 0}


def test_list_rejects_invalid_kind(monkeypatch):
    client, _ = _client(monkeypatch)
    resp = client.get("/api/admin/repeat-thresholds?kind=nope")
    assert resp.status_code == 400


def test_activate_marks_one_current_per_kind(monkeypatch):
    fetchone = [
        {"kind": "base"},                          # lookup
        {"id": 7, "kind": "base", "window_days": 14, "min_count": 3,
         "near_radius_m": 7500, "current": True},  # final update return
    ]
    client, _ = _client(monkeypatch, fetchone=fetchone)
    resp = client.put("/api/admin/repeat-thresholds/7/activate")
    assert resp.status_code == 200
    assert resp.json()["current"] is True


def test_activate_returns_404_when_missing(monkeypatch):
    client, _ = _client(monkeypatch, fetchone=[None])
    resp = client.put("/api/admin/repeat-thresholds/9999/activate")
    assert resp.status_code == 404


def test_delete_returns_404_when_missing(monkeypatch):
    client, _ = _client(monkeypatch, fetchone=[None])
    resp = client.delete("/api/admin/repeat-thresholds/9999")
    assert resp.status_code == 404


def test_get_current_threshold_returns_none_for_invalid_kind(monkeypatch):
    _, mod = _client(monkeypatch)
    assert mod.get_current_threshold("not-a-kind") is None


def test_get_current_threshold_returns_row(monkeypatch):
    fetchone = [{"window_days": 14, "min_count": 3, "near_radius_m": 7500}]
    _, mod = _client(monkeypatch, fetchone=fetchone)
    row = mod.get_current_threshold("base")
    assert row == {"window_days": 14, "min_count": 3, "near_radius_m": 7500}
