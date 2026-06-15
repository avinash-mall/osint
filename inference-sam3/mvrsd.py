"""MVRSD military-vehicle specialist detector.

Lightweight Ultralytics YOLO **detect** (axis-aligned box) wrapper, fine-tuned
from ``yolo11m`` on the Military Vehicle Remote Sensing Dataset (MVRSD). Returns
detections in the same tuple shape SAM 3's ``run_text_prompts`` emits:
``(mask, bbox_xyxy, score, label)`` — so the shared ``fusion.py`` path can
ingest results alongside SAM3 / DOTA-OBB / Grounding-DINO.

Categories (5, fixed indices, sub-meter ~0.3 m GSD optical RGB):
  0=SMV (Small Military Vehicle), 1=LMV (Large Military Vehicle),
  2=AFV (Armored Fighting Vehicle), 3=CV (Cargo Vehicle),
  4=MCV (Military Combat Vehicle).

Default-ON specialist (``SAM3_LOAD_MVRSD`` defaults to ``_DEFAULT`` = "1" when
``SAM3_LOAD_OPTIONAL_MODELS=1``, exactly like DOTA-OBB and DINOv3-SAT). It loads
with the ``imagery_rgb`` profile and runs on every RGB ``/detect`` via the normal
default-True ``_layer_active("mvrsd")`` filter — an unfiltered request triggers
it; a non-empty ``enabled_layers`` runs it only if ``mvrsd`` is included. The
civilian-misclassification tradeoff is accepted and mitigated by the
confidence-policy floor (``GLOBAL_CONFIDENCE_FLOOR`` + ``MVRSD_CONF``), RGB-only
scoping, and per-request opt-out (or ``SAM3_LOAD_MVRSD=0``).
See docs/decisions/why-mvrsd-military-vehicle-specialist.md.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


# In-container weights path. The orchestrator bakes the GitHub-release asset to
# this path at build time (Dockerfile + MODEL_MANIFEST, gated on
# MVRSD_WEIGHTS_URL); override at runtime with MVRSD_WEIGHTS_PATH.
MVRSD_WEIGHTS_PATH = os.getenv("MVRSD_WEIGHTS_PATH", "/models/mvrsd/mvrsd_yolo11m.pt")
MVRSD_CONF = float(os.getenv("MVRSD_CONF", "0.25"))
MVRSD_IOU = float(os.getenv("MVRSD_IOU", "0.50"))
MVRSD_IMGSZ = int(os.getenv("MVRSD_IMGSZ", "1024"))

# Fixed class-index → label map. The fine-tuned checkpoint embeds these names,
# but we keep an explicit fallback so a stripped checkpoint still emits the
# canonical labels at the right indices.
MVRSD_CLASS_NAMES: dict[int, str] = {
    0: "SMV",
    1: "LMV",
    2: "AFV",
    3: "CV",
    4: "MCV",
}

# GPU optimization flags (same env vars as YOLOE / DOTA-OBB — set once per
# process by scripts/gpu_profiles.py:runtime_env).
# - half: forced OFF for the same reason YOLOE/DOTA pin it off (see
#   docs/decisions/why-yoloe-half-disabled.md). A half-cast body trips
#   bf16/fp16 dtype mismatches and silently produces zero detections when the
#   .cpu().numpy() conversion below raises and is swallowed. Keep MVRSD in fp32.
MVRSD_FUSE = os.getenv("SAM3_YOLO_FUSE", "1").strip().lower() in {"1", "true", "yes", "on"}
MVRSD_HALF = False
MVRSD_CHANNELS_LAST = os.getenv("SAM3_YOLO_CHANNELS_LAST", "0").strip().lower() in {"1", "true", "yes", "on"}


def load(device: str) -> dict[str, Any]:
    """Load the fine-tuned MVRSD YOLO-detect checkpoint.

    Honour-gated: if the weight file is absent (empty MVRSD_WEIGHTS_URL at
    build time, so the bake step skipped), return an unloaded bundle instead
    of raising — the layer then contributes zero candidates."""
    if not os.path.isfile(MVRSD_WEIGHTS_PATH):
        # Loud, unmissable banner: reaching here means MVRSD was requested
        # (SAM3_LOAD_MVRSD on) but the weight is absent, so the layer silently
        # contributes zero detections. The image build swallows a failed/
        # unauthenticated GitHub-release fetch (needs a valid GITHUB_TOKEN +
        # MVRSD_WEIGHTS_URL), so a bake can "succeed" with an empty /models/mvrsd/.
        bar = "!" * 72
        print(
            f"\n{bar}\n"
            f"[mvrsd] WARNING: weights NOT FOUND at {MVRSD_WEIGHTS_PATH}\n"
            "[mvrsd] The MVRSD military-vehicle specialist is DISABLED and will\n"
            "[mvrsd] contribute ZERO detections (silent degradation). The image\n"
            "[mvrsd] build swallows an unauthenticated GitHub-release fetch, so a\n"
            "[mvrsd] bake can succeed with an empty /models/mvrsd/. Rebuild with a\n"
            "[mvrsd] valid GITHUB_TOKEN + MVRSD_WEIGHTS_URL, or set SAM3_LOAD_MVRSD=0\n"
            "[mvrsd] to opt out intentionally. Surfaced in /health degraded_layers.\n"
            "[mvrsd] See docs/inference/mvrsd-specialist.md.\n"
            f"{bar}",
            flush=True,
        )
        return {"model": None, "device": device, "model_id": MVRSD_WEIGHTS_PATH, "error": "weights_missing"}
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        print(f"[mvrsd] ultralytics not installed: {exc}")
        return {"model": None, "device": device, "model_id": MVRSD_WEIGHTS_PATH, "error": str(exc)}
    from inference_utils import apply_yolo_optimizations

    try:
        model = YOLO(MVRSD_WEIGHTS_PATH)
        if device and device != "cpu":
            try:
                model.to(device)
            except Exception:
                pass
            apply_yolo_optimizations(
                model,
                half=MVRSD_HALF,
                fuse=MVRSD_FUSE,
                channels_last=MVRSD_CHANNELS_LAST,
            )
        return {"model": model, "device": device, "model_id": MVRSD_WEIGHTS_PATH}
    except Exception as exc:
        print(f"[mvrsd] failed to load {MVRSD_WEIGHTS_PATH}: {exc}")
        return {"model": None, "device": device, "model_id": MVRSD_WEIGHTS_PATH, "error": str(exc)}


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    score_threshold: float = MVRSD_CONF,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Run MVRSD on a single RGB chip; return list of SAM3-shaped candidates."""
    if bundle is None or bundle.get("model") is None:
        return []
    model = bundle["model"]
    height, width = image_rgb_uint8.shape[:2]
    from inference_utils import safe_predict, cuda_cleanup, device_ctx

    def _do_predict():
        # Pin the current CUDA device to this replica's GPU for parity with the
        # other threadpool forwards (mirrors dota_obb.run). See
        # docs/decisions/optical-inference-throughput.md.
        with device_ctx(bundle.get("device")):
            return model.predict(
                source=image_rgb_uint8,
                imgsz=MVRSD_IMGSZ,
                conf=score_threshold,
                iou=MVRSD_IOU,
                verbose=False,
                device=bundle.get("device"),
                half=MVRSD_HALF,
            )

    try:
        results = safe_predict(
            _do_predict,
            on_oom=cuda_cleanup,
            max_retries=1,
            fallback=lambda: [],
            name="mvrsd.predict",
        )
    except Exception as exc:
        print(f"[mvrsd] inference failed: {exc}")
        return []

    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for r in results:
        names = r.names if hasattr(r, "names") else {}
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        try:
            # .float() before .cpu().numpy() — if a future code path turns the
            # model half-precision, bf16 tensors raise TypeError on .numpy().
            xyxy = boxes.xyxy.float().cpu().numpy()  # (N, 4)
            confs = boxes.conf.float().cpu().numpy()
            cls_ids = boxes.cls.cpu().numpy().astype(int)
        except Exception as exc:
            print(f"[mvrsd] box tensor conversion failed: {exc}")
            continue
        for box, conf, cls_id in zip(xyxy, confs, cls_ids):
            score = float(conf)
            if score < score_threshold:
                continue
            cid = int(cls_id)
            # Prefer the checkpoint's embedded names; fall back to the fixed map.
            label = str(names.get(cid) or MVRSD_CLASS_NAMES.get(cid, f"class_{cid}"))
            x1 = float(box[0]); y1 = float(box[1])
            x2 = float(box[2]); y2 = float(box[3])
            mask = _box_mask(x1, y1, x2, y2, height, width)
            out.append((mask, [x1, y1, x2, y2], score, label))
    return out


def _box_mask(x1: float, y1: float, x2: float, y2: float, height: int, width: int) -> np.ndarray:
    """Rasterise an axis-aligned box into a boolean mask matching the chip size.

    MVRSD is a detect (HBB) model — no polygon. The filled-box mask is the
    faithful representation: downstream, fusion.mask_to_obb_record runs
    cv2.minAreaRect on the rectangle's contour and recovers the same
    axis-aligned box (angle ~0). Only a degenerate mask (empty, no contour, or
    contour area < OBB_MIN_AREA_PX) routes through fusion._hbb_fallback to the
    raw box corners."""
    mask = np.zeros((height, width), dtype=bool)
    ix1 = max(0, int(round(x1))); ix2 = min(width, int(round(x2)))
    iy1 = max(0, int(round(y1))); iy2 = min(height, int(round(y2)))
    if ix2 > ix1 and iy2 > iy1:
        mask[iy1:iy2, ix1:ix2] = True
    return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False}
    return {
        "loaded": bundle.get("model") is not None,
        "model_id": bundle.get("model_id"),
        "threshold": MVRSD_CONF,
        "imgsz": MVRSD_IMGSZ,
        "classes": list(MVRSD_CLASS_NAMES.values()),
        "error": bundle.get("error"),
    }
