"""Tests for graph_pyg — the pure-numpy graph assembly layer.

The GNN training path needs torch (optional infra, exercised when installed);
here we verify the dependency-free assembly that feeds it, plus the availability
guards.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph_pyg import (  # noqa: E402
    GNNUnavailable,
    assemble_graph,
    is_torch_available,
    suggest_links,
)


def _nodes():
    return [
        {"id": "a", "confidence": 0.9, "latitude": 25.0, "longitude": 55.0},
        {"id": "b", "confidence": 0.5, "latitude": 25.1, "longitude": 55.1},
        {"id": "c", "confidence": 0.7, "latitude": 25.2, "longitude": 55.2},
    ]


def test_assemble_shapes_and_index():
    g = assemble_graph(_nodes(), [("a", "b"), ("b", "c")], ["confidence", "latitude", "longitude"])
    assert g["node_ids"] == ["a", "b", "c"]
    assert g["x"].shape == (3, 3)
    # 2 undirected edges → 4 directed columns.
    assert g["edge_index"].shape == (2, 4)


def test_assemble_normalizes_columns():
    g = assemble_graph(_nodes(), [], ["confidence", "latitude", "longitude"], normalize=True)
    # z-scored columns: mean≈0, std≈1.
    assert np.allclose(g["x"].mean(axis=0), 0.0, atol=1e-9)
    assert np.allclose(g["x"].std(axis=0), 1.0, atol=1e-9)


def test_assemble_drops_unknown_endpoints_and_self_loops():
    g = assemble_graph(_nodes(), [("a", "ghost"), ("b", "b"), ("a", "c")], ["confidence"], add_reverse=False)
    # Only a→c survives.
    assert g["edge_index"].shape == (2, 1)


def test_missing_features_default_zero():
    nodes = [{"id": "x"}, {"id": "y", "confidence": 1.0}]
    g = assemble_graph(nodes, [], ["confidence"], normalize=False)
    assert g["x"][0, 0] == 0.0
    assert g["x"][1, 0] == 1.0


def test_suggest_links_guard_when_torch_absent():
    if is_torch_available():
        # torch present: a tiny graph should yield ranked suggestions.
        out = suggest_links(_nodes(), [("a", "b")], [("a", "c")], ["confidence", "latitude", "longitude"], epochs=3)
        assert isinstance(out, list)
    else:
        try:
            suggest_links(_nodes(), [("a", "b")], [("a", "c")], ["confidence"], epochs=3)
            assert False, "expected GNNUnavailable"
        except GNNUnavailable:
            pass
