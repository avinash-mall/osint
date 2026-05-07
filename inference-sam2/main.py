import io
import json
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

cv2.setNumThreads(0)

app = FastAPI(title="SentinelOS AIP Node - SAM 2 Inference")

DEFAULT_MODEL_DIR = Path(os.getenv("SAM2_MODEL_DIR", "/models"))
SAM2_MODEL_SIZE = os.getenv("SAM2_MODEL_SIZE", "large")

# Mapping of size to config and checkpoint URL
MODEL_CONFIGS = {
    "tiny": {
        "cfg": "configs/sam2.1/sam2.1_hiera_t.yaml",
        "ckpt": "sam2.1_hiera_tiny.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt"
    },
    "small": {
        "cfg": "configs/sam2.1/sam2.1_hiera_s.yaml",
        "ckpt": "sam2.1_hiera_small.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt"
    },
    "base_plus": {
        "cfg": "configs/sam2.1/sam2.1_hiera_b+.yaml",
        "ckpt": "sam2.1_hiera_base_plus.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_base_plus.pt"
    },
    "large": {
        "cfg": "configs/sam2.1/sam2.1_hiera_l.yaml",
        "ckpt": "sam2.1_hiera_large.pt",
        "url": "https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt"
    }
}

config_info = MODEL_CONFIGS.get(SAM2_MODEL_SIZE, MODEL_CONFIGS["large"])
SAM2_CONFIG = config_info["cfg"]
SAM2_CHECKPOINT = str(DEFAULT_MODEL_DIR / config_info["ckpt"])
SAM2_CHECKPOINT_URL = config_info["url"]

MODEL_VERSION = os.getenv("MODEL_VERSION", f"sam2.1-hiera-{SAM2_MODEL_SIZE}")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
SAM2_GPU_PROFILE = os.getenv("SAM2_GPU_PROFILE", "unknown")

mask_generators: list[dict[str, Any]] = []
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
            f"[INFERENCE-SAM2] No visible CUDA device has an arch in the torch build "
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
                    f"[INFERENCE-SAM2] Using CUDA devices {', '.join(devices)}: "
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
    path = Path(SAM2_CHECKPOINT)
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFERENCE-SAM2] Downloading checkpoint to {path}")
    with requests.get(SAM2_CHECKPOINT_URL, stream=True, timeout=600) as response:
        response.raise_for_status()
        with path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)

def load_model() -> None:
    global mask_generators, model_error
    if mask_generators:
        return
    with load_lock:
        if mask_generators:
            return
        model_error = None
        loaded: list[dict[str, Any]] = []
        try:
            ensure_checkpoint()
            from sam2.build_sam import build_sam2
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            from sam2.sam2_image_predictor import SAM2ImagePredictor
        except Exception as exc:
            model_error = str(exc)
            print(f"[INFERENCE-SAM2] Model prerequisites failed: {exc}")
            return

        for device in DEVICES:
            try:
                sam2_model = build_sam2(SAM2_CONFIG, SAM2_CHECKPOINT, device=device)
                loaded.append({
                    "model": sam2_model,
                    "generator": SAM2AutomaticMaskGenerator(sam2_model),
                    "predictor": SAM2ImagePredictor(sam2_model),
                    "device": device,
                    "lock": threading.Lock(),
                })
                print(
                    f"[INFERENCE-SAM2] Loaded SAM 2 model config={SAM2_CONFIG} "
                    f"checkpoint={SAM2_CHECKPOINT} device={device}"
                )
            except Exception as exc:
                model_error = str(exc)
                print(f"[INFERENCE-SAM2] Model load failed on {device}: {exc}")

        mask_generators = loaded


def next_model_entry() -> dict[str, Any] | None:
    global model_pool_index
    if not mask_generators:
        load_model()
    if not mask_generators:
        return None
    with model_pool_lock:
        entry = mask_generators[model_pool_index % len(mask_generators)]
        model_pool_index += 1
    return entry

