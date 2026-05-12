from __future__ import annotations

import gc
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
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool

import requests
import torch

import dota_obb
import embedding
import fusion
import grounding_dino
import grounding_dino_gate
import multispectral
import prithvi_heads
import sam3_runner
import sar
import terramind


cv2.setNumThreads(0)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

# TF32 matmul defaults per GPU profile (sm_80 and above: enabled; sm_75: off).
# `scripts/gpu_profiles.GpuBuildProfile.enable_tf32` -> .env via
# `scripts/configure_host.py` -> here. Operators can force it via env.
if os.getenv("SAM3_ENABLE_TF32", "1").strip().lower() in {"1", "true", "yes", "on"}:
    try:
        import torch as _tf32_torch
        _tf32_torch.backends.cuda.matmul.allow_tf32 = True
        _tf32_torch.backends.cudnn.allow_tf32 = True
        _tf32_torch.set_float32_matmul_precision("high")
        logging.getLogger("inference-sam3").info(
            "TF32 matmul enabled (allow_tf32=%s, precision=%s)",
            _tf32_torch.backends.cuda.matmul.allow_tf32,
            _tf32_torch.get_float32_matmul_precision(),
        )
    except Exception as _tf32_exc:
        logging.getLogger("inference-sam3").warning("Could not enable TF32: %s", _tf32_exc)

# Silence known upstream deprecation chatter that floods logs at startup. The
# source libraries can't be changed from here; the warnings are non-actionable
# and bury the actually-useful "session started/ended" messages.
import warnings as _warnings
_warnings.filterwarnings(
    "ignore",
    message=r"pkg_resources is deprecated as an API.*",
)
_warnings.filterwarnings(
    "ignore",
    message=r"Importing from timm\.models\.layers is deprecated.*",
)
_warnings.filterwarnings(
    "ignore",
    message=r".*torch_dtype.*is deprecated.*",
)

app = FastAPI(title="Sentinel SAM3 Inference")
logger = logging.getLogger("inference-sam3")

MODEL_VERSION = os.getenv("MODEL_VERSION", "sam3-image+sam3.1-video+dinov3-sat-l+prithvi+terramind")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
SAM3_TEXT_THR = float(os.getenv("SAM3_TEXT_THRESHOLD", "0.50"))
SAM3_BOX_THR = float(os.getenv("SAM3_BOX_THRESHOLD", "0.25"))
SAM3_PRITHVI_OVERLAY_THR = float(os.getenv("SAM3_PRITHVI_OVERLAY_THRESHOLD", "0.30"))
SAM3_SAR_CONF_CAP = float(os.getenv("SAM3_SAR_CONF_CAP", "0.85"))
SAM3_MAX_PROMPTS = int(os.getenv("SAM3_MAX_PROMPTS_PER_REQUEST", "64"))
SAM3_MAX_IMAGE_PROMPTS = int(os.getenv("SAM3_MAX_IMAGE_PROMPTS", str(SAM3_MAX_PROMPTS)))
SAM3_MAX_VIDEO_PROMPTS = int(os.getenv("SAM3_MAX_VIDEO_PROMPTS", "128"))
SAM3_EMBED_DETECTIONS = os.getenv("SAM3_EMBED_DETECTIONS", "0").strip().lower() in {"1", "true", "yes", "on"}
def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


SAM3_PRELOAD_MODELS = _flag("SAM3_PRELOAD_MODELS", "0")
# Optional explicit preload profile (fmv|imagery). Empty = no preload, models
# load on first /load call or first request.
SAM3_PRELOAD_PROFILE = (os.getenv("SAM3_PRELOAD_PROFILE", "") or "").strip().lower()

# Master switch — when 0, individual flags below also default to 0 (kept for compatibility).
SAM3_LOAD_OPTIONAL_MODELS = _flag("SAM3_LOAD_OPTIONAL_MODELS", "1")
_DEFAULT = "1" if SAM3_LOAD_OPTIONAL_MODELS else "0"

