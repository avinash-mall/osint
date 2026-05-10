"""
mask_metrics.py
===============
Chip-level mask IoU metrics for PRITHVI semantic segmentation heads.

PRITHVI outputs ``prithvi_labels`` per detection — a dict mapping task name to
predicted label, e.g.::

    {"burn_scar": "burn", "flood": "no_flood", "crop": "cropland"}

Since the harness only receives bounding boxes from the service (not per-pixel
masks), we use a **chip-level label aggregation** simplification:

    * If ANY detection in the chip has ``prithvi_labels["<task>"] == "<positive_label>"``,
      we classify the whole chip as positive for that task.
    * Otherwise the chip is negative.

IoU is then computed as a 1×1 confusion table over chips:
    IoU = TP / (TP + FP + FN)

This gives a directional quality signal without requiring pixel-level masks
from the API.  It is intentionally coarse — a pixel-level mask head would
be needed for full semantic segmentation evaluation.

Public API
----------
- ``chip_level_iou(chips_results)`` — aggregate TP/FP/FN/TN and compute IoU.
- ``compute_mask_iou(pred_labels, gt_masks, chip_size)`` — higher-level helper
  that maps PRITHVI label lists to per-task IoU dicts.

Task positive-label mapping
---------------------------
The canonical positive labels recognised by PRITHVI:
    "burn_scar" → "burn"
    "flood"     → "flood"
    "crop"      → "cropland"
"""

from __future__ import annotations

from typing import Any

import numpy as np


# ---------------------------------------------------------------------------
# Task-level positive label mapping
# ---------------------------------------------------------------------------

#: Maps task name → the label string that counts as "positive".
POSITIVE_LABELS: dict[str, str] = {
    "burn_scar": "burn",
    "flood": "flood",
    "crop": "cropland",
}


# ---------------------------------------------------------------------------
# Chip-level IoU
# ---------------------------------------------------------------------------

def chip_level_iou(
    chips_results: list[dict],
) -> dict:
    """Compute TP/FP/FN/TN and IoU = TP/(TP+FP+FN) over chip-level predictions.

    Parameters
    ----------
    chips_results:
        List of dicts, each with keys:
            ``"pred_positive"`` : bool — model predicted positive for this chip.
            ``"gt_positive"``   : bool — ground truth is positive for this chip.

    Returns
    -------
    dict with keys:
        ``"tp"``  : int
        ``"fp"``  : int
        ``"fn"``  : int
        ``"tn"``  : int
        ``"iou"`` : float  — IoU = TP / (TP + FP + FN); 0.0 when denominator is 0.
        ``"n_chips"`` : int
    """
    tp = fp = fn = tn = 0

    for chip in chips_results:
        pred = bool(chip["pred_positive"])
        gt = bool(chip["gt_positive"])

        if pred and gt:
            tp += 1
        elif pred and not gt:
            fp += 1
        elif not pred and gt:
            fn += 1
        else:
            tn += 1

    denom = tp + fp + fn
    iou_val = tp / denom if denom > 0 else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "iou": round(iou_val, 4),
        "n_chips": len(chips_results),
    }


# ---------------------------------------------------------------------------
# Aggregate PRITHVI labels across detections → chip-level positives
# ---------------------------------------------------------------------------

def _chip_is_positive(
    detections_prithvi_labels: list[dict[str, str]],
    task: str,
) -> bool:
    """Return True if any detection in the chip predicts positive for *task*.

    Parameters
    ----------
    detections_prithvi_labels:
        List of ``prithvi_labels`` dicts from individual detections in one chip.
    task:
        Task name, e.g. ``"burn_scar"``, ``"flood"``, ``"crop"``.
    """
    positive_label = POSITIVE_LABELS.get(task)
    if positive_label is None:
        return False  # unknown task — treat as negative

    for labels_dict in detections_prithvi_labels:
        if labels_dict.get(task) == positive_label:
            return True
    return False


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def compute_mask_iou(
    pred_labels: list[dict],
    gt_masks: dict,
    chip_size: tuple[int, int],
) -> dict:
    """Compute chip-level IoU for each task in *gt_masks*.

    .. note::
        **Simplification**: pixel-level masks are not available from the service
        response — only bounding boxes.  We therefore aggregate PRITHVI labels
        to chip level: if *any* detection in the chip predicts the positive
        label for a task, the whole chip is predicted positive.  IoU is then
        a 1×1 confusion table over chips (TP/FP/FN).

        This is intentionally coarse and gives a directional quality signal.
        A pixel-level mask head would be required for rigorous evaluation.

    Parameters
    ----------
    pred_labels:
        List of dicts, each with keys:
            ``"task"``       : str  — task name (``"burn_scar"``, ``"flood"``, …).
            ``"pred_label"`` : str  — predicted label string from PRITHVI.
        Typically constructed by extracting ``prithvi_labels`` from each
        detection and flattening to ``[{"task": k, "pred_label": v}, …]``.

    gt_masks:
        Dict mapping task name → numpy bool array of shape ``(H, W)``.
        A chip is considered GT-positive for a task when ``gt_mask.any()``.

    chip_size:
        ``(H, W)`` of the chip.  Not used in chip-level aggregation but
        retained for API compatibility with future pixel-level implementations.

    Returns
    -------
    dict::

        {
          "per_task": {
            "burn_scar": {
              "iou": float,
              "pred_positive_fraction": float,
              "gt_positive_fraction": float,
            },
            ...
          },
          "mean_iou": float,
        }

    Notes
    -----
    When called per-chip (the typical harness usage), *gt_masks* should
    contain boolean scalars or 1-element arrays.  The function handles both:
    ``np.ndarray.any()`` works on scalars and full 2-D arrays alike.
    """
    # Build a quick lookup: task → bool (is predicted positive for THIS chip)
    # Reconstruct per-detection prithvi_labels dict from the flat list
    task_to_pred_label: dict[str, str] = {}
    for entry in pred_labels:
        task_to_pred_label[entry["task"]] = entry["pred_label"]

    per_task: dict[str, dict] = {}
    iou_values: list[float] = []

    for task, gt_mask in gt_masks.items():
        gt_mask_arr = np.asarray(gt_mask, dtype=bool)
        gt_positive = bool(gt_mask_arr.any())

        positive_label = POSITIVE_LABELS.get(task)
        pred_positive = (
            task_to_pred_label.get(task) == positive_label
            if positive_label is not None
            else False
        )

        # Single-chip IoU (chip-level: TP=1,FP=1,FN=1,TN=0 based on match)
        tp = int(pred_positive and gt_positive)
        fp = int(pred_positive and not gt_positive)
        fn = int(not pred_positive and gt_positive)
        denom = tp + fp + fn
        iou_val = tp / denom if denom > 0 else 0.0

        per_task[task] = {
            "iou": round(iou_val, 4),
            "pred_positive_fraction": 1.0 if pred_positive else 0.0,
            "gt_positive_fraction": 1.0 if gt_positive else 0.0,
        }
        iou_values.append(iou_val)

    mean_iou = float(np.mean(iou_values)) if iou_values else 0.0

    return {
        "per_task": per_task,
        "mean_iou": round(mean_iou, 4),
    }
