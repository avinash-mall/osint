"""End-to-end smoke test that the /detect path survives OOM-inducing requests.

Skipped unless a running inference service is reachable at INFERENCE_URL.
The point isn't to *cause* an OOM reliably — that depends on GPU size —
but to verify that *if* one occurs, the worker recovers (returns 200 or
500 with a json body) instead of crashing the process. The follow-up
``/health/memory`` call is the actual liveness probe.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests


_URL = os.getenv("INFERENCE_URL", "http://localhost:8001")
_CHIP = os.getenv(
    "BENCH_CHIP",
    str(Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "sample_chip.png"),
)


def _service_reachable() -> bool:
    try:
        r = requests.get(f"{_URL}/health/memory", timeout=2)
        return r.status_code == 200
    except requests.RequestException:
        return False


@pytest.mark.skipif(
    not _service_reachable(),
    reason=f"inference service not reachable at {_URL}",
)
@pytest.mark.skipif(
    not Path(_CHIP).exists(),
    reason=f"sample chip {_CHIP} not present",
)
def test_detect_survives_oom_inducing_request():
    """Send a request crafted to maximise VRAM pressure (long prompt list).
    Whatever the outcome, the service must still answer /health/memory.
    """
    huge_prompts = [f"object_{i}" for i in range(64)]
    metadata = json.dumps({"modality": "rgb", "prompts": huge_prompts})
    chip_bytes = Path(_CHIP).read_bytes()

    r = requests.post(
        f"{_URL}/detect",
        files={"image": ("chip.png", chip_bytes, "image/png")},
        data={"metadata": metadata},
        timeout=180,
    )
    # A graceful failure (500 + json body) is acceptable; a crash (connection
    # reset, no body) is not. Both 200 and 500 here count as "the worker is
    # still up and replying".
    assert r.status_code in (200, 500, 503), (
        f"unexpected status {r.status_code} (body: {r.text[:200]})"
    )

    # Liveness follow-up: the worker must still answer health checks.
    health = requests.get(f"{_URL}/health/memory", timeout=5)
    assert health.status_code == 200, (
        f"worker no longer responsive after stress request: {health.status_code}"
    )
