from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pycocotools import mask as coco_mask


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"

try:
    spec = importlib.util.spec_from_file_location("backend_detection_policy", BACKEND_DIR / "detection_policy.py")
    if spec is None or spec.loader is None:
        raise ImportError("backend detection policy unavailable")
    backend_detection_policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend_detection_policy)
    parent_class_for_label = backend_detection_policy.parent_class_for_label
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
    return labels


def mask_aware_nms(
    detections: list[dict[str, Any]],
    iou: float = 0.50,
    *,
    agnostic: bool = False,
    soft: bool = False,
) -> list[dict[str, Any]]:
    """Mask-aware NMS with optional class-agnostic and Soft-NMS modes.

    ``agnostic``: when True, suppress overlapping detections regardless of
    their ``class`` field — useful at the cross-tile stitch step where SAM3
    and a specialist detector often label the same object differently.

    ``soft``: when True, linearly decay overlapping detections' confidence
    by ``(1 - mask_iou)`` instead of dropping them outright. Raises recall
    in dense scenes (parking lots, ports) at the cost of more low-conf
    candidates downstream.
    """
    if not detections:
        return []
    ranked = sorted(detections, key=lambda d: float(d.get("confidence") or 0.0), reverse=True)
    rles = [_rle_for_coco_ops(d["mask_rle"]) for d in ranked]
    boxes = np.asarray([_xyxy_from_detection(d) for d in ranked], dtype=np.float32)
    areas = np.asarray([_detection_area(d, rle) for d, rle in zip(ranked, rles)], dtype=np.float32)
    keep: list[dict[str, Any]] = []
    suppressed = [False] * len(ranked)
    for i, det in enumerate(ranked):
        if suppressed[i]:
            continue
        keep.append(det)
        candidates: list[int] = []
        for j in range(i + 1, len(ranked)):
            if suppressed[j]:
                continue
            if not agnostic and det.get("class") != ranked[j].get("class"):
                continue
            if _can_reach_mask_iou(boxes[i], boxes[j], float(areas[i]), float(areas[j]), iou):
                candidates.append(j)
        if not candidates:
            continue
        # coco_mask.iou's third argument is per-element iscrowd flags, not
        # category ids — we pass all zeros (no crowd encoding). Class
        # gating happens above at line 154 when not agnostic; when
        # agnostic, candidates spans all classes and the IoU is computed
        # class-blind by design. Do not interpret the zeros as class ids.
        mask_ious = coco_mask.iou([rles[i]], [rles[j] for j in candidates], [0] * len(candidates))[0]
        for j, mask_iou in zip(candidates, mask_ious):
            miou = float(mask_iou)
            if miou < iou:
                continue
            if soft:
                # Linear Soft-NMS: keep the detection but down-weight it.
                ranked[j]["confidence"] = float(ranked[j].get("confidence") or 0.0) * (1.0 - miou)
            else:
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


def _rle_for_coco_ops(rle: dict[str, Any]) -> dict[str, Any]:
    payload = dict(rle)
    counts = payload.get("counts", b"")
    if isinstance(counts, str):
        payload["counts"] = base64.b64decode(counts)
    return payload


def _detection_area(det: dict[str, Any], rle: dict[str, Any]) -> float:
    try:
        area = max(0.0, float(det.get("area") or 0.0))
    except (TypeError, ValueError):
        area = 0.0
    return area if area > 0.0 else float(coco_mask.area(rle))


def _xyxy_from_detection(det: dict[str, Any]) -> list[float]:
    bbox = det.get("bbox") or []
    size = (det.get("mask_rle") or {}).get("size") or [1, 1]
    height, width = int(size[0]), int(size[1])
    if len(bbox) >= 4:
        cx, cy, bw, bh = [float(value) for value in bbox[:4]]
        x1 = (cx - bw / 2.0) * width
        y1 = (cy - bh / 2.0) * height
        x2 = (cx + bw / 2.0) * width
        y2 = (cy + bh / 2.0) * height
        return [x1, y1, x2, y2]
    x, y, w, h = [float(value) for value in coco_mask.toBbox(_rle_for_coco_ops(det["mask_rle"]))]
    return [x, y, x + w, y + h]


def _can_reach_mask_iou(box_a: np.ndarray, box_b: np.ndarray, area_a: float, area_b: float, threshold: float) -> bool:
    x1 = max(float(box_a[0]), float(box_b[0]))
    y1 = max(float(box_a[1]), float(box_b[1]))
    x2 = min(float(box_a[2]), float(box_b[2]))
    y2 = min(float(box_a[3]), float(box_b[3]))
    box_intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if box_intersection <= 0.0:
        return False

    # For mask IoU to reach t, mask_intersection must be at least
    # t * (area_a + area_b) / (1 + t). Since mask_intersection cannot exceed
    # bbox intersection, pairs below this bound can be skipped exactly.
    required_intersection = (threshold * (area_a + area_b)) / (1.0 + threshold)
    return box_intersection + 1e-6 >= required_intersection


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
