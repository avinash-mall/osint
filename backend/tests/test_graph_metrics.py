"""Tests for graph_metrics — in-memory graph metrics with the pure fallback.

Forces the dependency-free path (prefer_rustworkx=False) so the maths is
verified independently of whether rustworkx is installed in the image.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph_metrics import compute_metrics  # noqa: E402


def test_empty_graph():
    m = compute_metrics([], [])
    assert m["node_count"] == 0
    assert m["edge_count"] == 0


def test_path_graph_metrics():
    # a - b - c - d : b and c are the bridges.
    ids = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    m = compute_metrics(ids, edges, prefer_rustworkx=False)
    assert m["backend"] == "fallback"
    assert m["node_count"] == 4
    assert m["edge_count"] == 3
    assert m["component_count"] == 1
    assert m["largest_component"] == 4
    # Density of a 4-node, 3-edge graph = 2*3 / (4*3) = 0.5.
    assert abs(m["density"] - 0.5) < 1e-9
    # Betweenness: interior nodes b/c outrank endpoints a/d.
    top_bw = [e["id"] for e in m["top_centrality"]["betweenness"][:2]]
    assert set(top_bw) == {"b", "c"}


def test_disconnected_components():
    ids = ["a", "b", "c", "d", "e"]
    edges = [("a", "b"), ("b", "c"), ("d", "e")]  # triangleless: {a,b,c} and {d,e}
    m = compute_metrics(ids, edges, prefer_rustworkx=False)
    assert m["component_count"] == 2
    assert m["largest_component"] == 3


def test_star_graph_centrality():
    # hub connected to 4 leaves: hub dominates every centrality.
    ids = ["hub", "l1", "l2", "l3", "l4"]
    edges = [("hub", f"l{i}") for i in range(1, 5)]
    m = compute_metrics(ids, edges, prefer_rustworkx=False)
    assert m["top_centrality"]["degree"][0]["id"] == "hub"
    assert m["top_centrality"]["betweenness"][0]["id"] == "hub"
    assert m["top_centrality"]["pagerank"][0]["id"] == "hub"


def test_ignores_dangling_and_self_edges():
    ids = ["a", "b"]
    edges = [("a", "b"), ("a", "a"), ("a", "ghost")]  # self + unknown node dropped
    m = compute_metrics(ids, edges, prefer_rustworkx=False)
    assert m["edge_count"] == 1


def test_rustworkx_path_matches_fallback_when_available():
    # Guards the PyDiGraph fix: rx.pagerank rejects an undirected PyGraph, so the
    # rustworkx branch must build a mirrored digraph. Skips cleanly without rustworkx.
    from graph_metrics import RUSTWORKX_AVAILABLE
    if not RUSTWORKX_AVAILABLE:
        import pytest
        pytest.skip("rustworkx not installed")
    ids = ["a", "b", "c", "d"]
    edges = [("a", "b"), ("b", "c"), ("c", "d")]
    rx_m = compute_metrics(ids, edges, prefer_rustworkx=True)
    fb_m = compute_metrics(ids, edges, prefer_rustworkx=False)
    assert rx_m["backend"] == "rustworkx"
    assert rx_m["component_count"] == fb_m["component_count"] == 1
    assert rx_m["edge_count"] == fb_m["edge_count"] == 3
    # Interior nodes b/c outrank endpoints on both backends.
    assert {e["id"] for e in rx_m["top_centrality"]["betweenness"][:2]} == {"b", "c"}
    assert {e["id"] for e in rx_m["top_centrality"]["pagerank"][:2]} == {"b", "c"}
