"""Unit tests for graph_schema.ensure_graph_schema.

Stays offline: stubs out database.db so the Neo4j driver is never touched.
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock


def _install_db_stub(monkeypatch, *, session_run_side_effect=None):
    """Replace ``database.db`` with a MagicMock returning a configurable session.

    Returns the ``session.run`` mock so tests can assert which Cypher fired.
    """
    session = MagicMock()
    session.run = MagicMock(side_effect=session_run_side_effect)
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


def _load_graph_schema():
    """Import (or re-import) graph_schema with the current database stub."""
    if "graph_schema" in sys.modules:
        del sys.modules["graph_schema"]
    return importlib.import_module("graph_schema")


def test_ensure_graph_schema_creates_constraints_and_indexes(monkeypatch):
    run = _install_db_stub(monkeypatch)
    mod = _load_graph_schema()
    mod.reset_cache_for_tests()

    mod.ensure_graph_schema()

    cyphers = [call.args[0] for call in run.call_args_list]
    # Every node label gets a uniqueness constraint.
    assert any("FOR (n:Target) REQUIRE n.id IS UNIQUE" in c for c in cyphers)
    assert any("FOR (n:Detection) REQUIRE n.postgis_id IS UNIQUE" in c for c in cyphers)
    assert any("FOR (n:FMVDetection) REQUIRE (n.clip_id, n.track_uid) IS UNIQUE" in c for c in cyphers)
    assert any("FOR (n:Base) REQUIRE n.id IS UNIQUE" in c for c in cyphers)
    assert any("FOR (n:UnknownLabel) REQUIRE n.label IS UNIQUE" in c for c in cyphers)
    # Detection composite index and NEAR distance index.
    assert any("FOR (d:Detection) ON (d.class, d.created_at)" in c for c in cyphers)
    assert any("FOR ()-[r:NEAR]->() ON (r.distance_m)" in c for c in cyphers)


def test_ensure_graph_schema_is_idempotent(monkeypatch):
    run = _install_db_stub(monkeypatch)
    mod = _load_graph_schema()
    mod.reset_cache_for_tests()

    mod.ensure_graph_schema()
    first = run.call_count
    mod.ensure_graph_schema()  # second call should short-circuit.
    assert run.call_count == first


def test_ensure_graph_schema_swallows_failures(monkeypatch):
    # First constraint raises; the rest should still be attempted, and the
    # function must not bubble. Module-level _graph_schema_ready must remain
    # False so a later call can retry.
    raises_once = [True]

    def maybe_raise(_cypher, *_args, **_kwargs):
        if raises_once[0]:
            raises_once[0] = False
            raise RuntimeError("simulated transient failure")
        return None

    run = _install_db_stub(monkeypatch, session_run_side_effect=maybe_raise)
    mod = _load_graph_schema()
    mod.reset_cache_for_tests()

    mod.ensure_graph_schema()  # must not raise
    # All constraint+index statements still attempted (one failed, the rest run).
    assert run.call_count >= len(mod._NODE_CONSTRAINTS)
