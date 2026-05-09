from __future__ import annotations

import io
import threading

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

import main


def setup_function():
    main._pool.clear()
    main._model_error = None
    main._pool.append({
        "device": "cpu",
        "lock": threading.Lock(),
        "sam3_image": object(),
        "sam3_video": None,
        "dinov3_sat": None,
        "dinov3_lvd": None,
        "prithvi": None,
        "terramind": None,
    })


def test_rgb_text_prompt(monkeypatch):
    def fake_text(bundle, chip, prompts, threshold):
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
        data={"metadata": '{"text_prompts":["a ship"],"modality":"rgb"}'},
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["detections"][0]["original_class"] == "a ship"
    assert len(payload["detections"][0]["obb"]) == 8
    assert "timings_ms" in payload
    assert payload["detections"][0]["embedding"]["model"] == "disabled"


def test_prompt_limit_defaults_and_clamps():
    assert main._prompt_limit({}, "rgb") == main.SAM3_MAX_IMAGE_PROMPTS
    assert main._prompt_limit({}, "fmv") == main.SAM3_MAX_VIDEO_PROMPTS
    assert main._prompt_limit({"max_prompts": 2}, "rgb") == 2
    assert main._prompt_limit({"max_prompts": 999999}, "rgb") == main.SAM3_MAX_IMAGE_PROMPTS
    assert main._prompt_limit({"max_prompts": "bad"}, "fmv") == main.SAM3_MAX_VIDEO_PROMPTS
