"""Proximity-graph construction over geographic points (offline, pure-numpy).

Vendored and adapted from the proximity models in the open-source
``city2graph`` library (BSD-3) — see
``docs/decisions/why-proximity-colocation-graph.md``. Only the algorithm core
is taken; none of city2graph's online loaders (Overture/OSM/GTFS) are pulled
in, so this module stays air-gap clean and depends only on ``numpy`` + ``scipy``
(both already in ``backend/requirements.txt``).

Every builder takes a list of ``(node_id, lon, lat)`` records and returns a list
of undirected ``(id_a, id_b, distance_m)`` edges with ``id_a < id_b`` and no
duplicates. Distances are great-circle metres (haversine); the planar models
(Delaunay/Gabriel/RNG/EMST) operate on a local equirectangular projection
around the point centroid, which is accurate at co-location scale (single
AOI / city) and avoids any CRS dependency.

The high-level entry point is :func:`build_colocation_edges`, which the
co-location Celery builder uses to turn a batch of detection centroids into
``COLOCATED_WITH`` edge rows.
"""

from __future__ import annotations

import math
from typing import Iterable, Literal, Sequence

import numpy as np

try:  # scipy is a hard backend dep, but keep the import survivable for tooling.
    from scipy.spatial import Delaunay
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import minimum_spanning_tree
    from scipy.spatial import cKDTree
    _SCIPY = True
except Exception:  # pragma: no cover - scipy always present in the backend image
    _SCIPY = False

EARTH_RADIUS_M = 6_371_008.8

Record = tuple[str, float, float]  # (node_id, lon, lat)
Edge = tuple[str, str, float]      # (id_a, id_b, distance_m)
ProximityMethod = Literal[
    "knn", "delaunay", "gabriel", "relative_neighborhood", "mst", "fixed_radius"
]


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Great-circle distance in metres between two lon/lat points."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _coords(records: Sequence[Record]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Split records into ids, lon/lat array, and a local-metres XY projection."""
    ids = [str(r[0]) for r in records]
    lonlat = np.asarray([(float(r[1]), float(r[2])) for r in records], dtype=float)
    if len(lonlat) == 0:
        return ids, lonlat.reshape(0, 2), lonlat.reshape(0, 2)
    lon0 = float(np.mean(lonlat[:, 0]))
    lat0 = float(np.mean(lonlat[:, 1]))
    # Equirectangular: metres east/north of the centroid. cos(lat0) corrects the
    # longitude scale; good to <1% over an AOI, which is all co-location needs.
    x = (lonlat[:, 0] - lon0) * math.cos(math.radians(lat0)) * (math.pi / 180.0) * EARTH_RADIUS_M
    y = (lonlat[:, 1] - lat0) * (math.pi / 180.0) * EARTH_RADIUS_M
    return ids, lonlat, np.column_stack([x, y])


def _as_edges(
    ids: Sequence[str],
    lonlat: np.ndarray,
    pairs: Iterable[tuple[int, int]],
    *,
    max_distance_m: float | None = None,
) -> list[Edge]:
    """Dedupe index pairs, compute haversine distance, apply optional radius cap."""
    seen: set[tuple[int, int]] = set()
    out: list[Edge] = []
    for i, j in pairs:
        if i == j:
            continue
        a, b = (i, j) if i < j else (j, i)
        if (a, b) in seen:
            continue
        seen.add((a, b))
        dist = haversine_m(lonlat[a, 0], lonlat[a, 1], lonlat[b, 0], lonlat[b, 1])
        if max_distance_m is not None and dist > max_distance_m:
            continue
        out.append((ids[a], ids[b], round(dist, 3)))
    return out


def _delaunay_index_edges(xy: np.ndarray) -> list[tuple[int, int]]:
    """Undirected edge index pairs from a Delaunay triangulation."""
    n = len(xy)
    if n < 3:
        return [(i, j) for i in range(n) for j in range(i + 1, n)]
    try:
        tri = Delaunay(xy)
    except Exception:
        # Collinear / degenerate inputs: fall back to the complete graph.
        return [(i, j) for i in range(n) for j in range(i + 1, n)]
    edges: set[tuple[int, int]] = set()
    for simplex in tri.simplices:
        for a in range(3):
            for b in range(a + 1, 3):
                i, j = int(simplex[a]), int(simplex[b])
                edges.add((i, j) if i < j else (j, i))
    return list(edges)


