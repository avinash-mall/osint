"""Unit tests for the Phase 1 graph router endpoints — offline via db stubs."""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Result:
    """Iterable Neo4j result stub with `single()` support."""

    def __init__(self, records):
        self._records = list(records)
        self._iter = iter(self._records)

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


def _make_session_stub(run_side_effect):
    session = MagicMock()
    session.run = MagicMock(side_effect=run_side_effect)
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=session)
    cm.__exit__ = MagicMock(return_value=False)
    return session, cm


def _install_stubs(monkeypatch, *, neo4j_runs, postgis_runs=None):
    session, session_cm = _make_session_stub(neo4j_runs)
    db_stub = MagicMock()
    db_stub.get_session = MagicMock(return_value=session_cm)

    cursor = MagicMock()
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cursor)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    postgis_stub = MagicMock()
    postgis_stub.get_cursor = MagicMock(return_value=cursor_cm)
    if postgis_runs is not None:
        cursor.execute = MagicMock(side_effect=postgis_runs.get("execute"))
        cursor.fetchall = MagicMock(side_effect=postgis_runs.get("fetchall"))
        cursor.fetchone = MagicMock(side_effect=postgis_runs.get("fetchone"))

    database_module = types.ModuleType("database")
    database_module.db = db_stub
    database_module.postgis_db = postgis_stub
    monkeypatch.setitem(sys.modules, "database", database_module)
    return session, cursor


def _load_router():
    for name in ("routers.graph", "graph_writes", "schemas"):
        sys.modules.pop(name, None)
    return importlib.import_module("routers.graph")


def _client(monkeypatch, **kwargs):
    """Build a FastAPI app with the freshly-loaded graph router mounted."""
    _install_stubs(monkeypatch, **kwargs)
    router = _load_router().router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/graph/path
# ---------------------------------------------------------------------------


def test_path_endpoint_returns_empty_when_no_path(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.post("/api/graph/path", json={"from_id": "x", "to_id": "y", "max_depth": 3})
    assert resp.status_code == 200
    assert resp.json() == {"paths": [], "max_depth": 3, "count": 0}


def test_path_endpoint_caps_max_depth(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.post("/api/graph/path", json={"from_id": "x", "to_id": "y", "max_depth": 99})
    assert resp.status_code == 422  # Pydantic rejects >8


# ---------------------------------------------------------------------------
# /api/graph/investigation
# ---------------------------------------------------------------------------


def test_investigation_returns_empty_when_no_records(monkeypatch):
    # The Cypher returns a single record with empty nodes/rels.
    record = {"nodes": [], "rels": []}

    def neo4j_side_effect(*_a, **_kw):
        return _Result([record])

    client = _client(monkeypatch, neo4j_runs=neo4j_side_effect)
    resp = client.get("/api/graph/investigation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["nodes"] == []
    assert body["links"] == []
    assert body["limit"] == 150


def test_investigation_rejects_invalid_time_window(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.get("/api/graph/investigation?time_start=not-a-date")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# /api/graph/site-composition
# ---------------------------------------------------------------------------


def test_site_composition_returns_404_when_base_missing(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.get("/api/graph/site-composition/missing-id")
    assert resp.status_code == 404


def test_site_composition_returns_payload_for_valid_base(monkeypatch):
    base_record = {
        "b": MagicMock(),
        "labels": ["Base"],
        "props": {"aoi_postgis_id": 7, "name": "Test Base"},
    }
    observed_records: list = []  # no OBSERVED_AT links yet (Phase 1)
    calls = iter([_Result([base_record]), _Result(observed_records)])

    def neo4j_side_effect(*_a, **_kw):
        return next(calls)

    postgis_runs = {
        "execute": [None],
        "fetchall": [[{"class": "container_ship", "count": 12, "last_seen": "2026-05-01T00:00:00Z"}]],
        "fetchone": [],
    }
    client = _client(monkeypatch, neo4j_runs=neo4j_side_effect, postgis_runs=postgis_runs)

    resp = client.get("/api/graph/site-composition/elem-1?radius_m=2500&recent_days=14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_id"] == "elem-1"
    assert body["aoi_postgis_id"] == 7
    assert body["recent_detections"] == [
        {"class": "container_ship", "count": 12, "last_seen": "2026-05-01T00:00:00Z"}
    ]
    # Phase 1 placeholders are present and empty.
    assert body["vessels"] == []
    assert body["fmv_clips"] == []
    assert body["reports"] == []


# ---------------------------------------------------------------------------
# /api/graph/candidate-edges/{id}/promote
# ---------------------------------------------------------------------------


def test_promote_returns_404_when_postgis_row_missing(monkeypatch):
    postgis_runs = {
        "execute": [None],
        "fetchall": [],
        "fetchone": [None],
    }
    client = _client(
        monkeypatch,
        neo4j_runs=lambda *a, **kw: _Result([]),
        postgis_runs=postgis_runs,
    )
    resp = client.post("/api/graph/candidate-edges/9999/promote", json={})
    assert resp.status_code == 404


def test_promote_returns_candidate_and_graph_payload(monkeypatch):
    updated = {
        "id": 7,
        "detection_id": 42,
        "target_id": "t-1",
        "target_name": "Target One",
        "score": 0.8,
        "reason": "match",
        "status": "approved",
        "evidence": {},
        "reviewed_by": "alice",
        "reviewed_at": "2026-05-24T00:00:00Z",
        "created_at": "2026-05-23T00:00:00Z",
        "updated_at": "2026-05-24T00:00:00Z",
    }
    promote_record = {"detection_id": 42, "target_id": "t-1"}

    postgis_runs = {
        "execute": [None],
        "fetchall": [],
        "fetchone": [updated],
    }
    client = _client(
        monkeypatch,
        neo4j_runs=lambda *a, **kw: _Result([promote_record]),
        postgis_runs=postgis_runs,
    )

    resp = client.post("/api/graph/candidate-edges/7/promote", json={"analyst": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["candidate"]["id"] == 7
    assert body["graph"] == {"detection_id": 42, "target_id": "t-1"}
