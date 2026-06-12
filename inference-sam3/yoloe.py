"""YOLOE-26x-seg open-vocabulary instance segmentation specialist.

Loads two Ultralytics YOLOE checkpoints in one bundle:
  * ``yoloe-26x-seg-pf`` — prompt-free closed-set head (built-in vocabulary).
  * ``yoloe-26x-seg``    — text-promptable open-vocabulary head.

Both return the SAM3-shaped ``(mask, bbox_xyxy_px, score, label)`` 4-tuple so
the existing ``fusion.mask_aware_nms`` and ``run_video_yoloe`` paths can consume
them without any schema branching.

Pretrained-only — no training. Ultralytics auto-downloads the .pt files to its
on-disk cache on first call, same path ``dota_obb`` uses.
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import numpy as np


YOLOE_PF_MODEL_ID = os.getenv("YOLOE_PF_MODEL_ID", "yoloe-26x-seg-pf.pt")
YOLOE_SEG_MODEL_ID = os.getenv("YOLOE_SEG_MODEL_ID", "yoloe-26x-seg.pt")
YOLOE_THRESHOLD = float(os.getenv("YOLOE_THRESHOLD", "0.25"))
YOLOE_IOU = float(os.getenv("YOLOE_IOU", "0.50"))
YOLOE_IMGSZ = int(os.getenv("YOLOE_IMGSZ", "640"))

# GPU optimization flags emitted by scripts/gpu_profiles.py:runtime_env.
# - fuse: Conv+BN folding, safe everywhere
# - half: forced OFF — ultralytics' YOLOE Lrpc vocab head keeps fp32
#   sub-modules even after model.half(), so a half-cast body trips
#   "mat1 and mat2 must have the same dtype" inside set_classes() and
#   the SwiGLU w12 Linear during predict(). Producing zero detections
#   silently on GPU. See docs/decisions/why-yoloe-half-disabled.md.
# - channels_last: forced OFF — same Lrpc head doesn't honour the
#   layout transform; pinned off until tested per-GPU.
YOLOE_FUSE = os.getenv("SAM3_YOLO_FUSE", "1").strip().lower() in {"1", "true", "yes", "on"}
YOLOE_HALF = False
YOLOE_CHANNELS_LAST = False

# Where the Dockerfile bakes the MobileCLIP2-B TorchScript text encoder that
# ultralytics' YOLOE pulls in text-prompt mode. Default matches the path used
# by the bake step in Dockerfile.gpu — see MODEL_MANIFEST.json.
MOBILECLIP2_TS_PATH = os.getenv(
    "MOBILECLIP2_TS_PATH", "/models/torch/ultralytics/mobileclip2_b.ts"
)
_mobileclip_patch_applied = False


def _patch_mobileclip_asset_path() -> None:
    """Redirect ultralytics' attempt_download_asset for mobileclip2_b.ts.

    Ultralytics' build_text_model() resolves the weight via
    attempt_download_asset("mobileclip2_b.ts"), which writes to CWD on
    miss. CWD here is the bind-mounted /app, so the 242 MB asset leaks
    onto the host every time YOLOE.get_text_pe() runs on a fresh checkout.
    We force resolution to the baked copy under /models/torch/ultralytics
    instead — leaving the host tree clean. If the baked file is missing
    (dev mode, partial bake), we fall through to ultralytics' default
    behaviour so the call still works."""
    global _mobileclip_patch_applied
    if _mobileclip_patch_applied:
        return
    try:
        from ultralytics.utils import downloads as ul_downloads
    except ImportError:
        return
    original = ul_downloads.attempt_download_asset

    def _resolve(file, *args, **kwargs):
        name = os.path.basename(str(file))
        if name == "mobileclip2_b.ts" and os.path.exists(MOBILECLIP2_TS_PATH):
            return MOBILECLIP2_TS_PATH
        return original(file, *args, **kwargs)

    ul_downloads.attempt_download_asset = _resolve
    # ultralytics.nn.text_model imports the symbol directly, so the
    # rebinding above is invisible to it — patch the binding it sees.
    try:
        from ultralytics.nn import text_model as ul_text_model
        ul_text_model.attempt_download_asset = _resolve
    except ImportError:
        pass
    _mobileclip_patch_applied = True


def load(device: str) -> dict[str, Any]:
    """Load both YOLOE checkpoints; either failure is non-fatal."""
    bundle: dict[str, Any] = {
        "pf": None,
        "seg": None,
        "device": device,
        "pf_id": YOLOE_PF_MODEL_ID,
        "seg_id": YOLOE_SEG_MODEL_ID,
        "error": None,
    }
    try:
        from ultralytics import YOLOE  # type: ignore
    except ImportError as exc:
        print(f"[yoloe] ultralytics YOLOE class not available: {exc}")
        bundle["error"] = str(exc)
        return bundle

    _patch_mobileclip_asset_path()

    from inference_utils import apply_yolo_optimizations

    try:
        pf = YOLOE(YOLOE_PF_MODEL_ID)
        if device and device != "cpu":
            try:
                pf.to(device)
            except Exception:
                pass
            apply_yolo_optimizations(
                pf,
                half=YOLOE_HALF,
                fuse=YOLOE_FUSE,
                channels_last=YOLOE_CHANNELS_LAST,
            )
        bundle["pf"] = pf
    except Exception as exc:
        print(f"[yoloe] failed to load {YOLOE_PF_MODEL_ID}: {exc}")
        bundle["error"] = str(exc)

    try:
        seg = YOLOE(YOLOE_SEG_MODEL_ID)
        if device and device != "cpu":
            try:
                seg.to(device)
            except Exception:
                pass
            apply_yolo_optimizations(
                seg,
                half=YOLOE_HALF,
                fuse=YOLOE_FUSE,
                channels_last=YOLOE_CHANNELS_LAST,
            )
        bundle["seg"] = seg
    except Exception as exc:
        print(f"[yoloe] failed to load {YOLOE_SEG_MODEL_ID}: {exc}")
        # Don't overwrite a prior error from the pf load.
        bundle["error"] = bundle["error"] or str(exc)

    return bundle


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    prompts: Iterable[str] | None,
    score_threshold: float = YOLOE_THRESHOLD,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Run YOLOE on a single frame.

    ``prompts`` non-empty → text-conditioned ``-seg`` checkpoint.
    ``prompts`` empty/None → prompt-free ``-pf`` checkpoint.

    Returns SAM3-shaped 4-tuples ``(mask, bbox_xyxy_px, score, label)`` matching
    the chip's pixel dimensions.
    """
    if bundle is None:
        return []
    prompt_list = [p for p in (prompts or []) if p and not str(p).startswith("__")]
    use_seg = bool(prompt_list)
    model = bundle.get("seg") if use_seg else bundle.get("pf")
    if model is None:
        # Fall back to whichever checkpoint loaded so the path still emits
        # something usable on partial-load.
        model = bundle.get("pf") if use_seg else bundle.get("seg")
        use_seg = not use_seg if model is not None else False
    if model is None:
        return []

    height, width = image_rgb_uint8.shape[:2]

    if use_seg:
        if prompt_list:
            try:
                classes = list(prompt_list)
                model.set_classes(classes, model.get_text_pe(classes))
            except Exception as exc:
                import sam3_runner
                if sam3_runner._cuda_context_poisoned(exc):
                    raise
                print(f"[yoloe] set_classes failed: {exc}")
                return []
        else:
            # pf checkpoint missing → fell back to seg with NO prompts.
            # set_classes([], get_text_pe([])) raises (or leaves a zero-class
            # vocab), so the fallback always emitted nothing. Run seg with its
            # baked vocabulary instead.
            print(
                "[yoloe] pf checkpoint unavailable; running seg with its baked "
                "vocabulary (no text prompts)",
                flush=True,
            )

    from inference_utils import safe_predict, cuda_cleanup

    def _do_predict():
        return model.predict(
            source=image_rgb_uint8,
            imgsz=YOLOE_IMGSZ,
            conf=score_threshold,
            iou=YOLOE_IOU,
            verbose=False,
            device=bundle.get("device"),
            half=YOLOE_HALF,
        )

    try:
        results = safe_predict(
            _do_predict,
            on_oom=cuda_cleanup,
            max_retries=1,
            fallback=lambda: [],
            name="yoloe.predict",
        )
    except Exception as exc:
        # An unrecoverable CUDA fault must propagate so the caller's self-heal
        # (run_video_yoloe → os._exit(1)) fires; returning [] here made every
        # subsequent frame fail identically while looking like "no detections".
        import sam3_runner
        if sam3_runner._cuda_context_poisoned(exc):
            raise
        print(f"[yoloe] inference failed: {exc}", flush=True)
        return []

    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for r in results:
        names = getattr(r, "names", {}) or {}
        boxes = getattr(r, "boxes", None)
        masks = getattr(r, "masks", None)
        if boxes is None:
            continue
        try:
            # Cast to float32 first — under autocast / bf16 the model can
            # return bf16 boxes/conf, and numpy() refuses to convert
            # ScalarType BFloat16. The except clause used to swallow this
            # and silently return zero detections.
            xyxy = boxes.xyxy.float().cpu().numpy()
            confs = boxes.conf.float().cpu().numpy()
            cls_ids = boxes.cls.long().cpu().numpy()
        except Exception:
            continue
        mask_data: np.ndarray | None = None
        if masks is not None:
            try:
                # Same bf16-vs-numpy guard as the boxes block above.
                mask_data = masks.data.float().cpu().numpy()
            except Exception:
                mask_data = None
        for idx in range(len(confs)):
            score = float(confs[idx])
            if score < score_threshold:
                continue
            label = str(names.get(int(cls_ids[idx]), f"class_{int(cls_ids[idx])}"))
            x1, y1, x2, y2 = (float(v) for v in xyxy[idx])
            if mask_data is not None and idx < len(mask_data):
                mask = _resize_mask(mask_data[idx], height, width)
            else:
                mask = _bbox_mask(x1, y1, x2, y2, height, width)
            out.append((mask, [x1, y1, x2, y2], score, label))
    return out


