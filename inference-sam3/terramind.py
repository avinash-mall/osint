from __future__ import annotations

import base64
import os
from typing import Any

import numpy as np


TERRAMIND_MODEL_ID = os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large")


def load(device: str) -> dict[str, Any]:
    from terratorch import BACKBONE_REGISTRY, FULL_MODEL_REGISTRY

    backbone = BACKBONE_REGISTRY.build(
        TERRAMIND_MODEL_ID, pretrained=True, modalities=["S1GRD"]
    ).to(device).eval()
    generator = FULL_MODEL_REGISTRY.build(
        f"{TERRAMIND_MODEL_ID}_generate",
        pretrained=True,
        modalities=["S1GRD"],
        output_modalities=["S2L2A"],
        timesteps=10,
        standardize=True,
    ).to(device).eval()
    return {"backbone": backbone, "generator": generator, "device": device}


def s1_to_s2_rgb(bundle: dict[str, Any] | None, chip2_norm: np.ndarray, target_hw: tuple[int, int] | None = None) -> np.ndarray:
    if bundle is None:
        preview = _fallback_sar_rgb(chip2_norm)
    else:
        import torch
        import sar as sar_mod

        from inference_utils import device_ctx

        arr224 = sar_mod.resize_to_terramind(chip2_norm)
        # Pin the current CUDA device to this replica's GPU — this forward runs
        # in the anyio threadpool, same rationale as embedding.dinov3_pool.
        with device_ctx(bundle["device"]), torch.inference_mode():
            x = torch.from_numpy(arr224).unsqueeze(0).to(bundle["device"])
            generated = bundle["generator"]({"S1GRD": x})
        s2 = generated["S2L2A"].squeeze(0).detach().cpu().numpy()
        # Belt-and-braces against NaN escaping the generator: mirrors
        # multispectral.hls_to_rgb_preview's nan_to_num-before-percentile.
        rgb = np.nan_to_num(s2[[3, 2, 1]], nan=0.0)
        p2, p98 = np.percentile(rgb, [2, 98], axis=(1, 2), keepdims=True)
        rgb = np.clip((rgb - p2) / np.maximum(p98 - p2, 1e-6), 0.0, 1.0)
        preview = (rgb * 255).astype(np.uint8).transpose(1, 2, 0)

    if target_hw is not None and preview.shape[:2] != tuple(target_hw):
        import cv2

        preview = cv2.resize(preview, (target_hw[1], target_hw[0]), interpolation=cv2.INTER_LINEAR)
    return preview


def pool_patches(bundle: dict[str, Any] | None, chip2_norm: np.ndarray) -> dict[str, Any]:
    if bundle is None:
        return {"model": TERRAMIND_MODEL_ID, "dim": 0, "fp16_b64": ""}
    import torch
    import sar as sar_mod
    from inference_utils import device_ctx

    arr224 = sar_mod.resize_to_terramind(chip2_norm)
    # Pin the current CUDA device to this replica's GPU, matching how the other
    # specialists (dota_obb, embedding) pin theirs — this forward runs in the
    # anyio threadpool where the current device defaults to cuda:0.
    with device_ctx(bundle["device"]), torch.inference_mode():
        x = torch.from_numpy(arr224).unsqueeze(0).to(bundle["device"])
        out = bundle["backbone"]({"S1GRD": x})
    tokens = out[-1] if isinstance(out, list) else out
    vec = tokens.mean(dim=1).squeeze(0).to(torch.float16).cpu().numpy()
    return {
        "model": TERRAMIND_MODEL_ID,
        "dim": int(vec.shape[0]),
        "fp16_b64": base64.b64encode(vec.tobytes()).decode("ascii"),
    }


def _fallback_sar_rgb(chip2_norm: np.ndarray) -> np.ndarray:
    vv = np.clip(chip2_norm[0], 0.0, 1.0)
    vh = np.clip(chip2_norm[1], 0.0, 1.0)
    ratio = np.clip(vv - vh + 0.5, 0.0, 1.0)
    return (np.stack([vv, ratio, vh], axis=-1) * 255).astype(np.uint8)
