"""
test_mask_metrics.py
====================
Unit tests for scripts/eval_metrics/mask_metrics.py

Covers chip_level_iou and compute_mask_iou.
All expected values hand-computed.
"""
from __future__ import annotations

import sys
import os

import numpy as np
import pytest

# Ensure scripts/ is importable regardless of invocation directory
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from eval_metrics.mask_metrics import chip_level_iou, compute_mask_iou, POSITIVE_LABELS


# ---------------------------------------------------------------------------
# chip_level_iou
# ---------------------------------------------------------------------------

class TestChipLevelIou:
    """Tests for the chip_level_iou helper."""

    def test_all_true_positives(self):
        """All chips: pred=True, gt=True → IoU = 1.0."""
        chips = [{"pred_positive": True, "gt_positive": True}] * 4
        result = chip_level_iou(chips)
        assert result["tp"] == 4
        assert result["fp"] == 0
        assert result["fn"] == 0
        assert result["tn"] == 0
        assert result["iou"] == pytest.approx(1.0)

    def test_all_true_negatives(self):
        """All chips: pred=False, gt=False → IoU = 0.0 (denom is 0)."""
        chips = [{"pred_positive": False, "gt_positive": False}] * 3
        result = chip_level_iou(chips)
        assert result["tp"] == 0
        assert result["tn"] == 3
        assert result["iou"] == pytest.approx(0.0)

    def test_all_false_positives(self):
        """All chips: pred=True, gt=False → IoU = 0.0 (TP=0)."""
        chips = [{"pred_positive": True, "gt_positive": False}] * 3
        result = chip_level_iou(chips)
        assert result["fp"] == 3
        assert result["iou"] == pytest.approx(0.0)

    def test_all_false_negatives(self):
        """All chips: pred=False, gt=True → IoU = 0.0 (TP=0)."""
        chips = [{"pred_positive": False, "gt_positive": True}] * 3
        result = chip_level_iou(chips)
        assert result["fn"] == 3
        assert result["iou"] == pytest.approx(0.0)

    def test_mixed_chips(self):
        """Mixed chips: 2 TP, 1 FP, 1 FN, 1 TN → IoU = 2/(2+1+1) = 0.5."""
        chips = [
            {"pred_positive": True, "gt_positive": True},   # TP
            {"pred_positive": True, "gt_positive": True},   # TP
            {"pred_positive": True, "gt_positive": False},  # FP
            {"pred_positive": False, "gt_positive": True},  # FN
            {"pred_positive": False, "gt_positive": False}, # TN
        ]
        result = chip_level_iou(chips)
        assert result["tp"] == 2
        assert result["fp"] == 1
        assert result["fn"] == 1
        assert result["tn"] == 1
        assert result["n_chips"] == 5
        # IoU = 2 / (2+1+1) = 0.5
        assert result["iou"] == pytest.approx(0.5)

    def test_empty_list(self):
        """Empty chip list returns zeros and iou=0.0."""
        result = chip_level_iou([])
        assert result["tp"] == 0
        assert result["fp"] == 0
        assert result["fn"] == 0
        assert result["tn"] == 0
        assert result["iou"] == pytest.approx(0.0)
        assert result["n_chips"] == 0

    def test_n_chips_count(self):
        """n_chips matches the input list length."""
        chips = [{"pred_positive": True, "gt_positive": False}] * 7
        result = chip_level_iou(chips)
        assert result["n_chips"] == 7

    def test_iou_formula(self):
        """Verify IoU = TP/(TP+FP+FN) with known counts."""
        # 3 TP, 2 FP, 1 FN → IoU = 3/(3+2+1) = 0.5
        chips = (
            [{"pred_positive": True, "gt_positive": True}] * 3
            + [{"pred_positive": True, "gt_positive": False}] * 2
            + [{"pred_positive": False, "gt_positive": True}] * 1
        )
        result = chip_level_iou(chips)
        assert result["iou"] == pytest.approx(3 / (3 + 2 + 1))


# ---------------------------------------------------------------------------
# compute_mask_iou
# ---------------------------------------------------------------------------

