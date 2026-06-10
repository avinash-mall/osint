"""GeoDataFrame/record → PyTorch-Geometric bridge + GNN link prediction.

Vendored from city2graph's ``gdf_to_pyg`` (BSD-3) — see
``docs/decisions/why-gnn-link-prediction.md``. Two layers:

1. **Graph assembly (pure numpy, always importable):** :func:`assemble_graph`
   turns node records + an edge list into the tensors a GNN needs — a stable node
   index, an ``(N, F)`` feature matrix, and a ``(2, E)`` ``edge_index`` — without
   importing torch. This is unit-tested on its own.

2. **GNN link predictor (torch-guarded):** a minimal 2-layer GraphSAGE encoder +
   dot-product decoder trained with negative sampling to score *missing* links —
   the learned upgrade to the heuristic candidate scorer in ``candidate_linking``.
   It imports torch lazily; the backend image does not ship torch, so the request
   path returns an honest 503 (mirroring ``dem_available`` / ``osrm_available``).
   Installing ``torch`` (CPU) enables it with no other change.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np


class GNNUnavailable(RuntimeError):
    """Raised when the GNN path is requested but torch is not installed."""


def is_torch_available() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def is_pyg_available() -> bool:
    try:
        import torch_geometric  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Layer 1: pure-numpy graph assembly (no torch)
# ---------------------------------------------------------------------------

class AssembledGraph(dict):
    """Lightweight container: keys ``node_ids``, ``x``, ``edge_index``,
    ``feature_keys``. A dict subclass so it serialises trivially in tests."""


def assemble_graph(
    nodes: Sequence[dict],
    edges: Sequence[tuple],
    feature_keys: Sequence[str],
    *,
    normalize: bool = True,
    add_reverse: bool = True,
) -> AssembledGraph:
    """Assemble node/edge records into GNN-ready arrays.

    ``nodes`` are dicts each with an ``id`` plus numeric features; ``edges`` are
    ``(src_id, dst_id)`` pairs referencing those ids (unknown endpoints dropped).
    Missing/None features default to 0.0. With ``normalize`` each feature column is
    z-scored (zero-variance columns left as 0). With ``add_reverse`` every edge is
    duplicated in the opposite direction (undirected message passing).

    Returns an :class:`AssembledGraph` with ``x`` shape ``(N, F)`` and
    ``edge_index`` shape ``(2, E)`` as numpy arrays.
    """
    node_ids = [n["id"] for n in nodes]
    index = {nid: i for i, nid in enumerate(node_ids)}
    n = len(node_ids)
    f = len(feature_keys)

    x = np.zeros((n, f), dtype=np.float64)
    for i, node in enumerate(nodes):
        for j, key in enumerate(feature_keys):
            val = node.get(key)
            x[i, j] = float(val) if val is not None else 0.0

    if normalize and n > 1:
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std[std == 0] = 1.0
        x = (x - mean) / std

    pairs: list[tuple[int, int]] = []
    for a, b in edges:
        if a in index and b in index and a != b:
            pairs.append((index[a], index[b]))
            if add_reverse:
                pairs.append((index[b], index[a]))
    edge_index = (
        np.asarray(pairs, dtype=np.int64).T if pairs else np.zeros((2, 0), dtype=np.int64)
    )

    return AssembledGraph(
        node_ids=node_ids,
        x=x,
        edge_index=edge_index,
        feature_keys=list(feature_keys),
    )


def to_pyg_data(assembled: AssembledGraph):  # pragma: no cover - needs torch_geometric
    """Build a ``torch_geometric.data.Data`` from an assembled graph."""
    if not is_pyg_available():
        raise GNNUnavailable("torch_geometric is not installed")
    import torch
    from torch_geometric.data import Data

    return Data(
        x=torch.tensor(assembled["x"], dtype=torch.float32),
        edge_index=torch.tensor(assembled["edge_index"], dtype=torch.long),
    )


# ---------------------------------------------------------------------------
# Layer 2: GNN link predictor (torch only — no torch_geometric required)
# ---------------------------------------------------------------------------

def _build_model(in_dim: int, hidden: int, out: int):
    """A minimal 2-layer mean-aggregation GraphSAGE encoder (pure torch)."""
    import torch
    from torch import nn

    class SAGELayer(nn.Module):
        def __init__(self, i: int, o: int):
            super().__init__()
            self.lin_self = nn.Linear(i, o)
            self.lin_neigh = nn.Linear(i, o)

        def forward(self, x, edge_index):
            n = x.size(0)
            src, dst = edge_index[0], edge_index[1]
            agg = torch.zeros_like(x)
            agg.index_add_(0, dst, x[src])
            deg = torch.zeros(n, device=x.device).index_add_(
                0, dst, torch.ones(dst.size(0), device=x.device)
            ).clamp(min=1.0).unsqueeze(1)
            return self.lin_self(x) + self.lin_neigh(agg / deg)

    class Encoder(nn.Module):
        def __init__(self):
            super().__init__()
            self.c1 = SAGELayer(in_dim, hidden)
            self.c2 = SAGELayer(hidden, out)

        def forward(self, x, edge_index):
            h = torch.relu(self.c1(x, edge_index))
            return self.c2(h, edge_index)

    return Encoder()


def suggest_links(
    nodes: Sequence[dict],
    edges: Sequence[tuple],
    candidate_pairs: Sequence[tuple],
    feature_keys: Sequence[str],
    *,
    epochs: int = 60,
    hidden: int = 32,
    out: int = 16,
    top_k: int = 20,
    seed: int = 0,
) -> list[dict]:
    """Train a GraphSAGE auto-encoder on observed edges and score candidate links.

    ``candidate_pairs`` are ``(src_id, dst_id)`` pairs not currently connected;
    the model returns the ``top_k`` by predicted link probability (sigmoid of the
    embedding dot-product). Raises :class:`GNNUnavailable` if torch is missing.
    """
    if not is_torch_available():
        raise GNNUnavailable("torch is not installed in this image")
    import torch

    torch.manual_seed(seed)
    g = assemble_graph(nodes, edges, feature_keys)
    index = {nid: i for i, nid in enumerate(g["node_ids"])}
    x = torch.tensor(g["x"], dtype=torch.float32)
    edge_index = torch.tensor(g["edge_index"], dtype=torch.long)
    if edge_index.size(1) == 0:
        raise GNNUnavailable("graph has no edges to train on")

    model = _build_model(in_dim=x.size(1), hidden=hidden, out=out)
    opt = torch.optim.Adam(model.parameters(), lr=0.01)
    n = x.size(0)
    pos = edge_index

    model.train()
    for _ in range(epochs):
        opt.zero_grad()
        z = model(x, edge_index)
        # Negative sampling: random non-existent pairs.
        neg = torch.randint(0, n, (2, pos.size(1)))
        pos_score = (z[pos[0]] * z[pos[1]]).sum(dim=1)
        neg_score = (z[neg[0]] * z[neg[1]]).sum(dim=1)
        scores = torch.cat([pos_score, neg_score])
        labels = torch.cat([torch.ones_like(pos_score), torch.zeros_like(neg_score)])
        loss = torch.nn.functional.binary_cross_entropy_with_logits(scores, labels)
        loss.backward()
        opt.step()

    model.eval()
    with torch.no_grad():
        z = model(x, edge_index)
        out_rows: list[dict] = []
        for a, b in candidate_pairs:
            if a not in index or b not in index:
                continue
            score = torch.sigmoid((z[index[a]] * z[index[b]]).sum()).item()
            out_rows.append({"source": a, "target": b, "score": round(float(score), 6)})
    out_rows.sort(key=lambda r: r["score"], reverse=True)
    return out_rows[:top_k]