# Per-component flags so operators can selectively load on memory-constrained GPUs.
SAM3_LOAD_DINOV3_SAT = _flag("SAM3_LOAD_DINOV3_SAT", _DEFAULT)
# DINOV3_LVD removed: produces NaN embeddings on small drone-video crops and
# is 2.5× slower than DINOV3_SAT with no measured quality advantage. See
# docs/video_tracking_stability.md.
SAM3_LOAD_PRITHVI    = _flag("SAM3_LOAD_PRITHVI",    _DEFAULT)
SAM3_LOAD_TERRAMIND  = _flag("SAM3_LOAD_TERRAMIND",  _DEFAULT)

# Specialist detectors that complement SAM 3 zero-shot prompts.
# DEFENCE_YOLO was removed: produced 1297 false positives across 26 DOTA val
# chips with no true positives (see docs/inference_layer_comparison*).
SAM3_LOAD_DOTA_OBB        = _flag("SAM3_LOAD_DOTA_OBB",        _DEFAULT)
SAM3_LOAD_GROUNDING_DINO  = _flag("SAM3_LOAD_GROUNDING_DINO",  _DEFAULT)

# Profile -> component set. "fmv" keeps VRAM small for video tracking;
# "imagery" loads the full geospatial stack for satellite detection.
PROFILE_COMPONENTS: dict[str, tuple[str, ...]] = {
    # FMV hybrid pipeline (per the SAM 3.1 drone-FMV survey paper):
    #   sam3_image  — preprocessing layers shared with sam3_video
    #   sam3_video  — temporal mask propagation (Object Multiplex when it fits)
    #   grounding_dino — open-vocabulary box detector used at keyframes to
    #                    re-seed the tracker (text-grounded video alone misses
    #                    later-clip objects once the camera moves)
    #   dota_obb    — aerial-trained oriented-bbox detector for vehicles
    # DINOv3-SAT / Prithvi / Terramind stay out (they're satellite-imagery
    # specific and would tip the 16 GiB GPU over budget).
    "fmv": tuple(c for c in (
        "sam3_image",
        "sam3_video",
        "grounding_dino" if SAM3_LOAD_GROUNDING_DINO else None,
        "dota_obb" if SAM3_LOAD_DOTA_OBB else None,
    ) if c),
    "imagery": (
        "sam3_image",
        "dinov3_sat" if SAM3_LOAD_DINOV3_SAT else None,
        "prithvi" if SAM3_LOAD_PRITHVI else None,
        "terramind" if SAM3_LOAD_TERRAMIND else None,
        "dota_obb" if SAM3_LOAD_DOTA_OBB else None,
        "grounding_dino" if SAM3_LOAD_GROUNDING_DINO else None,
    ),
}
PROFILE_COMPONENTS["imagery"] = tuple(c for c in PROFILE_COMPONENTS["imagery"] if c)

# "all" is the union of every other profile's components — used by big GPUs
# (40-80 GiB datacenter cards) that want both fmv and imagery served without
# the unload/reload pause on profile switch. `_ensure_profile` treats "all"
# as satisfying any single-profile request whose components are a subset.
PROFILE_COMPONENTS["all"] = tuple(sorted({
    component
    for profile_name, components in PROFILE_COMPONENTS.items()
    if profile_name != "all"
    for component in components
}))

_pool: list[dict[str, Any]] = []
_pool_lock = threading.Lock()
_pool_idx = 0
_load_lock = threading.Lock()
_active_lock = threading.Lock()
_active_requests = 0
_model_error: str | None = None
_current_profile: str | None = None


# ---------------------------------------------------------------------------
# Prompt resolution: DB-derived defaults via backend ontology API.
# Replaces the deleted prompts.loader module which read static JSON profiles.
# ---------------------------------------------------------------------------
ONTOLOGY_BACKEND_URL = os.getenv("ONTOLOGY_BACKEND_URL", "http://backend:8080")
_DEFAULT_PROMPTS_TTL = 30.0  # seconds — matches backend ontology cache TTL
_DEFAULT_PROMPTS_CACHE: dict[str, tuple[float, list[str]]] = {}
_DEFAULT_PROMPTS_LOCK = threading.Lock()


class OntologyBackendUnavailable(RuntimeError):
    """Raised when the backend ontology API cannot be reached and no
    explicit text_prompts were supplied. Mapped to HTTP 503 by callers."""


