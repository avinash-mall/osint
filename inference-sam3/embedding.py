from __future__ import annotations

import base64
import os
from typing import Any

import numpy as np
from PIL import Image


DINOV3_SAT_MODEL_ID = os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m")
# Max crops per DINOv3 forward in the batched path. Bounds VRAM; the encoder
# resizes every crop to a fixed input size so a batch is a clean [B,C,H,W].
SAM3_EMBED_BATCH_SIZE = max(1, int(os.getenv("SAM3_EMBED_BATCH_SIZE", "32")))


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


def embed_crops_batched(
    bundle: dict[str, Any] | None,
    image_uint8: np.ndarray,
    bboxes: list[list[float]],
) -> list[dict[str, Any]]:
    """Batched equivalent of calling ``embed_crop`` once per bbox.

    Collects every in-bounds, non-degenerate crop and runs the DINOv3 encoder in
    batches of ``SAM3_EMBED_BATCH_SIZE`` so N detections cost ~ceil(N/B) forwards
    and a single host transfer instead of N. Returns one result dict per input
    bbox, in order; degenerate crops (<4 px) get the same dim-0 placeholder
    ``embed_crop`` would return. Each crop is encoded independently (the encoder
    resizes each to a fixed size), so per-detection output is identical to the
    one-at-a-time path.
    """
    n = len(bboxes)
    if bundle is None:
        return [{"model": "unloaded", "dim": 0, "fp16_b64": ""} for _ in range(n)]

    model_id = bundle.get("model_id", "dinov3")
    results: list[dict[str, Any]] = [
        {"model": model_id, "dim": 0, "fp16_b64": ""} for _ in range(n)
    ]
    if n == 0:
        return results

    h, w = image_uint8.shape[:2]
    crops: list[Image.Image] = []
    crop_idx: list[int] = []
    for i, bbox in enumerate(bboxes):
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        x1 = max(0, min(w, x1))
        x2 = max(0, min(w, x2))
        y1 = max(0, min(h, y1))
        y2 = max(0, min(h, y2))
        if x2 - x1 < 4 or y2 - y1 < 4:
            continue
        crops.append(Image.fromarray(image_uint8[y1:y2, x1:x2]))
        crop_idx.append(i)

    if not crops:
        return results

    import torch

    processor = bundle["processor"]
    model = bundle["model"]
    device = bundle["device"]
    for start in range(0, len(crops), SAM3_EMBED_BATCH_SIZE):
        chunk = crops[start:start + SAM3_EMBED_BATCH_SIZE]
        inp = processor(images=chunk, return_tensors="pt").to(device)
        with torch.inference_mode():
            out = model(**inp)
        vecs = out.last_hidden_state[:, 0, :].to(torch.float16).cpu().numpy()
        for j in range(vecs.shape[0]):
            vec = vecs[j]
            results[crop_idx[start + j]] = {
                "model": model_id,
                "dim": int(vec.shape[0]),
                "fp16_b64": base64.b64encode(vec.tobytes()).decode("ascii"),
            }
    return results


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