def run_inference(image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None) -> dict[str, Any]:
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(status_code=503, detail=f"No SAM 2 model loaded: {model_error or 'unknown error'}")
    
    start_time = time.time()
    try:
        import torch
        with entry["lock"], torch.inference_mode():
            if entry["device"].startswith("cuda"):
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                    masks = entry["generator"].generate(image_array)
            else:
                masks = entry["generator"].generate(image_array)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SAM 2 inference failed: {exc}") from exc
        
    img_w, img_h = image_size
    detections = []
    
    for mask_data in masks:
        # mask_data has 'segmentation', 'area', 'bbox' (XYWH), 'predicted_iou', 'point_coords', 'stability_score', 'crop_box'
        bbox = mask_data["bbox"] # [x, y, w, h]
        score = mask_data["predicted_iou"]
        
        # Convert to normalized CX, CY, W, H
        x, y, w, h = bbox
        cx = x + w / 2.0
        cy = y + h / 2.0
        
        # Convert to relative coordinates
        rel_cx = max(0.0, min(1.0, cx / img_w))
        rel_cy = max(0.0, min(1.0, cy / img_h))
        rel_w = max(0.0, min(1.0, w / img_w))
        rel_h = max(0.0, min(1.0, h / img_h))
        
        detections.append({
            "class": "segment", # SAM 2 is class-agnostic
            "original_class": "segment",
            "parent_class": "segment",
            "bbox": [rel_cx, rel_cy, rel_w, rel_h],
            "confidence": float(score),
            "area": mask_data["area"]
        })
        
    detections.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
    
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": SAM2_CHECKPOINT,
        "config": SAM2_CONFIG,
        "task": "segmentation",
        "device": entry["device"],
        "devices": DEVICES,
        "gpu_model": GPU_MODEL,
        "gpu_profile": SAM2_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION
    }