def _modality_to_sensor(modality: str) -> str:
    """Map /detect modality strings to ontology sensor type."""
    m = (modality or "rgb").lower()
    return {
        "rgb": "optical",
        "fmv": "optical",
        "multispectral": "multispectral",
        "hyperspectral": "multispectral",  # routed here per plan §F (UI surfaces a warning)
        "sar": "sar",
    }.get(m, "optical")


def _fetch_default_prompts(sensor: str, timeout: float = 5.0) -> list[str]:
    """Fetch the default prompt list from the backend ontology API.
    Caches per-sensor for _DEFAULT_PROMPTS_TTL seconds."""
    now = time.time()
    with _DEFAULT_PROMPTS_LOCK:
        cached = _DEFAULT_PROMPTS_CACHE.get(sensor)
        if cached and (now - cached[0]) < _DEFAULT_PROMPTS_TTL:
            return cached[1]
    # Network fetch happens outside the lock so concurrent requests for
    # different sensors don't serialize on each other.
    resp = requests.get(
        f"{ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts",
        params={"sensor": sensor},
        timeout=timeout,
    )
    resp.raise_for_status()
    prompts = [str(p) for p in (resp.json().get("prompts") or [])]
    with _DEFAULT_PROMPTS_LOCK:
        _DEFAULT_PROMPTS_CACHE[sensor] = (time.time(), prompts)
    return prompts


def resolve_prompts(meta: dict[str, Any] | None) -> list[str]:
    """Resolve the SAM3 prompt list for a request.

    Order of resolution:
      1. Explicit ``meta['text_prompts']`` (deduped + lowercased + stripped).
      2. Backend ontology defaults for the sensor mapped from ``meta['modality']``.

    Raises:
        ValueError: when neither source produces any prompts (e.g. caller
            passed an empty list and the backend returned nothing).
        OntologyBackendUnavailable: when the backend HTTP call fails and no
            explicit text_prompts were given. Callers should map to HTTP 503.
    """
    meta = meta or {}
    explicit = meta.get("text_prompts")
    if isinstance(explicit, list) and explicit:
        seen: set[str] = set()
        out: list[str] = []
        for raw in explicit:
            s = " ".join(str(raw).strip().lower().split())
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        if out:
            return out

    sensor = _modality_to_sensor(str(meta.get("modality") or "rgb"))
    try:
        prompts = _fetch_default_prompts(sensor)
    except Exception as exc:
        raise OntologyBackendUnavailable(
            f"No text_prompts provided and ontology backend unavailable "
            f"(sensor={sensor}): {exc}"
        ) from exc

    seen2: set[str] = set()
    out2: list[str] = []
    for raw in prompts:
        s = " ".join(str(raw).strip().lower().split())
        if s and s not in seen2:
            seen2.add(s)
            out2.append(s)
    if not out2:
        raise ValueError(
            f"No labels available for SAM3 (sensor={sensor}): backend returned "
            f"no prompts and no text_prompts were supplied"
        )
    return out2


@app.on_event("startup")
def preload_models_on_startup() -> None:
    profile = SAM3_PRELOAD_PROFILE or ("imagery" if SAM3_PRELOAD_MODELS else "")
    if not profile:
        logger.info("SAM3 preload disabled; models load on demand via POST /load")
        return
    if profile not in PROFILE_COMPONENTS:
        logger.error("Unknown SAM3_PRELOAD_PROFILE=%s — skipping preload", profile)
        return
    started = time.perf_counter()
    logger.info("Preloading SAM3 profile=%s on startup", profile)
    _load_profile(profile)
    elapsed = time.perf_counter() - started
    if _pool:
        logger.info("Preloaded SAM3 profile=%s in %.3fs", profile, elapsed)
    else:
        logger.error("SAM3 preload failed in %.3fs: %s", elapsed, _model_error or "unknown error")


def _build_component(name: str, device: str) -> Any:
    """Build a single component on the given device. Centralised so the
    profile loader and any future hot-add code share one path."""
    if name == "sam3_image":
        return sam3_runner.build_image(device)
    if name == "sam3_video":
        return sam3_runner.build_video(device)
    if name == "dinov3_sat":
        return embedding.load_sat(device)
    if name == "prithvi":
        return prithvi_heads.load_all(device)
    if name == "terramind":
        return terramind.load(device)
    if name == "dota_obb":
        return dota_obb.load(device)
    if name == "grounding_dino":
        return grounding_dino.load(device)
    raise ValueError(f"unknown component: {name}")


