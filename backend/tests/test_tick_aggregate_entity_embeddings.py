"""Unit tests for ``worker.tick_aggregate_entity_embeddings`` (Phase 5.J + 5.M)."""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock

import pytest


def _ensure_envs():
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")
    os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:9999/0")


def _stub_postgis(monkeypatch, *, fetchall_side_effect=None):
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchall = MagicMock(side_effect=fetchall_side_effect or [])
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=cursor); cm.__exit__ = MagicMock(return_value=False)
    pg = MagicMock(); pg.get_cursor = MagicMock(return_value=cm)
    import worker.graph as worker_legacy
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)
    return cursor


def test_aggregator_averages_embeddings_per_entity(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)
    # Sequence of fetchall calls:
    #   1. all entity ids
    #   2..N. anchors per entity (one fetchall per entity)
    fetchall_side_effect = [
        [{"id": "v1"}],                                  # entity ids
        [{"embedding_anchor": [1.0, 0.0]},               # tracks for v1
         {"embedding_anchor": [0.0, 1.0]}],
    ]
    _stub_postgis(monkeypatch, fetchall_side_effect=fetchall_side_effect)
    result = worker_legacy.tick_aggregate_entity_embeddings()
    assert result["aggregated"] == 1
    assert result["skipped"] == 0
    assert result["total"] == 1


def test_aggregator_skips_entity_without_anchors(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)
    fetchall_side_effect = [
        [{"id": "v1"}],
        [],
    ]
    _stub_postgis(monkeypatch, fetchall_side_effect=fetchall_side_effect)
    result = worker_legacy.tick_aggregate_entity_embeddings()
    assert result["aggregated"] == 0
    assert result["skipped"] == 1


def test_aggregator_skips_dim_mismatch(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)
    fetchall_side_effect = [
        [{"id": "v1"}],
        [{"embedding_anchor": [1.0, 0.0]},
         {"embedding_anchor": [1.0, 0.0, 0.0]}],  # different dim
    ]
    _stub_postgis(monkeypatch, fetchall_side_effect=fetchall_side_effect)
    result = worker_legacy.tick_aggregate_entity_embeddings()
    assert result["skipped"] == 1


@pytest.mark.integration
def test_aggregator_end_to_end():
    pytest.skip("integration coverage — exercised via docker-compose stack verify")
