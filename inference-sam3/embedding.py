from __future__ import annotations

import base64
import os
from typing import Any

import numpy as np
from PIL import Image


DINOV3_SAT_MODEL_ID = os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m")


def _load(model_id: str, device: str) -> dict[str, Any]:
    import torch
    from transformers import AutoImageProcessor, AutoModel

    return {
        "model_id": model_id,
        "processor": AutoImageProcessor.from_pretrained(model_id),
        "model": AutoModel.from_pretrained(model_id, torch_dtype=torch.float16).to(device).eval(),
        "device": device,
    }


def load_sat(device: str) -> dict[str, Any]:
    return _load(DINOV3_SAT_MODEL_ID, device)


def embed_crop(bundle: dict[str, Any] | None, image_uint8: np.ndarray, bbox_xyxy: list[float]) -> dict[str, Any]:
    if bundle is None:
        return {"model": "unloaded", "dim": 0, "fp16_b64": ""}
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_xyxy)
    h, w = image_uint8.shape[:2]
    x1 = max(0, min(w, x1))
    x2 = max(0, min(w, x2))
    y1 = max(0, min(h, y1))
    y2 = max(0, min(h, y2))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return {"model": bundle.get("model_id", "dinov3"), "dim": 0, "fp16_b64": ""}
    return dinov3_pool(bundle, Image.fromarray(image_uint8[y1:y2, x1:x2]))


def dinov3_pool(bundle: dict[str, Any], pil_image_or_array: Image.Image | np.ndarray) -> dict[str, Any]:
    if isinstance(pil_image_or_array, np.ndarray):
        pil_image_or_array = Image.fromarray(pil_image_or_array)
    import torch

    inp = bundle["processor"](images=pil_image_or_array, return_tensors="pt").to(bundle["device"])
    with torch.inference_mode():
        out = bundle["model"](**inp)
    vec = out.last_hidden_state[:, 0, :].squeeze(0).to(torch.float16).cpu().numpy()
    return {
        "model": bundle["model_id"],
        "dim": int(vec.shape[0]),
        "fp16_b64": base64.b64encode(vec.tobytes()).decode("ascii"),
    }