def _empty_bundle(device: str) -> dict[str, Any]:
    return {
        "device": device,
        "lock": threading.Lock(),
        "sam3_image": None,
        "sam3_video": None,
        "dinov3_sat": None,
        "prithvi": None,
        "terramind": None,
        "dota_obb": None,
        "grounding_dino": None,
    }


def _unload_pool_locked() -> None:
    """Drop every model in every bundle and free CUDA memory.
    Must be called while holding _load_lock."""
    global _pool, _current_profile
    if not _pool:
        _current_profile = None
        return
    for bundle in _pool:
        for key in list(bundle.keys()):
            if key in ("device", "lock"):
                continue
            bundle[key] = None
    _pool = []
    _current_profile = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


def _load_profile(profile: str) -> None:
    """Idempotent: if already loaded, no-op. If a different profile is
    loaded, unload it first. If the "all" superset is loaded, any
    single-profile request whose components are a subset is served as a
    no-op (datacenter GPUs keep both fmv and imagery resident)."""
    global _model_error, _current_profile
    if profile not in PROFILE_COMPONENTS:
        raise HTTPException(status_code=400, detail=f"unknown profile: {profile}")
    components = PROFILE_COMPONENTS[profile]
    with _load_lock:
        if _current_profile == profile and _pool:
            return
        if (
            _current_profile == "all"
            and _pool
            and set(components).issubset(PROFILE_COMPONENTS["all"])
        ):
            return
        if _pool:
            _unload_pool_locked()
        _model_error = None
        try:
            for device in sam3_runner.resolve_devices(os.getenv("DEVICE", "auto")):
                bundle = _empty_bundle(device)
                for name in components:
                    bundle[name] = _build_component(name, device)
                _pool.append(bundle)
                logger.info("Loaded profile=%s on %s components=%s", profile, device, _bundle_components(bundle))
            _current_profile = profile
        except (Exception, SystemExit) as exc:
            _model_error = str(exc)
            logger.exception("Failed to load SAM3 profile=%s", profile)
            # Roll back partial state so the next attempt starts clean.
            _unload_pool_locked()
            raise HTTPException(status_code=503, detail=f"Failed to load profile {profile}: {exc}") from exc


def _ensure_profile(profile: str) -> None:
    """Wrapper used by request handlers: load the profile if missing.

    If the pool is currently loaded as "all" (the multi-profile superset
    used by big GPUs to skip /load latency), any single-profile request
    whose components are a subset is served without a reload."""
    if _current_profile == profile and _pool:
        return
    if (
        _current_profile == "all"
        and _pool
        and profile in PROFILE_COMPONENTS
        and set(PROFILE_COMPONENTS[profile]).issubset(PROFILE_COMPONENTS["all"])
    ):
        return
    _load_profile(profile)


def _next_bundle() -> dict[str, Any]:
    if not _pool:
        raise HTTPException(
            status_code=503,
            detail=f"Models not loaded: {_model_error or 'no profile loaded; call POST /load?profile=fmv|imagery'}",
        )
    global _pool_idx
    with _pool_lock:
        bundle = _pool[_pool_idx % len(_pool)]
        _pool_idx += 1
    return bundle


