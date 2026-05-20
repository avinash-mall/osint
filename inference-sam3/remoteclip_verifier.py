"""Remote-sensing CLIP verifier for candidate detections.

The verifier never proposes objects. It only re-scores crops that another
detector already emitted, giving the backend an additional semantic signal for
evidence ranking. Loading is best-effort so air-gapped builds without
RemoteCLIP weights continue to run with the verifier disabled.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np
from PIL import Image


REMOTECLIP_MODEL_ID = os.getenv("REMOTECLIP_MODEL_ID", "chendelong/RemoteCLIP")
REMOTECLIP_ARCH = os.getenv("REMOTECLIP_ARCH", "ViT-B-32")
REMOTECLIP_MARGIN_THRESHOLD = float(os.getenv("REMOTECLIP_MARGIN_THRESHOLD", "0.05"))
REMOTECLIP_MIN_CROP_PX = int(os.getenv("REMOTECLIP_MIN_CROP_PX", "12"))
REMOTECLIP_CONTEXT_PAD = float(os.getenv("REMOTECLIP_CONTEXT_PAD", "0.35"))
REMOTECLIP_LOCAL_FILES_ONLY = os.getenv("REMOTECLIP_LOCAL_FILES_ONLY", "1").strip().lower() in {"1", "true", "yes", "on"}


def load(device: str) -> dict[str, Any]:
    """Load RemoteCLIP via OpenCLIP. Returns a disabled bundle on failure."""
    try:
        import open_clip
        import torch
        from huggingface_hub import hf_hub_download
    except Exception as exc:
        return {
            "model": None,
            "device": device,
            "model_id": REMOTECLIP_MODEL_ID,
            "arch": REMOTECLIP_ARCH,
            "error": f"open_clip unavailable: {exc}",
        }

    try:
        filename = f"RemoteCLIP-{REMOTECLIP_ARCH}.pt"
        checkpoint_path = hf_hub_download(
            REMOTECLIP_MODEL_ID,
            filename=filename,
            local_files_only=REMOTECLIP_LOCAL_FILES_ONLY,
        )
        model, _, preprocess = open_clip.create_model_and_transforms(REMOTECLIP_ARCH)
        state = torch.load(checkpoint_path, map_location="cpu")
        model.load_state_dict(state)
        model = model.to(device).eval()
        tokenizer = open_clip.get_tokenizer(REMOTECLIP_ARCH)
        return {
            "model": model,
            "preprocess": preprocess,
            "tokenizer": tokenizer,
            "torch": torch,
            "device": device,
            "model_id": REMOTECLIP_MODEL_ID,
            "arch": REMOTECLIP_ARCH,
        }
    except Exception as exc:
        return {
            "model": None,
            "device": device,
            "model_id": REMOTECLIP_MODEL_ID,
            "arch": REMOTECLIP_ARCH,
            "error": str(exc),
        }


def verify(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    bbox_xyxy: list[float],
    labels: list[str],
) -> dict[str, Any]:
    """Score a detector crop against candidate labels.

    Output is intentionally small and JSON-safe. ``semantic_margin`` is the
    top probability minus the second probability, which is more useful for
    evidence ranking than the raw top probability alone.
    """
    if not bundle or bundle.get("model") is None:
        return _disabled(bundle)
    cleaned = [_clean_label(label) for label in labels if _clean_label(label)]
    if not cleaned:
        return _disabled(bundle, reason="no labels")
    crop = _crop_with_context(image_rgb_uint8, bbox_xyxy)
    if crop is None:
        return _disabled(bundle, reason="crop too small")

    try:
        torch = bundle["torch"]
        model = bundle["model"]
        preprocess = bundle["preprocess"]
        tokenizer = bundle["tokenizer"]
        prompts = [f"a satellite image of {label.replace('_', ' ')}" for label in cleaned]
        image = preprocess(crop).unsqueeze(0).to(bundle["device"])
        text = tokenizer(prompts).to(bundle["device"])
        with torch.inference_mode():
            image_features = model.encode_image(image)
            text_features = model.encode_text(text)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            probs = (100.0 * image_features @ text_features.T).softmax(dim=-1)[0].float().cpu().numpy()
    except Exception as exc:
        return _disabled(bundle, reason=str(exc))

    order = np.argsort(-probs)
    top_i = int(order[0])
    second = float(probs[int(order[1])]) if len(order) > 1 else 0.0
    top_score = float(probs[top_i])
    margin = top_score - second
    return {
        "model": bundle.get("model_id", REMOTECLIP_MODEL_ID),
        "arch": bundle.get("arch", REMOTECLIP_ARCH),
        "enabled": True,
        "label": cleaned[top_i],
        "score": round(top_score, 6),
        "semantic_margin": round(margin, 6),
        "passed": bool(margin >= REMOTECLIP_MARGIN_THRESHOLD),
        "top_labels": [
            {"label": cleaned[int(i)], "score": round(float(probs[int(i)]), 6)}
            for i in order[: min(3, len(order))]
        ],
    }


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False, "model_id": REMOTECLIP_MODEL_ID, "arch": REMOTECLIP_ARCH}
    return {
        "loaded": bundle.get("model") is not None,
        "model_id": bundle.get("model_id", REMOTECLIP_MODEL_ID),
        "arch": bundle.get("arch", REMOTECLIP_ARCH),
        "margin_threshold": REMOTECLIP_MARGIN_THRESHOLD,
        "error": bundle.get("error"),
    }


def _crop_with_context(image_rgb_uint8: np.ndarray, bbox_xyxy: list[float]) -> Image.Image | None:
    h, w = image_rgb_uint8.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox_xyxy[:4]]
    bw = max(0.0, x2 - x1)
    bh = max(0.0, y2 - y1)
    if bw < REMOTECLIP_MIN_CROP_PX or bh < REMOTECLIP_MIN_CROP_PX:
        return None
    pad_x = bw * REMOTECLIP_CONTEXT_PAD
    pad_y = bh * REMOTECLIP_CONTEXT_PAD
    ix1 = max(0, int(round(x1 - pad_x)))
    iy1 = max(0, int(round(y1 - pad_y)))
    ix2 = min(w, int(round(x2 + pad_x)))
    iy2 = min(h, int(round(y2 + pad_y)))
    if ix2 - ix1 < REMOTECLIP_MIN_CROP_PX or iy2 - iy1 < REMOTECLIP_MIN_CROP_PX:
        return None
    return Image.fromarray(image_rgb_uint8[iy1:iy2, ix1:ix2])


def _clean_label(label: Any) -> str:
    return str(label or "").strip().lower().replace(" ", "_")


def _disabled(bundle: dict[str, Any] | None, reason: str | None = None) -> dict[str, Any]:
    out = {
        "model": (bundle or {}).get("model_id", REMOTECLIP_MODEL_ID),
        "arch": (bundle or {}).get("arch", REMOTECLIP_ARCH),
        "enabled": False,
        "semantic_margin": None,
        "passed": False,
    }
    error = reason or (bundle or {}).get("error")
    if error:
        out["error"] = str(error)
    return out
