"""Defence-specific YOLOv8m detector from HuggingFace (`spencercdz/YOLOv8m_defence`).

18 defence categories spanning aircraft, vehicles, and ships. Complements DOTA-OBB
on the axis-aligned bbox side; both are merged via fusion.mask_aware_nms.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


DEFENCE_YOLO_REPO_ID = os.getenv("DEFENCE_YOLO_REPO_ID", "spencercdz/YOLOv8m_defence")
DEFENCE_YOLO_FILENAME = os.getenv("DEFENCE_YOLO_FILENAME", "yolov8m_defence.pt")
DEFENCE_YOLO_THRESHOLD = float(os.getenv("DEFENCE_YOLO_THRESHOLD", "0.35"))
DEFENCE_YOLO_IOU = float(os.getenv("DEFENCE_YOLO_IOU", "0.50"))
DEFENCE_YOLO_IMGSZ = int(os.getenv("DEFENCE_YOLO_IMGSZ", "1024"))


def load(device: str) -> dict[str, Any]:
    try:
        from huggingface_hub import hf_hub_download
        from ultralytics import YOLO
    except ImportError as exc:
        print(f"[yolo_defence] dependency missing: {exc}")
        return {"model": None, "device": device, "error": str(exc)}
    try:
        weights_path = hf_hub_download(repo_id=DEFENCE_YOLO_REPO_ID, filename=DEFENCE_YOLO_FILENAME)
        model = YOLO(weights_path)
        if device and device != "cpu":
            try:
                model.to(device)
            except Exception:
                pass
        return {"model": model, "device": device, "weights": weights_path, "repo_id": DEFENCE_YOLO_REPO_ID}
    except Exception as exc:
        print(f"[yolo_defence] failed to load {DEFENCE_YOLO_REPO_ID}: {exc}")
        return {"model": None, "device": device, "repo_id": DEFENCE_YOLO_REPO_ID, "error": str(exc)}


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    score_threshold: float = DEFENCE_YOLO_THRESHOLD,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    if bundle is None or bundle.get("model") is None:
        return []
    model = bundle["model"]
    height, width = image_rgb_uint8.shape[:2]
    try:
        results = model.predict(
            source=image_rgb_uint8,
            imgsz=DEFENCE_YOLO_IMGSZ,
            conf=score_threshold,
            iou=DEFENCE_YOLO_IOU,
            verbose=False,
            device=bundle.get("device"),
        )
    except Exception as exc:
        print(f"[yolo_defence] inference failed: {exc}")
        return []

    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for r in results:
        names = r.names if hasattr(r, "names") else {}
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        try:
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            cls_ids = boxes.cls.cpu().numpy().astype(int)
        except Exception:
            continue
        for box, conf, cls_id in zip(xyxy, confs, cls_ids):
            score = float(conf)
            if score < score_threshold:
                continue
            label = str(names.get(int(cls_id), f"class_{cls_id}"))
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            mask = _bbox_mask(x1, y1, x2, y2, height, width)
            out.append((mask, [x1, y1, x2, y2], score, label))
    return out


def _bbox_mask(x1: float, y1: float, x2: float, y2: float, height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    xi1 = max(0, int(round(x1))); xi2 = min(width, int(round(x2)))
    yi1 = max(0, int(round(y1))); yi2 = min(height, int(round(y2)))
    if xi2 > xi1 and yi2 > yi1:
        mask[yi1:yi2, xi1:xi2] = True
    return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False}
    return {
        "loaded": bundle.get("model") is not None,
        "repo_id": bundle.get("repo_id"),
        "threshold": DEFENCE_YOLO_THRESHOLD,
        "imgsz": DEFENCE_YOLO_IMGSZ,
        "error": bundle.get("error"),
    }
