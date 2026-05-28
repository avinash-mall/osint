from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

import numpy as np


PRITHVI_FLOOD_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11"
PRITHVI_BURN_ID = "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars"
PRITHVI_WINDOW_SIZE = 512


def load_all(device: str):
    bundle = {
        "flood": _load_task_model(PRITHVI_FLOOD_ID, device, "PRITHVI_FLOOD"),
        "burn": _load_task_model(PRITHVI_BURN_ID, device, "PRITHVI_BURN"),
        "device": device,
    }
    bundle["loaded_heads"] = sorted(key for key in ("flood", "burn") if bundle.get(key) is not None)
    return bundle


def _load_task_model(repo_id: str, device: str, env_prefix: str):
    """Load a packaged Prithvi downstream head from config + checkpoint.

    The released Prithvi task repositories ship inference assets rather than a
    bare backbone. Operators may pin exact files with
    ``<PREFIX>_CONFIG``/``<PREFIX>_CHECKPOINT``; otherwise we inspect the HF
    snapshot and pick the first config/checkpoint pair. The registry fallback is
    retained for local TerraTorch installations that expose a direct model key.
    """
    config = os.getenv(f"{env_prefix}_CONFIG")
    checkpoint = os.getenv(f"{env_prefix}_CHECKPOINT")
    if not (config and checkpoint):
        try:
            from huggingface_hub import snapshot_download

            root = Path(snapshot_download(repo_id))
            config = config or _first_existing(root, ("*.yaml", "*.yml"))
            checkpoint = checkpoint or _first_existing(root, ("*.ckpt", "*.pth", "*.pt"))
        except Exception:
            config = config or None
            checkpoint = checkpoint or None

    if config and checkpoint:
        try:
            from terratorch.cli_tools import LightningInferenceModel

            with _clean_argv_for_lightning():
                model = LightningInferenceModel.from_config(str(config), str(checkpoint))
            return _to_eval_device(model, device)
        except (Exception, SystemExit) as exc:
            print(f"[prithvi_heads] LightningInferenceModel load failed for {repo_id}: {exc}; trying registry fallback")

    from terratorch.registry import BACKBONE_REGISTRY

    return _to_eval_device(BACKBONE_REGISTRY.build(repo_id), device)


@contextmanager
def _clean_argv_for_lightning():
    original = sys.argv[:]
    sys.argv = [original[0] if original else "python"]
    try:
        yield
    finally:
        sys.argv = original


def _first_existing(root: Path, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        matches = sorted(path for path in root.rglob(pattern) if path.is_file())
        if matches:
            for match in matches:
                if match.stat().st_size > 0:
                    return str(match)
    return None


def _to_eval_device(model, device: str):
    # LightningInferenceModel wraps a Lightning module under .model — its own
    # .to()/.eval() do NOT propagate to the inner module's weights, so on a
    # CUDA device we'd hit "Input type (cuda.FloatTensor) and weight type
    # (FloatTensor) should be the same". Move/eval the inner module too when
    # present.
    inner = getattr(model, "model", None)
    for target in (inner, model):
        if target is None:
            continue
        if hasattr(target, "to"):
            try:
                target.to(device)
            except Exception:
                pass
        if hasattr(target, "eval"):
            try:
                target.eval()
            except Exception:
                pass
    return model


def run_all(prithvi_bundle, chip6_full: np.ndarray, target_hw: tuple[int, int]) -> dict[str, np.ndarray]:
    if prithvi_bundle is None:
        return {}

    h, w = target_hw
    overlays: dict[str, np.ndarray] = {}
    overlays["water"] = _run_binary_windowed(prithvi_bundle, "flood", chip6_full, (h, w))
    overlays["burn_scar"] = _run_binary_windowed(prithvi_bundle, "burn", chip6_full, (h, w))
    return overlays


def _run_binary_windowed(prithvi_bundle, model_key: str, chip6_full: np.ndarray, target_hw: tuple[int, int]) -> np.ndarray:
    h, w = target_hw
    class_map = _run_windowed(prithvi_bundle, model_key, chip6_full, (h, w))
    return class_map == 1


def _run_windowed(prithvi_bundle, model_key: str, chip6_full: np.ndarray, target_hw: tuple[int, int]) -> np.ndarray:
    h, w = target_hw
    stitched = np.zeros((h, w), dtype=np.int16)
    for y1, y2, x1, x2 in _windows(h, w):
        window = chip6_full[:, y1:y2, x1:x2]
        stitched[y1:y2, x1:x2] = _predict_single_window(prithvi_bundle, model_key, window, (y2 - y1, x2 - x1))
    return stitched


def _windows(height: int, width: int):
    for y1 in range(0, height, PRITHVI_WINDOW_SIZE):
        for x1 in range(0, width, PRITHVI_WINDOW_SIZE):
            yield y1, min(height, y1 + PRITHVI_WINDOW_SIZE), x1, min(width, x1 + PRITHVI_WINDOW_SIZE)


def _invoke_prithvi(model_obj, x):
    # LightningInferenceModel wrappers from terratorch.cli_tools are not directly
    # callable — invoke their underlying Lightning module via .model(x). Bare
    # nn.Module instances (BACKBONE_REGISTRY fallback) ARE callable, so call
    # directly. Try the wrapper path first since it's the default loader.
    inner = getattr(model_obj, "model", None)
    if inner is not None and callable(inner):
        return inner(x)
    return model_obj(x)


def _predict_single_window(prithvi_bundle, model_key: str, window: np.ndarray, output_hw: tuple[int, int]) -> np.ndarray:
    import cv2
    import torch
    import multispectral

    x = torch.from_numpy(multispectral.resize_to_prithvi(window)).unsqueeze(0).to(prithvi_bundle["device"])
    with torch.inference_mode():
        logits = _extract_logits(_invoke_prithvi(prithvi_bundle[model_key], x))
    # argmax(1) assumes [B, C, H, W] (class-first). Some terratorch heads
    # return [B, H, W, C]; that layout would silently produce a garbage
    # seg mask without raising. Assert the channel axis is the class
    # dimension before reducing.
    if logits.ndim != 4 or logits.shape[0] != 1 or logits.shape[1] >= logits.shape[2] or logits.shape[1] >= logits.shape[3]:
        raise RuntimeError(
            f"prithvi logits shape {tuple(logits.shape)} is not [1, C, H, W] with C < H,W; refusing to argmax"
        )
    pred = logits.argmax(1)[0].detach().cpu().numpy().astype(np.int16)
    return cv2.resize(pred, (output_hw[1], output_hw[0]), interpolation=cv2.INTER_NEAREST)


def _extract_logits(output):
    # terratorch's SemanticSegmentationTask returns a ModelOutput with .output;
    # other backends return a tensor, dict, or tuple/list.
    direct = getattr(output, "output", None)
    if direct is not None:
        return direct
    if isinstance(output, dict):
        for key in ("logits", "prediction", "pred", "output"):
            if key in output:
                return output[key]
    if isinstance(output, (tuple, list)):
        return output[0]
    return output
