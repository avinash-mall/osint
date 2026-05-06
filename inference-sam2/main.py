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

mask_generator = None
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
            f"[INFERENCE-SAM2] No visible CUDA device has an arch in the torch build "
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
    global mask_generator, model_error
    if mask_generator is not None:
        return
    with model_lock:
        if mask_generator is not None:
            return
        model_error = None
        try:
            ensure_checkpoint()
            import torch
            from sam2.build_sam import build_sam2
            from sam2.automatic_mask_generator import SAM2AutomaticMaskGenerator
            
            if DEVICE.startswith("cuda"):
                torch.autocast(device_type="cuda", dtype=torch.bfloat16).__enter__()

            sam2_model = build_sam2(SAM2_CONFIG, SAM2_CHECKPOINT, device=DEVICE)
            
            # Use Automatic Mask Generator
            mask_generator = SAM2AutomaticMaskGenerator(sam2_model)
            print(
                f"[INFERENCE-SAM2] Loaded SAM 2 model config={SAM2_CONFIG} "
                f"checkpoint={SAM2_CHECKPOINT} device={DEVICE}"
            )
        except Exception as exc:
            model_error = str(exc)
            mask_generator = None
            print(f"[INFERENCE-SAM2] Model load failed: {exc}")

def run_inference(image_array: np.ndarray, image_size: tuple[int, int], metadata: dict | None = None) -> dict[str, Any]:
    load_model()
    if mask_generator is None:
        raise HTTPException(status_code=503, detail=f"No SAM 2 model loaded: {model_error or 'unknown error'}")
    
    start_time = time.time()
    try:
        import torch
        with model_lock, torch.inference_mode():
            # Generate masks
            masks = mask_generator.generate(image_array)
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
        "device": DEVICE,
        "gpu_model": GPU_MODEL,
        "gpu_profile": SAM2_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION
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
    result = await run_in_threadpool(run_inference, image_array, image_size, meta)
    result["input_metadata"] = meta
    return result

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": mask_generator is not None,
        "model_error": model_error,
        "model_path": SAM2_CHECKPOINT,
        "model_exists": Path(SAM2_CHECKPOINT).exists(),
        "config_path": SAM2_CONFIG,
        "device": DEVICE,
        "gpu_model": GPU_MODEL,
        "gpu_profile": SAM2_GPU_PROFILE,
        "cuda": torch_cuda_diagnostics(),
        "model_version": MODEL_VERSION
    }
