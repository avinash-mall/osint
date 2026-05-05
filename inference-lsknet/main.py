from __future__ import annotations

import io
import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import requests
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image
from starlette.concurrency import run_in_threadpool

import cv2
from detection_policy import active_detection_policy, detection_decision

cv2.setNumThreads(0)

app = FastAPI(title="Magritte AIP Node - LSKNet Inference")

DOTA_CLASSES = (
    "plane",
    "baseball-diamond",
    "bridge",
    "ground-track-field",
    "small-vehicle",
    "large-vehicle",
    "ship",
    "tennis-court",
    "basketball-court",
    "storage-tank",
    "soccer-ball-field",
    "roundabout",
    "harbor",
    "swimming-pool",
    "helicopter",
)

DEFAULT_MODEL_DIR = Path(os.getenv("LSKNET_MODEL_DIR", "/models"))
DEFAULT_CONFIG = "/opt/mmrotate/configs/oriented_rcnn/oriented_rcnn_r50_fpn_1x_dota_le90.py"
DEFAULT_CHECKPOINT = str(DEFAULT_MODEL_DIR / "oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth")
DEFAULT_CHECKPOINT_URL = (
    "https://download.openmmlab.com/mmrotate/v0.1.0/oriented_rcnn/"
    "oriented_rcnn_r50_fpn_1x_dota_le90/"
    "oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth"
)

LSKNET_CONFIG = os.getenv("LSKNET_CONFIG", DEFAULT_CONFIG)
LSKNET_CHECKPOINT = os.getenv("LSKNET_CHECKPOINT", DEFAULT_CHECKPOINT)
LSKNET_CHECKPOINT_URL = os.getenv("LSKNET_CHECKPOINT_URL", DEFAULT_CHECKPOINT_URL)
LSKNET_CONFIDENCE_THRESHOLD = float(os.getenv("LSKNET_CONFIDENCE_THRESHOLD", "0.10"))
MAX_DETECTIONS_PER_CHIP = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "lsknet-s-dota")
DETECTION_POLICY = active_detection_policy()

detection_model = None
model_lock = threading.Lock()
model_error: str | None = None


def normalize_device(value: str) -> str:
    requested = (value or "auto").strip().lower()
    if requested and requested != "auto":
        return f"cuda:{requested}" if requested.isdigit() else requested
    try:
        import torch

        if torch.cuda.is_available():
            capability = torch.cuda.get_device_capability(0)
            device_arch = f"sm_{capability[0]}{capability[1]}"
            supported_arches = set(torch.cuda.get_arch_list())
            if supported_arches and device_arch not in supported_arches:
                # Arch not natively compiled in, but PTX JIT forward-compat will handle it.
                print(
                    f"[INFERENCE-LSKNET] CUDA device arch {device_arch} not in torch build "
                    f"arch list ({sorted(supported_arches)}); using CUDA anyway via PTX JIT"
                )
            return "cuda:0"
    except Exception:
        pass
    return "cpu"


DEVICE = normalize_device(os.getenv("DEVICE", "auto"))


def ensure_checkpoint() -> None:
    path = Path(LSKNET_CHECKPOINT)
    if path.exists():
        return
    if not LSKNET_CHECKPOINT_URL:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFERENCE-LSKNET] Downloading checkpoint to {path}")
    with requests.get(LSKNET_CHECKPOINT_URL, stream=True, timeout=600) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def load_model() -> None:
    global detection_model, model_error
    if detection_model is not None:
        return
    with model_lock:
        if detection_model is not None:
            return
        model_error = None
        try:
            ensure_checkpoint()
            import mmrotate  # noqa: F401
            from mmdet.apis import init_detector

            detection_model = init_detector(LSKNET_CONFIG, LSKNET_CHECKPOINT, device=DEVICE)
            print(
                f"[INFERENCE-LSKNET] Loaded LSKNet model config={LSKNET_CONFIG} "
                f"checkpoint={LSKNET_CHECKPOINT} device={DEVICE}"
            )
        except Exception as exc:
            model_error = str(exc)
            detection_model = None
            print(f"[INFERENCE-LSKNET] Model load failed: {exc}")


def _as_numpy(value: Any) -> np.ndarray:
    if value is None:
        return np.empty((0,))
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        return value.numpy()
    return np.asarray(value)


def rbox_to_points(cx: float, cy: float, w: float, h: float, angle: float) -> list[float]:
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    dx = w / 2.0
    dy = h / 2.0
    corners = [(-dx, -dy), (dx, -dy), (dx, dy), (-dx, dy)]
    points: list[float] = []
    for ox, oy in corners:
        points.extend([cx + ox * cos_a - oy * sin_a, cy + ox * sin_a + oy * cos_a])
    return points


