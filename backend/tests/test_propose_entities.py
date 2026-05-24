"""Unit tests for the LLM + heuristic proposer paths in
``worker.tick_propose_entities`` (Phase 5.I).

Strategy: import the worker module, stub the cluster fetcher + ai.get_llm_json
+ postgis_db cursor, then call the task body directly.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock


def _ensure_envs():
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")
    os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:9999/0")


def _stub_postgis(monkeypatch, *, fetchone=None):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchone = MagicMock(side_effect=fetchone or [None] * 200)
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=cursor); cm.__exit__ = MagicMock(return_value=False)
    pg = MagicMock(); pg.get_cursor = MagicMock(return_value=cm)
    return cursor, pg


def test_llm_proposer_used_when_get_llm_json_returns_proposals(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)

    monkeypatch.setattr(worker_legacy, "_fetch_repeated_at_clusters", lambda: [
        {"detection_class": "container_ship", "cluster_count": 9, "site_id": "aoi-1", "site_name": "Pier 7", "sample_detection_id": 100},
    ])
    # LLM returns one structured proposal.
    fake_ai = types.SimpleNamespace(
        get_llm_json=lambda system, user, **kw: {
            "proposals": [
                {
                    "entity_kind": "vessel",
                    "proposed_name": "Pearl-9",
                    "reason": "9 container_ship detections at Pier 7",
                    "seed_detection_ids": [100],
                }
            ]
        },
        AIUnavailable=type("AIUnavailable", (RuntimeError,), {}),
    )
    monkeypatch.setitem(sys.modules, "ai", fake_ai)

    _, pg = _stub_postgis(monkeypatch, fetchone=[None, None])  # neither dup-check hits
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)

    result = worker_legacy.tick_propose_entities()
    assert result["proposed"] == 1
    assert result["source"] == "llm"


def test_falls_back_to_heuristic_when_llm_unavailable(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)

    monkeypatch.setattr(worker_legacy, "_fetch_repeated_at_clusters", lambda: [
        {"detection_class": "container_ship", "cluster_count": 9, "site_id": "aoi-1", "site_name": "Pier 7", "sample_detection_id": 100},
    ])

    class _AIUnavailable(RuntimeError): pass
    def _raise(*a, **kw):
        raise _AIUnavailable("simulated: no LLM endpoint")
    fake_ai = types.SimpleNamespace(get_llm_json=_raise, AIUnavailable=_AIUnavailable)
    monkeypatch.setitem(sys.modules, "ai", fake_ai)

    _, pg = _stub_postgis(monkeypatch, fetchone=[None, None])
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)

    result = worker_legacy.tick_propose_entities()
    assert result["proposed"] == 1  # heuristic picked up container_ship → vessel
    assert result["source"] == "heuristic"


def test_returns_zero_when_no_clusters(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)

    monkeypatch.setattr(worker_legacy, "_fetch_repeated_at_clusters", lambda: [])
    result = worker_legacy.tick_propose_entities()
    assert result["proposed"] == 0
    assert "no REPEATED_AT" in result.get("note", "")


def test_llm_filters_unknown_kinds(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)

    monkeypatch.setattr(worker_legacy, "_fetch_repeated_at_clusters", lambda: [
        {"detection_class": "container_ship", "cluster_count": 9, "site_id": "aoi-1", "site_name": "Pier 7", "sample_detection_id": 100},
    ])
    fake_ai = types.SimpleNamespace(
        get_llm_json=lambda system, user, **kw: {
            "proposals": [
                {"entity_kind": "spaceship", "proposed_name": "Enterprise"},
                {"entity_kind": "vessel", "proposed_name": "Pearl-9", "seed_detection_ids": [100]},
                {"entity_kind": "vessel"},  # missing proposed_name
            ]
        },
        AIUnavailable=type("AIUnavailable", (RuntimeError,), {}),
    )
    monkeypatch.setitem(sys.modules, "ai", fake_ai)
    _, pg = _stub_postgis(monkeypatch, fetchone=[None, None])
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)

    result = worker_legacy.tick_propose_entities()
    # Only the valid vessel proposal lands.
    assert result["proposed"] == 1
    assert result["source"] == "llm"
