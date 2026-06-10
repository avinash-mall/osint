"""inference-lae — LAE-DINO open-vocabulary remote-sensing detector sidecar.

Why a separate service
-----------------------
LAE-DINO ("Locate Anything on Earth", AAAI'25) is a Grounding-DINO derivative
fine-tuned on the LAE-1M aerial/satellite corpus (DIOR + DOTAv2 + FAIR1M +
xView + …). Unlike the natural-image Grounding-DINO it replaces, it ships as a
*forked mmdetection* with its own model registry (`LAEDINO(DINO)` + the DVC /
VisGT modules) and is driven by mmengine/mmcv — it cannot share a Python
process with the main `inference-sam3` service, which pins a newer torch +
transformers stack for SAM 3 / TerraMind / Prithvi. So it runs here, behind a
small HTTP contract, and `inference-sam3/grounding_dino.py` calls it as a
client. See docs/decisions/why-lae-dino-replaces-grounding-dino.md.

Contract
--------
GET  /health  → {"model_loaded": bool, "model": str, "model_error": str|None}
POST /detect  (multipart)
    file:           image bytes (PNG/JPEG), RGB
    prompts:        JSON array of class strings, e.g. ["ship","aircraft"]
    threshold:      float box score floor (optional)
    text_threshold: float (accepted for parity; mmdet applies it via the model)
  → {"detections": [{"bbox": [x1,y1,x2,y2], "score": float, "label": str}, ...],
     "model": str}

Boxes only — masks are synthesised client-side (bbox-mask) and refined
downstream by SAM 3, mirroring the previous Grounding-DINO behaviour.
"""
from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inference-lae")

# --- Config (all overridable; defaults point at the baked locations) --------
LAE_CONFIG = os.getenv(
    "LAE_CONFIG",
    "/opt/lae-dino/mmdetection_lae/configs/lae_dino/lae_dino_swin-t_pretrain_LAE-1M.py",
)
LAE_WEIGHTS = os.getenv(
    "LAE_WEIGHTS",
    "/models/lae/checkpoints/lae_dino_swint_lae1m-28ca3a15.pth",
)
# The fork's config references the BERT text encoder by a relative path; we
# override it to the baked absolute dir so nothing is fetched at runtime.
LAE_BERT_DIR = os.getenv("LAE_BERT_DIR", "/models/bert-base-uncased")
LAE_DEVICE = os.getenv("LAE_DEVICE", "cuda:0")
LAE_SCORE_THR = float(os.getenv("LAE_DINO_THRESHOLD", "0.30"))
# mmdet's `chunked_size` caps classes per forward pass. DISABLED by default (0):
# the LAE-DINO fork's chunked predict path is broken on this mmdet version
# (`LAEDINOHead.predict() argument after ** must be a mapping, not tuple`), and
# the client already chunks prompts to ≤GROUNDING_DINO_MAX_PHRASES per request,
# so the token-bleed concern is handled upstream. Leave at 0 unless you've
# confirmed the fork's chunked path works.
LAE_CHUNKED_SIZE = int(os.getenv("LAE_CHUNKED_SIZE", "0"))

# Cross-chip batching. The /detect_batch endpoint runs N chips through one mmdet
# DetInferencer forward (all chips in a pass share the same prompt caption, which
# mmdet broadcasts). Gated by the global SENTINEL_ENABLE_BATCHING; INFERENCE_LAE_
# BATCH_SIZE caps the per-forward image count (VRAM headroom on the dedicated
# LAE card). See docs/decisions/why-lae-cross-chip-batching.md.
_BATCH_ENABLED = os.getenv("SENTINEL_ENABLE_BATCHING", "0").strip().lower() in {"1", "true", "yes", "on"}
LAE_BATCH_SIZE = int(os.getenv("INFERENCE_LAE_BATCH_SIZE", "4")) if _BATCH_ENABLED else 1

app = FastAPI(title="inference-lae", version="1.0")

# Populated at startup by _load().
_STATE: dict[str, Any] = {"inferencer": None, "error": None, "model": "lae_dino_swint_lae1m"}