def _acquire_video_bundle() -> dict[str, Any]:
    """Return a bundle whose per-bundle lock has been acquired non-blocking.

    Each multiplex predictor allocates ~3 GiB of session activations the
    first time a session starts; running two concurrent sessions on the
    same GPU OOMs the second one. By gating on the bundle's existing
    `threading.Lock` (non-blocking) we ensure at most one in-flight video
    session per GPU. The caller must release the lock when its stream
    finishes (success or error).

    Returns 503 when every bundle is busy — the worker's bounded
    ThreadPoolExecutor sees this as backpressure and retries.
    """
    if not _pool:
        raise HTTPException(
            status_code=503,
            detail=f"Models not loaded: {_model_error or 'no profile loaded; call POST /load?profile=fmv|imagery'}",
        )
    global _pool_idx
    with _pool_lock:
        start = _pool_idx % len(_pool)
        _pool_idx += 1
    # Scan from `start` so subsequent callers prefer different bundles
    # under load (round-robin fairness when multiple slots are free).
    for offset in range(len(_pool)):
        bundle = _pool[(start + offset) % len(_pool)]
        if bundle["lock"].acquire(blocking=False):
            return bundle
    raise HTTPException(
        status_code=503,
        detail=f"All {len(_pool)} video bundles busy; retry after current sessions finish",
    )


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model_loaded": bool(_pool),
        "current_profile": _current_profile,
        "available_profiles": list(PROFILE_COMPONENTS.keys()),
        "model_error": _model_error,
        "device": os.getenv("DEVICE", "auto"),
        "pool_size": len(_pool),
        "replicas": [{"device": b["device"], "components": _bundle_components(b)} for b in _pool],
        "model_versions": sam3_runner.versions(),
        "model_version": MODEL_VERSION,
        "gpu_model": GPU_MODEL,
        "active_requests": _active_requests,
        "embed_detections": SAM3_EMBED_DETECTIONS,
        "load_flags": {
            "dinov3_sat": SAM3_LOAD_DINOV3_SAT,
            "prithvi": SAM3_LOAD_PRITHVI,
            "terramind": SAM3_LOAD_TERRAMIND,
            "dota_obb": SAM3_LOAD_DOTA_OBB,
            "grounding_dino": SAM3_LOAD_GROUNDING_DINO,
        },
    }


@app.post("/load")
def load_profile(profile: str = Query(...)) -> dict[str, Any]:
    """Load a named model profile, unloading any other profile first.

    Idempotent: if the requested profile is already loaded, returns the
    current state without rebuilding."""
    profile = (profile or "").strip().lower()
    if _active_requests > 0:
        raise HTTPException(status_code=409, detail=f"{_active_requests} request(s) in flight; retry after they finish")
    _load_profile(profile)
    return {"loaded": True, "current_profile": _current_profile, "replicas": [_bundle_components(b) for b in _pool]}


