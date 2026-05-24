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
    # Phase 4.C added a NEAR-count query + (when zero) skips the NEAR-group
    # query, then runs OBSERVED query. So: base, near_count, observed.
    calls = iter([
        _Result([base_record]),
        _Result([{"edges": 0}]),  # near_count = 0 → falls back to PostGIS
        _Result(observed_records),
    ])

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
    assert body["recent_detections_source"] == "live_st_dwithin"
    assert body["vessels"] == []
    assert body["fmv_clips"] == []
    assert body["reports"] == []


def test_site_composition_prefers_near_edges_when_present(monkeypatch):
    base_record = {
        "b": MagicMock(),
        "labels": ["Base"],
        "props": {"aoi_postgis_id": 7, "name": "Test Base"},
    }
    near_grouped = [
        {"class": "tank", "count": 5, "last_seen": "2026-05-15T00:00:00Z"},
    ]
    calls = iter([
        _Result([base_record]),
        _Result([{"edges": 17}]),       # near_count > 0
        _Result(near_grouped),           # NEAR group-by results
        _Result([]),                     # observed
    ])
    def neo4j_side_effect(*_a, **_kw):
        return next(calls)
    client = _client(monkeypatch, neo4j_runs=neo4j_side_effect)
    resp = client.get("/api/graph/site-composition/elem-9")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recent_detections_source"] == "neo4j_near"
    assert body["recent_detections"][0]["class"] == "tank"


# ---------------------------------------------------------------------------
# /api/graph/candidate-edges/{id}/promote
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/graph/evidence/{node_id}
# ---------------------------------------------------------------------------


def test_evidence_returns_404_when_seed_missing(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.get("/api/graph/evidence/missing")
    assert resp.status_code == 404


def test_evidence_returns_focus_plus_records_for_detection(monkeypatch):
    # The neighborhood Cypher returns one record with `nodes` list (seed +
    # neighbors) and `rels` list. We fake a Detection mirror so the PostGIS
    # detection fetch path runs.
    seed = MagicMock()
    seed.element_id = "seed-1"
    seed.labels = ["Detection"]
    seed.__iter__ = lambda self: iter([])
    seed.keys = MagicMock(return_value=[])
    # neo4j Node-like dict access:
    detection_node = MagicMock()
    detection_node.element_id = "det-1"
    detection_node.labels = {"Detection"}

    # Build a Node stand-in that dict()-converts to its properties.
    class _FakeNode:
        def __init__(self, eid, labels, props):
            self.element_id = eid
            self.labels = set(labels)
            self._props = props
        def __iter__(self):
            return iter(self._props)
        def keys(self):
            return list(self._props.keys())
        def __getitem__(self, k):
            return self._props[k]
        def values(self):
            return list(self._props.values())

    fake_seed = _FakeNode("det-1", ["Detection"], {"postgis_id": 42, "class": "container_ship"})
    record = {"nodes": [fake_seed], "rels": []}

    postgis_runs = {
        "execute": [None, None, None],  # one per _safe_fetch call we hit
        "fetchall": [
            [{"id": 42, "class": "container_ship", "confidence": 0.9, "created_at": None,
              "metadata": {}, "pass_id": 7, "pass_name": "p7", "sensor_type": "EO",
              "acquisition_time": None, "lon": 0.0, "lat": 0.0}],
        ],
        "fetchone": [],
    }

    client = _client(
        monkeypatch,
        neo4j_runs=lambda *a, **kw: _Result([record]),
        postgis_runs=postgis_runs,
    )
    resp = client.get("/api/graph/evidence/det-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["focus"]["id"] == "det-1"
    assert body["focus"]["label"] == "Detection"
    assert body["hops"] == 2
    # PostGIS detection row landed in evidence_records.detections.
    assert len(body["evidence_records"]["detections"]) == 1
    assert body["evidence_records"]["detections"][0]["id"] == 42
    # Buckets for not-yet-projected evidence types are empty arrays.
    assert body["evidence_records"]["fmv_clips"] == []
    assert body["evidence_records"]["documents"] == []


def test_evidence_clamps_hops(monkeypatch):
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    # hops=5 > max=3
    resp = client.get("/api/graph/evidence/seed?hops=5")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /api/graph/contradict
# ---------------------------------------------------------------------------


def test_contradict_returns_404_when_actor_or_detection_missing(monkeypatch):
    # merge_contradicted_by returns False (single() is None).
    client = _client(monkeypatch, neo4j_runs=lambda *a, **kw: _Result([]))
    resp = client.post(
        "/api/graph/contradict",
        json={"actor_id": "missing", "detection_postgis_id": 99},
    )
    assert resp.status_code == 404


def test_contradict_writes_edge_and_returns_payload(monkeypatch):
    client = _client(
        monkeypatch,
        neo4j_runs=lambda *a, **kw: _Result([{"rel_id": "rel-1"}]),
    )
    resp = client.post(
        "/api/graph/contradict",
        json={
            "actor_id": "elem-target-1",
            "detection_postgis_id": 42,
            "reason": "wrong class",
            "analyst": "alice",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["analyst"] == "alice"
    assert body["detection_postgis_id"] == 42


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