def knn_edges(records: Sequence[Record], k: int = 5, *, max_distance_m: float | None = None) -> list[Edge]:
    """Symmetric k-nearest-neighbour graph. Each node links to its k closest peers."""
    ids, lonlat, xy = _coords(records)
    n = len(ids)
    if n < 2:
        return []
    k = max(1, min(k, n - 1))
    tree = cKDTree(xy)
    # query k+1 because the first neighbour is the point itself.
    _, idx = tree.query(xy, k=k + 1)
    idx = np.atleast_2d(idx)
    pairs = [(i, int(j)) for i in range(n) for j in idx[i][1:]]
    return _as_edges(ids, lonlat, pairs, max_distance_m=max_distance_m)


def fixed_radius_edges(records: Sequence[Record], radius_m: float) -> list[Edge]:
    """Connect every pair of points within ``radius_m`` great-circle metres."""
    ids, lonlat, xy = _coords(records)
    n = len(ids)
    if n < 2:
        return []
    tree = cKDTree(xy)
    # Query in projected metres (radius slightly inflated to avoid edge truncation
    # from the projection), then filter precisely on haversine in _as_edges.
    pair_idx = tree.query_pairs(r=radius_m * 1.02)
    return _as_edges(ids, lonlat, pair_idx, max_distance_m=radius_m)


def delaunay_edges(records: Sequence[Record], *, max_distance_m: float | None = None) -> list[Edge]:
    """Delaunay triangulation edges — natural-adjacency backbone."""
    ids, lonlat, xy = _coords(records)
    return _as_edges(ids, lonlat, _delaunay_index_edges(xy), max_distance_m=max_distance_m)


def gabriel_edges(records: Sequence[Record], *, max_distance_m: float | None = None) -> list[Edge]:
    """Gabriel graph: a Delaunay edge (p,q) survives iff no other point lies in
    the circle whose diameter is pq. Subgraph of Delaunay; tighter adjacency.
    """
    ids, lonlat, xy = _coords(records)
    cand = _delaunay_index_edges(xy)
    kept: list[tuple[int, int]] = []
    for i, j in cand:
        mid = (xy[i] + xy[j]) / 2.0
        r2 = float(np.sum((xy[i] - xy[j]) ** 2)) / 4.0
        d2 = np.sum((xy - mid) ** 2, axis=1)
        # Allow the two endpoints themselves; reject if any *other* point is inside.
        inside = np.where(d2 < r2 - 1e-9)[0]
        if all(p in (i, j) for p in inside):
            kept.append((i, j))
    return _as_edges(ids, lonlat, kept, max_distance_m=max_distance_m)


def relative_neighborhood_edges(records: Sequence[Record], *, max_distance_m: float | None = None) -> list[Edge]:
    """Relative neighbourhood graph: edge (p,q) survives iff no point r is closer
    to BOTH p and q than they are to each other (the empty-lune test). Subgraph
    of Gabriel; the sparsest of the contiguity backbones.
    """
    ids, lonlat, xy = _coords(records)
    cand = _delaunay_index_edges(xy)
    kept: list[tuple[int, int]] = []
    for i, j in cand:
        dij = math.dist(xy[i], xy[j])
        di = np.linalg.norm(xy - xy[i], axis=1)
        dj = np.linalg.norm(xy - xy[j], axis=1)
        # A point in the lune has max(d(r,p), d(r,q)) < d(p,q).
        blocked = np.where((np.maximum(di, dj) < dij - 1e-9))[0]
        if all(p in (i, j) for p in blocked):
            kept.append((i, j))
    return _as_edges(ids, lonlat, kept, max_distance_m=max_distance_m)


def euclidean_mst_edges(records: Sequence[Record]) -> list[Edge]:
    """Euclidean minimum spanning tree — the minimal-length connected backbone.

    Computed over the Delaunay edge set, which provably contains the EMST, so we
    avoid the O(n^2) dense distance matrix.
    """
    ids, lonlat, xy = _coords(records)
    n = len(ids)
    if n < 2:
        return []
    cand = _delaunay_index_edges(xy)
    rows = [i for i, _ in cand]
    cols = [j for _, j in cand]
    weights = [math.dist(xy[i], xy[j]) for i, j in cand]
    graph = coo_matrix((weights, (rows, cols)), shape=(n, n))
    mst = minimum_spanning_tree(graph).tocoo()
    pairs = list(zip(mst.row.tolist(), mst.col.tolist()))
    return _as_edges(ids, lonlat, pairs)