@app.post("/unload")
def unload_models() -> dict[str, Any]:
    """Release all loaded models and free CUDA memory.

    `_unload_pool_locked` can't reliably free SAM3's VRAM because upstream
    library code retains references (model caches, torch.compile artifacts,
    dynamo state) that survive `del + gc.collect + torch.cuda.empty_cache`.
    The only reliable way to give every byte back to the driver is to kill
    the process and let docker compose's restart policy bring it back
    fresh. We respond first, then exit after a short delay so the HTTP
    body is flushed."""
    if _active_requests > 0:
        raise HTTPException(status_code=409, detail=f"{_active_requests} request(s) in flight; retry after they finish")
    # Tear down soft state for callers that immediately read /health.
    with _load_lock:
        _unload_pool_locked()
    def _bye():
        time.sleep(0.5)
        # Non-zero exit so any restart policy (on-failure, unless-stopped)
        # treats this as a death worth respawning.
        os._exit(1)
    threading.Thread(target=_bye, daemon=True).start()
    return {"loaded": False, "current_profile": None, "restarting": True}


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

    # Per-request layer toggle. When enabled_layers is present, only the named
    # layers run (SAM3 always runs regardless). When absent, all loaded layers
    # run as usual (backward-compatible default).
    _raw_enabled = meta.get("enabled_layers")
    _enabled = set(_raw_enabled) if isinstance(_raw_enabled, list) else set()
    _layer_active = (lambda layer: (layer in _enabled)) if _enabled else (lambda _: True)

    # Surface layers that were requested but are not loaded in the bundle.
    # We compute this after bundle selection so we can check bundle keys.
    # Note: bundle is resolved below; this list is populated after _next_bundle().

    try:
        raw = await image.read()
        t0 = mark("read_upload", started)
        modality = str(meta.get("modality") or "rgb").lower()
        # Auto-heal: if no profile is loaded (or the wrong one is loaded),
        # swap to imagery before this request runs. The frontend normally
        # calls /load on tab switch; this is the safety net.
        _ensure_profile("imagery")
        bundle = _next_bundle()
        t0 = mark("model_queue", t0)

        # Compute unavailable layers now that we have the bundle.
        _unavailable = [l for l in _enabled if l not in ("sam3",) and not bundle.get(l)]

        try:
            if modality == "multispectral":
                chip6 = await run_in_threadpool(multispectral.decode_hls6, raw)
                chip3 = multispectral.hls_to_rgb_preview(chip6)
                chip2 = None
            elif modality == "sar":
                chip2 = await run_in_threadpool(sar.decode_s1grd, raw)
                chip3 = await run_in_threadpool(terramind.s1_to_s2_rgb, bundle.get("terramind"), chip2, chip2.shape[-2:])
                chip6 = None
            else:
                modality = "rgb"
                chip3 = await run_in_threadpool(_decode_rgb, raw)
                chip6 = chip2 = None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to decode {modality} chip: {exc}") from exc
        t0 = mark("decode", t0)

        height, width = chip3.shape[:2]
        valid_mask = _decode_valid_mask(meta.get("valid_mask"), (height, width))
        prompt_boxes = meta.get("prompt_boxes")
        prompt_count = 0
        prompts: list[str] = []
        if isinstance(prompt_boxes, list) and prompt_boxes:
            prompt_count = len(prompt_boxes)
            candidates = await run_in_threadpool(sam3_runner.run_box_prompts, bundle, chip3, prompt_boxes, SAM3_BOX_THR)
        else:
            try:
                prompts = resolve_prompts(meta)
            except OntologyBackendUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            prompt_count = len(prompts)
            candidates = await run_in_threadpool(sam3_runner.run_text_prompts, bundle, chip3, prompts, SAM3_TEXT_THR)
        t0 = mark("sam3_inference", t0)

        # Specialist detectors run alongside SAM 3 and feed into the same fusion
        # NMS pipeline. DOTA-OBB uses Ultralytics' DOTA-v1 class names, the
        # defence-YOLO module uses its own 18 categories, Grounding DINO is
        # an open-vocab text-to-box detector that takes the same prompt list
        # we sent to SAM 3. fusion.mask_aware_nms dedupes overlapping
        # detections across SAM 3 + specialists.
        if bundle.get("dota_obb") and _layer_active("dota_obb"):
            candidates.extend(
                await run_in_threadpool(dota_obb.run, bundle["dota_obb"], chip3, dota_obb.DOTA_OBB_THRESHOLD)
            )
        # GROUNDING_DINO: auto-gated unless prompts include uncommon classes.
        # The gate skips GROUNDING_DINO (~115 ms) when SAM3's pretrained vocab
        # already covers every prompt — see grounding_dino_gate.py for the
        # common-vocab definition (576 ground_v1 + 18 DOTA + geo terms).
        # `force_grounding_dino: true` in metadata bypasses the gate.
        gd_force = bool(meta.get("force_grounding_dino", False))
        gd_should_run, gd_gated_reason = grounding_dino_gate.should_run_grounding_dino(
            prompts, force=gd_force,
        )
        if (
            bundle.get("grounding_dino")
            and prompts
            and _layer_active("grounding_dino")
            and gd_should_run
        ):
            candidates.extend(
                await run_in_threadpool(
                    grounding_dino.run,
                    bundle["grounding_dino"],
                    chip3,
                    prompts,
                    grounding_dino.GROUNDING_DINO_THR,
                )
            )
        elif _layer_active("grounding_dino") and gd_gated_reason:
            logger.debug(
                "grounding_dino auto-gated: reason=%s n_prompts=%d", gd_gated_reason, len(prompts),
            )
        t0 = mark("specialists", t0)

        overlays: dict[str, np.ndarray] = {}
        if modality == "multispectral":
            if _layer_active("prithvi"):
                overlays = await run_in_threadpool(prithvi_heads.run_all, bundle.get("prithvi"), chip6, (height, width))
            else:
                overlays = {}
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
            # DINOV3_SAT is the only embedding backend. DINOV3_LVD was removed
            # (NaN on drone-video crops, 2.5× slower than SAT, no measured
            # quality advantage — see docs/video_tracking_stability.md).
            if SAM3_EMBED_DETECTIONS and _layer_active("dinov3_sat") and bundle.get("dinov3_sat"):
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
                if _layer_active("terramind") and bundle.get("terramind"):
                    det["terramind_embedding"] = terramind.pool_patches(bundle.get("terramind"), chip2)
                else:
                    det["terramind_embedding"] = None
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
            "enabled_layers_unavailable": _unavailable,
            "grounding_dino_gated": gd_gated_reason,
        }
    finally:
        _leave_request()


