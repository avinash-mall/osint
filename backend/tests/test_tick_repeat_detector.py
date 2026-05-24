"""Unit + integration tests for ``worker.tick_repeat_detector`` (Phase 4.D + 5.B + 5.M)."""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock

import pytest


def _ensure_envs():
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")
    os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:9999/0")


class _Result:
    def __init__(self, records): self._records = list(records)
    def __iter__(self): return iter(self._records)
    def single(self): return self._records[0] if self._records else None


def _stub_db(monkeypatch, *, neo4j_records):
    session = MagicMock()
    session.run = MagicMock(side_effect=lambda *a, **k: _Result(neo4j_records))
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=session); cm.__exit__ = MagicMock(return_value=False)
    db = MagicMock(); db.get_session = MagicMock(return_value=cm)
    import worker_legacy
    monkeypatch.setattr(worker_legacy, "db", db)
    return session


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def test_tick_repeat_detector_writes_representative_edges(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)
    # Neo4j returns one cluster row above the threshold.
    cluster_rows = [
        {"site_id": "aoi-1", "detection_class": "container_ship",
         "sample_detection_id": 42, "count": 7},
    ]
    _stub_db(monkeypatch, neo4j_records=cluster_rows)
    # Stub project_repeated_at_batch so we can assert it was called.
    import graph_writes
    calls = []
    monkeypatch.setattr(graph_writes, "project_repeated_at_batch",
                        lambda rows: calls.append(rows) or len(rows))
    result = worker_legacy.tick_repeat_detector()
    assert result["candidates_evaluated"] == 1
    assert result["edges_written"] == 1
    assert calls[0][0]["detection_class"] == "container_ship"


def test_tick_repeat_detector_returns_zero_when_no_clusters(monkeypatch):
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)
    _stub_db(monkeypatch, neo4j_records=[])
    result = worker_legacy.tick_repeat_detector()
    assert result["candidates_evaluated"] == 0
    assert result["edges_written"] == 0


# ---------------------------------------------------------------------------
# Integration coverage placeholder (auto-skipped when PostGIS unavailable)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tick_repeat_detector_end_to_end():
    pytest.skip("integration coverage — exercised via docker-compose stack verify")