def _load() -> None:
    """Build the DetInferencer once. Any failure is captured (not raised) so the
    container still serves /health with a useful model_error instead of crash-
    looping — the client degrades gracefully when the model is unavailable."""
    try:
        import torch  # noqa: F401  (import here so /health works even if torch is broken)
        from mmengine.config import Config
        from mmdet.apis import DetInferencer

        cfg = Config.fromfile(LAE_CONFIG)
        # Point the text encoder at the baked BERT dir (config ships a relative
        # path resolved from the fork cwd).
        try:
            cfg.model.language_model.name = LAE_BERT_DIR
        except Exception as exc:  # config shape drift — surface, don't crash
            logger.warning("could not override language_model.name: %s", exc)
        # The Swin backbone init_cfg points at a GitHub URL; the full LAE-DINO
        # checkpoint supersedes it, so null it out to guarantee no runtime fetch.
        try:
            if "backbone" in cfg.model and cfg.model.backbone.get("init_cfg"):
                cfg.model.backbone.init_cfg = None
        except Exception:
            pass
        # The LAE-1M config wraps its test dataset in a ConcatDataset, so the
        # test pipeline isn't at cfg.test_dataloader.dataset.pipeline where
        # DetInferencer._init_pipeline looks for it. Hoist the top-level
        # test_pipeline onto the dataset cfg. The dataset itself is never built
        # for inference (palette != 'none' skips it; custom_entities supplies
        # the classes), so the ConcatDataset's training annotation JSONs — which
        # aren't shipped — are never read.
        try:
            cfg.test_dataloader.dataset.pipeline = cfg.test_pipeline
        except Exception as exc:
            logger.warning("could not hoist test_pipeline: %s", exc)

        # palette='random' (not 'none') is deliberate: 'none' makes
        # DetInferencer build the test dataset to read a palette, which fails on
        # the missing training annotation files. We don't visualise, so the
        # palette is irrelevant.
        inferencer = DetInferencer(
            model=cfg, weights=LAE_WEIGHTS, device=LAE_DEVICE, palette="random",
        )
        if LAE_CHUNKED_SIZE > 0:
            try:
                inferencer.model.test_cfg["chunked_size"] = LAE_CHUNKED_SIZE
            except Exception as exc:
                logger.warning("could not set chunked_size: %s", exc)
        _STATE["inferencer"] = inferencer
        _STATE["error"] = None
        logger.info("LAE-DINO loaded: weights=%s device=%s", LAE_WEIGHTS, LAE_DEVICE)
    except Exception as exc:  # noqa: BLE001 — capture every load failure
        _STATE["inferencer"] = None
        _STATE["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("LAE-DINO load failed")


@app.on_event("startup")
def _startup() -> None:
    _load()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "model_loaded": _STATE["inferencer"] is not None,
        "model": _STATE["model"],
        "model_error": _STATE["error"],
    }


