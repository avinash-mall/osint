"""Tests for od_flows — OD matrix / track-based flow graph construction."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from od_flows import (  # noqa: E402
    build_od_flows_from_tracks,
    flows_to_geojson,
    od_matrix_to_graph,
    snap_cell,
)


def test_snap_cell_centres():
    cx, cy = snap_cell(55.013, 25.007, 0.02)
    assert cx == 55.01 and cy == 25.01  # cell [55.00,55.02)×[25.00,25.02) centre


def test_od_matrix_drops_self_and_below_min():
    centroids = [(0.0, 0.0), (1.0, 1.0)]
    matrix = [[5.0, 3.0], [0.0, 9.0]]
    edges = od_matrix_to_graph(matrix, centroids, min_flow=1.0)
    assert len(edges) == 1  # self-loops (0→0, 1→1) and zero 1→0 dropped
    assert edges[0]["origin_cell"] == 0 and edges[0]["dest_cell"] == 1
    assert edges[0]["weight"] == 3.0


def test_tracks_aggregate_repeated_corridor():
    # Two tracks walking the same west→east corridor produce weight 2.
    corridor = [(55.001, 25.001), (55.021, 25.001), (55.041, 25.001)]
    edges = build_od_flows_from_tracks([corridor, corridor], cell_deg=0.02)
    assert edges  # at least one flow edge
    assert edges[0]["weight"] == 2  # the busiest segment seen in both tracks


def test_stationary_track_makes_no_edges():
    # All points snap to one cell → no movement.
    pts = [(55.001, 25.001), (55.002, 25.002), (55.003, 25.001)]
    edges = build_od_flows_from_tracks([pts], cell_deg=0.02)
    assert edges == []


def test_min_flow_threshold():
    corridor = [(55.001, 25.001), (55.021, 25.001)]
    assert build_od_flows_from_tracks([corridor], cell_deg=0.02, min_flow=2) == []


def test_geojson_shape():
    corridor = [(55.001, 25.001), (55.021, 25.001)]
    fc = flows_to_geojson(build_od_flows_from_tracks([corridor], cell_deg=0.02))
    assert fc["type"] == "FeatureCollection"
    assert fc["features"][0]["geometry"]["type"] == "LineString"
