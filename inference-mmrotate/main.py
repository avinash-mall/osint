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
INFERENCE_INTERNAL_TILING = os.getenv("INFERENCE_INTERNAL_TILING", "off").strip().lower()
INFERENCE_TILE_SIZE = int(os.getenv("INFERENCE_TILE_SIZE", "1024"))
INFERENCE_TILE_OVERLAP = int(os.getenv("INFERENCE_TILE_OVERLAP", "512"))
INFERENCE_TILE_NMS_IOU = float(os.getenv("INFERENCE_TILE_NMS_IOU", "0.5"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "mmrotate-oriented-rcnn-dota")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
MMROTATE_GPU_PROFILE = os.getenv("MMROTATE_GPU_PROFILE", "unknown")
DETECTION_POLICY = active_detection_policy()

detection_models: list[dict[str, Any]] = []
model_pool_lock = threading.Lock()
model_pool_index = 0
load_lock = threading.Lock()
model_error: str | None = None


def _cuda_unsupported_arch_policy() -> str:
    policy = os.getenv("CUDA_UNSUPPORTED_ARCH_POLICY", "cpu").strip().lower()
    return policy if policy in {"cpu", "cuda"} else "cpu"


def _auto_cuda_devices(torch_module: Any) -> list[str]:
    supported_arches = set(torch_module.cuda.get_arch_list())
    unsupported: list[str] = []
    devices: list[str] = []
    for index in range(torch_module.cuda.device_count()):
        capability = torch_module.cuda.get_device_capability(index)
        device_arch = f"sm_{capability[0]}{capability[1]}"
        device_name = torch_module.cuda.get_device_name(index)
        if not supported_arches or device_arch in supported_arches:
            devices.append(f"cuda:{index}")
            continue
        unsupported.append(f"cuda:{index} {device_name} {device_arch}")
    if devices:
        return devices

    if unsupported:
        message = (
            f"[INFERENCE-MMROTATE] No visible CUDA device has an arch in the torch build "
            f"arch list ({sorted(supported_arches)}); unsupported devices: {unsupported}"
        )
        if _cuda_unsupported_arch_policy() == "cuda":
            devices = [f"cuda:{index}" for index in range(torch_module.cuda.device_count())]
            print(f"{message}; forcing CUDA devices by CUDA_UNSUPPORTED_ARCH_POLICY=cuda")
            return devices
        print(f"{message}; falling back to CPU")
    return []


def normalize_device_list(value: str) -> list[str]:
    devices: list[str] = []
    for item in value.split(","):
        device = item.strip()
        if not device:
            continue
        devices.append(f"cuda:{device}" if device.isdigit() else device)
    return devices or ["cpu"]


def resolve_devices(value: str) -> list[str]:
    requested = (value or "auto").strip().lower()
    if requested and requested != "auto":
        return normalize_device_list(requested)
    try:
        import torch

        if torch.cuda.is_available():
            devices = _auto_cuda_devices(torch)
            if devices:
                names = [torch.cuda.get_device_name(int(device.split(":")[1])) for device in devices]
                print(
                    f"[INFERENCE-MMROTATE] Using CUDA devices {', '.join(devices)}: "
                    f"{', '.join(names)}"
                )
                return devices
    except Exception:
        pass
    return ["cpu"]


DEVICES = resolve_devices(os.getenv("DEVICE", "auto"))
DEVICE = ",".join(DEVICES)


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
    global detection_models, model_error
    if detection_models:
        return
    with load_lock:
        if detection_models:
            return
        model_error = None
        loaded: list[dict[str, Any]] = []
        try:
            ensure_checkpoint()
            import mmrotate  # noqa: F401
            from mmdet.apis import init_detector
        except Exception as exc:
            model_error = str(exc)
            print(f"[INFERENCE-MMROTATE] Model prerequisites failed: {exc}")
            return

        for device in DEVICES:
            try:
                model = init_detector(MMROTATE_CONFIG, MMROTATE_CHECKPOINT, device=device)
                loaded.append({"model": model, "device": device, "lock": threading.Lock()})
                print(
                    f"[INFERENCE-MMROTATE] Loaded MMRotate model config={MMROTATE_CONFIG} "
                    f"checkpoint={MMROTATE_CHECKPOINT} device={device}"
                )
            except Exception as exc:
                model_error = str(exc)
                print(f"[INFERENCE-MMROTATE] Model load failed on {device}: {exc}")

        detection_models = loaded


def next_model_entry() -> dict[str, Any] | None:
    global model_pool_index
    if not detection_models:
        load_model()
    if not detection_models:
        return None
    with model_pool_lock:
        entry = detection_models[model_pool_index % len(detection_models)]
        model_pool_index += 1
    return entry


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


def _aabb_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
    iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _cross_tile_nms(detections: list[dict[str, Any]], iou_threshold: float) -> list[dict[str, Any]]:
    if not detections:
        return []
    boxes: list[tuple[float, float, float, float]] = []
    for det in detections:
        if det.get("obb") and len(det["obb"]) == 8:
            xs = det["obb"][0::2]; ys = det["obb"][1::2]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        else:
            cx, cy, w, h = det["bbox"][:4]
            boxes.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
    order = sorted(range(len(detections)), key=lambda i: float(detections[i].get("confidence", 0.0)), reverse=True)
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [
            j for j in order
            if detections[i].get("class") != detections[j].get("class")
            or _aabb_iou(boxes[i], boxes[j]) < iou_threshold
        ]
    return [detections[i] for i in keep]


def _plan_tiles(width: int, height: int, tile: int, overlap: int) -> list[tuple[int, int, int, int]]:
    step = max(1, tile - overlap)
    xs: list[int] = []
    x = 0
    while x + tile < width:
        xs.append(x); x += step
    xs.append(max(0, width - tile))
    ys: list[int] = []
    y = 0
    while y + tile < height:
        ys.append(y); y += step
    ys.append(max(0, height - tile))
    xs = sorted(set(xs)); ys = sorted(set(ys))
    plan: list[tuple[int, int, int, int]] = []
    for y in ys:
        for x in xs:
            tw = min(tile, width - x)
            th = min(tile, height - y)
            if tw <= 0 or th <= 0:
                continue
            plan.append((x, y, tw, th))
    return plan


def _should_internally_tile(image_size: tuple[int, int]) -> bool:
    if INFERENCE_INTERNAL_TILING == "off":
        return False
    img_w, img_h = image_size
    if INFERENCE_INTERNAL_TILING == "on":
        return True
    return max(img_w, img_h) > 2 * INFERENCE_TILE_SIZE


def _remap_detection(det: dict[str, Any], x: int, y: int, tw: int, th: int, full_w: int, full_h: int) -> dict[str, Any]:
    cx, cy, w, h = det["bbox"][:4]
    det["bbox"] = [
        max(0.0, min(1.0, (x + cx * tw) / full_w)),
        max(0.0, min(1.0, (y + cy * th) / full_h)),
        max(0.0, min(1.0, (w * tw) / full_w)),
        max(0.0, min(1.0, (h * th) / full_h)),
    ]
    if det.get("obb") and len(det["obb"]) == 8:
        det["obb"] = [
            max(0.0, min(1.0, (x + det["obb"][i] * tw) / full_w)) if i % 2 == 0
            else max(0.0, min(1.0, (y + det["obb"][i] * th) / full_h))
            for i in range(8)
        ]
    return det


def run_inference_tiled(image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None) -> dict[str, Any]:
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(status_code=503, detail=f"No MMRotate model loaded: {model_error or 'unknown error'}")
    full_w, full_h = image_size
    plan = _plan_tiles(full_w, full_h, INFERENCE_TILE_SIZE, INFERENCE_TILE_OVERLAP)
    start_time = time.time()
    merged: list[dict[str, Any]] = []
    try:
        import torch
        from mmdet.apis import inference_detector

        device = entry["device"]
        on_cuda = device.startswith("cuda")
        with entry["lock"], torch.inference_mode():
            for (x, y, tw, th) in plan:
                crop = image_array[y:y + th, x:x + tw]
                if on_cuda:
                    with torch.autocast(device_type="cuda", dtype=torch.float16):
                        raw = inference_detector(entry["model"], crop)
                else:
                    raw = inference_detector(entry["model"], crop)
                tile_dets = detections_from_mmrotate_result(raw, (tw, th))
                for det in tile_dets:
                    merged.append(_remap_detection(det, x, y, tw, th, full_w, full_h))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"MMRotate tiled inference failed: {exc}") from exc
    deduped = _cross_tile_nms(merged, INFERENCE_TILE_NMS_IOU)
    deduped.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    if MAX_DETECTIONS_PER_CHIP > 0:
        deduped = deduped[:MAX_DETECTIONS_PER_CHIP]
    return {
        "status": "success",
        "detections": deduped,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": MMROTATE_CHECKPOINT,
        "config": MMROTATE_CONFIG,
        "task": "rotated_detect",
        "device": entry["device"],
        "devices": DEVICES,
        "gpu_model": GPU_MODEL,
        "gpu_profile": MMROTATE_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": DETECTION_POLICY["global_confidence_floor"],
        "confidence_threshold": MMROTATE_CONFIDENCE_THRESHOLD,
        "internal_tiled": True,
        "inference_diagnostics": {
            "tiles": len(plan),
            "raw_detections": len(merged),
            "after_cross_tile_nms": len(deduped),
            "tile_size": INFERENCE_TILE_SIZE,
            "tile_overlap": INFERENCE_TILE_OVERLAP,
        },
    }


