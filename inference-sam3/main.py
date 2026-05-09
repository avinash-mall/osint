from __future__ import annotations

import io
import json
import logging
import os
import tempfile
import threading
import time
import base64
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

import embedding
import fusion
import multispectral
import prithvi_heads
import sam3_runner
import sar
import terramind
from prompts.loader import resolve_prompts


cv2.setNumThreads(0)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Sentinel SAM3 Inference")
logger = logging.getLogger("inference-sam3")

MODEL_VERSION = os.getenv("MODEL_VERSION", "sam3-image+sam3.1-video+dinov3-sat-l+prithvi+terramind")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
SAM3_TEXT_THR = float(os.getenv("SAM3_TEXT_THRESHOLD", "0.30"))
SAM3_BOX_THR = float(os.getenv("SAM3_BOX_THRESHOLD", "0.25"))
SAM3_PRITHVI_OVERLAY_THR = float(os.getenv("SAM3_PRITHVI_OVERLAY_THRESHOLD", "0.30"))
SAM3_SAR_CONF_CAP = float(os.getenv("SAM3_SAR_CONF_CAP", "0.85"))
SAM3_MAX_PROMPTS = int(os.getenv("SAM3_MAX_PROMPTS_PER_REQUEST", "64"))
SAM3_MAX_IMAGE_PROMPTS = int(os.getenv("SAM3_MAX_IMAGE_PROMPTS", str(SAM3_MAX_PROMPTS)))
SAM3_MAX_VIDEO_PROMPTS = int(os.getenv("SAM3_MAX_VIDEO_PROMPTS", "128"))
SAM3_EMBED_DETECTIONS = os.getenv("SAM3_EMBED_DETECTIONS", "0").strip().lower() in {"1", "true", "yes", "on"}
def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}

# Master switch — when 0, individual flags below also default to 0 (kept for compatibility).
SAM3_LOAD_OPTIONAL_MODELS = _flag("SAM3_LOAD_OPTIONAL_MODELS", "1")
_DEFAULT = "1" if SAM3_LOAD_OPTIONAL_MODELS else "0"

# Per-component flags so operators can selectively load on memory-constrained GPUs.
SAM3_LOAD_DINOV3_SAT = _flag("SAM3_LOAD_DINOV3_SAT", _DEFAULT)
SAM3_LOAD_DINOV3_LVD = _flag("SAM3_LOAD_DINOV3_LVD", _DEFAULT)
SAM3_LOAD_PRITHVI    = _flag("SAM3_LOAD_PRITHVI",    _DEFAULT)
SAM3_LOAD_TERRAMIND  = _flag("SAM3_LOAD_TERRAMIND",  _DEFAULT)

_pool: list[dict[str, Any]] = []
_pool_lock = threading.Lock()
_pool_idx = 0
_load_lock = threading.Lock()
_active_lock = threading.Lock()
_active_requests = 0
_model_error: str | None = None


def _load_pool() -> None:
    global _model_error
    if _pool:
        return
    with _load_lock:
        if _pool:
            return
        _model_error = None
        try:
            for device in sam3_runner.resolve_devices(os.getenv("DEVICE", "auto")):
                bundle = {
                    "device": device,
                    "lock": threading.Lock(),
                    "sam3_image": sam3_runner.build_image(device),
                    "sam3_video": None,
                    "dinov3_sat": None,
                    "dinov3_lvd": None,
                    "prithvi": None,
                    "terramind": None,
                }
                if SAM3_LOAD_DINOV3_SAT:
                    bundle["dinov3_sat"] = embedding.load_sat(device)
                if SAM3_LOAD_DINOV3_LVD:
                    bundle["dinov3_lvd"] = embedding.load_lvd(device)
                if SAM3_LOAD_PRITHVI:
                    bundle["prithvi"] = prithvi_heads.load_all(device)
                if SAM3_LOAD_TERRAMIND:
                    bundle["terramind"] = terramind.load(device)
                _pool.append(bundle)
                logger.info("Loaded model bundle on %s with components=%s", device, _bundle_components(bundle))
        except (Exception, SystemExit) as exc:
            _model_error = str(exc)
            logger.exception("Failed to load SAM3 model pool")


def _next_bundle() -> dict[str, Any]:
    if not _pool:
        _load_pool()
    if not _pool:
        raise HTTPException(status_code=503, detail=f"Models not loaded: {_model_error or 'unknown error'}")
    global _pool_idx
    with _pool_lock:
        bundle = _pool[_pool_idx % len(_pool)]
        _pool_idx += 1
    return bundle