_BUILDERS = {
    "knn": lambda recs, **kw: knn_edges(recs, k=int(kw.get("k", 5)), max_distance_m=kw.get("max_distance_m")),
    "delaunay": lambda recs, **kw: delaunay_edges(recs, max_distance_m=kw.get("max_distance_m")),
    "gabriel": lambda recs, **kw: gabriel_edges(recs, max_distance_m=kw.get("max_distance_m")),
    "relative_neighborhood": lambda recs, **kw: relative_neighborhood_edges(recs, max_distance_m=kw.get("max_distance_m")),
    "mst": lambda recs, **kw: euclidean_mst_edges(recs),
    "fixed_radius": lambda recs, **kw: fixed_radius_edges(recs, radius_m=float(kw.get("radius_m", 1000.0))),
}


def build_proximity_edges(records: Sequence[Record], method: ProximityMethod = "knn", **kwargs) -> list[Edge]:
    """Dispatch to one proximity builder by name. See module docstring for the
    record / edge contract. Unknown method → ValueError (HTTP boundary validates).
    """
    builder = _BUILDERS.get(method)
    if builder is None:
        raise ValueError(f"unknown proximity method: {method!r} (valid: {sorted(_BUILDERS)})")
    return builder(records, **kwargs)


def bridge_edges(
    source: Sequence[Record],
    target: Sequence[Record],
    *,
    method: Literal["knn", "fixed_radius"] = "knn",
    k: int = 1,
    radius_m: float | None = None,
) -> list[Edge]:
    """Heterogeneous proximity edges from every ``source`` node to nearby
    ``target`` nodes (city2graph ``bridge_nodes`` semantics). Used to wire one
    layer (e.g. Vessel detections) to another (e.g. Facility sites). Returns
    directed-but-stored-as ``(source_id, target_id, distance_m)`` rows; the two
    layers are assumed disjoint so no dedupe ordering is applied.
    """
    if not source or not target:
        return []
    t_ids = [str(r[0]) for r in target]
    t_lonlat = np.asarray([(float(r[1]), float(r[2])) for r in target], dtype=float)
    # Project target points with the SAME centroid basis used for sources so the
    # KD-tree distances are consistent across the two layers.
    combined = list(source) + list(target)
    _, _, xy_all = _coords(combined)
    xy_src = xy_all[: len(source)]
    xy_tgt = xy_all[len(source):]
    tree = cKDTree(xy_tgt)
    out: list[Edge] = []
    if method == "fixed_radius":
        r = float(radius_m if radius_m is not None else 1000.0)
        for si, rec in enumerate(source):
            for ti in tree.query_ball_point(xy_src[si], r=r * 1.02):
                dist = haversine_m(float(rec[1]), float(rec[2]), t_lonlat[ti, 0], t_lonlat[ti, 1])
                if dist <= r:
                    out.append((str(rec[0]), t_ids[ti], round(dist, 3)))
    else:
        kk = max(1, min(k, len(target)))
        _, idx = tree.query(xy_src, k=kk)
        idx = np.atleast_2d(idx.reshape(len(source), kk))
        for si, rec in enumerate(source):
            for ti in idx[si]:
                ti = int(ti)
                dist = haversine_m(float(rec[1]), float(rec[2]), t_lonlat[ti, 0], t_lonlat[ti, 1])
                out.append((str(rec[0]), t_ids[ti], round(dist, 3)))
    return out


def build_colocation_edges(
    records: Sequence[Record],
    *,
    method: ProximityMethod = "knn",
    k: int = 5,
    radius_m: float | None = None,
    max_distance_m: float | None = None,
) -> list[dict]:
    """High-level helper for the co-location builder: returns persistence-ready
    rows ``{a_id, b_id, distance_m, method}`` for ``project_colocation_edges_batch``.

    ``records`` are ``(detection_postgis_id, lon, lat)``. A radius cap is always
    sensible for co-location (default falls back to ``radius_m`` when set), so a
    base 5 km cap is applied if the caller passes neither.
    """
    cap = max_distance_m if max_distance_m is not None else (radius_m if method != "fixed_radius" else None)
    if cap is None and method != "fixed_radius":
        cap = 5000.0
    edges = build_proximity_edges(
        records, method, k=k, radius_m=radius_m or 1000.0, max_distance_m=cap
    )
    return [
        {"a_id": a, "b_id": b, "distance_m": dist, "method": method}
        for (a, b, dist) in edges
    ]
