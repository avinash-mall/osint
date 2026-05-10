"""
test_box_metrics.py
===================
Unit tests for scripts/eval_metrics/box_metrics.py

All expected values are hand-computed; no scikit-learn used.
"""

import sys
import os

# Ensure the scripts package is importable when running from the repo root
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import pytest
from scripts.eval_metrics.box_metrics import compute_box_metrics, iou


# ---------------------------------------------------------------------------
# Helper boxes
# ---------------------------------------------------------------------------
BOX_A = [10, 10, 50, 50]      # 40x40 = 1600 area
BOX_B = [60, 60, 90, 90]      # completely disjoint from BOX_A


# ---------------------------------------------------------------------------
# 1. Perfect match
# ---------------------------------------------------------------------------
class TestPerfectMatch:
    """One prediction exactly matching one GT box, same label, score=1.0"""

    def setup_method(self):
        self.preds = [{"label": "tank", "bbox_xyxy": BOX_A, "score": 1.0}]
        self.gt    = [{"label": "tank", "bbox_xyxy": BOX_A}]
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_precision(self):
        assert self.result["per_class"]["tank"]["precision"] == pytest.approx(1.0)

    def test_recall(self):
        assert self.result["per_class"]["tank"]["recall"] == pytest.approx(1.0)

    def test_f1(self):
        assert self.result["per_class"]["tank"]["f1"] == pytest.approx(1.0)

    def test_ap(self):
        assert self.result["per_class"]["tank"]["ap"] == pytest.approx(1.0)

    def test_tp_fp_fn(self):
        cls = self.result["per_class"]["tank"]
        assert cls["tp"] == 1
        assert cls["fp"] == 0
        assert cls["fn"] == 0

    def test_map_50(self):
        assert self.result["map_50"] == pytest.approx(1.0)

    def test_macro_f1(self):
        assert self.result["macro_f1"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2. No overlap (IoU == 0)
# ---------------------------------------------------------------------------
class TestNoOverlap:
    """One prediction far from one GT box -- IoU = 0"""

    def setup_method(self):
        self.preds = [{"label": "ship", "bbox_xyxy": BOX_A, "score": 0.9}]
        self.gt    = [{"label": "ship", "bbox_xyxy": BOX_B}]
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_iou_is_zero(self):
        assert iou(BOX_A, BOX_B) == pytest.approx(0.0)

    def test_precision(self):
        assert self.result["per_class"]["ship"]["precision"] == pytest.approx(0.0)

    def test_recall(self):
        assert self.result["per_class"]["ship"]["recall"] == pytest.approx(0.0)

    def test_f1(self):
        assert self.result["per_class"]["ship"]["f1"] == pytest.approx(0.0)

    def test_tp_fp_fn(self):
        cls = self.result["per_class"]["ship"]
        assert cls["tp"] == 0
        assert cls["fp"] == 1
        assert cls["fn"] == 1


# ---------------------------------------------------------------------------
# 3. Partial match (2 GT, 1 matching prediction)
# ---------------------------------------------------------------------------
class TestPartialMatch:
    """2 GT boxes, 1 prediction matching only the first one.
    Expected: recall=0.5, fp=0, fn=1.
    """

    def setup_method(self):
        gt_box1 = [0, 0, 40, 40]
        gt_box2 = [100, 100, 140, 140]
        pred_box = [0, 0, 40, 40]   # matches gt_box1 exactly

        self.preds = [{"label": "plane", "bbox_xyxy": pred_box, "score": 0.8}]
        self.gt    = [
            {"label": "plane", "bbox_xyxy": gt_box1},
            {"label": "plane", "bbox_xyxy": gt_box2},
        ]
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_recall(self):
        # 1 TP out of 2 GT -> recall = 0.5
        assert self.result["per_class"]["plane"]["recall"] == pytest.approx(0.5)

    def test_fp(self):
        assert self.result["per_class"]["plane"]["fp"] == 0

    def test_fn(self):
        assert self.result["per_class"]["plane"]["fn"] == 1

    def test_precision(self):
        # 1 TP, 0 FP -> precision = 1.0
        assert self.result["per_class"]["plane"]["precision"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 4. Empty predictions
# ---------------------------------------------------------------------------
class TestEmptyPredictions:
    """No predictions, 1 GT box -> all FN."""

    def setup_method(self):
        self.preds = []
        self.gt    = [{"label": "truck", "bbox_xyxy": BOX_A}]
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_precision(self):
        assert self.result["per_class"]["truck"]["precision"] == pytest.approx(0.0)

    def test_recall(self):
        assert self.result["per_class"]["truck"]["recall"] == pytest.approx(0.0)

    def test_f1(self):
        assert self.result["per_class"]["truck"]["f1"] == pytest.approx(0.0)

    def test_fn(self):
        assert self.result["per_class"]["truck"]["fn"] == 1

    def test_totals(self):
        assert self.result["total_predictions"] == 0
        assert self.result["total_ground_truth"] == 1


# ---------------------------------------------------------------------------
# 5. Empty ground truth
# ---------------------------------------------------------------------------
class TestEmptyGroundTruth:
    """1 prediction, 0 GT boxes -> all FP, precision=0.0."""

    def setup_method(self):
        self.preds = [{"label": "helicopter", "bbox_xyxy": BOX_A, "score": 0.7}]
        self.gt    = []
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_precision(self):
        # No GT -> AP=0 -> precision treated as 0 (no valid GT class)
        assert self.result["per_class"]["helicopter"]["precision"] == pytest.approx(0.0)

    def test_totals(self):
        assert self.result["total_predictions"] == 1
        assert self.result["total_ground_truth"] == 0

    def test_map_is_zero(self):
        # No GT classes -> map_50 = 0.0
        assert self.result["map_50"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# 6. mAP@0.5 across 2 classes, all matching
# ---------------------------------------------------------------------------
class TestMap50:
    """2 classes, 2 GT each, 2 predictions each, all perfectly matching.
    Expected: map_50 == 1.0, macro_f1 == 1.0.
    """

    def setup_method(self):
        self.preds = [
            {"label": "tank",  "bbox_xyxy": [0,  0,  40, 40], "score": 1.0},
            {"label": "tank",  "bbox_xyxy": [50, 50, 90, 90], "score": 0.9},
            {"label": "plane", "bbox_xyxy": [0,  0,  30, 30], "score": 1.0},
            {"label": "plane", "bbox_xyxy": [60, 60, 90, 90], "score": 0.8},
        ]
        self.gt = [
            {"label": "tank",  "bbox_xyxy": [0,  0,  40, 40]},
            {"label": "tank",  "bbox_xyxy": [50, 50, 90, 90]},
            {"label": "plane", "bbox_xyxy": [0,  0,  30, 30]},
            {"label": "plane", "bbox_xyxy": [60, 60, 90, 90]},
        ]
        self.result = compute_box_metrics(self.preds, self.gt)

    def test_map_50(self):
        assert self.result["map_50"] == pytest.approx(1.0)

    def test_macro_f1(self):
        assert self.result["macro_f1"] == pytest.approx(1.0)

    def test_per_class_ap_tank(self):
        assert self.result["per_class"]["tank"]["ap"] == pytest.approx(1.0)

    def test_per_class_ap_plane(self):
        assert self.result["per_class"]["plane"]["ap"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 7. Label normalizer
# ---------------------------------------------------------------------------
class TestWithNormalizer:
    """Predictions have label "large-vehicle", GT has label "logistics".
    A normalizer maps "large-vehicle" -> "logistics" -> tp=1.
    """

    def setup_method(self):
        def _norm(label: str, layer: str) -> str:
            if label == "large-vehicle":
                return "logistics"
            return label

        self.preds = [
            {"label": "large-vehicle", "bbox_xyxy": BOX_A, "score": 0.95}
        ]
        self.gt = [
            {"label": "logistics", "bbox_xyxy": BOX_A}
        ]
        self.result = compute_box_metrics(
            self.preds, self.gt, normalizer=_norm, layer="dota_obb"
        )

    def test_tp(self):
        assert self.result["per_class"]["logistics"]["tp"] == 1

    def test_precision(self):
        assert self.result["per_class"]["logistics"]["precision"] == pytest.approx(1.0)

    def test_recall(self):
        assert self.result["per_class"]["logistics"]["recall"] == pytest.approx(1.0)

    def test_no_large_vehicle_key(self):
        # After normalisation, "large-vehicle" should NOT appear as a key
        assert "large-vehicle" not in self.result["per_class"]
