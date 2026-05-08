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
