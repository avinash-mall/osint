"""
box_metrics.py
==============
Computes per-class Precision, Recall, F1, and AP (Average Precision at IoU=0.5)
for bounding-box detections by comparing predicted boxes against ground-truth
boxes via greedy IoU matching.

Only numpy + stdlib -- no scikit-learn or other ML libraries.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

import numpy as np


# ---------------------------------------------------------------------------
# IoU helper
# ---------------------------------------------------------------------------

def iou(box_a: list, box_b: list) -> float:
    """Compute Intersection-over-Union for two boxes in [x1, y1, x2, y2] format.

    Parameters
    ----------
    box_a, box_b : list of 4 floats  [x1, y1, x2, y2]

    Returns
    -------
    float in [0.0, 1.0]
    """
    ax1, ay1, ax2, ay2 = box_a
    bx1, by1, bx2, by2 = box_b

    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)

    inter_w = max(0.0, inter_x2 - inter_x1)
    inter_h = max(0.0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)

    union_area = area_a + area_b - inter_area
    if union_area <= 0.0:
        return 0.0
    return inter_area / union_area


# ---------------------------------------------------------------------------
# Average Precision -- 11-point PASCAL VOC interpolation
# ---------------------------------------------------------------------------

def _compute_ap_11point(tp_flags: list, n_gt: int) -> float:
    """Compute AP using 11-point interpolation (PASCAL VOC style).

    Parameters
    ----------
    tp_flags : list of 0/1 in score-descending order
        1 = true positive, 0 = false positive
    n_gt : int
        Total number of ground-truth boxes for this class.

    Returns
    -------
    float  Average Precision in [0.0, 1.0]
    """
    if n_gt == 0:
        return 0.0

    tp_flags_arr = np.array(tp_flags, dtype=float)
    tp_cumsum = np.cumsum(tp_flags_arr)
    fp_cumsum = np.cumsum(1.0 - tp_flags_arr)

    recalls = tp_cumsum / n_gt
    precisions = tp_cumsum / (tp_cumsum + fp_cumsum)

    # 11-point interpolation
    ap = 0.0
    for recall_thr in np.linspace(0.0, 1.0, 11):
        # Max precision at recall >= recall_thr
        prec_at_thr = precisions[recalls >= recall_thr]
        ap += float(np.max(prec_at_thr)) if len(prec_at_thr) > 0 else 0.0

    return ap / 11.0


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def compute_box_metrics(
    predictions: list,
    ground_truth: list,
    iou_threshold: float = 0.5,
    normalizer: Optional[Callable] = None,
    layer: str = "",
) -> dict:
    """Compute per-class P/R/F1/AP and aggregate mAP@0.5 for box detections.

    Parameters
    ----------
    predictions : list of dict
        Each dict has keys:
            "label"     : str
            "bbox_xyxy" : [x1, y1, x2, y2]
            "score"     : float  (confidence)
    ground_truth : list of dict
        Each dict has keys:
            "label"     : str
            "bbox_xyxy" : [x1, y1, x2, y2]
    iou_threshold : float
        IoU threshold for a match (default 0.5).
    normalizer : callable(label: str, layer: str) -> str, optional
        When provided, prediction labels are mapped through this function
        before matching against GT labels.  GT labels are used as-is.
    layer : str
        Passed as the second argument to normalizer.  Ignored when
        normalizer is None.

    Returns
    -------
    dict with keys:
        "per_class"          : dict label -> {precision, recall, f1, tp, fp, fn, ap}
        "macro_f1"           : float
        "map_50"             : float
        "total_predictions"  : int
        "total_ground_truth" : int
    """
    # ------------------------------------------------------------------
    # Normalise prediction labels when a normalizer is supplied
    # ------------------------------------------------------------------
    norm_preds = []
    for p in predictions:
        label = p["label"]
        if normalizer is not None:
            label = normalizer(label, layer)
        norm_preds.append({
            "label": label,
            "bbox_xyxy": p["bbox_xyxy"],
            "score": p.get("score", 0.0),
        })

    # ------------------------------------------------------------------
    # Group by label
    # ------------------------------------------------------------------
    # Collect all labels from both GT and (normalised) predictions
    all_labels = set()
    for p in norm_preds:
        all_labels.add(p["label"])
    for g in ground_truth:
        all_labels.add(g["label"])

    # Per-label GT boxes (list of bbox_xyxy)
    gt_by_label = defaultdict(list)
    for g in ground_truth:
        gt_by_label[g["label"]].append(g["bbox_xyxy"])

    # Per-label predictions sorted by score descending
    preds_by_label = defaultdict(list)
    for p in norm_preds:
        preds_by_label[p["label"]].append(p)
    for label in preds_by_label:
        preds_by_label[label].sort(key=lambda x: x["score"], reverse=True)

    # ------------------------------------------------------------------
    # Per-class matching
    # ------------------------------------------------------------------
    per_class = {}

    for label in sorted(all_labels):
        gt_boxes = gt_by_label[label]          # may be empty
        preds = preds_by_label[label]          # may be empty; already sorted

        n_gt = len(gt_boxes)
        n_pred = len(preds)

        matched_gt = [False] * n_gt            # tracks which GT boxes are used
        tp_flags = []                          # 1=TP, 0=FP, in score order

        for pred in preds:
            pred_box = pred["bbox_xyxy"]
            best_iou = -1.0
            best_idx = -1

            for gi, gt_box in enumerate(gt_boxes):
                if matched_gt[gi]:
                    continue
                current_iou = iou(pred_box, gt_box)
                if current_iou > best_iou:
                    best_iou = current_iou
                    best_idx = gi

            if best_iou >= iou_threshold and best_idx >= 0:
                matched_gt[best_idx] = True
                tp_flags.append(1)
            else:
                tp_flags.append(0)

        tp = int(sum(tp_flags))
        fp = n_pred - tp
        fn = n_gt - tp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )

        if n_gt == 0:
            ap = 0.0
        else:
            ap = _compute_ap_11point(tp_flags, n_gt)

        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "ap": ap,
        }

    # ------------------------------------------------------------------
    # Aggregate metrics
    # ------------------------------------------------------------------
    # Only include classes that appear in GT for macro averages
    gt_labels = set(gt_by_label.keys())
    f1_values = [per_class[l]["f1"] for l in per_class if l in gt_labels]
    ap_values = [per_class[l]["ap"] for l in per_class if l in gt_labels]

    macro_f1 = float(np.mean(f1_values)) if f1_values else 0.0
    map_50 = float(np.mean(ap_values)) if ap_values else 0.0

    return {
        "per_class": per_class,
        "macro_f1": macro_f1,
        "map_50": map_50,
        "total_predictions": len(predictions),
        "total_ground_truth": len(ground_truth),
    }
