"""Graph-based routing for the analytics router.

Expects a pre-built ``networkx`` graph pickled at ``ROUTING_GRAPH_PATH``
(defaults to ``/data/routing/graph.pkl``). Each node carries ``y`` (lat) and
``x`` (lon); each edge carries ``length`` (meters) and, optionally,
``elevation`` (mean meters above sea level) and ``exposure`` (0..1 score
indicating proximity to known threats). The graph is typically produced by
OSMnx during deploy:

    import osmnx as ox, pickle
    g = ox.graph_from_bbox(...)
    with open("/data/routing/graph.pkl", "wb") as f:
        pickle.dump(g, f)

The module falls back gracefully when the graph is missing — callers should
treat ``None`` returns as "no real routing available" unless an explicit
demo-fixture mode was requested.
"""

from __future__ import annotations

import math
import os
import pickle
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

try:
    import networkx as nx
except Exception:  # pragma: no cover - networkx is in requirements
    nx = None  # type: ignore[assignment]


DEFAULT_SPEED_KMH = 60.0  # used when an edge has no `maxspeed`


def graph_path() -> Path:
    return Path(os.getenv("ROUTING_GRAPH_PATH", "/data/routing/graph.pkl"))


def graph_available() -> bool:
    return nx is not None and graph_path().exists()


@lru_cache(maxsize=1)
def _load_graph():  # type: ignore[no-untyped-def]
    if not graph_available():
        return None
    with open(graph_path(), "rb") as f:
        return pickle.load(f)


def reset_graph_cache() -> None:
    _load_graph.cache_clear()


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_008.8
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _nearest_node(g, lat: float, lon: float):  # type: ignore[no-untyped-def]
    best = None
    best_d = math.inf
    for n, data in g.nodes(data=True):
        ny = data.get("y")
        nx_ = data.get("x")
        if ny is None or nx_ is None:
            continue
        d = _haversine(lat, lon, float(ny), float(nx_))
        if d < best_d:
            best_d = d
            best = n
    return best


def _edge_length_m(g, u, v, key=None) -> float:  # type: ignore[no-untyped-def]
    data = g.get_edge_data(u, v) if key is None else g.get_edge_data(u, v, key=key)
    if data is None:
        return 0.0
    if isinstance(data, dict) and "length" in data:
        try:
            return float(data["length"])
        except (TypeError, ValueError):
            pass
    # MultiGraph fallback: pick the shortest parallel edge.
    if isinstance(data, dict):
        candidates = [d for d in data.values() if isinstance(d, dict) and "length" in d]
        if candidates:
            return min(float(d["length"]) for d in candidates)
    # As a last resort use the great-circle distance between the endpoints.
    nu = g.nodes[u]
    nv = g.nodes[v]
    return _haversine(float(nu["y"]), float(nu["x"]), float(nv["y"]), float(nv["x"]))


def _edge_attr(g, u, v, name: str, default: float = 0.0) -> float:  # type: ignore[no-untyped-def]
    data = g.get_edge_data(u, v)
    if data is None:
        return default
    if isinstance(data, dict) and name in data:
        try:
            return float(data[name])
        except (TypeError, ValueError):
            return default
    if isinstance(data, dict):
        vals = [d.get(name) for d in data.values() if isinstance(d, dict) and name in d]
        nums = [float(v) for v in vals if v is not None]
        if nums:
            return sum(nums) / len(nums)
    return default


def _path_coords(g, path: Iterable) -> list[list[float]]:  # type: ignore[no-untyped-def]
    coords: list[list[float]] = []
    for n in path:
        data = g.nodes[n]
        y = data.get("y")
        x = data.get("x")
        if y is None or x is None:
            continue
        coords.append([float(x), float(y)])
    return coords


def _path_metrics(g, path: list) -> dict:  # type: ignore[no-untyped-def]
    length_m = 0.0
    exposure_sum = 0.0
    weighted_speed = 0.0
    for u, v in zip(path, path[1:]):
        seg = _edge_length_m(g, u, v)
        length_m += seg
        exposure_sum += _edge_attr(g, u, v, "exposure", 0.0) * seg
        maxspeed = _edge_attr(g, u, v, "maxspeed", DEFAULT_SPEED_KMH) or DEFAULT_SPEED_KMH
        weighted_speed += maxspeed * seg
    avg_speed = (weighted_speed / length_m) if length_m else DEFAULT_SPEED_KMH
    duration_min = (length_m / 1000.0) / max(avg_speed, 1.0) * 60.0
    return {
        "length_m": length_m,
        "duration_minutes": duration_min,
        "exposure": exposure_sum / length_m if length_m else 0.0,
    }


def _route_with_weight(g, src, dst, weight_fn):  # type: ignore[no-untyped-def]
    try:
        return nx.shortest_path(g, src, dst, weight=weight_fn)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None


def compute_routes(
    obs_lat: float,
    obs_lon: float,
    dst_lat: float,
    dst_lon: float,
    *,
    strategy: Optional[str] = None,
) -> Optional[list[dict]]:
    """Compute up to three route options between observer and destination.

    Strategies, ordered:
        - "shortest"        — minimum length (length attribute)
        - "least_exposure"  — minimum cumulative exposure * length
        - "balanced"        — length * (1 + 0.5 * exposure)

    When ``strategy`` is provided, only that strategy is returned. Otherwise
    all three are returned and de-duplicated by node-sequence.

    Returns ``None`` when no routing graph is available.
    """
    if not graph_available():
        return None
    g = _load_graph()
    if g is None:
        return None

    src = _nearest_node(g, obs_lat, obs_lon)
    dst = _nearest_node(g, dst_lat, dst_lon)
    if src is None or dst is None or src == dst:
        return None

    def w_shortest(u, v, _data):  # type: ignore[no-untyped-def]
        return _edge_length_m(g, u, v)

    def w_least_exposure(u, v, _data):  # type: ignore[no-untyped-def]
        length = _edge_length_m(g, u, v)
        return length * (1 + 4 * _edge_attr(g, u, v, "exposure", 0.0))

    def w_balanced(u, v, _data):  # type: ignore[no-untyped-def]
        length = _edge_length_m(g, u, v)
        return length * (1 + 0.5 * _edge_attr(g, u, v, "exposure", 0.0))

    plans = {
        "shortest":       ("shortest path",        w_shortest),
        "least_exposure": ("least exposure",       w_least_exposure),
        "balanced":       ("balanced",             w_balanced),
    }
    chosen = [strategy] if strategy in plans else list(plans.keys())
    seen_paths: set[tuple] = set()
    out: list[dict] = []
    for idx, key in enumerate(chosen, start=1):
        label, weight_fn = plans[key]
        path = _route_with_weight(g, src, dst, weight_fn)
        if not path:
            continue
        sig = tuple(path)
        if sig in seen_paths:
            continue
        seen_paths.add(sig)
        coords = _path_coords(g, path)
        if len(coords) < 2:
            continue
        metrics = _path_metrics(g, path)
        out.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "option": idx,
                "strategy": key,
                "label": label,
                "length_m": metrics["length_m"],
                "duration_minutes": metrics["duration_minutes"],
                "exposure": metrics["exposure"],
                "risk": _risk_label(key, metrics["exposure"]),
            },
        })
    return out or None


def _risk_label(strategy: str, exposure: float) -> str:
    if strategy == "least_exposure":
        return "least exposure"
    if strategy == "shortest":
        return "shortest"
    if exposure < 0.2:
        return "low risk"
    if exposure < 0.5:
        return "medium risk"
    return "high risk"
