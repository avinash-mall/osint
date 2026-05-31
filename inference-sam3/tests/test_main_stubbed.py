from __future__ import annotations

import io
import base64
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

if "psutil" not in sys.modules:
    sys.modules["psutil"] = types.SimpleNamespace(
        cpu_percent=lambda interval=None: 0.0,
        virtual_memory=lambda: types.SimpleNamespace(percent=0.0, available=0, total=0),
        disk_usage=lambda path: types.SimpleNamespace(percent=0.0, free=0, total=0),
    )

if "torch" not in sys.modules:
    _cuda = types.SimpleNamespace(
        is_available=lambda: False,
        current_device=lambda: 0,
        reset_peak_memory_stats=lambda *_a, **_k: None,
        max_memory_allocated=lambda *_a, **_k: 0,
    )
    sys.modules["torch"] = types.SimpleNamespace(
        cuda=_cuda,
        backends=types.SimpleNamespace(
            cuda=types.SimpleNamespace(matmul=types.SimpleNamespace(allow_tf32=False)),
            cudnn=types.SimpleNamespace(allow_tf32=False, benchmark=False),
        ),
        set_float32_matmul_precision=lambda *_a, **_k: None,
        get_float32_matmul_precision=lambda: "highest",
    )

import main


def setup_function():
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
    })


def test_rgb_text_prompt(monkeypatch):
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        mask = np.zeros(chip.shape[:2], dtype=bool)
        mask[2:8, 2:8] = True
        return [(mask, [2, 2, 8, 8], 0.8, prompts[0])]

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)
    img = Image.new("RGB", (16, 16), color=(20, 30, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={"metadata": '{"text_prompts":["a ship"],"modality":"rgb","enabled_layers":["sam3"]}'},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["detections"][0]["original_class"] == "a ship"
    assert payload["detections"][0]["source_layer"] == "sam3"
    assert payload["debug_counts"]["prompt_count"] == 1
    assert payload["debug_counts"]["candidates_by_layer"]["sam3"] == 1
    assert len(payload["detections"][0]["obb"]) == 8
    assert "timings_ms" in payload
    assert payload["detections"][0]["embedding"]["model"] == "disabled"


def test_prompt_limit_defaults_and_clamps():
    assert main._prompt_limit({}, "rgb") == main.SAM3_MAX_IMAGE_PROMPTS
    assert main._prompt_limit({}, "fmv") == main.SAM3_MAX_VIDEO_PROMPTS
    assert main._prompt_limit({"max_prompts": 2}, "rgb") == 2
    assert main._prompt_limit({"max_prompts": 999999}, "rgb") == main.SAM3_MAX_IMAGE_PROMPTS
    assert main._prompt_limit({"max_prompts": "bad"}, "fmv") == main.SAM3_MAX_VIDEO_PROMPTS


def test_enabled_layers_sam3_only(monkeypatch):
    """When enabled_layers=["sam3"], specialist detectors must not be called."""
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        mask = np.zeros(chip.shape[:2], dtype=bool)
        mask[2:8, 2:8] = True
        return [(mask, [2, 2, 8, 8], 0.75, prompts[0])]

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)

    mock_dota = MagicMock(return_value=[])
    mock_gdino = MagicMock(return_value=[])
    monkeypatch.setattr(main.dota_obb, "run", mock_dota)
    monkeypatch.setattr(main.grounding_dino, "run", mock_gdino)

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={
            "metadata": '{"modality":"rgb","text_prompts":["vehicle"],"enabled_layers":["sam3"]}'
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled_layers_unavailable"] == []
    assert mock_dota.call_count == 0
    assert mock_gdino.call_count == 0


def test_detect_rejects_image_yoloe_layers():
    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={
            "metadata": '{"modality":"rgb","text_prompts":["vehicle"],"enabled_layers":["yoloe_seg"]}'
        },
    )

    assert resp.status_code == 400
    assert "FMV-only" in resp.json()["detail"]


def test_detect_raw_rejects_image_yoloe_layers():
    chip = np.zeros((4, 4, 3), dtype=np.uint8)
    meta = '{"modality":"rgb","text_prompts":["vehicle"],"enabled_layers":["yoloe_pf"]}'

    client = TestClient(main.app)
    resp = client.post(
        "/detect_raw",
        content=chip.tobytes(),
        headers={
            "content-type": "application/octet-stream",
            "X-Chip-Shape": "4,4,3",
            "X-Chip-Meta-B64": base64.b64encode(meta.encode("utf-8")).decode("ascii"),
        },
    )

    assert resp.status_code == 400
    assert "FMV-only" in resp.json()["detail"]


def test_resolve_prompts_explicit_dedupes_without_backend(monkeypatch):
    monkeypatch.setattr(main, "_fetch_default_prompts", MagicMock(side_effect=AssertionError("backend should not be called")))

    assert main.resolve_prompts({"text_prompts": [" Ship ", "ship", "  VEHICLE  "]}) == ["ship", "vehicle"]


def test_resolve_prompts_explicit_empty_is_error(monkeypatch):
    monkeypatch.setattr(main, "_fetch_default_prompts", MagicMock(side_effect=AssertionError("backend should not be called")))

    try:
        main.resolve_prompts({"text_prompts": []})
    except ValueError as exc:
        assert "text_prompts was provided but empty" in str(exc)
        return

    raise AssertionError("empty explicit text_prompts should fail")


def test_resolve_prompts_uses_bounded_precision_defaults(monkeypatch):
    monkeypatch.delenv("SAM3_DEFAULT_PROMPT_SOURCE", raising=False)
    monkeypatch.delenv("SAM3_PRECISION_DEFAULT_PROMPTS", raising=False)
    monkeypatch.setattr(main, "_fetch_default_prompts", MagicMock(side_effect=AssertionError("backend should not be called")))

    prompts = main.resolve_prompts({"modality": "rgb"})

    assert prompts == ["vehicle", "ship", "aircraft", "building"]


def test_dota_obb_skips_unrelated_prompt(monkeypatch):
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        return []

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)
    mock_dota = MagicMock(return_value=[])
    monkeypatch.setattr(main.dota_obb, "run", mock_dota)

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={"metadata": '{"modality":"rgb","text_prompts":["person"]}'},
    )

    assert resp.status_code == 200
    assert mock_dota.call_count == 0


