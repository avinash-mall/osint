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


def test_project_fmv_with_tracks_runs_unwind_merge(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return=None)
    mod = _load_graph_writes()

    result = mod.project_fmv_clip_and_tracks(
        clip_id=7, clip_name="clip-7",
        duration_seconds=12.5, fps=30, width=1920, height=1080,
        tracks=[
            {"track_uid": "t-1", "cls": "car", "confidence": 0.91, "first_frame": 0, "last_frame": 50},
            {"track_uid": "t-2", "cls": "person", "confidence": 0.6, "first_frame": 10, "last_frame": 70},
        ],
    )
    assert result == {"clip": 1, "tracks": 2}
    cypher, params = run.call_args.args
    assert "MERGE (c:FMVClip {postgis_id: $clip_id})" in cypher
    assert "UNWIND $tracks AS t" in cypher
    assert "MERGE (d:FMVDetection {clip_id: $clip_id, track_uid: t.track_uid})" in cypher
    assert "MERGE (c)-[:CONTAINS_DETECTION]->(d)" in cypher
    assert params["clip_id"] == 7
    assert len(params["tracks"]) == 2


def test_project_fmv_without_tracks_still_merges_clip(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return=None)
    mod = _load_graph_writes()

    result = mod.project_fmv_clip_and_tracks(
        clip_id=9, clip_name="empty-clip",
        duration_seconds=None, fps=None, width=None, height=None,
        tracks=[],
    )
    assert result == {"clip": 1, "tracks": 0}
    cypher, _ = run.call_args.args
    assert "MERGE (c:FMVClip {postgis_id: $clip_id})" in cypher
    # No UNWIND when tracks empty.
    assert "UNWIND" not in cypher


def test_project_document_with_no_index_writes_only_stub(monkeypatch):
    run = _install_db_stub(monkeypatch, single_return=None)
    mod = _load_graph_writes()

    result = mod.project_document_with_mentions(
        document_id=42,
        title="Daily Sit Rep",
        media_type="document",
        summary="…",
        extracted_entities=[{"label": "Some Vessel", "confidence": 0.7}],
        entity_label_index=None,
    )
    assert result == {"document": 1, "mentions": 0}
    cypher, _ = run.call_args.args
    assert "MERGE (d:Document {postgis_id: $doc_id})" in cypher
    # No MENTIONS edges when no index provided.
    assert "MENTIONS" not in cypher


def test_project_document_writes_mentions_for_matched_entities(monkeypatch):
    # Two run calls: one MERGE for the doc stub, one UNWIND for MENTIONS.
    calls: list = []

    class _R:
        def __init__(self, single): self._single = single
        def single(self): return self._single

    def neo_run(cypher, params=None):
        calls.append((cypher, params))
        if "RETURN count(m) AS mentions" in cypher:
            return _R({"mentions": 2})
        return _R(None)

    _install_db_stub(monkeypatch, single_return=None)
    # Replace .run after install to capture call sequence:
    import sys
    sys.modules["database"].db.get_session.return_value.__enter__.return_value.run = neo_run
    mod = _load_graph_writes()

    index = {
        "alpha base": [{"element_id": "e1", "label": "Base", "id": "b1", "name": "Alpha Base"}],
        "bravo vessel": [{"element_id": "e2", "label": "Vessel", "id": "v1", "name": "Bravo Vessel"}],
    }
    extracted = [
        {"label": "alpha base near canal", "confidence": 0.8},   # matches alpha base
        {"label": "bravo vessel", "confidence": 0.9},            # matches bravo vessel
        {"label": "no match", "confidence": 0.4},                # no match
    ]
    result = mod.project_document_with_mentions(
        document_id=7, title="Brief", media_type="document",
        summary="x", extracted_entities=extracted, entity_label_index=index,
    )
    assert result == {"document": 1, "mentions": 2}
    # Two Cypher calls: stub MERGE + MENTIONS UNWIND.
    assert len(calls) == 2
    assert "MENTIONS" in calls[1][0]
    edges = calls[1][1]["edges"]
    assert len(edges) == 2
    target_ids = {e["target_element_id"] for e in edges}
    assert target_ids == {"e1", "e2"}
