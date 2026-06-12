"""Unit tests for main.py request-boundary helpers added by the 2026-06-11
API-layer audit: packed-embedding decode (similar-detections) and ISO datetime
validation. Offline — no DB, no LLM.
"""

from __future__ import annotations

import base64

import numpy as np
import pytest
from fastapi import HTTPException

from main import _decode_packed_embedding, _detection_embedding, parse_iso_datetime


def _packed(values: list[float]) -> dict:
    raw = np.array(values, dtype=np.float16).tobytes()
    return {"model": "dinov3", "dim": len(values), "fp16_b64": base64.b64encode(raw).decode()}


def test_decode_packed_embedding_roundtrip():
    assert _decode_packed_embedding(_packed([1.0, 2.0, 3.0])) == [1.0, 2.0, 3.0]


def test_decode_packed_embedding_garbage():
    assert _decode_packed_embedding({"fp16_b64": "not base64!!"}) is None
    assert _decode_packed_embedding({}) is None


def test_detection_embedding_accepts_worker_packed_dict():
    meta = {"embedding": _packed([0.5, 0.25])}
    assert _detection_embedding(meta) == [0.5, 0.25]


def test_detection_embedding_keeps_legacy_list_branch():
    assert _detection_embedding({"embedding": [1.0, 2.0]}) == [1.0, 2.0]


def test_detection_embedding_terramind_fallback():
    meta = {"embedding": {}, "terramind_embedding": _packed([4.0])}
    assert _detection_embedding(meta) == [4.0]


def test_detection_embedding_none_cases():
    assert _detection_embedding(None) is None
    assert _detection_embedding({}) is None
    assert _detection_embedding({"embedding": "oops"}) is None


def test_parse_iso_datetime_valid():
    dt = parse_iso_datetime("2026-06-12T10:00:00+00:00", "start_time")
    assert dt.year == 2026


def test_parse_iso_datetime_malformed_is_400():
    with pytest.raises(HTTPException) as exc:
        parse_iso_datetime("not-a-date", "start_time")
    assert exc.value.status_code == 400
    assert "start_time" in exc.value.detail
