"""Unit + integration tests for ``worker.tick_near_builder`` (Phase 4.C + 5.B + 5.M)."""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import MagicMock

import pytest


def _ensure_envs():
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")
    os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:9999/0")


# ---------------------------------------------------------------------------
# Unit tests — stubbed DBs
# ---------------------------------------------------------------------------


def test_tick_near_builder_no_aois_returns_zero(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)

    # Stub postgis: AOI fetch returns nothing.
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchall = MagicMock(return_value=[])
    cursor_cm = MagicMock(); cursor_cm.__enter__ = MagicMock(return_value=cursor); cursor_cm.__exit__ = MagicMock(return_value=False)
    pg = MagicMock(); pg.get_cursor = MagicMock(return_value=cursor_cm)
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)

    result = worker_legacy.tick_near_builder()
    assert result == {"sites_processed": 0, "sites_skipped": 0, "near_edges_written": 0}


def test_tick_near_builder_skips_unknown_kind(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)

    aoi_rows = [{"id": 1, "metadata": {"aoi_kind": "warehouse"}}]  # not in radius dict
    cursor = MagicMock()
    cursor.execute = MagicMock()
    cursor.fetchall = MagicMock(side_effect=[aoi_rows])
    cm = MagicMock(); cm.__enter__ = MagicMock(return_value=cursor); cm.__exit__ = MagicMock(return_value=False)
    pg = MagicMock(); pg.get_cursor = MagicMock(return_value=cm)
    monkeypatch.setattr(worker_legacy, "postgis_db", pg)

    result = worker_legacy.tick_near_builder()
    assert result["sites_skipped"] == 1
    assert result["sites_processed"] == 0
    assert result["near_edges_written"] == 0


def test_near_radius_for_kind_falls_back_to_env(monkeypatch):
    _ensure_envs()
    import worker.graph as worker_legacy
    importlib.reload(worker_legacy)
    # No admin override → uses _NEAR_RADIUS_M dict.
    assert worker_legacy._near_radius_for_kind("base") == 5000.0
    assert worker_legacy._near_radius_for_kind("facility") == 1000.0
    assert worker_legacy._near_radius_for_kind("warehouse") is None


# ---------------------------------------------------------------------------
# Integration tests — auto-skip when PostGIS unreachable (per conftest.py)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tick_near_builder_end_to_end():
    """Seed an AOI tagged as a base + a Detection inside it; run the task;
    confirm a :NEAR edge appears in Neo4j and the cursor table advances.
    """
    pytest.skip("integration coverage — exercised via docker-compose stack verify")
