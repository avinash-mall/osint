"""Unit tests for graph_writes helpers — stays offline via a database stub."""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock


class _FakeResult:
    def __init__(self, record=None):
        self._record = record

    def single(self):
        return self._record


def _install_db_stub(monkeypatch, single_return):
    session = MagicMock()
    session.run = MagicMock(return_value=_FakeResult(single_return))
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=session)
    session_cm.__exit__ = MagicMock(return_value=False)
    db_stub = MagicMock()
    db_stub.get_session = MagicMock(return_value=session_cm)
    database_module = types.ModuleType("database")
    database_module.db = db_stub
    database_module.postgis_db = MagicMock()
    monkeypatch.setitem(sys.modules, "database", database_module)
    return session.run


def _load_graph_writes():
    if "graph_writes" in sys.modules:
        del sys.modules["graph_writes"]
    return importlib.import_module("graph_writes")


def test_merge_candidate_writes_expected_cypher(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return={"rel_id": "abc"})
    mod = _load_graph_writes()

    ok = mod.merge_candidate_detected_as(
        detection_id=42,
        detection_class="container_ship",
        detection_confidence=0.91,
        detection_lat=12.34,
        detection_lon=56.78,
        target_id="t-1",
        candidate_id=7,
        score=0.78,
        reason="distance + class match",
    )

    assert ok is True
    run.assert_called_once()
    cypher, params = run.call_args.args
    assert "MERGE (t)-[rel:CANDIDATE_DETECTED_AS]->(d)" in cypher
    assert "MERGE (d:Detection {postgis_id: $det_id})" in cypher
    assert params["candidate_id"] == 7
    assert params["det_id"] == 42
    assert params["score"] == 0.78


def test_merge_candidate_returns_false_when_target_missing(monkeypatch):
    _install_db_stub(monkeypatch, single_return=None)
    mod = _load_graph_writes()

    ok = mod.merge_candidate_detected_as(
        detection_id=1, detection_class="x", detection_confidence=0.1,
        detection_lat=0.0, detection_lon=0.0,
        target_id="missing", candidate_id=1, score=0.0, reason="",
    )
    assert ok is False


def test_merge_candidate_swallows_exceptions(monkeypatch):
    session = MagicMock()
    session.run = MagicMock(side_effect=RuntimeError("neo4j down"))
    session_cm = MagicMock()
    session_cm.__enter__ = MagicMock(return_value=session)
    session_cm.__exit__ = MagicMock(return_value=False)
    db_stub = MagicMock(); db_stub.get_session = MagicMock(return_value=session_cm)
    database_module = types.ModuleType("database")
    database_module.db = db_stub
    database_module.postgis_db = MagicMock()
    monkeypatch.setitem(sys.modules, "database", database_module)
    mod = _load_graph_writes()

    ok = mod.merge_candidate_detected_as(
        detection_id=1, detection_class="x", detection_confidence=0.1,
        detection_lat=0.0, detection_lon=0.0,
        target_id="t", candidate_id=1, score=0.0, reason="",
    )
    assert ok is False  # never raises — PostGIS row is the source of truth


def test_delete_candidate_returns_removed_count(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return={"removed": 1})
    mod = _load_graph_writes()

    removed = mod.delete_candidate_detected_as(detection_id=42, target_id="t-1")

    assert removed == 1
    cypher, params = run.call_args.args
    assert "DELETE rel" in cypher
    assert params["det_id"] == 42


def test_promote_candidate_returns_pair_on_success(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return={"detection_id": 99, "target_id": "t-9"})
    mod = _load_graph_writes()

    result = mod.promote_candidate_to_detected_as(candidate_id=7, reviewed_by="alice")

    assert result == {"detection_id": 99, "target_id": "t-9"}
    cypher, params = run.call_args.args
    assert "MERGE (t)-[rel:DETECTED_AS]->(d)" in cypher
    assert "DELETE c" in cypher
    assert params["cid"] == 7
    assert params["reviewed_by"] == "alice"


def test_promote_candidate_returns_none_when_no_match(monkeypatch):
    _install_db_stub(monkeypatch, single_return=None)
    mod = _load_graph_writes()
    assert mod.promote_candidate_to_detected_as(candidate_id=999, reviewed_by="a") is None