def _ensure_video_model(bundle: dict[str, Any]) -> None:
    if bundle.get("sam3_video") is not None:
        return
    with bundle["lock"]:
        if bundle.get("sam3_video") is None:
            try:
                bundle["sam3_video"] = sam3_runner.build_video(bundle["device"])
            except Exception as exc:
                logger.exception("Failed to load SAM3 video model")
                raise HTTPException(status_code=503, detail=f"Video model not loaded: {exc}") from exc


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": bool(_pool),
        "model_error": _model_error,
        "device": os.getenv("DEVICE", "auto"),
        "replicas": [{"device": b["device"], "components": _bundle_components(b)} for b in _pool],
        "model_versions": sam3_runner.versions(),
        "model_version": MODEL_VERSION,
        "gpu_model": GPU_MODEL,
        "active_requests": _active_requests,
        "max_image_prompts": SAM3_MAX_IMAGE_PROMPTS,
        "max_video_prompts": SAM3_MAX_VIDEO_PROMPTS,
        "embed_detections": SAM3_EMBED_DETECTIONS,
        "load_flags": {
            "dinov3_sat": SAM3_LOAD_DINOV3_SAT,
            "dinov3_lvd": SAM3_LOAD_DINOV3_LVD,
            "prithvi": SAM3_LOAD_PRITHVI,
            "terramind": SAM3_LOAD_TERRAMIND,
        },
    }


@app.post("/detect")
async def detect(image: UploadFile = File(...), metadata: str = Form("{}")):
    started = time.perf_counter()
    timings: dict[str, float] = {}
    queue_depth = _enter_request()

    def mark(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 3)
        return now

    try:
        meta = json.loads(metadata or "{}")
    except json.JSONDecodeError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    try:
        raw = await image.read()
        t0 = mark("read_upload", started)
        modality = str(meta.get("modality") or "rgb").lower()
        bundle = _next_bundle()
        t0 = mark("model_queue", t0)

        try:
            if modality == "multispectral":
                chip6 = await run_in_threadpool(multispectral.decode_hls6, raw)
                chip3 = multispectral.hls_to_rgb_preview(chip6)
                chip6_temporal_3 = (
                    await run_in_threadpool(multispectral.decode_hls6_temporal_3, raw)
                    if int(meta.get("hls_timesteps") or 0) == 3
                    else None
                )
                chip2 = None
            elif modality == "sar":
                chip2 = await run_in_threadpool(sar.decode_s1grd, raw)
                chip3 = await run_in_threadpool(terramind.s1_to_s2_rgb, bundle.get("terramind"), chip2, chip2.shape[-2:])
                chip6 = chip6_temporal_3 = None
            else:
                modality = "rgb"
                chip3 = await run_in_threadpool(_decode_rgb, raw)
                chip6 = chip6_temporal_3 = chip2 = None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to decode {modality} chip: {exc}") from exc
        t0 = mark("decode", t0)

        height, width = chip3.shape[:2]
        valid_mask = _decode_valid_mask(meta.get("valid_mask"), (height, width))
        prompt_boxes = meta.get("prompt_boxes")
        prompt_count = 0
        if isinstance(prompt_boxes, list) and prompt_boxes:
            prompt_count = len(prompt_boxes)
            candidates = await run_in_threadpool(sam3_runner.run_box_prompts, bundle, chip3, prompt_boxes, SAM3_BOX_THR)
        else:
            try:
                prompts = resolve_prompts(meta, max_prompts=_prompt_limit(meta, modality))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            prompt_count = len(prompts)
            candidates = await run_in_threadpool(sam3_runner.run_text_prompts, bundle, chip3, prompts, SAM3_TEXT_THR)
        t0 = mark("sam3_inference", t0)

        overlays: dict[str, np.ndarray] = {}
        if modality == "multispectral":
            overlays = await run_in_threadpool(prithvi_heads.run_all, bundle.get("prithvi"), chip6, (height, width), chip6_temporal_3)
        t0 = mark("overlays", t0)

        detections = []
        embedding_ms = 0.0
        for mask, bbox_xyxy, score, label in candidates:
            det = fusion.candidate_to_detection(
                mask,
                bbox_xyxy,
                score,
                label,
                image_size=(width, height),
                modality=modality,
                valid_mask=valid_mask,
            )
            if meta.get("geo"):
                det["geo"] = {**meta["geo"], "obb_map_crs": None, "obb_map_geojson": None}
            if SAM3_EMBED_DETECTIONS:
                emb_start = time.perf_counter()
                det["embedding"] = embedding.embed_crop(bundle.get("dinov3_sat"), chip3, bbox_xyxy)
                embedding_ms += (time.perf_counter() - emb_start) * 1000
            else:
                det["embedding"] = {"model": "disabled", "dim": 0, "fp16_b64": ""}
            if modality == "multispectral":
                det["prithvi_labels"] = fusion.overlay_labels(mask, overlays, threshold=SAM3_PRITHVI_OVERLAY_THR)
            if modality == "sar":
                det["confidence"] = float(min(det["confidence"], SAM3_SAR_CONF_CAP))
                det["sar_proxy"] = True
                det["review_status"] = "review_candidate"
                det["terramind_embedding"] = terramind.pool_patches(bundle.get("terramind"), chip2)
            detections.append(det)
        timings["embedding"] = round(embedding_ms, 3)
        t0 = mark("postprocess", t0)
        detections = fusion.mask_aware_nms(detections, iou=0.50)
        mark("nms", t0)
        timings["total"] = round((time.perf_counter() - started) * 1000, 3)
        logger.info(
            "sam3_detect_timing modality=%s prompts=%s candidates=%s detections=%s queue_depth=%s timings_ms=%s",
            modality,
            prompt_count,
            len(candidates),
            len(detections),
            queue_depth,
            timings,
        )

        return {
            "status": "success",
            "modality": modality,
            "detections": detections,
            "model_version": MODEL_VERSION,
            "model_versions": sam3_runner.versions(),
            "timings_ms": timings,
            "queue_depth": queue_depth,
            "input_metadata": meta,
        }
    finally:
        _leave_request()


