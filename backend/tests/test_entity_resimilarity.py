"""Unit tests for the cosine helper + branch selector in
``worker.tick_entity_resimilarity`` (Phase 5.J).
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock


def _ensure_envs():
    os.environ.setdefault("NEO4J_URI", "bolt://localhost:9999")
    os.environ.setdefault("POSTGIS_URI", "postgresql://nobody:nobody@localhost:9999/none")
    os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:9999/0")


def test_cosine_basic():
    _ensure_envs()
    sys.modules.pop("graph_writes", None)
    import graph_writes as gw
    assert gw.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert gw.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    score = gw.cosine_similarity([1.0, 1.0], [1.0, 0.0])
    assert 0.7 < score < 0.71


def test_cosine_returns_none_for_bad_input():
    _ensure_envs()
    sys.modules.pop("graph_writes", None)
    import graph_writes as gw
    assert gw.cosine_similarity(None, [1.0]) is None
    assert gw.cosine_similarity([], []) is None
    assert gw.cosine_similarity([1.0, 0.0], [1.0]) is None
    assert gw.cosine_similarity([0.0, 0.0], [1.0, 0.0]) is None


def test_parse_embedding_anchor_handles_raw_list():
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)
    parsed = worker_legacy._parse_embedding_anchor([0.1, 0.2, 0.3])
    assert parsed == [0.1, 0.2, 0.3]


def test_parse_embedding_anchor_handles_json_string():
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)
    parsed = worker_legacy._parse_embedding_anchor('[1.5, 2.5]')
    assert parsed == [1.5, 2.5]


def test_parse_embedding_anchor_handles_fp16_b64():
    _ensure_envs()
    import worker_legacy
    importlib.reload(worker_legacy)
    import base64
    import numpy as np
    arr = np.array([1.0, 0.5, 0.25], dtype=np.float16)
    blob = {"fp16_b64": base64.b64encode(arr.tobytes()).decode("ascii"), "dim": 3}
    parsed = worker_legacy._parse_embedding_anchor(blob)
    assert parsed is not None
    assert len(parsed) == 3
    assert abs(parsed[0] - 1.0) < 1e-3
