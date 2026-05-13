"""Pure-Python unit tests for AMG helpers in sam3_runner.

These tests don't require the SAM 3 model or GPU — they exercise the
deterministic point-grid / NMS / track-linking primitives that underpin
``run_video_amg``. End-to-end tests that need the loaded model live in the
integration test suite gated on GPU availability.
"""
from __future__ import annotations

import numpy as np
import pytest


@pytest.fixture(scope="module")
def runner_module():
    """Import sam3_runner without triggering the heavy module-init paths.

    `_install_flash_attn_fallback` and `build_video` are not called at
    import time, only when models are loaded — so a bare import is safe.
    """
    import sam3_runner  # noqa: WPS433 — test-only import
    return sam3_runner


def test_point_grid_shape(runner_module):
    grid = runner_module._build_point_grid_norm(4)
    assert grid.shape == (16, 2)
    # All points strictly inside [0, 1] — centered cells, never on edges.
    assert (grid > 0).all() and (grid < 1).all()


def test_point_grid_centered(runner_module):
    grid = runner_module._build_point_grid_norm(2)
    # 2×2 grid: centers at (0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)
    expected = {(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)}
    got = {(round(float(x), 3), round(float(y), 3)) for x, y in grid}
    assert got == expected


def test_mask_iou_matrix_identity(runner_module):
    m = np.zeros((8, 8), dtype=bool)
    m[2:6, 2:6] = True
    iou = runner_module._mask_iou_matrix([m, m, m])
    assert np.allclose(iou, 1.0)


def test_mask_iou_matrix_disjoint(runner_module):
    a = np.zeros((8, 8), dtype=bool); a[0:4, 0:4] = True
    b = np.zeros((8, 8), dtype=bool); b[4:8, 4:8] = True
    iou = runner_module._mask_iou_matrix([a, b])
    assert iou[0, 0] == pytest.approx(1.0)
    assert iou[1, 1] == pytest.approx(1.0)
    assert iou[0, 1] == pytest.approx(0.0)


def test_mask_nms_collapses_duplicates(runner_module):
    # 30 near-identical masks of one object — NMS at 0.7 should keep one.
    base = np.zeros((16, 16), dtype=bool); base[4:12, 4:12] = True
    masks = [base.copy() for _ in range(30)]
    boxes = [[4.0, 4.0, 12.0, 12.0]] * 30
    scores = list(np.linspace(0.5, 0.9, 30))
    km, kb, ks = runner_module._mask_nms(masks, boxes, scores, iou_thresh=0.7)
    assert len(km) == 1
    # Highest score is kept (NMS sorts score-descending).
    assert ks[0] == pytest.approx(0.9)


def test_mask_nms_preserves_distinct_objects(runner_module):
    a = np.zeros((16, 16), dtype=bool); a[1:5, 1:5] = True
    b = np.zeros((16, 16), dtype=bool); b[10:14, 10:14] = True
    c = np.zeros((16, 16), dtype=bool); c[1:5, 10:14] = True
    km, _, _ = runner_module._mask_nms(
        [a, b, c], [[1, 1, 5, 5], [10, 10, 14, 14], [10, 1, 14, 5]],
        [0.9, 0.85, 0.8], iou_thresh=0.7,
    )
    assert len(km) == 3


def test_hungarian_iou_link_assignment(runner_module):
    """Tracks at the same location across frames are linked, new objects get -1."""
    prev_a = np.zeros((16, 16), dtype=bool); prev_a[2:6, 2:6] = True
    prev_b = np.zeros((16, 16), dtype=bool); prev_b[10:14, 10:14] = True
    # Current frame: track A drifted slightly, track B vanished, NEW C appeared.
    curr_a = np.zeros((16, 16), dtype=bool); curr_a[3:7, 3:7] = True
    curr_c = np.zeros((16, 16), dtype=bool); curr_c[1:5, 11:15] = True
    assignment = runner_module._hungarian_iou_link(
        [prev_a, prev_b], [curr_a, curr_c], iou_min=0.30,
    )
    # curr_a matches prev_a (index 0); curr_c has no good match → -1.
    assert assignment[0] == 0
    assert assignment[1] == -1


def test_hungarian_iou_link_empty_inputs(runner_module):
    assert runner_module._hungarian_iou_link([], [], iou_min=0.3) == []
    m = np.zeros((4, 4), dtype=bool); m[1:3, 1:3] = True
    assert runner_module._hungarian_iou_link([], [m], iou_min=0.3) == [-1]
    assert runner_module._hungarian_iou_link([m], [], iou_min=0.3) == []
