"""Tests for graph_proximity — vendored proximity-graph builders.

Pure/offline: no PostGIS or Neo4j. Verifies the classical subgraph nesting
EMST ⊆ RNG ⊆ Gabriel ⊆ Delaunay and the radius/knn contracts.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from graph_proximity import (  # noqa: E402
    bridge_edges,
    build_colocation_edges,
    delaunay_edges,
    euclidean_mst_edges,
    fixed_radius_edges,
    gabriel_edges,
    haversine_m,
    knn_edges,
    relative_neighborhood_edges,
)


def _grid(n: int = 4, step: float = 0.01):
    """n×n lon/lat grid near the equator; ids are 'r{c}'."""
    recs = []
    for i in range(n):
        for j in range(n):
            recs.append((f"{i}{j}", j * step, i * step))
    return recs


def _edge_set(edges):
    return {(a, b) for (a, b, _d) in edges}


def test_haversine_known_distance():
    # 0.01 degree of latitude ≈ 1111 m.
    d = haversine_m(0.0, 0.0, 0.0, 0.01)
    assert 1100 < d < 1120


def test_edges_are_canonical_and_deduped():
    edges = knn_edges(_grid(), k=3)
    for a, b, dist in edges:
        assert a < b  # canonical ordering
        assert dist > 0
    assert len(edges) == len(set(_edge_set(edges)))  # no duplicates


def test_subgraph_nesting():
    recs = _grid(5, 0.02)
    delaunay = _edge_set(delaunay_edges(recs))
    gabriel = _edge_set(gabriel_edges(recs))
    rng = _edge_set(relative_neighborhood_edges(recs))
    mst = _edge_set(euclidean_mst_edges(recs))
    assert mst <= rng <= gabriel <= delaunay
    # MST of a connected n-node graph has exactly n-1 edges.
    assert len(mst) == len(recs) - 1


def test_fixed_radius_contract():
    recs = _grid(4, 0.01)  # neighbours ~1.11 km apart, diagonals ~1.57 km
    edges = fixed_radius_edges(recs, radius_m=1200.0)
    for _a, _b, dist in edges:
        assert dist <= 1200.0
    # Every orthogonal neighbour (≈1.11 km) must be present; diagonals excluded.
    assert len(edges) == 2 * 4 * (4 - 1)  # horizontal + vertical grid edges


def test_knn_respects_max_distance():
    recs = _grid(4, 0.01)
    capped = knn_edges(recs, k=8, max_distance_m=1200.0)
    for _a, _b, dist in capped:
        assert dist <= 1200.0


def test_bridge_edges_link_layers():
    source = [("s1", 0.0, 0.0), ("s2", 0.1, 0.1)]
    target = [("t1", 0.001, 0.001), ("t2", 0.2, 0.2)]
    edges = bridge_edges(source, target, method="knn", k=1)
    pairs = {(a, b) for (a, b, _d) in edges}
    assert ("s1", "t1") in pairs  # s1's nearest target is t1
    assert len(edges) == 2  # one edge per source


def test_build_colocation_rows_shape():
    recs = [(i, 0.01 * i, 0.0) for i in range(6)]
    rows = build_colocation_edges(recs, method="knn", k=2)
    assert rows
    for row in rows:
        assert set(row) == {"a_id", "b_id", "distance_m", "method"}
        assert row["a_id"] < row["b_id"]
        assert row["method"] == "knn"


def test_colocation_rows_preserve_int_ids():
    # Regression: build_proximity_edges stringifies node ids internally, but the
    # persistence MATCH compares against Neo4j's integer `postgis_id`. The rows
    # must carry the caller's original int ids (not "10"-style strings), and the
    # direction must be value-ordered so "10" doesn't sort before "9".
    recs = [(9, 0.0, 0.0), (10, 0.0001, 0.0), (11, 0.0002, 0.0)]
    rows = build_colocation_edges(recs, method="knn", k=2)
    assert rows
    for row in rows:
        assert isinstance(row["a_id"], int) and isinstance(row["b_id"], int)
        assert row["a_id"] < row["b_id"]  # value-ordered, not lexicographic


def test_degenerate_inputs():
    assert knn_edges([], k=3) == []
    assert knn_edges([("only", 1.0, 1.0)], k=3) == []
    assert euclidean_mst_edges([("a", 0.0, 0.0), ("b", 0.01, 0.0)]) != []
