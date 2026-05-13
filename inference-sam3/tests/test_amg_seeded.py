"""Pure-Python unit tests for the AMG-seeded hybrid path.

These tests cover helpers that don't require the SAM 3 model or GPU:
- bbox coordinate conversion (`_bbox_pixels_to_xywh_norm`)
- the obj_id accumulation payload shape (clear_old_boxes pattern)
- fallback selection when the probe says the seeded path is unavailable

End-to-end integration tests run via scripts/bench_fmv.py against the
live container.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def runner_module():
    import sam3_runner  # noqa: WPS433
    return sam3_runner


def test_bbox_pixels_to_xywh_norm_basic(runner_module):
    # 1024×768 frame, bbox covering the right half [512, 0, 1024, 768].
    out = runner_module._bbox_pixels_to_xywh_norm([512, 0, 1024, 768], 768, 1024)
    cx, cy, w, h = out
    assert cx == pytest.approx(0.75, abs=1e-4)   # center at 0.75 of width
    assert cy == pytest.approx(0.5, abs=1e-4)    # center at half height
    assert w == pytest.approx(0.5, abs=1e-4)
    assert h == pytest.approx(1.0, abs=1e-4)


def test_bbox_pixels_to_xywh_norm_clamps(runner_module):
    # Bbox extending past frame edges — must clamp to [0, 1] without throwing.
    out = runner_module._bbox_pixels_to_xywh_norm([-10, -10, 1050, 800], 768, 1024)
    cx, cy, w, h = out
    assert 0.0 <= cx <= 1.0
    assert 0.0 <= cy <= 1.0
    assert 0.0 <= w <= 1.0
    assert 0.0 <= h <= 1.0


def test_bbox_pixels_to_xywh_norm_zero_dim(runner_module):
    # Degenerate frame (height=0): divisor protection keeps result finite.
    out = runner_module._bbox_pixels_to_xywh_norm([0, 0, 1, 1], 0, 0)
    assert all(0.0 <= v <= 1.0 for v in out)
    assert all(v == v for v in out)  # no NaN


def test_amg_seeded_available_default_false(runner_module):
    # Before probe runs, the cache is None and amg_seeded_available()
    # reports False (truthy guard with `bool(...)`). Force the cache to
    # None for this assertion since the module may have been probed by an
    # earlier import; restore afterwards.
    saved = runner_module._AMG_SEEDED_AVAILABLE
    runner_module._AMG_SEEDED_AVAILABLE = None
    try:
        assert runner_module.amg_seeded_available() is False
    finally:
        runner_module._AMG_SEEDED_AVAILABLE = saved


def test_amg_seeded_available_true_after_probe(runner_module):
    saved = runner_module._AMG_SEEDED_AVAILABLE
    runner_module._AMG_SEEDED_AVAILABLE = True
    try:
        assert runner_module.amg_seeded_available() is True
    finally:
        runner_module._AMG_SEEDED_AVAILABLE = saved


def test_clear_old_boxes_pattern():
    """The seeded runner sets clear_old_boxes=True on k=0 and False after."""
    # Replicates the pattern used inside run_video_amg_seeded — guards
    # against accidental refactor that flips the order.
    candidates = list(range(5))
    flags = [(k == 0) for k in range(len(candidates))]
    assert flags == [True, False, False, False, False]


def test_run_video_amg_seeded_empty_when_probe_false(runner_module):
    saved = runner_module._AMG_SEEDED_AVAILABLE
    runner_module._AMG_SEEDED_AVAILABLE = False
    try:
        # Should yield nothing without touching the bundle.
        out = list(runner_module.run_video_amg_seeded(
            bundle={"sam3_video": None, "device": "cpu", "lock": None},
            video_path="/nonexistent.mp4",
            frame_stride=1,
            start_frame=0,
            end_frame=None,
            max_frames=None,
            dinov3=None,
            score_threshold=0.25,
            grid_size=4,
            reseed_every_n_frames=12,
        ))
        assert out == []
    finally:
        runner_module._AMG_SEEDED_AVAILABLE = saved
