"""Tests for the RemoteCLIP verifier gate.

These tests do NOT load the real RemoteCLIP weights. They stub
``remoteclip_verifier.verify`` and check that the gate in
``main._detect_pipeline`` only invokes the verifier for source layers
listed in ``REMOTECLIP_VERIFIER_LAYERS`` (default ``{sam3,
grounding_dino}``). DOTA-OBB is intentionally excluded — see
[docs/decisions/why-remoteclip-default-on.md](../../docs/decisions/why-remoteclip-default-on.md).
"""
from __future__ import annotations

import io
import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

# conftest.py already stubs psutil / torch and puts inference-sam3 on sys.path.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import main


def _seed_pool_with_remoteclip() -> None:
    """Install a one-replica imagery pool with a non-None remoteclip bundle."""
    main._pool.clear()
    main._model_error = None
    main._current_profile = "imagery"
    main._pool.append({
        "device": "cpu",
        "lock": threading.Lock(),
        "sam3_image": object(),
        "sam3_video": None,
        "dinov3_sat": None,
        "prithvi": None,
        "terramind": None,
        "dota_obb": {"model": object()},
        "grounding_dino": {"model": object()},
        # Truthy bundle — gate decides whether verify() actually runs.
        "remoteclip": {"model": object(), "model_id": "stub", "arch": "ViT-B-32"},
    })


def _png_bytes(size: int = 16) -> bytes:
    img = Image.new("RGB", (size, size), color=(20, 30, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _post_detect(client: TestClient, *, layer: str, prompt: str = "a ship") -> dict:
    # Include "remoteclip" so `_layer_active("remoteclip")` is True; the
    # source-layer gate is the thing under test.
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", _png_bytes(), "image/png")},
        data={
            "metadata": (
                '{"text_prompts":["%s"],"modality":"rgb","enabled_layers":["%s","remoteclip"]}'
                % (prompt, layer)
            )
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _stub_verify_recorder(monkeypatch) -> MagicMock:
    """Replace remoteclip_verifier.verify with a recorder that returns a
    fixed margin so we can assert the verifier ran."""
    mock = MagicMock(
        return_value={
            "model": "stub",
            "arch": "ViT-B-32",
            "enabled": True,
            "label": "ship",
            "score": 0.9,
            "semantic_margin": 0.42,
            "passed": True,
            "top_labels": [{"label": "ship", "score": 0.9}],
        }
    )
    monkeypatch.setattr(main.remoteclip_verifier, "verify", mock)
    return mock


def _stub_sam3_one_detection(monkeypatch, label: str = "a ship") -> None:
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        mask = np.zeros(chip.shape[:2], dtype=bool)
        mask[2:8, 2:8] = True
        return [(mask, [2, 2, 8, 8], 0.8, prompts[0] if prompts else label)]

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)


def _stub_dota_one_detection(monkeypatch, label: str = "ship") -> None:
    def fake_run(model, chip, threshold):
        mask = np.zeros(chip.shape[:2], dtype=bool)
        mask[2:8, 2:8] = True
        return [(mask, [2, 2, 8, 8], 0.8, label)]

    monkeypatch.setattr(main.dota_obb, "run", fake_run)


def _stub_gdino_one_detection(monkeypatch, label: str = "ship") -> None:
    def fake_run(*args, **kwargs):
        mask = np.zeros((16, 16), dtype=bool)
        mask[2:8, 2:8] = True
        return [(mask, [2, 2, 8, 8], 0.8, label)]

    monkeypatch.setattr(main.grounding_dino, "run", fake_run)


# ---------------------------------------------------------------------------


def test_default_verifier_layers_set():
    """Default gate covers exactly the open-vocab detectors."""
    assert "sam3" in main.REMOTECLIP_VERIFIER_LAYERS
    assert "grounding_dino" in main.REMOTECLIP_VERIFIER_LAYERS
    assert "dota_obb" not in main.REMOTECLIP_VERIFIER_LAYERS


def test_verifier_gate_runs_on_sam3(monkeypatch):
    _seed_pool_with_remoteclip()
    _stub_sam3_one_detection(monkeypatch)
    verify_mock = _stub_verify_recorder(monkeypatch)

    client = TestClient(main.app)
    payload = _post_detect(client, layer="sam3")

    assert verify_mock.call_count == 1, "RemoteCLIP must verify SAM3 detections"
    det = payload["detections"][0]
    assert det["source_layer"] == "sam3"
    assert det["semantic_margin"] == pytest.approx(0.42)
    assert det["semantic_verifier"]["enabled"] is True


def test_verifier_gate_runs_on_grounding_dino(monkeypatch):
    _seed_pool_with_remoteclip()
    # Bypass the auto-gate so GDINO actually runs in this test.
    monkeypatch.setattr(
        main.grounding_dino_gate,
        "should_run_grounding_dino",
        lambda prompts, force=False: (True, None),
    )
    # Suppress SAM3 candidates so the only detection in the response is GDINO's.
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    _stub_gdino_one_detection(monkeypatch)
    verify_mock = _stub_verify_recorder(monkeypatch)

    client = TestClient(main.app)
    payload = _post_detect(client, layer="grounding_dino")

    assert verify_mock.call_count == 1, "RemoteCLIP must verify GDINO detections"
    det = payload["detections"][0]
    assert det["source_layer"] == "grounding_dino"
    assert det["semantic_margin"] == pytest.approx(0.42)


def test_verifier_gate_skips_dota_obb(monkeypatch):
    _seed_pool_with_remoteclip()
    # Suppress SAM3 candidates so only DOTA-OBB produces detections.
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    _stub_dota_one_detection(monkeypatch)
    verify_mock = _stub_verify_recorder(monkeypatch)

    client = TestClient(main.app)
    # Prompt is DOTA-relevant so the layer's relevance gate lets it run.
    payload = _post_detect(client, layer="dota_obb", prompt="ship")

    assert verify_mock.call_count == 0, "RemoteCLIP must not second-guess DOTA-OBB"
    det = payload["detections"][0]
    assert det["source_layer"] == "dota_obb"
    assert "semantic_margin" not in det or det.get("semantic_margin") is None
    assert "semantic_verifier" not in det


def test_verifier_layers_env_override(monkeypatch):
    """REMOTECLIP_VERIFIER_LAYERS=sam3,dota_obb makes DOTA-OBB verified too.

    The env parses on module import; monkeypatch the resulting frozenset
    directly to simulate an operator who launched the service with the
    override. Restoring the default afterwards is automatic via monkeypatch.
    """
    overridden = frozenset({"sam3", "dota_obb"})
    monkeypatch.setattr(main, "REMOTECLIP_VERIFIER_LAYERS", overridden)
    assert "dota_obb" in main.REMOTECLIP_VERIFIER_LAYERS
    assert "grounding_dino" not in main.REMOTECLIP_VERIFIER_LAYERS

    _seed_pool_with_remoteclip()
    # Suppress SAM3 candidates so only DOTA-OBB produces detections.
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    _stub_dota_one_detection(monkeypatch, label="ship")
    verify_mock = _stub_verify_recorder(monkeypatch)

    client = TestClient(main.app)
    payload = _post_detect(client, layer="dota_obb", prompt="ship")
    assert verify_mock.call_count == 1, (
        "Env override should make DOTA-OBB verified"
    )
    det = payload["detections"][0]
    assert det["source_layer"] == "dota_obb"
    assert det["semantic_margin"] == pytest.approx(0.42)