@app.post("/detect_video")
async def detect_video(video: UploadFile | None = File(None), metadata: str = Form("{}")):
    try:
        meta = json.loads(metadata or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc}") from exc
    bundle = _next_bundle()
    _ensure_video_model(bundle)
    cleanup_path: Path | None = None
    if video is not None:
        suffix = Path(video.filename or "clip.mp4").suffix or ".mp4"
        fd, tmp_name = tempfile.mkstemp(prefix=f"sam3_{int(time.time() * 1000)}_", suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(await video.read())
        video_path = tmp_name
        cleanup_path = Path(tmp_name)
    else:
        video_path = meta.get("video_path")
        if not video_path:
            raise HTTPException(status_code=400, detail="video upload or metadata.video_path required")

    prompts = resolve_prompts({**meta, "modality": "fmv"}, max_prompts=_prompt_limit(meta, "fmv"))
    frame_stride = max(1, int(meta.get("frame_stride", 1)))
    start_frame = int(meta.get("start_frame", 0))
    end_frame = meta.get("end_frame")
    max_frames = meta.get("max_frames")

    def stream():
        try:
            yield from (
                line + "\n"
                for line in sam3_runner.run_video(
                    bundle,
                    video_path,
                    prompts,
                    frame_stride=frame_stride,
                    start_frame=start_frame,
                    end_frame=end_frame,
                    max_frames=max_frames,
                    dinov3=bundle.get("dinov3_lvd"),
                    score_threshold=SAM3_TEXT_THR,
                )
            )
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)

    return StreamingResponse(stream(), media_type="application/x-ndjson")


def _decode_rgb(raw: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(raw))
    if img.mode != "RGB":
        img = img.convert("RGB")
    return np.array(img)


def _enter_request() -> int:
    global _active_requests
    with _active_lock:
        _active_requests += 1
        return _active_requests


def _leave_request() -> None:
    global _active_requests
    with _active_lock:
        _active_requests = max(0, _active_requests - 1)


def _bundle_components(bundle: dict[str, Any]) -> dict[str, Any]:
    prithvi_bundle = bundle.get("prithvi") or {}
    return {
        "sam3_image": bundle.get("sam3_image") is not None,
        "sam3_video": bundle.get("sam3_video") is not None,
        "dinov3_sat": bundle.get("dinov3_sat") is not None,
        "dinov3_lvd": bundle.get("dinov3_lvd") is not None,
        "prithvi": bool(prithvi_bundle),
        "prithvi_heads": list(prithvi_bundle.get("loaded_heads") or []),
        "terramind": bundle.get("terramind") is not None,
    }


def _prompt_limit(meta: dict[str, Any], modality: str) -> int:
    default = SAM3_MAX_VIDEO_PROMPTS if (modality or "").lower() == "fmv" else SAM3_MAX_IMAGE_PROMPTS
    override = meta.get("max_prompts")
    try:
        requested = int(override)
    except (TypeError, ValueError):
        requested = default
    return max(1, min(requested, default))


def _decode_valid_mask(payload: Any, expected_hw: tuple[int, int]) -> np.ndarray | None:
    if not isinstance(payload, dict):
        return None
    shape = payload.get("shape")
    data_b64 = payload.get("data_b64")
    if not isinstance(shape, list) or len(shape) != 2 or not data_b64:
        return None
    height, width = int(shape[0]), int(shape[1])
    if (height, width) != expected_hw:
        return None
    raw = base64.b64decode(str(data_b64))
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder=payload.get("bitorder", "little"))
    return bits[: height * width].reshape(height, width).astype(bool)