@app.post("/detect")
async def detect(
    file: UploadFile = File(...),
    prompts: str = Form(...),
    threshold: float = Form(LAE_SCORE_THR),
    text_threshold: float = Form(0.25),  # accepted for parity; mmdet owns it
) -> dict[str, Any]:
    inferencer = _STATE["inferencer"]
    if inferencer is None:
        return {"detections": [], "model": _STATE["model"], "error": _STATE["error"]}

    try:
        label_list = [p for p in json.loads(prompts) if isinstance(p, str) and p.strip()]
    except Exception as exc:
        return {"detections": [], "model": _STATE["model"], "error": f"bad prompts: {exc}"}
    if not label_list:
        return {"detections": [], "model": _STATE["model"]}

    raw = await file.read()
    try:
        pil = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        return {"detections": [], "model": _STATE["model"], "error": f"bad image: {exc}"}
    # mmdet Grounding-DINO data_preprocessor expects BGR (bgr_to_rgb=True), so
    # hand it a BGR ndarray.
    rgb = np.asarray(pil)
    bgr = rgb[:, :, ::-1].copy()

    # mmdet's open-vocab caption format: '.'-separated class names + custom
    # entities so each phrase stays cleanly grounded to its own token span.
    texts = " . ".join(label_list)

    try:
        out = inferencer(
            inputs=bgr,
            texts=texts,
            custom_entities=True,
            pred_score_thr=float(threshold),
            return_datasamples=True,
            no_save_vis=True,
            no_save_pred=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("inference failed")
        return {"detections": [], "model": _STATE["model"], "error": str(exc)}

    detections = _extract(out, label_list, float(threshold))
    return {"detections": detections, "model": _STATE["model"]}


@app.post("/detect_batch")
async def detect_batch(
    files: list[UploadFile] = File(...),
    prompts: str = Form(...),
    threshold: float = Form(LAE_SCORE_THR),
    text_threshold: float = Form(0.25),  # accepted for parity; mmdet owns it
) -> dict[str, Any]:
    """Run N chips through ONE mmdet batched forward. All chips share the same
    prompt caption (true for a Sentinel pass), which mmdet broadcasts across the
    batch. Returns one detection list per input file, in order."""
    inferencer = _STATE["inferencer"]
    n = len(files)
    if inferencer is None:
        return {"results": [[] for _ in range(n)], "model": _STATE["model"], "error": _STATE["error"]}
    try:
        label_list = [p for p in json.loads(prompts) if isinstance(p, str) and p.strip()]
    except Exception as exc:
        return {"results": [[] for _ in range(n)], "model": _STATE["model"], "error": f"bad prompts: {exc}"}
    if not label_list or n == 0:
        return {"results": [[] for _ in range(n)], "model": _STATE["model"]}

    try:
        images = [_decode_bgr(await f.read()) for f in files]
    except Exception as exc:
        return {"results": [[] for _ in range(n)], "model": _STATE["model"], "error": f"bad image: {exc}"}

    texts = " . ".join(label_list)
    try:
        out = inferencer(
            inputs=images,
            texts=texts,
            custom_entities=True,
            pred_score_thr=float(threshold),
            batch_size=max(1, LAE_BATCH_SIZE),
            return_datasamples=True,
            no_save_vis=True,
            no_save_pred=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("batched inference failed")
        return {"results": [[] for _ in range(n)], "model": _STATE["model"], "error": str(exc)}

    results = _extract_batch(out, label_list, float(threshold))
    # Defensive: guarantee one list per input even if mmdet returns fewer.
    if len(results) != n:
        results = (results + [[] for _ in range(n)])[:n]
    return {"results": results, "model": _STATE["model"]}


def _preds_list(out: Any) -> Any:
    """mmdet returns {'predictions': [DetDataSample, ...]} (or the bare list)."""
    return out.get("predictions") if isinstance(out, dict) else out


def _extract_one(sample: Any, label_list: list[str], thr: float) -> list[dict[str, Any]]:
    """Pull (bbox_xyxy, score, label) out of one DetDataSample."""
    inst = getattr(sample, "pred_instances", None)
    dets: list[dict[str, Any]] = []
    if inst is None:
        return dets
    bboxes = _to_numpy(getattr(inst, "bboxes", None))
    scores = _to_numpy(getattr(inst, "scores", None))
    labels = _to_numpy(getattr(inst, "labels", None))
    names = getattr(inst, "label_names", None)
    if bboxes is None or scores is None:
        return dets
    for i in range(len(bboxes)):
        score = float(scores[i])
        if score < thr:
            continue
        if names is not None and i < len(names):
            label = str(names[i])
        elif labels is not None and i < len(labels) and int(labels[i]) < len(label_list):
            label = label_list[int(labels[i])]
        else:
            label = ""
        x1, y1, x2, y2 = (float(v) for v in bboxes[i][:4])
        dets.append({"bbox": [x1, y1, x2, y2], "score": score, "label": label})
    return dets


def _extract(out: Any, label_list: list[str], thr: float) -> list[dict[str, Any]]:
    """Single-image extract (back-compat for /detect)."""
    preds = _preds_list(out)
    if not preds:
        return []
    return _extract_one(preds[0], label_list, thr)


def _extract_batch(out: Any, label_list: list[str], thr: float) -> list[list[dict[str, Any]]]:
    """One detection list per input image, in input order."""
    preds = _preds_list(out)
    return [_extract_one(s, label_list, thr) for s in (preds or [])]


def _decode_bgr(raw: bytes) -> np.ndarray:
    """Image bytes -> BGR ndarray (mmdet Grounding-DINO data_preprocessor expects
    BGR via bgr_to_rgb=True)."""
    rgb = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    return rgb[:, :, ::-1].copy()


def _to_numpy(x: Any) -> np.ndarray | None:
    if x is None:
        return None
    if hasattr(x, "detach"):
        return x.detach().cpu().numpy()
    return np.asarray(x)
