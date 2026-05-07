from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pycocotools import mask as coco_mask


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if BACKEND_DIR.exists() and str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

try:
    from detection_policy import parent_class_for_label
except Exception:
    def parent_class_for_label(label: Any) -> str:
        return str(label or "object").strip().lower().replace(" ", "_") or "object"


OBB_OPENING_KERNEL_PCT = float(os.getenv("SAM3_OBB_OPENING_KERNEL_PCT", "0.01"))
OBB_MIN_AREA_PX = int(os.getenv("SAM3_OBB_MIN_AREA_PX", "4"))


def candidate_to_detection(mask_bool, bbox_xyxy, score, label, *, image_size, modality, valid_mask=None) -> dict[str, Any]:
    width, height = image_size
    x1, y1, x2, y2 = [float(v) for v in bbox_xyxy]
    parent = parent_class_for_label(label)
    obb = mask_to_obb_record(mask_bool, [x1, y1, x2, y2], width, height, valid_mask=valid_mask)
    return {
        "class": parent,
        "original_class": label,
        "parent_class": parent,
        "bbox": [
            _clamp(((x1 + x2) / 2.0) / width),
            _clamp(((y1 + y2) / 2.0) / height),
            _clamp((x2 - x1) / width),
            _clamp((y2 - y1) / height),
        ],
        "obb": obb["points"],
        "obb_format": "yolo_obb_normalized_xyxyxyxy",
        "obb_source": obb["source"],
        "obb_angle_deg": obb["angle_deg"],
        "obb_area_px": obb["area_px"],
        "edge_truncated": obb["edge_truncated"],
        "confidence": float(score),
        "mask_rle": coco_rle(mask_bool),
        "area": int(np.asarray(mask_bool, dtype=bool).sum()),
        "modality": modality,
        "task": "sam3_open_vocab_object_detection",
    }


def mask_to_obb_record(mask_bool, bbox_xyxy, width: int, height: int, *, valid_mask=None) -> dict[str, Any]:
    work = np.asarray(mask_bool, dtype=bool)
    if valid_mask is not None:
        work = np.logical_and(work, np.asarray(valid_mask, dtype=bool))
    edge_truncated = _touches_edge(work)
    binary = work.astype(np.uint8)
    if binary.sum() == 0:
        return _hbb_fallback(bbox_xyxy, width, height, edge_truncated)

    ys, xs = np.where(binary)
    extent = max(1, min(int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)))
    kernel_size = int(round(extent * OBB_OPENING_KERNEL_PCT))
    if kernel_size >= 2:
        if kernel_size % 2 == 0:
            kernel_size += 1
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((kernel_size, kernel_size), np.uint8))

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return _hbb_fallback(bbox_xyxy, width, height, edge_truncated)
    contour = max(contours, key=cv2.contourArea)
    area = float(cv2.contourArea(contour))
    if area < OBB_MIN_AREA_PX:
        return _hbb_fallback(bbox_xyxy, width, height, edge_truncated)
    rect = cv2.minAreaRect(contour)
    pts = cv2.boxPoints(rect)
    return {
        "points": _normalize_obb_points(pts, width, height),
        "source": "mask_min_area_rect",
        "angle_deg": float(rect[2]),
        "area_px": area,
        "edge_truncated": edge_truncated,
    }


def coco_rle(mask_bool: np.ndarray) -> dict[str, Any]:
    rle = coco_mask.encode(np.asfortranarray(np.asarray(mask_bool, dtype=np.uint8)))
    rle["counts"] = base64.b64encode(rle["counts"]).decode("ascii")
    return rle


def decode_rle(rle: dict[str, Any]) -> np.ndarray:
    payload = dict(rle)
    counts = payload.get("counts", b"")
    if isinstance(counts, str):
        payload["counts"] = base64.b64decode(counts)
    return coco_mask.decode(payload).astype(bool)


def overlay_labels(mask_bool, overlays, *, threshold) -> list[str]:
    labels: list[str] = []
    mask = np.asarray(mask_bool, dtype=bool)
    if "water" in overlays and _iou(mask, overlays["water"]) >= threshold:
        labels.append("water")
    if "burn_scar" in overlays and _iou(mask, overlays["burn_scar"]) >= threshold:
        labels.append("burn_scar")
    if "crop" in overlays and mask.any():
        from prithvi_heads import crop_class_name

        ys, xs = np.where(mask)
        labels.append(f"crop:{crop_class_name(overlays['crop'], [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1])}")
    return labels


def mask_aware_nms(detections: list[dict[str, Any]], iou: float = 0.50) -> list[dict[str, Any]]:
    if not detections:
        return []
    ranked = sorted(detections, key=lambda d: float(d.get("confidence") or 0.0), reverse=True)
    masks = [decode_rle(d["mask_rle"]) for d in ranked]
    keep: list[dict[str, Any]] = []
    suppressed = [False] * len(ranked)
    for i, det in enumerate(ranked):
        if suppressed[i]:
            continue
        keep.append(det)
        for j in range(i + 1, len(ranked)):
            if suppressed[j] or det.get("class") != ranked[j].get("class"):
                continue
            if _iou(masks[i], masks[j]) >= iou:
                suppressed[j] = True
    return keep


def _normalize_obb_points(pts, width: int, height: int) -> list[float]:
    out: list[float] = []
    for px, py in pts:
        out.extend([_clamp(float(px) / width), _clamp(float(py) / height)])
    return out


def _hbb_fallback(bbox_xyxy, width: int, height: int, edge_truncated: bool) -> dict[str, Any]:
    x1, y1, x2, y2 = [float(v) for v in bbox_xyxy]
    pts = np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], dtype=np.float32)
    return {
        "points": _normalize_obb_points(pts, width, height),
        "source": "hbb_fallback",
        "angle_deg": 0.0,
        "area_px": float(max(0.0, x2 - x1) * max(0.0, y2 - y1)),
        "edge_truncated": edge_truncated,
    }


def _touches_edge(mask_bool: np.ndarray) -> bool:
    if not mask_bool.any():
        return False
    h, w = mask_bool.shape[-2:]
    return bool(mask_bool[0, :].any() or mask_bool[h - 1, :].any() or mask_bool[:, 0].any() or mask_bool[:, w - 1].any())


def _iou(a_bool, b_bool) -> float:
    a = np.asarray(a_bool, dtype=bool)
    b = np.asarray(b_bool, dtype=bool)
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / float(union) if union else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
