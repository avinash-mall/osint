from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from pycocotools import mask as coco_mask


logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


# Per-detector trust weights for WBF fusion. Sourced from the plan's
# triage-set tuning recommendations (T2.8). Operators override via the
# SAM3_WBF_WEIGHTS env (JSON dict source_layer -> float).
_DEFAULT_WBF_WEIGHTS: dict[str, float] = {
    "sam3":           0.5,
    "dota_obb":       1.0,
    "grounding_dino": 0.3,
    "yoloe":          0.5,
    "sar_cfar":       0.7,
}


def _wbf_weights() -> dict[str, float]:
    """Merge SAM3_WBF_WEIGHTS env overrides on top of the defaults."""
    raw = os.getenv("SAM3_WBF_WEIGHTS", "").strip()
    if not raw:
        return dict(_DEFAULT_WBF_WEIGHTS)
    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError:
        return dict(_DEFAULT_WBF_WEIGHTS)
    merged = dict(_DEFAULT_WBF_WEIGHTS)
    if not isinstance(overrides, dict):
        return merged
    for k, v in overrides.items():
        try:
            merged[str(k).lower()] = float(v)
        except (TypeError, ValueError):
            continue
    return merged


_WBF_IOU_THRESHOLD = float(os.getenv("SAM3_WBF_IOU", "0.55"))
_WBF_SKIP_BOX_THRESHOLD = float(os.getenv("SAM3_WBF_SKIP_THRESHOLD", "0.05"))

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
    edge_truncated = _touches_edge(work)  # full-mask: edge = image edge
    if not work.any():
        return _hbb_fallback(bbox_xyxy, width, height, edge_truncated)

    # Run the cv2 / np.where work on the mask's own bounding-box ROI instead of
    # the full frame, so per-detection cost is O(object), not O(image). The ROI
    # is derived from the mask itself (cheap 1-D reductions) — never the passed
    # bbox — so no mask pixel can be clipped. minAreaRect points are offset back
    # to full-image coordinates before normalising, so the output is identical
    # to operating on the full mask.
    rows = np.any(work, axis=1)
    cols = np.any(work, axis=0)
    y_idx = np.where(rows)[0]
    x_idx = np.where(cols)[0]
    y0, y1 = int(y_idx[0]), int(y_idx[-1])
    x0, x1 = int(x_idx[0]), int(x_idx[-1])
    extent = max(1, min(x1 - x0 + 1, y1 - y0 + 1))
    kernel_size = int(round(extent * OBB_OPENING_KERNEL_PCT))
    # Pad the ROI by the kernel size so MORPH_OPEN's footprint only ever reaches
    # real (zero) mask pixels, never the ROI border — identical to full-frame.
    pad = kernel_size if kernel_size >= 2 else 0
    ry0, ry1 = max(0, y0 - pad), min(work.shape[0], y1 + 1 + pad)
    rx0, rx1 = max(0, x0 - pad), min(work.shape[1], x1 + 1 + pad)
    binary = work[ry0:ry1, rx0:rx1].astype(np.uint8)

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
    pts = cv2.boxPoints(rect) + np.array([rx0, ry0], dtype=np.float32)  # ROI -> full image
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


