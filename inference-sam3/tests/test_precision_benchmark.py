"""Lightweight precision-profile benchmark for a running inference service.

Run with:
    INFERENCE_URL=http://localhost:8001 python3 -m pytest -q inference-sam3/tests/test_precision_benchmark.py -s

The test is skipped when the service is not reachable. With ``-s`` it prints a
compact per-layer candidate/timing summary for the fixture chip.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests


_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")
_CHIP = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_chip.png"


def _service_reachable() -> bool:
    try:
        return requests.get(f"{_URL}/health", timeout=2).status_code == 200
    except requests.RequestException:
        return False


@pytest.mark.skipif(not _service_reachable(), reason=f"inference service not reachable at {_URL}")
@pytest.mark.skipif(not _CHIP.exists(), reason=f"sample chip {_CHIP} not present")
def test_precision_profile_reports_layer_counts_and_latency():
    metadata = {
        "modality": "rgb",
        "text_prompts": ["vehicle", "ship", "aircraft"],
        "enabled_layers": ["sam3", "dota_obb"],
    }
    response = requests.post(
        f"{_URL}/detect",
        files={"image": ("sample_chip.png", _CHIP.read_bytes(), "image/png")},
        data={"metadata": json.dumps(metadata)},
        timeout=120,
    )

    assert response.status_code == 200
    payload = response.json()
    debug_counts = payload.get("debug_counts") or {}
    timings = payload.get("timings_ms") or {}
    print(
        "precision_benchmark",
        {
            "detections": len(payload.get("detections") or []),
            "candidates_by_layer": debug_counts.get("candidates_by_layer"),
            "suppressed_by_nms": debug_counts.get("suppressed_by_nms"),
            "total_ms": timings.get("total"),
            "sam3_ms": timings.get("sam3_inference"),
            "specialists_ms": timings.get("specialists"),
        },
    )
    assert "candidates_by_layer" in debug_counts
    assert "total" in timings
