"""
Unit tests for scripts/eval_datasets/inria_fallback.py

Run with:
    cd <repo_root>
    python -m pytest scripts/eval_datasets/tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure scripts/ is importable without installation
_SCRIPTS_DIR = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from eval_datasets.inria_fallback import load  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def chips():
    """Load up to 3 chips once for the whole test module."""
    return load(max_chips=3)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_returns_list(chips):
    """load() must return a list (possibly empty if source is unavailable)."""
    assert isinstance(chips, list)
    assert len(chips) <= 3


def test_chip_dict_has_required_keys(chips):
    """Every chip dict must contain the four required keys."""
    required = {"chip_path", "modality", "prompts", "gt_boxes"}
    for chip in chips:
        missing = required - chip.keys()
        assert not missing, f"Chip dict missing keys: {missing}"


def test_modality_is_rgb(chips):
    """modality must be the string 'rgb' for all chips."""
    for chip in chips:
        assert chip["modality"] == "rgb", (
            f"Expected modality='rgb', got {chip['modality']!r}"
        )


def test_gt_boxes_have_bbox_and_label(chips):
    """Each gt_box must have a 'label' (str) and 'bbox_xyxy' (list of 4 numbers)."""
    for chip in chips:
        for box in chip["gt_boxes"]:
            assert "label" in box, f"gt_box missing 'label': {box}"
            assert isinstance(box["label"], str), (
                f"'label' must be str, got {type(box['label'])}"
            )
            assert "bbox_xyxy" in box, f"gt_box missing 'bbox_xyxy': {box}"
            bbox = box["bbox_xyxy"]
            assert isinstance(bbox, list) and len(bbox) == 4, (
                f"'bbox_xyxy' must be a list of 4 numbers, got {bbox!r}"
            )
            assert all(isinstance(v, (int, float)) for v in bbox), (
                f"'bbox_xyxy' elements must be numeric, got {bbox!r}"
            )


def test_chip_file_exists(chips):
    """chip_path must point to a file that actually exists on disk."""
    for chip in chips:
        path = Path(chip["chip_path"])
        assert path.exists(), f"chip_path does not exist: {path}"