def wbf_fusion(
    detections: list[dict[str, Any]],
    image_w: int,
    image_h: int,
    *,
    agnostic: bool = False,
) -> list[dict[str, Any]]:
    """Weighted Boxes Fusion across detector source layers.

    Groups input detections by ``source_layer``, builds the per-source
    lists `(boxes_xyxy_norm, scores, labels)`, and calls
    ``ensemble_boxes.weighted_boxes_fusion`` with per-source weights from
    :func:`_wbf_weights`. The fused boxes are remapped to detection dicts
    by picking the highest-confidence input detection that contributed to
    each fused box (so ``mask_rle`` / OBB / ``source_layer`` survive),
    overriding ``confidence`` with the WBF-fused score and tagging
    ``wbf_member_count`` + ``wbf_member_sources`` on the survivor.

    When ``agnostic`` is True, fusion ignores class identity (one
    universal label). Otherwise, fusion is per-class.

    If ``ensemble_boxes`` is not importable, falls back to
    :func:`mask_aware_nms` with the same IoU threshold.
    """
    if not detections:
        return []
    try:
        from ensemble_boxes import weighted_boxes_fusion  # type: ignore
    except ImportError:
        logger.warning("ensemble_boxes unavailable; falling back to mask_aware_nms")
        return mask_aware_nms(detections, iou=_WBF_IOU_THRESHOLD, agnostic=agnostic)

    width = max(1, int(image_w))
    height = max(1, int(image_h))

    # Build a stable per-source ordering so weights line up with input lists.
    weights_map = _wbf_weights()
    sources_in_order: list[str] = []
    per_source_indices: dict[str, list[int]] = {}
    for idx, det in enumerate(detections):
        src = str(det.get("source_layer") or "unknown").lower()
        if src not in per_source_indices:
            per_source_indices[src] = []
            sources_in_order.append(src)
        per_source_indices[src].append(idx)

    # Universal label map (stable int per class), needed because WBF takes
    # integer labels. When agnostic, every detection gets label 0.
    if agnostic:
        label_to_int: dict[str, int] = {"__all__": 0}
    else:
        label_to_int = {}
        for det in detections:
            cls = str(det.get("class") or "")
            if cls not in label_to_int:
                label_to_int[cls] = len(label_to_int)

    boxes_list: list[list[list[float]]] = []
    scores_list: list[list[float]] = []
    labels_list: list[list[int]] = []
    weights: list[float] = []
    # Track which detection indices contributed to each per-source row.
    source_det_indices: list[list[int]] = []

    for src in sources_in_order:
        indices = per_source_indices[src]
        rows_boxes: list[list[float]] = []
        rows_scores: list[float] = []
        rows_labels: list[int] = []
        kept_indices: list[int] = []
        for idx in indices:
            det = detections[idx]
            x1, y1, x2, y2 = _xyxy_from_detection(det)
            nx1 = max(0.0, min(1.0, x1 / width))
            ny1 = max(0.0, min(1.0, y1 / height))
            nx2 = max(0.0, min(1.0, x2 / width))
            ny2 = max(0.0, min(1.0, y2 / height))
            if nx2 <= nx1 or ny2 <= ny1:
                continue
            rows_boxes.append([nx1, ny1, nx2, ny2])
            rows_scores.append(float(det.get("confidence") or 0.0))
            if agnostic:
                rows_labels.append(0)
            else:
                rows_labels.append(label_to_int.get(str(det.get("class") or ""), 0))
            kept_indices.append(idx)
        if not rows_boxes:
            continue
        boxes_list.append(rows_boxes)
        scores_list.append(rows_scores)
        labels_list.append(rows_labels)
        weights.append(float(weights_map.get(src, 0.5)))
        source_det_indices.append(kept_indices)

    if not boxes_list:
        return []

    fused_boxes, fused_scores, fused_labels = weighted_boxes_fusion(
        boxes_list,
        scores_list,
        labels_list,
        weights=weights,
        iou_thr=_WBF_IOU_THRESHOLD,
        skip_box_thr=_WBF_SKIP_BOX_THRESHOLD,
        conf_type="avg",
    )

    # For each fused box, find the contributing input detections by box
    # IoU >= iou_thr (within the same fused class when not agnostic). Pick
    # the highest-confidence one as the survivor and stamp WBF metadata.
    fused_results: list[dict[str, Any]] = []
    used_input_indices: set[int] = set()
    for fb, fs, fl in zip(fused_boxes, fused_scores, fused_labels):
        fx1, fy1, fx2, fy2 = [float(v) for v in fb]
        fb_pix = [fx1 * width, fy1 * height, fx2 * width, fy2 * height]
        members: list[int] = []
        for source_rows, kept_indices in zip(boxes_list, source_det_indices):
            for row_idx, row in enumerate(source_rows):
                det_idx = kept_indices[row_idx]
                if det_idx in used_input_indices:
                    continue
                input_pix = [row[0] * width, row[1] * height, row[2] * width, row[3] * height]
                if _box_iou_xyxy(fb_pix, input_pix) < _WBF_IOU_THRESHOLD:
                    continue
                if not agnostic:
                    cls = str(detections[det_idx].get("class") or "")
                    if label_to_int.get(cls, -1) != int(fl):
                        continue
                members.append(det_idx)
        if not members:
            # WBF can emit a fused box with no exact-IoU match (e.g. tight
            # clusters with averaged geometry). Fall back to the nearest
            # input across all sources by centre distance.
            best_idx = -1
            best_d = float("inf")
            fcx = (fx1 + fx2) / 2.0
            fcy = (fy1 + fy2) / 2.0
            for source_rows, kept_indices in zip(boxes_list, source_det_indices):
                for row_idx, row in enumerate(source_rows):
                    det_idx = kept_indices[row_idx]
                    if det_idx in used_input_indices:
                        continue
                    icx = (row[0] + row[2]) / 2.0
                    icy = (row[1] + row[3]) / 2.0
                    d = (icx - fcx) ** 2 + (icy - fcy) ** 2
                    if d < best_d:
                        best_d = d
                        best_idx = det_idx
            if best_idx < 0:
                continue
            members = [best_idx]
        # Survivor = highest-confidence member.
        members.sort(key=lambda i: float(detections[i].get("confidence") or 0.0), reverse=True)
        survivor_idx = members[0]
        survivor = dict(detections[survivor_idx])
        survivor["confidence"] = float(fs)
        survivor["wbf_member_count"] = len(members)
        survivor["wbf_member_sources"] = sorted({
            str(detections[m].get("source_layer") or "unknown").lower() for m in members
        })
        fused_results.append(survivor)
        for m in members:
            used_input_indices.add(m)

    return fused_results


def fuse_detections(
    detections: list[dict[str, Any]],
    *,
    image_w: int,
    image_h: int,
    agnostic: bool = False,
) -> list[dict[str, Any]]:
    """Dispatch cross-detector fusion by ``SAM3_FUSION_MODE`` env.

    ``wbf`` (default) -> :func:`wbf_fusion`.
    ``nms``           -> :func:`mask_aware_nms` (legacy behaviour preserved).
    """
    mode = os.getenv("SAM3_FUSION_MODE", "wbf").strip().lower()
    if mode == "wbf":
        return wbf_fusion(detections, image_w, image_h, agnostic=agnostic)
    return mask_aware_nms(detections, iou=_WBF_IOU_THRESHOLD, agnostic=agnostic)


def _box_iou_xyxy(a: list[float], b: list[float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


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