def _normalize_prompt_boxes(
    prompt_boxes: list[dict[str, Any]],
    img_w: int,
    img_h: int,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Convert per-chip normalized prompt boxes to absolute XYXY pixels.

    Accepts either bbox=[cx,cy,w,h] in [0,1] or obb=[x1,y1,...,x4,y4] in [0,1].
    Returns (xyxy_array shape (N,4) float32, kept_metadata) — boxes that fall
    outside the chip are silently dropped.
    """
    xyxy: list[list[float]] = []
    kept: list[dict[str, Any]] = []
    for entry in prompt_boxes:
        if not isinstance(entry, dict):
            continue
        bbox = entry.get("bbox")
        obb = entry.get("obb")
        if obb and len(obb) >= 8:
            xs = [float(obb[i]) for i in range(0, 8, 2)]
            ys = [float(obb[i]) for i in range(1, 8, 2)]
            x1n, y1n, x2n, y2n = min(xs), min(ys), max(xs), max(ys)
        elif bbox and len(bbox) >= 4:
            cx, cy, w, h = (float(v) for v in bbox[:4])
            x1n, y1n, x2n, y2n = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
        else:
            continue
        x1 = max(0.0, min(float(img_w), x1n * img_w))
        y1 = max(0.0, min(float(img_h), y1n * img_h))
        x2 = max(0.0, min(float(img_w), x2n * img_w))
        y2 = max(0.0, min(float(img_h), y2n * img_h))
        if x2 - x1 < 1.0 or y2 - y1 < 1.0:
            continue
        xyxy.append([x1, y1, x2, y2])
        kept.append(entry)
    if not xyxy:
        return np.zeros((0, 4), dtype=np.float32), []
    return np.asarray(xyxy, dtype=np.float32), kept


def _mask_to_obb_normalized(mask: np.ndarray, img_w: int, img_h: int) -> list[float] | None:
    """Compute a 4-corner OBB (cv2.minAreaRect) for the largest contour of mask.

    Returns 8 floats in [0,1] image-normalized order [x1,y1,x2,y2,x3,y3,x4,y4]
    or None if the mask is empty/degenerate."""
    binary = (mask.astype(np.uint8) if mask.dtype != np.uint8 else mask)
    if binary.ndim == 3:
        binary = binary.squeeze()
    if binary.max() == 0:
        return None
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 4:
        return None
    rect = cv2.minAreaRect(contour)
    points = cv2.boxPoints(rect)  # shape (4,2) [x,y]
    flat: list[float] = []
    for px, py in points:
        flat.append(max(0.0, min(1.0, float(px) / img_w)))
        flat.append(max(0.0, min(1.0, float(py) / img_h)))
    return flat


def run_grounded_inference(
    image_array: np.ndarray,
    image_size: tuple[int, int],
    prompt_boxes: list[dict[str, Any]],
    metadata: dict | None = None,
) -> dict[str, Any]:
    entry = next_model_entry()
    if entry is None or "predictor" not in entry:
        raise HTTPException(
            status_code=503,
            detail=f"No SAM 2 image predictor loaded: {model_error or 'unknown error'}",
        )

    img_w, img_h = image_size
    boxes_xyxy, kept_meta = _normalize_prompt_boxes(prompt_boxes, img_w, img_h)
    start_time = time.time()
    detections: list[dict[str, Any]] = []
    if boxes_xyxy.shape[0] == 0:
        return {
            "status": "success",
            "detections": detections,
            "processing_time_ms": round((time.time() - start_time) * 1000, 2),
            "model": SAM2_CHECKPOINT,
            "config": SAM2_CONFIG,
            "task": "grounded_segmentation",
            "device": entry["device"],
            "devices": DEVICES,
            "gpu_model": GPU_MODEL,
            "gpu_profile": SAM2_GPU_PROFILE,
            "model_version": MODEL_VERSION,
            "input_prompt_boxes": 0,
            "kept_prompt_boxes": 0,
        }

    try:
        import torch
        with entry["lock"], torch.inference_mode():
            predictor = entry["predictor"]
            if entry["device"].startswith("cuda"):
                with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                    predictor.set_image(image_array)
                    masks, scores, _ = predictor.predict(
                        box=boxes_xyxy,
                        multimask_output=False,
                    )
            else:
                predictor.set_image(image_array)
                masks, scores, _ = predictor.predict(
                    box=boxes_xyxy,
                    multimask_output=False,
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SAM 2 grounded inference failed: {exc}") from exc

    # masks shape: (N, 1, H, W) when multimask_output=False; scores shape (N, 1)
    masks_np = np.asarray(masks)
    scores_np = np.asarray(scores)
    if masks_np.ndim == 3:
        masks_np = masks_np[:, None, :, :]
    if scores_np.ndim == 1:
        scores_np = scores_np[:, None]

    for index, prompt in enumerate(kept_meta):
        mask = masks_np[index, 0] if index < masks_np.shape[0] else None
        if mask is None:
            continue
        mask_iou = float(scores_np[index, 0]) if index < scores_np.shape[0] else 0.0
        obb = _mask_to_obb_normalized(mask, img_w, img_h)
        x1, y1, x2, y2 = boxes_xyxy[index].tolist()
        cls_name = str(prompt.get("class") or prompt.get("original_class") or "segment")
        original_class = str(prompt.get("original_class") or cls_name)
        parent_class = str(prompt.get("parent_class") or cls_name)
        source_provider = prompt.get("provider") or (prompt.get("providers") or [None])[0]
        source_confidence = float(prompt.get("confidence") or 0.0)
        # Combine the source detector's class confidence with SAM2's mask quality
        combined_confidence = max(source_confidence, mask_iou)
        detection: dict[str, Any] = {
            "class": cls_name,
            "original_class": original_class,
            "parent_class": parent_class,
            "bbox": [
                max(0.0, min(1.0, ((x1 + x2) / 2.0) / img_w)),
                max(0.0, min(1.0, ((y1 + y2) / 2.0) / img_h)),
                max(0.0, min(1.0, (x2 - x1) / img_w)),
                max(0.0, min(1.0, (y2 - y1) / img_h)),
            ],
            "confidence": combined_confidence,
            "mask_iou": mask_iou,
            "source_provider": source_provider,
            "source_confidence": source_confidence,
            "area": int(mask.sum()),
            "task": "grounded_segmentation",
        }
        if obb:
            detection["obb"] = obb
        detections.append(detection)

    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": SAM2_CHECKPOINT,
        "config": SAM2_CONFIG,
        "task": "grounded_segmentation",
        "device": entry["device"],
        "devices": DEVICES,
        "gpu_model": GPU_MODEL,
        "gpu_profile": SAM2_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION,
        "input_prompt_boxes": len(prompt_boxes),
        "kept_prompt_boxes": len(kept_meta),
    }


@app.on_event("startup")
def startup_event() -> None:
    # Do not load model at startup immediately to prevent memory spikes if not used
    pass

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

    prompt_boxes = meta.get("prompt_boxes") if isinstance(meta, dict) else None
    if isinstance(prompt_boxes, list) and prompt_boxes:
        result = await run_in_threadpool(
            run_grounded_inference, image_array, image_size, prompt_boxes, meta
        )
    else:
        result = await run_in_threadpool(run_inference, image_array, image_size, meta)
    result["input_metadata"] = meta
    return result

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": bool(mask_generators),
        "model_error": model_error,
        "model_path": SAM2_CHECKPOINT,
        "model_exists": Path(SAM2_CHECKPOINT).exists(),
        "config_path": SAM2_CONFIG,
        "device": DEVICE,
        "devices": DEVICES,
        "model_replicas": len(mask_generators),
        "replicas": [
            {"device": entry["device"]}
            for entry in mask_generators
        ],
        "gpu_model": GPU_MODEL,
        "gpu_profile": SAM2_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION
    }
