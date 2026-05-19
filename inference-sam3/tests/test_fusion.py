from __future__ import annotations

import numpy as np

import fusion


def test_rle_roundtrip():
    mask = np.zeros((16, 16), dtype=bool)
    mask[4:10, 5:12] = True
    rle = fusion.coco_rle(mask)
    assert np.array_equal(fusion.decode_rle(rle), mask)


def test_mask_to_obb_record_rotated_shape():
    mask = np.zeros((64, 64), dtype=np.uint8)
    import cv2

    rect = ((32, 32), (28, 10), 35)
    pts = cv2.boxPoints(rect).astype(np.int32)
    cv2.fillPoly(mask, [pts], 1)
    obb = fusion.mask_to_obb_record(mask.astype(bool), [18, 18, 46, 46], 64, 64)

    assert obb["source"] == "mask_min_area_rect"
    assert len(obb["points"]) == 8
    assert all(0.0 <= value <= 1.0 for value in obb["points"])


def test_mask_aware_nms_keeps_highest_same_class():
    mask = np.zeros((16, 16), dtype=bool)
    mask[3:10, 3:10] = True
    low = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.2, "ship", image_size=(16, 16), modality="rgb")
    high = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.9, "ship", image_size=(16, 16), modality="rgb")

    kept = fusion.mask_aware_nms([low, high], iou=0.5)

    assert len(kept) == 1
    assert kept[0]["confidence"] == 0.9


def test_mask_aware_nms_agnostic_drops_cross_class_duplicate():
    """Two overlapping detections with *different* class labels: default NMS
    keeps both (per-class), agnostic=True suppresses the lower-conf one.
    Models a SAM3+DOTA-OBB cross-tile collision where the same object got
    two different labels.
    """
    mask = np.zeros((16, 16), dtype=bool)
    mask[3:10, 3:10] = True
    ship = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.9, "ship", image_size=(16, 16), modality="rgb")
    boat = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.7, "boat", image_size=(16, 16), modality="rgb")

    # Default behaviour preserved: both classes kept.
    assert len(fusion.mask_aware_nms([ship, boat], iou=0.5)) == 2

    # Agnostic dedup: only the higher-confidence detection survives.
    out = fusion.mask_aware_nms([ship, boat], iou=0.5, agnostic=True)
    assert len(out) == 1
    assert out[0]["confidence"] == 0.9


def test_mask_aware_nms_soft_downweights_instead_of_dropping():
    """Soft-NMS keeps both overlapping detections but rescales the lower one."""
    mask = np.zeros((16, 16), dtype=bool)
    mask[3:10, 3:10] = True
    a = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.9, "ship", image_size=(16, 16), modality="rgb")
    b = fusion.candidate_to_detection(mask, [3, 3, 10, 10], 0.8, "ship", image_size=(16, 16), modality="rgb")

    kept = fusion.mask_aware_nms([a, b], iou=0.5, soft=True)
    # Soft-NMS doesn't drop b; it down-weights it. Both still appear in `keep`
    # because the loop's `suppressed[j]=True` branch is skipped for soft mode.
    assert len(kept) == 2
    confs = sorted([k["confidence"] for k in kept])
    assert confs[1] == 0.9
    assert confs[0] < 0.8  # down-weighted by (1 - iou)