class TestComputeMaskIou:
    """Tests for compute_mask_iou."""

    def _make_gt_mask(self, positive: bool, shape=(64, 64)):
        """Return a bool numpy mask array."""
        mask = np.zeros(shape, dtype=bool)
        if positive:
            mask[10:20, 10:20] = True
        return mask

    def test_perfect_burn_prediction(self):
        """Pred positive for burn_scar, GT positive → IoU=1.0."""
        pred_labels = [{"task": "burn_scar", "pred_label": "burn"}]
        gt_masks = {"burn_scar": self._make_gt_mask(True)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert "per_task" in result
        assert "burn_scar" in result["per_task"]
        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(1.0)
        assert result["per_task"]["burn_scar"]["pred_positive_fraction"] == pytest.approx(1.0)
        assert result["per_task"]["burn_scar"]["gt_positive_fraction"] == pytest.approx(1.0)
        assert result["mean_iou"] == pytest.approx(1.0)

    def test_false_positive_burn(self):
        """Pred positive for burn_scar, GT negative → IoU=0.0."""
        pred_labels = [{"task": "burn_scar", "pred_label": "burn"}]
        gt_masks = {"burn_scar": self._make_gt_mask(False)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(0.0)
        assert result["per_task"]["burn_scar"]["pred_positive_fraction"] == pytest.approx(1.0)
        assert result["per_task"]["burn_scar"]["gt_positive_fraction"] == pytest.approx(0.0)

    def test_false_negative_burn(self):
        """Pred negative for burn_scar, GT positive → IoU=0.0."""
        pred_labels = [{"task": "burn_scar", "pred_label": "no_burn"}]
        gt_masks = {"burn_scar": self._make_gt_mask(True)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(0.0)
        assert result["per_task"]["burn_scar"]["pred_positive_fraction"] == pytest.approx(0.0)
        assert result["per_task"]["burn_scar"]["gt_positive_fraction"] == pytest.approx(1.0)

    def test_true_negative_burn(self):
        """Pred negative, GT negative → IoU=0.0 (denom=0)."""
        pred_labels = [{"task": "burn_scar", "pred_label": "no_burn"}]
        gt_masks = {"burn_scar": self._make_gt_mask(False)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(0.0)

    def test_multiple_tasks(self):
        """Multi-task: burn_scar hit, flood miss → mean IoU = 0.5."""
        pred_labels = [
            {"task": "burn_scar", "pred_label": "burn"},
            {"task": "flood", "pred_label": "no_flood"},
        ]
        gt_masks = {
            "burn_scar": self._make_gt_mask(True),
            "flood": self._make_gt_mask(True),
        }
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(1.0)
        assert result["per_task"]["flood"]["iou"] == pytest.approx(0.0)
        assert result["mean_iou"] == pytest.approx(0.5)

    def test_no_pred_labels(self):
        """Empty pred_labels → predicted negative for all tasks."""
        gt_masks = {"burn_scar": self._make_gt_mask(True)}
        result = compute_mask_iou([], gt_masks, chip_size=(64, 64))

        assert result["per_task"]["burn_scar"]["pred_positive_fraction"] == pytest.approx(0.0)
        assert result["per_task"]["burn_scar"]["iou"] == pytest.approx(0.0)

    def test_empty_gt_masks(self):
        """Empty gt_masks → per_task empty dict, mean_iou=0.0."""
        pred_labels = [{"task": "burn_scar", "pred_label": "burn"}]
        result = compute_mask_iou(pred_labels, {}, chip_size=(64, 64))

        assert result["per_task"] == {}
        assert result["mean_iou"] == pytest.approx(0.0)

    def test_flood_task_positive_label(self):
        """flood task recognises 'flood' as the positive label."""
        pred_labels = [{"task": "flood", "pred_label": "flood"}]
        gt_masks = {"flood": self._make_gt_mask(True)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["flood"]["iou"] == pytest.approx(1.0)

    def test_crop_task_positive_label(self):
        """crop task recognises 'cropland' as the positive label."""
        pred_labels = [{"task": "crop", "pred_label": "cropland"}]
        gt_masks = {"crop": self._make_gt_mask(True)}
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))

        assert result["per_task"]["crop"]["iou"] == pytest.approx(1.0)

    def test_chip_size_parameter_accepted(self):
        """chip_size is accepted without error (API compatibility)."""
        pred_labels = []
        gt_masks = {"burn_scar": self._make_gt_mask(False)}
        # Should not raise
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(128, 128))
        assert "per_task" in result

    def test_unknown_task_treated_as_negative(self):
        """An unknown task in pred_labels doesn't raise; treated as negative."""
        pred_labels = [{"task": "unknown_task", "pred_label": "whatever"}]
        gt_masks = {"burn_scar": self._make_gt_mask(False)}
        # Should not raise
        result = compute_mask_iou(pred_labels, gt_masks, chip_size=(64, 64))
        assert "burn_scar" in result["per_task"]

    def test_positive_labels_constant(self):
        """POSITIVE_LABELS contains expected task → label mapping."""
        assert POSITIVE_LABELS["burn_scar"] == "burn"
        assert POSITIVE_LABELS["flood"] == "flood"
        assert POSITIVE_LABELS["crop"] == "cropland"