def _iter_legacy_results(result: Any):
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        result = result.get("bbox_results") or result.get("results") or result.get("pred_instances") or result
    if isinstance(result, (list, tuple)):
        for class_index, rows in enumerate(result):
            arr = _as_numpy(rows)
            if arr.size == 0:
                continue
            arr = np.reshape(arr, (-1, arr.shape[-1]))
            for row in arr:
                yield class_index, row


def _iter_datasample_results(result: Any):
    pred = getattr(result, "pred_instances", None)
    if pred is None and isinstance(result, dict):
        pred = result.get("pred_instances")
    if pred is None:
        return
    bboxes = _as_numpy(getattr(pred, "bboxes", None) if not isinstance(pred, dict) else pred.get("bboxes"))
    scores = _as_numpy(getattr(pred, "scores", None) if not isinstance(pred, dict) else pred.get("scores"))
    labels = _as_numpy(getattr(pred, "labels", None) if not isinstance(pred, dict) else pred.get("labels"))
    if bboxes.size == 0 or scores.size == 0 or labels.size == 0:
        return
    bboxes = np.reshape(bboxes, (-1, bboxes.shape[-1]))
    for bbox, score, label in zip(bboxes, scores, labels):
        yield int(label), np.concatenate([bbox[:5], [float(score)]])


def iter_mmrotate_rows(result: Any):
    yielded = False
    for item in _iter_datasample_results(result) or ():
        yielded = True
        yield item
    if yielded:
        return
    yield from _iter_legacy_results(result)


def detections_from_mmrotate_result(result: Any, image_size: tuple[int, int]) -> list[dict[str, Any]]:
    img_w, img_h = image_size
    detections: list[dict[str, Any]] = []
    for class_index, row in iter_mmrotate_rows(result):
        if len(row) < 6 or class_index < 0 or class_index >= len(DOTA_CLASSES):
            continue
        cx, cy, w, h, angle, score = (float(value) for value in row[:6])
        if score < LSKNET_CONFIDENCE_THRESHOLD or w <= 0 or h <= 0:
            continue
        cls_name = DOTA_CLASSES[class_index]
        decision = detection_decision(cls_name, score, DETECTION_POLICY)
        if not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
            continue
        points = rbox_to_points(cx, cy, w, h, angle)
        xs = points[0::2]
        ys = points[1::2]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        detections.append({
            "class": decision["parent_class"],
            "original_class": decision["original_class"],
            "parent_class": decision["parent_class"],
            "bbox": [
                max(0.0, min(1.0, ((x1 + x2) / 2.0) / img_w)),
                max(0.0, min(1.0, ((y1 + y2) / 2.0) / img_h)),
                max(0.0, min(1.0, (x2 - x1) / img_w)),
                max(0.0, min(1.0, (y2 - y1) / img_h)),
            ],
            "obb": [
                max(0.0, min(1.0, points[index] / (img_w if index % 2 == 0 else img_h)))
                for index in range(8)
            ],
            "confidence": score,
            **decision,
        })
    detections.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    if MAX_DETECTIONS_PER_CHIP > 0:
        detections = detections[:MAX_DETECTIONS_PER_CHIP]
    return detections


def run_inference(image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None) -> dict[str, Any]:
    load_model()
    if detection_model is None:
        raise HTTPException(status_code=503, detail=f"No LSKNet model loaded: {model_error or 'unknown error'}")
    start_time = time.time()
    try:
        import torch
        from mmdet.apis import inference_detector

        with model_lock, torch.inference_mode():
            raw_result = inference_detector(detection_model, image_array)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LSKNet inference failed: {exc}") from exc
    detections = detections_from_mmrotate_result(raw_result, image_size)
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": LSKNET_CHECKPOINT,
        "config": LSKNET_CONFIG,
        "task": "rotated_detect",
        "device": DEVICE,
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": DETECTION_POLICY["global_confidence_floor"],
        "confidence_threshold": LSKNET_CONFIDENCE_THRESHOLD,
    }


@app.on_event("startup")
def startup_event() -> None:
    load_model()


def decode_image(contents: bytes) -> tuple[np.ndarray, tuple[int, int]]:
    pil_image = Image.open(io.BytesIO(contents))
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    image_array = np.array(pil_image)
    return image_array, pil_image.size


@app.post("/detect")
async def detect_objects(
    image: UploadFile = File(...),
    metadata: str = Form("{}"),
):
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}
    contents = await image.read()
    try:
        image_array, image_size = await run_in_threadpool(decode_image, contents)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {exc}") from exc
    result = await run_in_threadpool(run_inference, image_array, image_size, meta)
    result["input_metadata"] = meta
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": detection_model is not None,
        "model_error": model_error,
        "model_path": LSKNET_CHECKPOINT,
        "model_exists": Path(LSKNET_CHECKPOINT).exists(),
        "config_path": LSKNET_CONFIG,
        "config_exists": Path(LSKNET_CONFIG).exists(),
        "device": DEVICE,
        "model_version": MODEL_VERSION,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": LSKNET_CONFIDENCE_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
        "classes": DOTA_CLASSES,
    }
