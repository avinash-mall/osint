"""Origin-Destination flow graphs from movement tracks (offline, pure-python).

Vendored from city2graph's ``od_matrix_to_graph`` (BSD-3) — see
``docs/decisions/why-od-flow-graphs.md``. Turns movement data (ordered track
point sequences, or a raw OD matrix) into a spatial flow graph: weighted edges
between grid cells / zones that surface movement corridors for pattern-of-life.

No new dependencies — plain ``math`` only. The two entry points:

- :func:`od_matrix_to_graph` — the direct city2graph form: a zone×zone weight
  matrix + zone centroids → flow edges.
- :func:`build_od_flows_from_tracks` — convenience for Sentinel's ``track_points``:
  snaps each point to a grid cell, builds per-track consecutive cell transitions,
  aggregates them into an OD matrix, and returns the same flow edges.
"""

from __future__ import annotations

from typing import Sequence

LonLat = tuple[float, float]
FlowEdge = dict  # {origin, dest, weight, origin_cell, dest_cell}


def snap_cell(lon: float, lat: float, cell_deg: float) -> tuple[float, float]:
    """Snap a point to the centre of its ``cell_deg`` grid cell."""
    cx = (lon // cell_deg) * cell_deg + cell_deg / 2.0
    cy = (lat // cell_deg) * cell_deg + cell_deg / 2.0
    return round(cx, 6), round(cy, 6)


def od_matrix_to_graph(
    matrix: Sequence[Sequence[float]],
    centroids: Sequence[LonLat],
    *,
    min_flow: float = 1.0,
    drop_self: bool = True,
) -> list[FlowEdge]:
    """Convert a zone×zone OD weight matrix into flow edges.

    ``matrix[i][j]`` is the flow from zone ``i`` to zone ``j``; ``centroids[i]``
    is that zone's ``(lon, lat)``. Edges below ``min_flow`` (and self-loops when
    ``drop_self``) are dropped. Returns edges sorted by descending weight.
    """
    n = len(centroids)
    edges: list[FlowEdge] = []
    for i in range(n):
        row = matrix[i]
        for j in range(n):
            if drop_self and i == j:
                continue
            w = float(row[j])
            if w < min_flow:
                continue
            edges.append({
                "origin": [centroids[i][0], centroids[i][1]],
                "dest": [centroids[j][0], centroids[j][1]],
                "weight": w,
                "origin_cell": i,
                "dest_cell": j,
            })
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges


def build_od_flows_from_tracks(
    tracks: Sequence[Sequence[LonLat]],
    *,
    cell_deg: float = 0.02,
    min_flow: int = 1,
) -> list[FlowEdge]:
    """Aggregate ordered track point sequences into OD flow edges.

    Each track is a time-ordered list of ``(lon, lat)``. Points are snapped to a
    ``cell_deg`` grid; consecutive *distinct* cells within a track become one
    movement; movements are counted across all tracks. Returns flow edges between
    cell centroids with ``weight`` = movement count, dropping edges below
    ``min_flow``.
    """
    counts: dict[tuple[tuple[float, float], tuple[float, float]], int] = {}
    for track in tracks:
        prev: tuple[float, float] | None = None
        for lon, lat in track:
            cell = snap_cell(lon, lat, cell_deg)
            if prev is not None and cell != prev:
                counts[(prev, cell)] = counts.get((prev, cell), 0) + 1
            prev = cell
    edges: list[FlowEdge] = []
    for (origin, dest), w in counts.items():
        if w < min_flow:
            continue
        edges.append({
            "origin": [origin[0], origin[1]],
            "dest": [dest[0], dest[1]],
            "weight": w,
            "origin_cell": list(origin),
            "dest_cell": list(dest),
        })
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges


def flows_to_geojson(edges: Sequence[FlowEdge]) -> dict:
    """Render flow edges as a FeatureCollection of weighted LineStrings."""
    features = [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [e["origin"], e["dest"]]},
        "properties": {"weight": e["weight"], "origin_cell": e["origin_cell"], "dest_cell": e["dest_cell"]},
    } for e in edges]
    return {"type": "FeatureCollection", "features": features}