def test_dota_obb_runs_for_relevant_prompt(monkeypatch):
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        return []

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)
    mock_dota = MagicMock(return_value=[])
    monkeypatch.setattr(main.dota_obb, "run", mock_dota)

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={"metadata": '{"modality":"rgb","text_prompts":["vehicle"]}'},
    )

    assert resp.status_code == 200
    assert mock_dota.call_count == 1


def test_grounding_dino_requires_explicit_enable_for_uncommon_prompt(monkeypatch):
    def fake_text(bundle, chip, prompts, threshold, timings=None):
        return []

    monkeypatch.setattr(main.sam3_runner, "run_text_prompts", fake_text)
    monkeypatch.setattr(main.grounding_dino_gate, "should_run_grounding_dino", lambda prompts, force=False: (True, None))
    mock_gdino = MagicMock(return_value=[])
    monkeypatch.setattr(main.grounding_dino, "run", mock_gdino)

    img = Image.new("RGB", (16, 16), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    client = TestClient(main.app)
    resp = client.post(
        "/detect",
        files={"image": ("chip.png", buf.getvalue(), "image/png")},
        data={"metadata": '{"modality":"rgb","text_prompts":["zxqkk_unknown_thing"]}'},
    )

    assert resp.status_code == 200
    assert mock_gdino.call_count == 0

    buf2 = io.BytesIO()
    img.save(buf2, format="PNG")
    resp2 = client.post(
        "/detect",
        files={"image": ("chip.png", buf2.getvalue(), "image/png")},
        data={
            "metadata": (
                '{"modality":"rgb","text_prompts":["zxqkk_unknown_thing"],'
                '"enabled_layers":["sam3","grounding_dino"]}'
            )
        },
    )

    assert resp2.status_code == 200
    assert mock_gdino.call_count == 1
