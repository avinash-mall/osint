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

from detection_policy import active_detection_policy, detection_decision


app = FastAPI(title="Magritte AIP Node - MMRotate Inference")

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

DEFAULT_MODEL_DIR = Path(os.getenv("MMROTATE_MODEL_DIR", "/models"))
DEFAULT_CONFIG = "/opt/mmrotate/configs/oriented_rcnn/oriented_rcnn_r50_fpn_1x_dota_le90.py"
DEFAULT_CHECKPOINT = str(DEFAULT_MODEL_DIR / "oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth")
DEFAULT_CHECKPOINT_URL = (
    "https://download.openmmlab.com/mmrotate/v0.1.0/oriented_rcnn/"
    "oriented_rcnn_r50_fpn_1x_dota_le90/"
    "oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth"
)

MMROTATE_CONFIG = os.getenv("MMROTATE_CONFIG", DEFAULT_CONFIG)
MMROTATE_CHECKPOINT = os.getenv("MMROTATE_CHECKPOINT", DEFAULT_CHECKPOINT)
MMROTATE_CHECKPOINT_URL = os.getenv("MMROTATE_CHECKPOINT_URL", DEFAULT_CHECKPOINT_URL)
MMROTATE_CONFIDENCE_THRESHOLD = float(os.getenv("MMROTATE_CONFIDENCE_THRESHOLD", "0.10"))
MAX_DETECTIONS_PER_CHIP = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "mmrotate-oriented-rcnn-dota")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
MMROTATE_GPU_PROFILE = os.getenv("MMROTATE_GPU_PROFILE", "unknown")
DETECTION_POLICY = active_detection_policy()

detection_model = None
model_lock = threading.Lock()
model_error: str | None = None


def _cuda_unsupported_arch_policy() -> str:
    policy = os.getenv("CUDA_UNSUPPORTED_ARCH_POLICY", "cpu").strip().lower()
    return policy if policy in {"cpu", "cuda"} else "cpu"


def _auto_cuda_device(torch_module: Any) -> str | None:
    supported_arches = set(torch_module.cuda.get_arch_list())
    unsupported: list[str] = []
    for index in range(torch_module.cuda.device_count()):
        capability = torch_module.cuda.get_device_capability(index)
        device_arch = f"sm_{capability[0]}{capability[1]}"
        device_name = torch_module.cuda.get_device_name(index)
        if not supported_arches or device_arch in supported_arches:
            return f"cuda:{index}"
        unsupported.append(f"cuda:{index} {device_name} {device_arch}")

    if unsupported:
        message = (
            f"[INFERENCE-MMROTATE] No visible CUDA device has an arch in the torch build "
            f"arch list ({sorted(supported_arches)}); unsupported devices: {unsupported}"
        )
        if _cuda_unsupported_arch_policy() == "cuda":
            print(f"{message}; forcing cuda:0 by CUDA_UNSUPPORTED_ARCH_POLICY=cuda")
            return "cuda:0"
        print(f"{message}; falling back to CPU")
    return None


def normalize_device(value: str) -> str:
    requested = (value or "auto").strip().lower()
    if requested and requested != "auto":
        return f"cuda:{requested}" if requested.isdigit() else requested
    try:
        import torch

        if torch.cuda.is_available():
            device = _auto_cuda_device(torch)
            if device:
                return device
    except Exception:
        pass
    return "cpu"


DEVICE = normalize_device(os.getenv("DEVICE", "auto"))


def torch_cuda_diagnostics() -> dict[str, Any]:
    try:
        import torch

        diagnostics: dict[str, Any] = {
            "torch_version": getattr(torch, "__version__", None),
            "torch_cuda": getattr(torch.version, "cuda", None),
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
            "torch_cuda_arch_list": torch.cuda.get_arch_list() if hasattr(torch.cuda, "get_arch_list") else [],
            "visible_devices": [],
        }
        if torch.cuda.is_available():
            diagnostics["visible_devices"] = [
                {
                    "index": index,
                    "name": torch.cuda.get_device_name(index),
                    "capability": list(torch.cuda.get_device_capability(index)),
                }
                for index in range(torch.cuda.device_count())
            ]
        return diagnostics
    except Exception as exc:
        return {"error": str(exc)}


def ensure_checkpoint() -> None:
    path = Path(MMROTATE_CHECKPOINT)
    if path.exists():
        return
    if not MMROTATE_CHECKPOINT_URL:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFERENCE-MMROTATE] Downloading checkpoint to {path}")
    with requests.get(MMROTATE_CHECKPOINT_URL, stream=True, timeout=600) as response:
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

            detection_model = init_detector(MMROTATE_CONFIG, MMROTATE_CHECKPOINT, device=DEVICE)
            print(
                f"[INFERENCE-MMROTATE] Loaded MMRotate model config={MMROTATE_CONFIG} "
                f"checkpoint={MMROTATE_CHECKPOINT} device={DEVICE}"
            )
        except Exception as exc:
            model_error = str(exc)
            detection_model = None
            print(f"[INFERENCE-MMROTATE] Model load failed: {exc}")


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
        if score < MMROTATE_CONFIDENCE_THRESHOLD or w <= 0 or h <= 0:
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
        raise HTTPException(status_code=503, detail=f"No MMRotate model loaded: {model_error or 'unknown error'}")
    start_time = time.time()
    try:
        import torch
        from mmdet.apis import inference_detector

        on_cuda = DEVICE.startswith("cuda")
        with model_lock, torch.inference_mode():
            if on_cuda:
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    raw_result = inference_detector(detection_model, image_array)
            else:
                raw_result = inference_detector(detection_model, image_array)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"MMRotate inference failed: {exc}") from exc
    detections = detections_from_mmrotate_result(raw_result, image_size)
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": MMROTATE_CHECKPOINT,
        "config": MMROTATE_CONFIG,
        "task": "rotated_detect",
        "device": DEVICE,
        "gpu_model": GPU_MODEL,
        "gpu_profile": MMROTATE_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": DETECTION_POLICY["global_confidence_floor"],
        "confidence_threshold": MMROTATE_CONFIDENCE_THRESHOLD,
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
        "model_path": MMROTATE_CHECKPOINT,
        "model_exists": Path(MMROTATE_CHECKPOINT).exists(),
        "config_path": MMROTATE_CONFIG,
        "config_exists": Path(MMROTATE_CONFIG).exists(),
        "device": DEVICE,
        "gpu_model": GPU_MODEL,
        "gpu_profile": MMROTATE_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": MMROTATE_CONFIDENCE_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
        "classes": DOTA_CLASSES,
    }
