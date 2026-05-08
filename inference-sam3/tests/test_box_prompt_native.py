from __future__ import annotations

import contextlib
import threading

import numpy as np
import pytest

import sam3_runner


@pytest.fixture(autouse=True)
def _stub_torch_contexts(monkeypatch):
    """The native runner wraps inference in torch.inference_mode + autocast.
    The unit tests don't depend on torch — replace the helpers with no-op
    context managers so the tests run in pure-python environments.
    """
    monkeypatch.setattr(sam3_runner, "_inference_mode", contextlib.nullcontext)
    monkeypatch.setattr(sam3_runner, "_autocast_ctx", lambda device: contextlib.nullcontext())


class _StubProcessor:
    """Minimal Sam3Processor stub that records calls and returns canned state.

    Mirrors the upstream native API surface used by ``run_box_prompts``:
    ``set_image``, ``reset_all_prompts``, ``add_geometric_prompt``.
    """

    def __init__(self, mask, box, score):
        self.mask = mask
        self.box = box
        self.score = score
        self.calls: list[tuple] = []

    def set_image(self, image):
        self.calls.append(("set_image",))
        return {
            "backbone_out": {},
            "original_height": image.height,
            "original_width": image.width,
        }

    def reset_all_prompts(self, state):
        self.calls.append(("reset_all_prompts",))
        for key in ("masks", "boxes", "scores"):
            state.pop(key, None)

    def add_geometric_prompt(self, *, box, label, state):
        self.calls.append(("add_geometric_prompt", tuple(box), bool(label)))
        state["masks"] = [self.mask]
        state["boxes"] = [self.box]
        state["scores"] = [self.score]
        return state


def _make_bundle(processor):
    return {
        "device": "cpu",
        "lock": threading.Lock(),
        "sam3_image": {"model": object(), "processor": processor},
    }


def test_run_box_prompts_returns_pixel_xyxy_and_label():
    H, W = 32, 32
    mask = np.zeros((H, W), dtype=bool)
    mask[8:24, 8:24] = True
    processor = _StubProcessor(
        mask=mask,
        box=np.array([8.0, 8.0, 24.0, 24.0], dtype=np.float32),
        score=np.float32(0.7),
    )
    bundle = _make_bundle(processor)

    image = np.zeros((H, W, 3), dtype=np.uint8)
    out = sam3_runner.run_box_prompts(
        bundle,
        image,
        [{"bbox": [0.5, 0.5, 0.5, 0.5], "class": "vessel"}],
        score_threshold=0.1,
    )

    assert len(out) == 1
    mask_out, box_out, score_out, label_out = out[0]
    assert mask_out.dtype == bool
    assert mask_out.shape == (H, W)
    assert box_out == [8.0, 8.0, 24.0, 24.0]
    assert score_out == pytest.approx(0.7, abs=1e-3)
    assert label_out == "vessel"

    add_calls = [c for c in processor.calls if c[0] == "add_geometric_prompt"]
    assert len(add_calls) == 1
    _, box_passed, label_passed = add_calls[0]
    assert box_passed == (0.5, 0.5, 0.5, 0.5)
    assert label_passed is True


def test_run_box_prompts_filters_below_threshold():
    H, W = 16, 16
    processor = _StubProcessor(
        mask=np.zeros((H, W), dtype=bool),
        box=np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32),
        score=np.float32(0.05),
    )
    bundle = _make_bundle(processor)
    out = sam3_runner.run_box_prompts(
        bundle,
        np.zeros((H, W, 3), dtype=np.uint8),
        [{"bbox": [0.5, 0.5, 0.4, 0.4], "class": "ship"}],
        score_threshold=0.1,
    )
    assert out == []


def test_run_box_prompts_derives_cxcywh_from_obb_when_bbox_missing():
    H, W = 16, 16
    processor = _StubProcessor(
        mask=np.zeros((H, W), dtype=bool),
        box=np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32),
        score=np.float32(0.5),
    )
    bundle = _make_bundle(processor)
    out = sam3_runner.run_box_prompts(
        bundle,
        np.zeros((H, W, 3), dtype=np.uint8),
        [{"obb": [0.2, 0.3, 0.6, 0.3, 0.6, 0.7, 0.2, 0.7], "original_class": "tank"}],
        score_threshold=0.1,
    )

    assert len(out) == 1
    assert out[0][3] == "tank"
    add_calls = [c for c in processor.calls if c[0] == "add_geometric_prompt"]
    assert len(add_calls) == 1
    _, box_passed, _ = add_calls[0]
    cx, cy, w, h = box_passed
    assert cx == pytest.approx(0.4)
    assert cy == pytest.approx(0.5)
    assert w == pytest.approx(0.4)
    assert h == pytest.approx(0.4)


def test_run_box_prompts_skips_entry_with_neither_bbox_nor_obb():
    H, W = 16, 16
    processor = _StubProcessor(
        mask=np.zeros((H, W), dtype=bool),
        box=np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32),
        score=np.float32(0.9),
    )
    bundle = _make_bundle(processor)
    out = sam3_runner.run_box_prompts(
        bundle,
        np.zeros((H, W, 3), dtype=np.uint8),
        [{"class": "ghost"}],
        score_threshold=0.1,
    )
    assert out == []
    add_calls = [c for c in processor.calls if c[0] == "add_geometric_prompt"]
    assert add_calls == []


def test_run_box_prompts_resets_state_between_entries():
    H, W = 16, 16
    processor = _StubProcessor(
        mask=np.zeros((H, W), dtype=bool),
        box=np.array([0.0, 0.0, 1.0, 1.0], dtype=np.float32),
        score=np.float32(0.5),
    )
    bundle = _make_bundle(processor)
    sam3_runner.run_box_prompts(
        bundle,
        np.zeros((H, W, 3), dtype=np.uint8),
        [
            {"bbox": [0.25, 0.25, 0.2, 0.2], "class": "a"},
            {"bbox": [0.75, 0.75, 0.2, 0.2], "class": "b"},
        ],
        score_threshold=0.1,
    )

    reset_calls = [c for c in processor.calls if c[0] == "reset_all_prompts"]
    add_calls = [c for c in processor.calls if c[0] == "add_geometric_prompt"]
    assert len(reset_calls) == 2
    assert len(add_calls) == 2
    # First reset must precede first add_geometric_prompt to ensure state is
    # clean before the per-entry call.
    first_reset = processor.calls.index(("reset_all_prompts",))
    first_add = next(i for i, c in enumerate(processor.calls) if c[0] == "add_geometric_prompt")
    assert first_reset < first_add
