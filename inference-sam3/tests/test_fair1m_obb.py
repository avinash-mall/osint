"""Tests for the FAIR1M-2.0 OBB specialist (runner + gate + dispatch).

These tests do NOT load real weights; they mock the YOLO model and
validate the SAM3-shaped tuple contract, the missing-weights graceful
no-op, and the gate's DOTA-exclusion logic. The integration test mirrors
``test_remoteclip_verifier.py`` — installs a one-replica imagery pool,
stubs ``fair1m_obb.run``, posts to ``/detect``, asserts the
``source_layer="fair1m_obb"`` tag appears on the response.
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

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import fair1m_gate
import fair1m_obb
import main


# ---------------------------------------------------------------------------
# fair1m_obb.run + fair1m_obb.load
# ---------------------------------------------------------------------------


def test_fair1m_classes_count_is_37():
    """The FAIR1M-2.0 spec defines exactly 37 fine-grained classes."""
    assert len(fair1m_obb.FAIR1M_CLASSES) == 37


def test_fair1m_load_returns_empty_bundle_when_weights_missing(tmp_path, monkeypatch):
    """No weights → bundle with model=None and an error string. Does NOT raise."""
    monkeypatch.setattr(fair1m_obb, "FAIR1M_OBB_WEIGHTS_DIR", str(tmp_path))
    bundle = fair1m_obb.load("cpu")
    assert bundle["model"] is None
    assert bundle["error"] and "weights file not found" in bundle["error"]
    assert bundle["device"] == "cpu"


def test_fair1m_run_returns_empty_when_model_is_none():
    bundle = {"model": None, "device": "cpu", "model_id": "stub"}
    chip = np.zeros((16, 16, 3), dtype=np.uint8)
    assert fair1m_obb.run(bundle, chip) == []


def test_fair1m_run_returns_sam3_tuples(monkeypatch):
    """With a mocked YOLO model, run() returns (mask, bbox, score, label) tuples."""

    class _FakeOBB:
        # Two detections: one "Boeing 737" above threshold, one below.
        xyxyxyxy = type("T", (), {"float": lambda self: self,
                                  "cpu": lambda self: self,
                                  "numpy": lambda self: np.array([
                                      [[2.0, 3.0], [10.0, 3.0], [10.0, 11.0], [2.0, 11.0]],
                                      [[12.0, 12.0], [14.0, 12.0], [14.0, 14.0], [12.0, 14.0]],
                                  ], dtype=np.float32)})()
        conf = type("T", (), {"float": lambda self: self,
                              "cpu": lambda self: self,
                              "numpy": lambda self: np.array([0.85, 0.10], dtype=np.float32)})()
        cls = type("T", (), {"cpu": lambda self: self,
                             "numpy": lambda self: np.array([0, 1], dtype=np.int64)})()

    class _FakeResult:
        names = {0: "Boeing 737", 1: "A330"}
        obb = _FakeOBB()

    class _FakeModel:
        def predict(self, **kwargs):
            return [_FakeResult()]

    bundle = {"model": _FakeModel(), "device": "cpu", "model_id": "stub"}
    chip = np.zeros((32, 32, 3), dtype=np.uint8)

    # Default threshold is 0.30 → only the 0.85 detection survives.
    out = fair1m_obb.run(bundle, chip)
    assert len(out) == 1
    mask, bbox, score, label = out[0]
    assert mask.shape == (32, 32) and mask.dtype == bool
    assert mask.any()
    assert bbox == [2.0, 3.0, 10.0, 11.0]
    assert score == pytest.approx(0.85)
    assert label == "Boeing 737"


def test_fair1m_model_versions_reports_class_count():
    bundle = {"model": None, "model_id": "stub", "weights_path": "/x/y.pt", "error": None}
    versions = fair1m_obb.model_versions(bundle)
    assert versions["loaded"] is False
    assert versions["class_count"] == 37
    assert versions["weights_path"] == "/x/y.pt"


# ---------------------------------------------------------------------------
# fair1m_gate
# ---------------------------------------------------------------------------


def test_fair1m_gate_fires_on_fair1m_vocab():
    """FAIR1M sub-class labels trigger the gate."""
    assert fair1m_gate.should_run_fair1m(["Boeing 737"]) is True
    assert fair1m_gate.should_run_fair1m(["warship"]) is True
    assert fair1m_gate.should_run_fair1m(["dump truck"]) is True


def test_fair1m_gate_skips_on_dota_only_vocab():
    """Generic DOTA-v1 labels do NOT trigger FAIR1M (DOTA already covers them)."""
    assert fair1m_gate.should_run_fair1m(["plane", "ship"]) is False
    assert fair1m_gate.should_run_fair1m(["bridge"]) is False
    assert fair1m_gate.should_run_fair1m(["helicopter", "harbor"]) is False


def test_fair1m_gate_skips_on_empty_prompts():
    assert fair1m_gate.should_run_fair1m([]) is False


def test_fair1m_gate_force_override():
    """metadata.force_fair1m_obb=True bypasses the gate even on DOTA-only vocab."""
    assert fair1m_gate.should_run_fair1m(["plane"], force=True) is True
    assert fair1m_gate.should_run_fair1m([], force=True) is True


def test_fair1m_gate_skips_on_unrelated_uncommon_vocab():
    """Uncommon prompts that don't match any FAIR1M sub-class are also skipped."""
    assert fair1m_gate.should_run_fair1m(["zxqkk_unicorn_battalion"]) is False


