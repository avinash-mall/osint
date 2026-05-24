"""Unit tests for the AOI router + Base/LaunchPoint/Facility projection.

Stays offline via db + postgis stubs; verifies the projector is only invoked
when ``metadata.aoi_kind`` is set, and that the mirror is removed when the
AOI is deleted.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Result:
    def __init__(self, records):
        self._records = list(records)

    def __iter__(self):
        return iter(self._records)

    def single(self):
        return self._records[0] if self._records else None


def _install_stubs(monkeypatch, *, postgis_fetchone=None, postgis_fetchall=None, neo4j_records=None):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchone = MagicMock(side_effect=postgis_fetchone or [])
    cursor.fetchall = MagicMock(side_effect=postgis_fetchall or [])
    cursor_cm = MagicMock()
    cursor_cm.__enter__ = MagicMock(return_value=cursor)
    cursor_cm.__exit__ = MagicMock(return_value=False)
    postgis = MagicMock()
    postgis.get_cursor = MagicMock(return_value=cursor_cm)

    session = MagicMock()
    session.run = MagicMock(side_effect=lambda *a, **k: _Result(neo4j_records or []))
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=session)
    session_cm.__exit__ = MagicMock(return_value=False)
    db_stub = MagicMock()
    db_stub.get_session = MagicMock(return_value=session_cm)

    database_module = types.ModuleType("database")
    database_module.db = db_stub
    database_module.postgis_db = postgis
    monkeypatch.setitem(sys.modules, "database", database_module)
    return cursor, session


def _platform_schema_stub(monkeypatch):
    stub = types.ModuleType("platform_schema")
    stub.ensure_platform_tables = MagicMock()
    monkeypatch.setitem(sys.modules, "platform_schema", stub)


def _load_router():
    for name in ("routers.aois", "graph_writes"):
        sys.modules.pop(name, None)
    return importlib.import_module("routers.aois")


def _client(monkeypatch, **kwargs):
    _platform_schema_stub(monkeypatch)
    cursor, session = _install_stubs(monkeypatch, **kwargs)
    router = _load_router().router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), cursor, session


_GEOJSON_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
}


def test_create_aoi_projects_to_graph_when_aoi_kind_set(monkeypatch):
    insert_row = {
        "id": 17,
        "name": "Test Base",
        "priority": "Medium",
        "metadata": {"aoi_kind": "base"},
        "default_allegiance": "unknown",
        "created_at": "2026-05-24T00:00:00Z",
        "centroid_lat": 0.5,
        "centroid_lon": 0.5,
    }
    client, cursor, session = _client(
        monkeypatch,
        postgis_fetchone=[insert_row],
        neo4j_records=[{"element_id": "elem-17"}],
    )

    resp = client.post(
        "/api/aois",
        json={
            "name": "Test Base",
            "geometry": _GEOJSON_POLYGON,
            "metadata": {"aoi_kind": "base"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["aoi"]["id"] == 17
    assert body["graph_node_id"] == "elem-17"

    # Last Neo4j call should be the MERGE for the Base node.
    cypher, params = session.run.call_args.args
    assert "MERGE (n:Base" in cypher
    assert params["id"] == "aoi-17"
    assert params["aoi_postgis_id"] == 17


def test_create_aoi_skips_projection_when_aoi_kind_absent(monkeypatch):
    insert_row = {
        "id": 18,
        "name": "Generic AOI",
        "priority": "Medium",
        "metadata": {},  # no aoi_kind
        "default_allegiance": "unknown",
        "created_at": "2026-05-24T00:00:00Z",
        "centroid_lat": 0.0,
        "centroid_lon": 0.0,
    }
    client, cursor, session = _client(
        monkeypatch,
        postgis_fetchone=[insert_row],
        neo4j_records=[],
    )

    resp = client.post(
        "/api/aois",
        json={"name": "Generic AOI", "geometry": _GEOJSON_POLYGON, "metadata": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["graph_node_id"] is None
    session.run.assert_not_called()  # no Neo4j MERGE


def test_create_aoi_rejects_non_polygon(monkeypatch):
    client, _, _ = _client(monkeypatch, postgis_fetchone=[], neo4j_records=[])
    resp = client.post(
        "/api/aois",
        json={"name": "x", "geometry": {"type": "Point", "coordinates": [0, 0]}},
    )
    assert resp.status_code == 400


def test_delete_aoi_removes_neo4j_mirror(monkeypatch):
    client, cursor, session = _client(
        monkeypatch,
        postgis_fetchone=[{"id": 42}],
        neo4j_records=[{"removed": 1}],
    )

    resp = client.delete("/api/aois/42")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 42
    assert body["graph_nodes_removed"] == 1
    cypher, params = session.run.call_args.args
    assert "DETACH DELETE n" in cypher
    assert params["aoi_postgis_id"] == 42


def test_patch_aoi_clears_graph_mirror_when_kind_removed(monkeypatch):
    updated_row = {
        "id": 9,
        "name": "Demoted AOI",
        "priority": "Medium",
        "metadata": {},  # aoi_kind cleared
        "default_allegiance": "unknown",
        "created_at": "2026-05-24T00:00:00Z",
        "centroid_lat": 0.0,
        "centroid_lon": 0.0,
    }
    client, cursor, session = _client(
        monkeypatch,
        postgis_fetchone=[updated_row],
        neo4j_records=[{"removed": 1}],
    )

    resp = client.patch("/api/aois/9", json={"metadata": {}})
    assert resp.status_code == 200
    cypher, _ = session.run.call_args.args
    assert "DETACH DELETE n" in cypher  # delete path taken


def test_get_aoi_returns_geojson_geometry(monkeypatch):
    row = {
        "id": 3,
        "name": "X",
        "priority": "Medium",
        "metadata": {"aoi_kind": "facility"},
        "default_allegiance": "unknown",
        "created_at": "2026-05-24T00:00:00Z",
        "geometry": json.dumps(_GEOJSON_POLYGON),
        "centroid_lat": 0.5,
        "centroid_lon": 0.5,
    }
    client, _, _ = _client(monkeypatch, postgis_fetchone=[row], neo4j_records=[])
    resp = client.get("/api/aois/3")
    assert resp.status_code == 200
    assert resp.json()["geometry"]["type"] == "Polygon"
