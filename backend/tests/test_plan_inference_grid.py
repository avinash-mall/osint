"""Unit tests for plan_inference_grid block-snapped chip coverage.

Regression for the snap-down gap: with chip 1008 / overlap 252 (step 756)
and a 512-px COG block grid, snapping origins down made consecutive snapped
offsets alternate deltas of 512 and 1024 px — the 1024 step exceeds the chip
size and left recurring ~16 px never-analyzed strips on both axes while
coverage_fraction still reported 1.0. The planner now extends each window by
its snap delta so every chip ends where the un-snapped chip would have.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from worker_legacy import plan_inference_grid


def _axis_fully_covered(offsets: list[int], sizes: list[int], dim: int) -> bool:
    covered = [False] * dim
    for off, size in zip(offsets, sizes):
        for px in range(off, min(off + size, dim)):
            covered[px] = True
    return all(covered)


def test_block_snapped_grid_covers_every_pixel_both_axes():
    grid = plan_inference_grid(
        width=6000, height=6000, chip_size=1008, overlap=252,
        max_chips=0, block_size=(512, 512),
    )
    assert _axis_fully_covered(grid["x_offsets"], grid["x_window_sizes"], 6000)
    assert _axis_fully_covered(grid["y_offsets"], grid["y_window_sizes"], 6000)
    # Origins stay block-aligned — the point of the snapping.
    assert all(off % 512 == 0 for off in grid["x_offsets"])
    assert all(off % 512 == 0 for off in grid["y_offsets"])
    # Windows never overshoot the raster.
    assert all(off + size <= 6000 for off, size in zip(grid["x_offsets"], grid["x_window_sizes"]))
    assert all(off + size <= 6000 for off, size in zip(grid["y_offsets"], grid["y_window_sizes"]))


def test_unblocked_grid_unchanged_and_fully_covered():
    grid = plan_inference_grid(
        width=6000, height=4000, chip_size=1008, overlap=252, max_chips=0,
    )
    step = grid["step"]
    assert step == 756
    assert grid["x_offsets"] == [idx * step for idx in grid["x_indices"]]
    assert grid["x_window_sizes"][0] == 1008
    assert _axis_fully_covered(grid["x_offsets"], grid["x_window_sizes"], 6000)
    assert _axis_fully_covered(grid["y_offsets"], grid["y_window_sizes"], 4000)