def _resize_mask(mask: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resize a YOLOE mask (returned at imgsz) to the chip's H,W."""
    arr = np.asarray(mask)
    if arr.shape[:2] == (height, width):
        return arr.astype(bool)
    try:
        import cv2  # type: ignore
        resized = cv2.resize(
            arr.astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
        )
        return resized.astype(bool)
    except Exception:
        return _bbox_from_mask_fallback(arr, height, width)


def _bbox_from_mask_fallback(mask: np.ndarray, height: int, width: int) -> np.ndarray:
    """Last-resort: project the mask's bounding box into an HxW mask."""
    if mask.ndim != 2 or mask.size == 0 or not mask.any():
        return np.zeros((height, width), dtype=bool)
    ys, xs = np.where(mask)
    mh, mw = mask.shape[:2]
    sx = width / max(1, mw)
    sy = height / max(1, mh)
    x1 = max(0, int(xs.min() * sx)); x2 = min(width, int((xs.max() + 1) * sx))
    y1 = max(0, int(ys.min() * sy)); y2 = min(height, int((ys.max() + 1) * sy))
    out = np.zeros((height, width), dtype=bool)
    if x2 > x1 and y2 > y1:
        out[y1:y2, x1:x2] = True
    return out


def _bbox_mask(x1: float, y1: float, x2: float, y2: float, height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    xi1 = max(0, int(round(x1))); xi2 = min(width, int(round(x2)))
    yi1 = max(0, int(round(y1))); yi2 = min(height, int(round(y2)))
    if xi2 > xi1 and yi2 > yi1:
        mask[yi1:yi2, xi1:xi2] = True
    return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False}
    return {
        "loaded": bundle.get("pf") is not None or bundle.get("seg") is not None,
        "pf_id": bundle.get("pf_id"),
        "seg_id": bundle.get("seg_id"),
        "pf_loaded": bundle.get("pf") is not None,
        "seg_loaded": bundle.get("seg") is not None,
        "threshold": YOLOE_THRESHOLD,
        "imgsz": YOLOE_IMGSZ,
        "error": bundle.get("error"),
    }