def run_inference(image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None) -> dict[str, Any]:
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(status_code=503, detail=f"No MMRotate model loaded: {model_error or 'unknown error'}")
    start_time = time.time()
    try:
        import torch
        from mmdet.apis import inference_detector

        device = entry["device"]
        on_cuda = device.startswith("cuda")
        with entry["lock"], torch.inference_mode():
            if on_cuda:
                with torch.autocast(device_type="cuda", dtype=torch.float16):
                    raw_result = inference_detector(entry["model"], image_array)
            else:
                raw_result = inference_detector(entry["model"], image_array)
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
        "device": device,
        "devices": DEVICES,
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
    if _should_internally_tile(image_size):
        result = await run_in_threadpool(run_inference_tiled, image_array, image_size, meta)
    else:
        result = await run_in_threadpool(run_inference, image_array, image_size, meta)
    result["input_metadata"] = meta
    return result


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": bool(detection_models),
        "model_error": model_error,
        "model_path": MMROTATE_CHECKPOINT,
        "model_exists": Path(MMROTATE_CHECKPOINT).exists(),
        "config_path": MMROTATE_CONFIG,
        "config_exists": Path(MMROTATE_CONFIG).exists(),
        "device": DEVICE,
        "devices": DEVICES,
        "model_replicas": len(detection_models),
        "replicas": [
            {"device": entry["device"]}
            for entry in detection_models
        ],
        "gpu_model": GPU_MODEL,
        "gpu_profile": MMROTATE_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": MMROTATE_CONFIDENCE_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
        "classes": DOTA_CLASSES,
    }