@app.post("/detect_video")
async def detect_video(video: UploadFile | None = File(None), metadata: str = Form("{}")):
    queue_depth = _enter_request()
    cleanup_path: Path | None = None
    bundle: dict[str, Any] | None = None
    try:
        try:
            meta = json.loads(metadata or "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid metadata JSON: {exc}") from exc
        _ensure_profile("fmv")
        # Reserve a free bundle for the duration of this video session.
        # Released in the stream's `finally` (success) or the outer except
        # (early failure). The worker fan-out relies on the 503 backpressure
        # when all bundles are busy.
        bundle = _acquire_video_bundle()
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

        try:
            prompts = resolve_prompts({**meta, "modality": "fmv"})
        except OntologyBackendUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Pre-flight the video file. SAM3's `start_session` raises
        # ValueError("Could not open video: ...") from inside the
        # StreamingResponse generator if the file is missing or has no
        # decodable streams (e.g. an FMV chunker wrote a 261-byte stub
        # containing only the `ftyp` box — see Truck.win01 incident
        # 2026-05-12). Catching it here returns a clean 4xx body
        # instead of the 500 + ASGI traceback once the stream has
        # started.
        if not os.path.isfile(video_path):
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)
            raise HTTPException(status_code=404, detail=f"video not found: {video_path}")
        _probe_cap = cv2.VideoCapture(video_path)
        _probe_ok = _probe_cap.isOpened()
        _probe_cap.release()
        if not _probe_ok:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=422,
                detail=f"Could not open video: {video_path} (file exists but has no decodable streams)",
            )

        frame_stride = max(1, int(meta.get("frame_stride", 1)))
        start_frame = int(meta.get("start_frame", 0))
        end_frame = meta.get("end_frame")
        max_frames = meta.get("max_frames")

        reserved = bundle

        def stream():
            try:
                yield from (
                    line + "\n"
                    for line in sam3_runner.run_video(
                        reserved,
                        video_path,
                        prompts,
                        frame_stride=frame_stride,
                        start_frame=start_frame,
                        end_frame=end_frame,
                        max_frames=max_frames,
                        dinov3=None,  # DINOV3_LVD removed (NaN on real video crops)
                        score_threshold=SAM3_TEXT_THR,
                    )
                )
            finally:
                if cleanup_path is not None:
                    cleanup_path.unlink(missing_ok=True)
                reserved["lock"].release()
                _leave_request()

        logger.info(
            "sam3_detect_video_start prompts=%s queue_depth=%s path=%s device=%s",
            len(prompts),
            queue_depth,
            video_path,
            reserved.get("device"),
        )
        # From here on, the stream owns the bundle lock and the
        # _leave_request counter. Mark the outer bundle as "transferred"
        # so the except branch below doesn't double-release on any path
        # after this point.
        bundle = None
        return StreamingResponse(stream(), media_type="application/x-ndjson")
    except Exception:
        if cleanup_path is not None:
            cleanup_path.unlink(missing_ok=True)
        if bundle is not None:
            bundle["lock"].release()
        _leave_request()
        raise


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
    def _model_loaded(b):
        return bool(b) and b.get("model") is not None
    return {
        "sam3_image": bundle.get("sam3_image") is not None,
        "sam3_video": bundle.get("sam3_video") is not None,
        "dinov3_sat": bundle.get("dinov3_sat") is not None,
        "prithvi": bool(prithvi_bundle),
        "prithvi_heads": list(prithvi_bundle.get("loaded_heads") or []),
        "terramind": bundle.get("terramind") is not None,
        "dota_obb": _model_loaded(bundle.get("dota_obb")),
        "grounding_dino": _model_loaded(bundle.get("grounding_dino")),
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
