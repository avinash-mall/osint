"""In-memory graph metrics over a Neo4j snapshot (rustworkx fast path).

Sentinel's entity/link graph lives in Neo4j; running global analytics (centrality,
connected components, density) as Cypher is awkward and slow. This module pulls a
bounded snapshot into an in-memory graph and computes the metrics there — the
pattern city2graph uses via its ``nx_to_rx`` rustworkx interop. See
``docs/decisions/why-rustworkx-graph-metrics.md``.

Design: ``rustworkx`` is the fast path (compiled, used when present in the image),
but the backend image ships it only after a rebuild, so every metric also has a
dependency-free fallback (union-find + Brandes + power-iteration PageRank) that is
correct for the ≤1500-node snapshots the graph endpoint returns. The public
:func:`compute_metrics` picks the fast path automatically and reports which backend
ran via the ``backend`` field.

The ``nx_to_rx`` / ``rx_to_nx`` helpers mirror city2graph's interop surface and are
only usable when both libraries are installed; they are not on the request path.
"""

from __future__ import annotations

from collections import deque
from typing import Sequence

try:
    import rustworkx as rx  # type: ignore
    RUSTWORKX_AVAILABLE = True
except Exception:  # pragma: no cover - absent until the image is rebuilt
    rx = None  # type: ignore
    RUSTWORKX_AVAILABLE = False

try:
    import networkx as nx  # type: ignore
    NETWORKX_AVAILABLE = True
except Exception:  # pragma: no cover - not a backend dep
    nx = None  # type: ignore
    NETWORKX_AVAILABLE = False


Edge = tuple[str, str]


def _index(node_ids: Sequence[str]) -> dict[str, int]:
    return {nid: i for i, nid in enumerate(node_ids)}


def _adjacency(n: int, edges: Sequence[tuple[int, int]]) -> list[set[int]]:
    adj: list[set[int]] = [set() for _ in range(n)]
    for a, b in edges:
        if a == b:
            continue
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _connected_components(n: int, edges: Sequence[tuple[int, int]]) -> list[int]:
    """Union-find component sizes, descending."""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    sizes: dict[int, int] = {}
    for i in range(n):
        r = find(i)
        sizes[r] = sizes.get(r, 0) + 1
    return sorted(sizes.values(), reverse=True)