def test_fair1m_vocab_contains_all_classes():
    """Every FAIR1M class should be present in the vocab (lowercased)."""
    for label in fair1m_obb.FAIR1M_CLASSES:
        assert label.lower() in fair1m_gate.FAIR1M_VOCAB


# ---------------------------------------------------------------------------
# Dispatch integration via /detect
# ---------------------------------------------------------------------------


def _seed_pool_with_fair1m() -> None:
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
        "fair1m_obb": {"model": object(), "model_id": "stub-fair1m"},
        "grounding_dino": None,
        "remoteclip": None,
        "yoloe": None,
    })


def _png_bytes(size: int = 16) -> bytes:
    img = Image.new("RGB", (size, size), color=(20, 30, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_fair1m_dispatch_integration(monkeypatch):
    """When FAIR1M-vocab prompt arrives, run() fires and source_layer is tagged."""
    _seed_pool_with_fair1m()

    # Silence SAM3 so the only candidate in the response originates from FAIR1M.
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    # Disable DOTA-OBB so we don't get cross-talk on the prompt.
    monkeypatch.setattr(main.dota_obb, "run", lambda *a, **kw: [])

    mask = np.zeros((16, 16), dtype=bool)
    mask[2:8, 2:8] = True
    fair1m_run = MagicMock(return_value=[(mask, [2.0, 2.0, 8.0, 8.0], 0.75, "Boeing 737")])
    monkeypatch.setattr(main.fair1m_obb, "run", fair1m_run)

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", _png_bytes(), "image/png")},
        data={"metadata": '{"text_prompts":["Boeing 737"],"modality":"rgb"}'},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert fair1m_run.call_count == 1, "FAIR1M-OBB must fire on a FAIR1M-vocab prompt"
    dets = payload.get("detections", [])
    assert any(d.get("source_layer") == "fair1m_obb" for d in dets), (
        f"Expected source_layer=fair1m_obb in {dets}"
    )


def test_fair1m_dispatch_skips_on_dota_only_prompt(monkeypatch):
    """A pure DOTA-v1 vocab prompt should not trigger FAIR1M-OBB."""
    _seed_pool_with_fair1m()
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    monkeypatch.setattr(main.dota_obb, "run", lambda *a, **kw: [])
    fair1m_run = MagicMock(return_value=[])
    monkeypatch.setattr(main.fair1m_obb, "run", fair1m_run)

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", _png_bytes(), "image/png")},
        data={"metadata": '{"text_prompts":["plane","ship"],"modality":"rgb"}'},
    )
    assert resp.status_code == 200, resp.text
    assert fair1m_run.call_count == 0, "FAIR1M-OBB must skip when prompts are DOTA-only"


def test_fair1m_dispatch_force_override(monkeypatch):
    """metadata.force_fair1m_obb=true bypasses the gate."""
    _seed_pool_with_fair1m()
    monkeypatch.setattr(
        main.sam3_runner,
        "run_text_prompts",
        lambda bundle, chip, prompts, threshold, timings=None: [],
    )
    monkeypatch.setattr(main.dota_obb, "run", lambda *a, **kw: [])
    fair1m_run = MagicMock(return_value=[])
    monkeypatch.setattr(main.fair1m_obb, "run", fair1m_run)

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", _png_bytes(), "image/png")},
        data={
            "metadata": (
                '{"text_prompts":["plane"],"modality":"rgb",'
                '"force_fair1m_obb":true}'
            )
        },
    )
    assert resp.status_code == 200, resp.text
    assert fair1m_run.call_count == 1, (
        "force_fair1m_obb must run FAIR1M even on DOTA-only prompts"
    )
