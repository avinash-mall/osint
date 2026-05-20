"""YOLO26-OBB DOTA-v1 specialist detector.

Lightweight Ultralytics YOLO wrapper that returns detections in the same tuple
shape SAM 3's ``run_text_prompts`` emits: ``(mask, bbox_xyxy, score, label)``.

Categories (18, from DOTA-v1):
  plane, ship, storage tank, baseball diamond, tennis court, basketball court,
  ground track field, harbor, bridge, large vehicle, small vehicle, helicopter,
  roundabout, soccer ball field, swimming pool, container crane, airport, helipad.

These map cleanly into the defence ontology branches via the existing
``classifyToBranch`` regex matchers — no schema change needed.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import numpy as np


DOTA_OBB_MODEL_ID = os.getenv("DOTA_OBB_MODEL_ID", "yolo26m-obb.pt")
DOTA_OBB_THRESHOLD = float(os.getenv("DOTA_OBB_THRESHOLD", "0.30"))
DOTA_OBB_IOU = float(os.getenv("DOTA_OBB_IOU", "0.50"))
DOTA_OBB_IMGSZ = int(os.getenv("DOTA_OBB_IMGSZ", "1024"))

# GPU optimization flags (same env vars as YOLOE — set once per process by
# scripts/gpu_profiles.py:runtime_env).
DOTA_OBB_FUSE = os.getenv("SAM3_YOLO_FUSE", "1").strip().lower() in {"1", "true", "yes", "on"}
DOTA_OBB_HALF = os.getenv("SAM3_YOLO_HALF", "0").strip().lower() in {"1", "true", "yes", "on"}
DOTA_OBB_CHANNELS_LAST = os.getenv("SAM3_YOLO_CHANNELS_LAST", "0").strip().lower() in {"1", "true", "yes", "on"}


def load(device: str) -> dict[str, Any]:
    """Load the YOLO-OBB model. Ultralytics auto-downloads weights to its cache."""
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        print(f"[dota_obb] ultralytics not installed: {exc}")
        return {"model": None, "device": device, "model_id": DOTA_OBB_MODEL_ID, "error": str(exc)}
    from inference_utils import apply_yolo_optimizations

    try:
        model = YOLO(DOTA_OBB_MODEL_ID)
        if device and device != "cpu":
            try:
                model.to(device)
            except Exception:
                pass
            apply_yolo_optimizations(
                model,
                half=DOTA_OBB_HALF,
                fuse=DOTA_OBB_FUSE,
                channels_last=DOTA_OBB_CHANNELS_LAST,
            )
        return {"model": model, "device": device, "model_id": DOTA_OBB_MODEL_ID}
    except Exception as exc:
        print(f"[dota_obb] failed to load {DOTA_OBB_MODEL_ID}: {exc}")
        return {"model": None, "device": device, "model_id": DOTA_OBB_MODEL_ID, "error": str(exc)}


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    score_threshold: float = DOTA_OBB_THRESHOLD,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Run DOTA-OBB on a single chip; return list of SAM3-shaped candidates."""
    if bundle is None or bundle.get("model") is None:
        return []
    model = bundle["model"]
    height, width = image_rgb_uint8.shape[:2]
    from inference_utils import safe_predict, cuda_cleanup

    def _do_predict():
        return model.predict(
            source=image_rgb_uint8,
            imgsz=DOTA_OBB_IMGSZ,
            conf=score_threshold,
            iou=DOTA_OBB_IOU,
            verbose=False,
            device=bundle.get("device"),
            half=DOTA_OBB_HALF,
        )

    try:
        results = safe_predict(
            _do_predict,
            on_oom=cuda_cleanup,
            max_retries=1,
            fallback=lambda: [],
            name="dota_obb.predict",
        )
    except Exception as exc:
        print(f"[dota_obb] inference failed: {exc}")
        return []

    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for r in results:
        names = r.names if hasattr(r, "names") else {}
        obb = getattr(r, "obb", None)
        if obb is None:
            continue
        try:
            xyxyxyxy = obb.xyxyxyxy.cpu().numpy()  # (N, 4, 2)
            confs = obb.conf.cpu().numpy()
            cls_ids = obb.cls.cpu().numpy().astype(int)
        except Exception:
            continue
        for poly, conf, cls_id in zip(xyxyxyxy, confs, cls_ids):
            label = str(names.get(int(cls_id), f"class_{cls_id}"))
            score = float(conf)
            if score < score_threshold:
                continue
            x1 = float(poly[:, 0].min()); x2 = float(poly[:, 0].max())
            y1 = float(poly[:, 1].min()); y2 = float(poly[:, 1].max())
            mask = _polygon_mask(poly, height, width)
            out.append((mask, [x1, y1, x2, y2], score, label))
    return out


def _polygon_mask(poly: np.ndarray, height: int, width: int) -> np.ndarray:
    """Rasterise a 4-point polygon into a boolean mask matching the chip size."""
    try:
        import cv2  # type: ignore
        mask = np.zeros((height, width), dtype=np.uint8)
        pts = poly.astype(np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [pts], 1)
        return mask.astype(bool)
    except Exception:
        # Fallback: axis-aligned bounding box mask
        x1 = max(0, int(poly[:, 0].min()))
        x2 = min(width, int(poly[:, 0].max()))
        y1 = max(0, int(poly[:, 1].min()))
        y2 = min(height, int(poly[:, 1].max()))
        mask = np.zeros((height, width), dtype=bool)
        mask[y1:y2, x1:x2] = True
        return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False}
    return {
        "loaded": bundle.get("model") is not None,
        "model_id": bundle.get("model_id"),
        "threshold": DOTA_OBB_THRESHOLD,
        "imgsz": DOTA_OBB_IMGSZ,
        "error": bundle.get("error"),
    }
