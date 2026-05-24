"""Unit tests for the /api/operational-entities router."""

from __future__ import annotations

import importlib
import json
import sys
import types
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Result:
    def __init__(self, records): self._records = list(records)
    def single(self): return self._records[0] if self._records else None
    def __iter__(self): return iter(self._records)


def _install_stubs(monkeypatch, *, postgis_fetchone=None, postgis_fetchall=None, neo4j_records=None):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchone = MagicMock(side_effect=postgis_fetchone or [])
    cursor.fetchall = MagicMock(side_effect=postgis_fetchall or [])
    cursor_cm = MagicMock(); cursor_cm.__enter__ = MagicMock(return_value=cursor); cursor_cm.__exit__ = MagicMock(return_value=False)
    postgis = MagicMock(); postgis.get_cursor = MagicMock(return_value=cursor_cm)

    session = MagicMock()
    session.run = MagicMock(side_effect=lambda *a, **k: _Result(neo4j_records or []))
    session_cm = MagicMock(); session_cm.__enter__ = MagicMock(return_value=session); session_cm.__exit__ = MagicMock(return_value=False)
    db_stub = MagicMock(); db_stub.get_session = MagicMock(return_value=session_cm)

    database_module = types.ModuleType("database")
    database_module.db = db_stub
    database_module.postgis_db = postgis
    monkeypatch.setitem(sys.modules, "database", database_module)
    return cursor, session


def _load(monkeypatch, **kwargs):
    # Stub platform_schema to skip real DB setup.
    ps = types.ModuleType("platform_schema")
    ps.ensure_platform_tables = MagicMock()
    monkeypatch.setitem(sys.modules, "platform_schema", ps)
    _install_stubs(monkeypatch, **kwargs)
    for name in ("routers.operational_entities", "graph_writes"):
        sys.modules.pop(name, None)
    mod = importlib.import_module("routers.operational_entities")
    app = FastAPI()
    app.include_router(mod.router)
    return TestClient(app)


