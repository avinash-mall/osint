"""FAIR1M-2.0 fine-grained OBB specialist detector.

FAIR1M-2.0 is the GaoFen Challenge benchmark for fine-grained oriented-box
detection in aerial imagery. It enumerates 37 sub-classes that the DOTA-v1
head does NOT distinguish — aircraft families (Boeing 737/747/777/787,
A220/A321/A330/A350, ARJ21, Cessna), ship sub-types (Liquid/Dry Cargo,
Warship, Tugboat, Fishing Boat, Engineering Ship), and vehicle sub-types
(Dump Truck, Tractor, Truck Tractor, Excavator).

Same interface contract as ``inference-sam3/dota_obb.py``: ``load(device) ->
bundle``, ``run(bundle, image, threshold) -> [(mask, bbox_xyxy, score,
label), ...]``, ``model_versions(bundle) -> dict``. SAM3's fusion path
ingests these tuples without modification — the new label set survives
verbatim through ``classifyToBranch`` and the ontology layer.

The trained checkpoint is NOT shipped with the repo. Operators bake it
via the workflow documented in ``docs/operations/fair1m-bake.md``. When
the weights file is absent the runner returns an empty bundle and
contributes zero candidates — mirrors the DOTA-OBB graceful-degradation
pattern at ``dota_obb.py#L63-L65``.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


# FAIR1M-2.0 fine-grained classes (37 total).
# Source: https://www.gaofen-challenge.com/benchmark (FAIR1M-2.0 spec).
# Spelling matches the GaoFen Challenge label files verbatim so a baked
# checkpoint trained from the public dataset emits these names directly.
FAIR1M_CLASSES: list[str] = [
    # Aircraft (11)
    "Boeing 737", "Boeing 747", "Boeing 777", "Boeing 787",
    "A220", "A321", "A330", "A350",
    "ARJ21", "Cessna", "other-airplane",
    # Naval (9)
    "Liquid Cargo Ship", "Dry Cargo Ship", "Passenger Ship",
    "Warship", "Tugboat", "Fishing Boat", "Engineering Ship",
    "Motorboat", "other-ship",
    # Vehicles (10)
    "Small Car", "Bus", "Cargo Truck", "Dump Truck",
    "Van", "Trailer", "Tractor", "Truck Tractor",
    "Excavator", "other-vehicle",
    # Court / sports (4)
    "Basketball Court", "Tennis Court", "Football Field", "Baseball Field",
    # Infrastructure (3)
    "Roundabout", "Intersection", "Bridge",
]
assert len(FAIR1M_CLASSES) == 37, (
    f"FAIR1M-2.0 spec has 37 classes; got {len(FAIR1M_CLASSES)}"
)


FAIR1M_OBB_WEIGHTS_DIR = os.getenv(
    "FAIR1M_OBB_WEIGHTS_DIR", "/data/inference-weights/fair1m"
)
FAIR1M_OBB_MODEL_ID = os.getenv("FAIR1M_OBB_MODEL_ID", "yolo11m-obb-fair1m.pt")
FAIR1M_OBB_THRESHOLD = float(os.getenv("FAIR1M_OBB_THRESHOLD", "0.30"))
FAIR1M_OBB_IOU = float(os.getenv("FAIR1M_OBB_IOU", "0.50"))
FAIR1M_OBB_IMGSZ = int(os.getenv("FAIR1M_OBB_IMGSZ", "1024"))

# GPU optimization flags shared with DOTA-OBB (see dota_obb.py#L29-L37 for
# rationale on why half-precision stays off for the Ultralytics OBB head).
FAIR1M_OBB_FUSE = os.getenv("SAM3_YOLO_FUSE", "1").strip().lower() in {"1", "true", "yes", "on"}
FAIR1M_OBB_HALF = False
FAIR1M_OBB_CHANNELS_LAST = os.getenv("SAM3_YOLO_CHANNELS_LAST", "0").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_weights_path() -> str:
    """Return absolute path to the checkpoint; relative model_id resolves
    under ``FAIR1M_OBB_WEIGHTS_DIR`` so operators can override either piece."""
    if os.path.isabs(FAIR1M_OBB_MODEL_ID):
        return FAIR1M_OBB_MODEL_ID
    return os.path.join(FAIR1M_OBB_WEIGHTS_DIR, FAIR1M_OBB_MODEL_ID)


def load(device: str) -> dict[str, Any]:
    """Load the FAIR1M-OBB checkpoint.

    Graceful degradation: if Ultralytics is missing, or the weights file
    doesn't exist, or YOLO() raises, the function returns a bundle with
    ``model=None`` and an ``error`` string. Callers (``main.py``) check
    ``bundle.get("model")`` before dispatch — the layer simply contributes
    zero candidates when unloaded. NEVER raises; the absent-weights path
    is the default state on fresh installs.
    """
    weights_path = _resolve_weights_path()
    bundle: dict[str, Any] = {
        "model": None,
        "device": device,
        "model_id": FAIR1M_OBB_MODEL_ID,
        "weights_path": weights_path,
        "error": None,
    }
    if not os.path.exists(weights_path):
        bundle["error"] = f"weights file not found: {weights_path}"
        print(
            f"[fair1m_obb] weights file not found at {weights_path} — "
            "specialist disabled. See docs/operations/fair1m-bake.md."
        )
        return bundle
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        bundle["error"] = str(exc)
        print(f"[fair1m_obb] ultralytics not installed: {exc}")
        return bundle
    from inference_utils import apply_yolo_optimizations

    try:
        model = YOLO(weights_path)
        if device and device != "cpu":
            try:
                model.to(device)
            except Exception:
                pass
            apply_yolo_optimizations(
                model,
                half=FAIR1M_OBB_HALF,
                fuse=FAIR1M_OBB_FUSE,
                channels_last=FAIR1M_OBB_CHANNELS_LAST,
            )
        bundle["model"] = model
        return bundle
    except Exception as exc:
        bundle["error"] = str(exc)
        print(f"[fair1m_obb] failed to load {weights_path}: {exc}")
        return bundle


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    score_threshold: float = FAIR1M_OBB_THRESHOLD,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Run FAIR1M-OBB on a single chip; return SAM3-shaped candidates."""
    if bundle is None or bundle.get("model") is None:
        return []
    model = bundle["model"]
    height, width = image_rgb_uint8.shape[:2]
    from inference_utils import safe_predict, cuda_cleanup

    def _do_predict():
        return model.predict(
            source=image_rgb_uint8,
            imgsz=FAIR1M_OBB_IMGSZ,
            conf=score_threshold,
            iou=FAIR1M_OBB_IOU,
            verbose=False,
            device=bundle.get("device"),
            half=FAIR1M_OBB_HALF,
        )

    try:
        results = safe_predict(
            _do_predict,
            on_oom=cuda_cleanup,
            max_retries=1,
            fallback=lambda: [],
            name="fair1m_obb.predict",
        )
    except Exception as exc:
        print(f"[fair1m_obb] inference failed: {exc}")
        return []

    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for r in results:
        names = r.names if hasattr(r, "names") else {}
        obb = getattr(r, "obb", None)
        if obb is None:
            continue
        try:
            xyxyxyxy = obb.xyxyxyxy.float().cpu().numpy()  # (N, 4, 2)
            confs = obb.conf.float().cpu().numpy()
            cls_ids = obb.cls.cpu().numpy().astype(int)
        except Exception as exc:
            print(f"[fair1m_obb] obb tensor conversion failed: {exc}")
            continue
        for poly, conf, cls_id in zip(xyxyxyxy, confs, cls_ids):
            label = str(names.get(int(cls_id), f"class_{cls_id}"))
            score = float(conf)
            if score < score_threshold:
                continue
            x1 = float(poly[:, 0].min()); x2 = float(poly[:, 0].max())
            y1 = float(poly[:, 1].min()); y2 = float(poly[:, 1].max())
            mask = _polygon_mask(poly, height, width)
            out.append((mask, [x1, y1, x2, y2], score, label))
    return out


def _polygon_mask(poly: np.ndarray, height: int, width: int) -> np.ndarray:
    """Rasterise a 4-point polygon into a boolean mask matching the chip size."""
    try:
        import cv2  # type: ignore
        mask = np.zeros((height, width), dtype=np.uint8)
        pts = poly.astype(np.int32).reshape(-1, 1, 2)
        cv2.fillPoly(mask, [pts], 1)
        return mask.astype(bool)
    except Exception:
        x1 = max(0, int(poly[:, 0].min()))
        x2 = min(width, int(poly[:, 0].max()))
        y1 = max(0, int(poly[:, 1].min()))
        y2 = min(height, int(poly[:, 1].max()))
        mask = np.zeros((height, width), dtype=bool)
        mask[y1:y2, x1:x2] = True
        return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False, "class_count": len(FAIR1M_CLASSES)}
    return {
        "loaded": bundle.get("model") is not None,
        "model_id": bundle.get("model_id"),
        "weights_path": bundle.get("weights_path"),
        "threshold": FAIR1M_OBB_THRESHOLD,
        "imgsz": FAIR1M_OBB_IMGSZ,
        "class_count": len(FAIR1M_CLASSES),
        "error": bundle.get("error"),
    }