def _betweenness(n: int, adj: list[set[int]]) -> list[float]:
    """Brandes betweenness centrality (unweighted, undirected), normalised."""
    bc = [0.0] * n
    for s in range(n):
        stack: list[int] = []
        pred: list[list[int]] = [[] for _ in range(n)]
        sigma = [0.0] * n
        sigma[s] = 1.0
        dist = [-1] * n
        dist[s] = 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = [0.0] * n
        while stack:
            w = stack.pop()
            for v in pred[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                bc[w] += delta[w]
    # Undirected: each pair counted twice. Normalise by (n-1)(n-2).
    scale = 1.0
    if n > 2:
        scale = 1.0 / ((n - 1) * (n - 2))
    return [v * scale for v in bc]


def _pagerank(n: int, adj: list[set[int]], *, damping: float = 0.85, iters: int = 100, tol: float = 1e-6) -> list[float]:
    if n == 0:
        return []
    rank = [1.0 / n] * n
    deg = [len(a) for a in adj]
    for _ in range(iters):
        new = [(1.0 - damping) / n] * n
        dangling = sum(rank[i] for i in range(n) if deg[i] == 0) * damping / n
        for i in range(n):
            if deg[i] == 0:
                continue
            share = damping * rank[i] / deg[i]
            for j in adj[i]:
                new[j] += share
        for i in range(n):
            new[i] += dangling
        if sum(abs(new[i] - rank[i]) for i in range(n)) < tol:
            rank = new
            break
        rank = new
    return rank


def _top(node_ids: Sequence[str], scores: Sequence[float], k: int) -> list[dict]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [{"id": node_ids[i], "score": round(float(scores[i]), 6)} for i in order[:k]]


def compute_metrics(
    node_ids: Sequence[str],
    edges: Sequence[Edge],
    *,
    top_k: int = 10,
    prefer_rustworkx: bool = True,
) -> dict:
    """Graph-level + per-node centrality metrics over an undirected snapshot.

    ``node_ids`` are opaque stable identifiers (Neo4j elementId on the request
    path); ``edges`` reference them. Returns counts, density, component sizes, and
    top-``k`` nodes by degree / betweenness / PageRank centrality. Picks the
    rustworkx backend when available, else the pure-Python fallback.
    """
    idx = _index(node_ids)
    n = len(node_ids)
    int_edges = [(idx[a], idx[b]) for a, b in edges if a in idx and b in idx and a != b]
    if n == 0:
        return {"backend": "none", "node_count": 0, "edge_count": 0, "density": 0.0,
                "component_count": 0, "largest_component": 0, "top_centrality": {}}

    density = (2.0 * len(int_edges)) / (n * (n - 1)) if n > 1 else 0.0

    if prefer_rustworkx and RUSTWORKX_AVAILABLE:
        g = rx.PyGraph()
        g.add_nodes_from(list(node_ids))
        g.add_edges_from_no_data(int_edges)
        components = sorted((len(c) for c in rx.connected_components(g)), reverse=True)
        deg = [g.degree(i) for i in range(n)]
        deg_cent = [d / (n - 1) if n > 1 else 0.0 for d in deg]
        # Index by node id, not .values(): the CentralityMapping iteration order
        # is not guaranteed to match node_ids order.
        bc_map = rx.betweenness_centrality(g, normalized=True)
        between = [bc_map[i] for i in range(n)]
        # rustworkx.pagerank only accepts a directed graph, so mirror each
        # undirected edge both ways — pagerank on the symmetric digraph equals
        # undirected PageRank.
        dg = rx.PyDiGraph()
        dg.add_nodes_from(list(node_ids))
        dg.add_edges_from_no_data([(a, b) for a, b in int_edges] + [(b, a) for a, b in int_edges])
        pr_map = rx.pagerank(dg)
        pr = [pr_map[i] for i in range(n)]
        backend = "rustworkx"
    else:
        adj = _adjacency(n, int_edges)
        components = _connected_components(n, int_edges)
        deg_cent = [len(adj[i]) / (n - 1) if n > 1 else 0.0 for i in range(n)]
        between = _betweenness(n, adj)
        pr = _pagerank(n, adj)
        backend = "fallback"

    return {
        "backend": backend,
        "node_count": n,
        "edge_count": len(int_edges),
        "density": round(density, 6),
        "component_count": len(components),
        "largest_component": components[0] if components else 0,
        "top_centrality": {
            "degree": _top(node_ids, deg_cent, top_k),
            "betweenness": _top(node_ids, between, top_k),
            "pagerank": _top(node_ids, pr, top_k),
        },
    }


def nx_to_rx(graph):  # pragma: no cover - interop convenience, not on request path
    """Convert a NetworkX (Multi)Graph to a rustworkx PyGraph (city2graph interop)."""
    if not (RUSTWORKX_AVAILABLE and NETWORKX_AVAILABLE):
        raise RuntimeError("nx_to_rx requires both rustworkx and networkx installed")
    g = rx.PyGraph()
    node_map = {node: g.add_node(node) for node in graph.nodes()}
    g.add_edges_from([(node_map[u], node_map[v], data) for u, v, data in graph.edges(data=True)])
    return g


def rx_to_nx(graph):  # pragma: no cover - interop convenience, not on request path
    """Convert a rustworkx PyGraph back to a NetworkX Graph (city2graph interop)."""
    if not (RUSTWORKX_AVAILABLE and NETWORKX_AVAILABLE):
        raise RuntimeError("rx_to_nx requires both rustworkx and networkx installed")
    g = nx.Graph()
    for i in graph.node_indices():
        g.add_node(graph[i])
    for a, b in graph.edge_list():
        g.add_edge(graph[a], graph[b])
    return g