def test_create_vessel_projects_to_graph(monkeypatch):
    row = {
        "id": "vessel-1", "kind": "vessel", "name": "Black Pearl",
        "callsign": None, "hull": None, "entity_class": None,
        "unit_id": None, "operates_from_base_id": None,
        "metadata": {"flag": "BB"}, "created_by": None, "created_at": "2026-05-24T00:00:00Z",
    }
    client = _load(monkeypatch, postgis_fetchone=[row], neo4j_records=[{"rel_id": "r1"}])
    resp = client.post("/api/operational-entities", json={"kind": "vessel", "name": "Black Pearl", "metadata": {"flag": "BB"}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"]["id"] == "vessel-1"
    assert body["graph_written"] is True


def test_create_rejects_invalid_kind(monkeypatch):
    client = _load(monkeypatch)
    resp = client.post("/api/operational-entities", json={"kind": "spaceship", "name": "X"})
    assert resp.status_code == 400


def test_get_returns_404_when_missing(monkeypatch):
    client = _load(monkeypatch, postgis_fetchone=[None])
    resp = client.get("/api/operational-entities/nope")
    assert resp.status_code == 404


def test_list_filters_by_kind(monkeypatch):
    client = _load(monkeypatch, postgis_fetchall=[[]])
    resp = client.get("/api/operational-entities?kind=vessel&limit=10")
    assert resp.status_code == 200
    assert resp.json() == {"entities": [], "count": 0}


def test_list_rejects_invalid_kind(monkeypatch):
    client = _load(monkeypatch)
    resp = client.get("/api/operational-entities?kind=spaceship")
    assert resp.status_code == 400


def test_delete_removes_graph_mirror(monkeypatch):
    client = _load(
        monkeypatch,
        postgis_fetchone=[{"id": "v-1"}],
        neo4j_records=[{"removed": 1}],
    )
    resp = client.delete("/api/operational-entities/v-1")
    assert resp.status_code == 200
    assert resp.json()["graph_nodes_removed"] == 1


def test_same_as_writes_edge(monkeypatch):
    client = _load(monkeypatch, neo4j_records=[{"1": 1}])
    resp = client.post("/api/operational-entities/v-1/same-as/v-2", json={"analyst": "alice"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"success": True, "a": "v-1", "b": "v-2", "merged_by": "alice"}


def test_approve_candidate_creates_entity(monkeypatch):
    candidate_row = {
        "id": 5, "entity_kind": "vessel", "proposed_name": "Container Ship at Pier 7",
        "proposed_metadata": {"site_id": "aoi-7", "detection_class": "container_ship"},
    }
    entity_row = {
        "id": "container-ship-at-pier-7", "kind": "vessel",
        "name": "Container Ship at Pier 7",
        "callsign": None, "hull": None, "entity_class": None,
        "unit_id": None, "operates_from_base_id": None,
        "metadata": {"site_id": "aoi-7"}, "created_by": "alice",
        "created_at": "2026-05-24T00:00:00Z",
    }
    updated = {
        "id": 5, "entity_kind": "vessel", "proposed_name": "Container Ship at Pier 7",
        "status": "approved", "reviewed_by": "alice",
        "reviewed_at": "2026-05-24T00:00:00Z",
        "approved_entity_id": "container-ship-at-pier-7",
    }
    client = _load(
        monkeypatch,
        postgis_fetchone=[candidate_row, entity_row, updated],
        neo4j_records=[{"rel_id": "r1"}],
    )
    resp = client.post("/api/operational-entity-candidates/5/approve?analyst=alice")
    assert resp.status_code == 200
    body = resp.json()
    assert body["entity"]["id"] == "container-ship-at-pier-7"
    assert body["candidate"]["status"] == "approved"


def test_reject_candidate_returns_404_when_already_handled(monkeypatch):
    client = _load(monkeypatch, postgis_fetchone=[None])
    resp = client.post("/api/operational-entity-candidates/99/reject")
    assert resp.status_code == 404


def test_pending_same_as_lists_pairs(monkeypatch):
    # Two pending pairs returned by the Cypher.
    rows = [
        {
            "a_id": "vessel-1", "b_id": "vessel-2",
            "a_labels": ["Vessel", "Asset"], "b_labels": ["Vessel", "Asset"],
            "a_props": {"id": "vessel-1", "name": "Pearl"},
            "b_props": {"id": "vessel-2", "name": "Pearl II"},
            "score": 0.82, "source": "name_match", "created_at": "2026-05-24T00:00:00Z",
        },
    ]
    client = _load(monkeypatch, neo4j_records=rows)
    resp = client.get("/api/operational-entities/pending-same-as")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["pending"][0]["a"]["id"] == "vessel-1"
    assert body["pending"][0]["score"] == 0.82


def test_reject_pending_same_as_removes_edge(monkeypatch):
    client = _load(monkeypatch, neo4j_records=[{"removed": 1}])
    resp = client.post(
        "/api/operational-entities/pending-same-as/reject",
        json={"a_id": "vessel-1", "b_id": "vessel-2"},
    )
    assert resp.status_code == 200
    assert resp.json()["removed"] == 1


def test_reject_pending_same_as_returns_404_when_missing(monkeypatch):
    client = _load(monkeypatch, neo4j_records=[{"removed": 0}])
    resp = client.post(
        "/api/operational-entities/pending-same-as/reject",
        json={"a_id": "x", "b_id": "y"},
    )
    assert resp.status_code == 404


def test_merge_into_combines_rows(monkeypatch):
    a_row = {
        "id": "v1", "kind": "vessel", "name": "Black Pearl",
        "callsign": "PEAR-1", "hull": None, "entity_class": None,
        "unit_id": None, "operates_from_base_id": None,
        "metadata": {"flag": "BB"},
    }
    b_row = {
        "id": "v2", "kind": "vessel", "name": "Black Pearl II",
        "callsign": None, "hull": "BP-002", "entity_class": "container_ship",
        "unit_id": "unit-1", "operates_from_base_id": "aoi-7",
        "metadata": {"flag": "BB", "extra": True},
    }
    updated = {
        "id": "v2", "kind": "vessel", "name": "Black Pearl II",
        "callsign": "PEAR-1", "hull": "BP-002", "entity_class": "container_ship",
        "unit_id": "unit-1", "operates_from_base_id": "aoi-7",
        "metadata": {"flag": "BB", "extra": True},
        "created_by": None, "created_at": "2026-05-24T00:00:00Z",
    }
    # SELECT returns both rows via fetchall (one call), then UPDATE returns
    # `updated` (fetchone), then DELETE returns {id: 'v1'} (fetchone).
    client = _load(
        monkeypatch,
        postgis_fetchall=[[a_row, b_row]],
        postgis_fetchone=[updated, {"id": "v1"}],
        neo4j_records=[{"rel_id": "r1"}],
    )
    resp = client.post(
        "/api/operational-entities/v1/merge-into/v2",
        json={"resolutions": {"callsign": "a"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["merged_into"] == "v2"
    assert body["deleted"] == "v1"
    assert body["entity"]["callsign"] == "PEAR-1"


def test_merge_into_rejects_different_kinds(monkeypatch):
    a_row = {"id": "v1", "kind": "vessel", "name": "x", "callsign": None, "hull": None, "entity_class": None, "unit_id": None, "operates_from_base_id": None, "metadata": {}}
    b_row = {"id": "a1", "kind": "aircraft", "name": "y", "callsign": None, "hull": None, "entity_class": None, "unit_id": None, "operates_from_base_id": None, "metadata": {}}
    client = _load(monkeypatch, postgis_fetchall=[[a_row, b_row]])
    resp = client.post("/api/operational-entities/v1/merge-into/a1", json={"resolutions": {}})
    assert resp.status_code == 400


def test_merge_into_rejects_self(monkeypatch):
    client = _load(monkeypatch)
    resp = client.post("/api/operational-entities/v1/merge-into/v1", json={"resolutions": {}})
    assert resp.status_code == 400


def test_part_of_updates_unit_and_graph(monkeypatch):
    client = _load(
        monkeypatch,
        postgis_fetchone=[{"id": "v-1"}],
        neo4j_records=[{"rel_id": "r1"}],
    )
    resp = client.post("/api/operational-entities/v-1/part-of/u-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"success": True, "entity_id": "v-1", "unit_id": "u-1"}
