from __future__ import annotations

import collections
import contextlib
import gc
import io
import json
import logging
import os
import statistics
import tempfile
import threading
import time
import base64
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import psutil
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
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
import yoloe


cv2.setNumThreads(0)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))


def _setdefault_gdal_env() -> None:
    """Apply tuned GDAL defaults before any rasterio.open() runs.

    Mirrors backend/worker_legacy._setdefault_gdal_env so both processes
    use the same block-cache / vsicurl / mmap settings — keeps the
    chip-decode path consistent with the chip-emit path. Operators
    override via .env / docker-compose.
    """
    defaults = {
        "GDAL_CACHEMAX": "1024",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
        "GDAL_HTTP_MULTIPLEX": "YES",
        "GDAL_HTTP_VERSION": "2",
        "VSI_CACHE": "TRUE",
        "VSI_CACHE_SIZE": "5000000",
        "CPL_VSIL_CURL_CACHE_SIZE": "200000000",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff,.jp2",
        "GTIFF_VIRTUAL_MEM_IO": "YES",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


_setdefault_gdal_env()

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

# cuDNN benchmark per GPU profile. Picks the fastest conv kernel per input
# shape. Helpful when chip sizes are stable (1008x1008 or 640x640 — both
# fixed by env vars). Disabled on Turing because the cu126 stack re-searches
# on every new shape and hurts short bursts.
if os.getenv("SAM3_CUDNN_BENCHMARK", "0").strip().lower() in {"1", "true", "yes", "on"}:
    try:
        import torch as _cudnn_torch
        if _cudnn_torch.cuda.is_available():
            _cudnn_torch.backends.cudnn.benchmark = True
            logging.getLogger("inference-sam3").info("cudnn.benchmark enabled")
    except Exception as _cudnn_exc:
        logging.getLogger("inference-sam3").warning("Could not enable cudnn.benchmark: %s", _cudnn_exc)

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

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Forward reference: preload_models_on_startup is defined below, but
    # is only invoked at server-startup time after module import completes.
    preload_models_on_startup()
    # Unconditionally ensure the "imagery" profile is resident by the end
    # of lifespan startup unless explicitly skipped. This matches what the
    # compose healthcheck expects (model_loaded=true). SAM3_SKIP_PRELOAD=1
    # restores the prior "load on demand" behavior for constrained GPUs.
    if os.getenv("SAM3_SKIP_PRELOAD", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        if not _pool:
            # Rest on SAM3_RESTING_PROFILE (default the full "imagery" union).
            # Tight-VRAM cards set this to "imagery_rgb" so startup fits the
            # GPU budget while still reporting model_loaded=true; MSI/SAR/FMV
            # requests swap to their own modality profile on first use.
            resting = os.getenv("SAM3_RESTING_PROFILE", "imagery").strip() or "imagery"
            try:
                logger.info("lifespan: ensuring %s profile resident for healthcheck", resting)
                _ensure_profile(resting)
            except Exception as exc:  # noqa: BLE001 — startup must not crash
                logger.error("lifespan %s preload failed: %s", resting, exc)
    yield


app = FastAPI(title="Sentinel SAM3 Inference", lifespan=lifespan)
logger = logging.getLogger("inference-sam3")

MODEL_VERSION = os.getenv("MODEL_VERSION", "sam3-image+sam3.1-video+dinov3-sat-l+prithvi+terramind")
GPU_MODEL = os.getenv("GPU_MODEL", "unknown")
SAM3_TEXT_THR = float(os.getenv("SAM3_TEXT_THRESHOLD", "0.50"))
SAM3_BOX_THR = float(os.getenv("SAM3_BOX_THRESHOLD", "0.25"))
SAM3_PRITHVI_OVERLAY_THR = float(os.getenv("SAM3_PRITHVI_OVERLAY_THRESHOLD", "0.30"))
SAM3_SAR_CONF_CAP = float(os.getenv("SAM3_SAR_CONF_CAP", "0.85"))
# SAR detections are produced through a TerraMind S1→S2 synthetic-optical
# proxy and must remain visibly below optical-native confidence. See
# docs/decisions/why-sar-confidence-cap.md. Refuse to start if the env
# override removes the proxy-flag ceiling.
if not (0.0 < SAM3_SAR_CONF_CAP <= 0.95):
    raise RuntimeError(
        f"SAM3_SAR_CONF_CAP={SAM3_SAR_CONF_CAP} is out of range (0, 0.95]; "
        "SAR is a synthetic-optical proxy and must remain below the optical ceiling"
    )
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
# Phase 8.37: Prithvi default-OFF. The burn-scar head measured chip-level
# IoU = 0.0000 on HLS Burn Scars test set (see docs/inference_layer_comparison.md)
# while still costing ~20 ms per chip. Operators with a known-good multispectral
# AOI can re-enable with SAM3_LOAD_PRITHVI=1.
SAM3_LOAD_PRITHVI    = _flag("SAM3_LOAD_PRITHVI",    "0")
SAM3_LOAD_TERRAMIND  = _flag("SAM3_LOAD_TERRAMIND",  _DEFAULT)

# Specialist detectors that complement SAM 3 zero-shot prompts.
# DEFENCE_YOLO was removed: produced 1297 false positives across 26 DOTA val
# chips with no true positives (see docs/inference_layer_comparison*).
SAM3_LOAD_DOTA_OBB        = _flag("SAM3_LOAD_DOTA_OBB",        _DEFAULT)
# Phase 8.38: Grounding-DINO default-OFF. Only +0.0144 mAP improvement on
# DOTA-v1.0 for +241 ms cumulative cost (see docs/inference_layer_comparison.md).
# The auto-gate in grounding_dino_gate.py keeps it from loading when prompts
# are already covered by SAM3+DOTA-OBB. Operators wanting open-vocab recall on
# truly novel labels can re-enable with SAM3_LOAD_GROUNDING_DINO=1.
SAM3_LOAD_GROUNDING_DINO  = _flag("SAM3_LOAD_GROUNDING_DINO",  "0")
# YOLOE-26x open-vocabulary segmentation specialist used by the standalone
# FMV tracker. Bundles both -pf (prompt-free) and -seg (text-prompted)
# checkpoints; intentionally not loaded by the imagery profile.
SAM3_LOAD_YOLOE           = _flag("SAM3_LOAD_YOLOE",           _DEFAULT)

# Profile -> component set. "fmv" keeps VRAM small for video tracking;
# "imagery" loads the full geospatial stack for satellite detection.
PROFILE_COMPONENTS: dict[str, tuple[str, ...]] = {
    # FMV pipeline. Two engines, both selectable from the upload UI:
    #   sam3_image  — preprocessing layers shared with sam3_video
    #   sam3_video  — SAM 3.1 PCS (text-prompted multiplex tracking)
    #   yoloe       — YOLOE-26x-seg(-pf) standalone tracker. -pf covers the
    #                 promptless workflow that AMG used to serve; -seg
    #                 handles text-prompted detection. Skips sam3_video.
    #   dota_obb    — aerial-trained oriented-bbox detector for vehicles
    # Grounding-DINO is no longer part of the FMV bundle: AMG was its only
    # FMV consumer (SAM 3 can't emit labels without text prompts, and we
    # removed AMG-via-GD). GD stays in the imagery profile for /detect.
    # DINOv3-SAT / Prithvi / Terramind stay out (satellite-imagery specific).
    "fmv": tuple(c for c in (
        "sam3_image",
        "sam3_video",
        "dota_obb" if SAM3_LOAD_DOTA_OBB else None,
        "yoloe" if SAM3_LOAD_YOLOE else None,
    ) if c),
    # Per-modality imagery profiles. On tight-VRAM cards (dynamic loading
    # policy, SAM3_LOAD_POLICY=dynamic) only ONE of these is resident at a
    # time, so the modality-specific heavies (prithvi for multispectral,
    # terramind for SAR) never share VRAM with each other or with the RGB
    # detectors. `_profile_for_modality` routes /detect by request modality;
    # `_ensure_profile`'s "all"-superset short-circuit keeps hot cards (that
    # preload "all") reload-free. sam3_image + dinov3_sat are common to all
    # three (DINOv3-SAT powers re-ID embeddings + the /embed endpoint).
    "imagery_rgb": tuple(c for c in (
        "sam3_image",
        "dinov3_sat" if SAM3_LOAD_DINOV3_SAT else None,
        "dota_obb" if SAM3_LOAD_DOTA_OBB else None,
        "grounding_dino" if SAM3_LOAD_GROUNDING_DINO else None,
    ) if c),
    "imagery_msi": tuple(c for c in (
        "sam3_image",
        "dinov3_sat" if SAM3_LOAD_DINOV3_SAT else None,
        "prithvi" if SAM3_LOAD_PRITHVI else None,
    ) if c),
    "imagery_sar": tuple(c for c in (
        "sam3_image",
        "dinov3_sat" if SAM3_LOAD_DINOV3_SAT else None,
        "terramind" if SAM3_LOAD_TERRAMIND else None,
        "dota_obb" if SAM3_LOAD_DOTA_OBB else None,
    ) if c),
}
# "imagery" stays as the union of the per-modality profiles — the resting
# profile used by hot cards and by `POST /load?profile=imagery` (admin / FMV
# revert). On dynamic cards the auto-heal path routes to the per-modality
# profiles instead, so the union is only loaded when explicitly requested.
PROFILE_COMPONENTS["imagery"] = tuple(sorted({
    component
    for name in ("imagery_rgb", "imagery_msi", "imagery_sar")
    for component in PROFILE_COMPONENTS[name]
}))

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
# Health metrics: per-component latency, request count, error count, and a
# rolling 60-second request rate. Powers the Admin · Health Dashboard.
# ---------------------------------------------------------------------------
BOOT_TS = time.time()
# Prime psutil's internal counter so the first /health call returns a real
# CPU percentage instead of 0.0.
psutil.cpu_percent(interval=None)

_HEALTH_COMPONENT_SLUGS = (
    "sam3_image", "sam3_video", "dinov3_sat", "prithvi", "terramind",
    "dota_obb", "grounding_dino", "yoloe_pf", "yoloe_seg",
)
_METRIC_WINDOW = int(os.getenv("SAM3_METRIC_WINDOW", "200"))
_metrics_lock = threading.Lock()
_metrics: dict[str, dict[str, Any]] = {}
_req_log: collections.deque[float] = collections.deque(maxlen=2048)
_IMAGE_YOLOE_LAYERS = {"yoloe", "yoloe_pf", "yoloe_seg"}


def _reject_image_yoloe_layers(enabled: set[str]) -> None:
    normalized = {str(layer).strip().lower() for layer in enabled}
    if normalized & _IMAGE_YOLOE_LAYERS:
        raise HTTPException(
            status_code=400,
            detail="YOLOE is FMV-only; image /detect requests cannot enable yoloe_pf or yoloe_seg.",
        )


def _record_metric(slug: str, ms: float, *, error: bool = False) -> None:
    with _metrics_lock:
        m = _metrics.setdefault(slug, {
            "samples": collections.deque(maxlen=_METRIC_WINDOW),
            "count": 0,
            "errors": 0,
            "last_ts": 0.0,
        })
        if not error:
            m["samples"].append(float(ms))
        m["count"] += 1
        if error:
            m["errors"] += 1
        m["last_ts"] = time.time()


def _metrics_snapshot() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with _metrics_lock:
        for slug in _HEALTH_COMPONENT_SLUGS:
            m = _metrics.get(slug)
            if not m:
                out[slug] = {"requests": 0, "errors": 0, "last_request_ts": None, "p50_ms": None, "p95_ms": None}
                continue
            samples_sorted = sorted(m["samples"])
            p50 = statistics.median(samples_sorted) if samples_sorted else None
            p95 = samples_sorted[int(0.95 * (len(samples_sorted) - 1))] if samples_sorted else None
            out[slug] = {
                "requests": m["count"],
                "errors": m["errors"],
                "last_request_ts": m["last_ts"] or None,
                "p50_ms": round(p50, 2) if p50 is not None else None,
                "p95_ms": round(p95, 2) if p95 is not None else None,
            }
    return out


def _request_rate_60s() -> float:
    now = time.time()
    cutoff = now - 60.0
    while _req_log and _req_log[0] < cutoff:
        _req_log.popleft()
    return len(_req_log) / 60.0


@contextlib.contextmanager
def _track(slug: str):
    t0 = time.perf_counter()
    err = False
    try:
        yield
    except BaseException:
        err = True
        raise
    finally:
        _record_metric(slug, (time.perf_counter() - t0) * 1000, error=err)


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


def _fetch_default_prompts(
    sensor: str, branch: str | None = None, timeout: float = 5.0
) -> list[str]:
    """Fetch the default prompt list from the backend ontology API.

    Caches per ``(sensor, branch)`` for _DEFAULT_PROMPTS_TTL seconds. ``branch``
    scopes the vocabulary to one ontology branch + its descendants — a smaller,
    scene-relevant prompt set that keeps open-vocabulary detection precise
    instead of fanning every chip out across the whole ~130-class vocabulary."""
    key = (sensor, branch or "")
    now = time.time()
    with _DEFAULT_PROMPTS_LOCK:
        cached = _DEFAULT_PROMPTS_CACHE.get(key)
        if cached and (now - cached[0]) < _DEFAULT_PROMPTS_TTL:
            return cached[1]
    # Network fetch happens outside the lock so concurrent requests for
    # different sensors don't serialize on each other.
    params = {"sensor": sensor}
    if branch:
        params["branch"] = branch
    resp = requests.get(
        f"{ONTOLOGY_BACKEND_URL}/api/ontology/default-prompts",
        params=params,
        timeout=timeout,
    )
    resp.raise_for_status()
    prompts = [str(p) for p in (resp.json().get("prompts") or [])]
    with _DEFAULT_PROMPTS_LOCK:
        _DEFAULT_PROMPTS_CACHE[key] = (time.time(), prompts)
    return prompts


def get_ontology_optical_labels() -> frozenset[str]:
    """Lowercase set of admin-ontology prompts for the ``optical`` sensor.

    Used by sam3_runner's FMV AMG path to decide which GD-returned labels
    qualify for the recall-friendly threshold. Returns an empty frozenset
    when the backend ontology is unreachable so callers fall back to the
    high default threshold for every class. Reuses the 30-s TTL cache in
    ``_fetch_default_prompts`` — no extra network traffic on warm cache.
    """
    try:
        prompts = _fetch_default_prompts("optical")
    except Exception as exc:  # noqa: BLE001 — graceful degrade
        logger.debug("ontology fetch failed; per-class GD floor disabled: %s", exc)
        return frozenset()
    return frozenset(p.strip().lower() for p in prompts if p and p.strip())


# Bounded precision-first defaults used when a request omits text_prompts (and
# is not in ontology fan-out mode). Kept deliberately small — a scene-relevant
# common-target set, NOT the full ~130-class ontology — but rich enough that an
# upload with no explicit prompts still detects the usual GEOINT objects instead
# of the anaemic 4-word list that returned almost nothing. Operators override
# per-sensor with SAM3_PRECISION_DEFAULT_PROMPTS; full fan-out stays available
# via SAM3_DEFAULT_PROMPT_SOURCE=ontology. See
# docs/decisions/why-precision-first-inference-defaults.md.
_PRECISION_DEFAULT_PROMPTS: dict[str, tuple[str, ...]] = {
    "optical": (
        "building", "vehicle", "car", "truck", "bus", "aircraft", "helicopter",
        "ship", "boat", "road", "bridge", "storage tank", "shipping container",
    ),
    "multispectral": (
        "building", "vehicle", "aircraft", "ship", "road", "bridge", "storage tank",
    ),
    "sar": ("ship", "vehicle", "building", "storage tank", "aircraft", "bridge"),
}


def _dedupe_prompt_list(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        s = " ".join(str(raw).strip().lower().split())
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _precision_default_prompts(sensor: str) -> list[str]:
    """Small bounded defaults for the precision-first analyst workflow.

    Operators that need the historical broad ontology fan-out can set
    ``SAM3_DEFAULT_PROMPT_SOURCE=ontology``. Per-sensor precision defaults can
    be overridden with ``SAM3_PRECISION_DEFAULT_PROMPTS`` as JSON:
    ``{"optical": ["vehicle", "ship"]}``.
    """
    raw = (os.getenv("SAM3_PRECISION_DEFAULT_PROMPTS") or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                values = parsed.get(sensor) or parsed.get("default")
                if isinstance(values, list):
                    prompts = _dedupe_prompt_list(values)
                    if prompts:
                        return prompts
        except json.JSONDecodeError:
            logger.warning("SAM3_PRECISION_DEFAULT_PROMPTS is not valid JSON; using built-in defaults")
    return list(_PRECISION_DEFAULT_PROMPTS.get(sensor, _PRECISION_DEFAULT_PROMPTS["optical"]))


def resolve_prompts(meta: dict[str, Any] | None) -> list[str]:
    """Resolve the SAM3 prompt list for a request.

    Order of resolution:
      1. Explicit ``meta['text_prompts']`` (deduped + lowercased + stripped).
      2. Precision-first bounded defaults for the sensor mapped from
         ``meta['modality']``.

    Raises:
        ValueError: when the caller explicitly supplied an empty prompt list.
        OntologyBackendUnavailable: when the backend HTTP call fails and no
            explicit text_prompts were given and legacy ontology defaults are
            explicitly enabled. Callers should map to HTTP 503.
    """
    meta = meta or {}
    explicit = meta.get("text_prompts")
    if isinstance(explicit, list):
        out = _dedupe_prompt_list(explicit)
        if out:
            return out
        raise ValueError("text_prompts was provided but empty; provide at least one prompt or use prompt_boxes")

    sensor = _modality_to_sensor(str(meta.get("modality") or "rgb"))
    if (os.getenv("SAM3_DEFAULT_PROMPT_SOURCE", "precision") or "precision").strip().lower() not in {
        "ontology", "backend",
    }:
        return _precision_default_prompts(sensor)[: _prompt_limit(meta, str(meta.get("modality") or "rgb"))]

    # Optional scene scope: when the request names an ontology branch, fetch
    # only that branch's (much smaller) vocabulary. Running a scene-relevant
    # subset instead of the whole ~130-class list is the primary lever against
    # open-vocabulary false positives — see decisions/why-deconflicted-detection-prompts.md.
    branch_raw = meta.get("ontology_branch")
    branch = str(branch_raw).strip() or None if branch_raw else None
    try:
        prompts = _fetch_default_prompts(sensor, branch)
    except Exception as exc:
        raise OntologyBackendUnavailable(
            f"No text_prompts provided and ontology backend unavailable "
            f"(sensor={sensor}, branch={branch}): {exc}"
        ) from exc

    out2 = _dedupe_prompt_list(prompts)
    if not out2:
        raise ValueError(
            f"No labels available for SAM3 (sensor={sensor}): backend returned "
            f"no prompts and no text_prompts were supplied"
        )
    return out2[: _prompt_limit(meta, str(meta.get("modality") or "rgb"))]


_DOTA_RELEVANT_TERMS = frozenset({
    "aircraft", "airplane", "airport", "plane", "fixed wing", "fixed-wing",
    "helicopter", "helipad", "ship", "vessel", "warship", "boat", "harbor",
    "harbour", "vehicle", "truck", "car", "tank", "armored", "armoured",
    "apc", "ifv", "bridge", "storage tank", "container crane", "crane",
    "roundabout", "tennis court", "basketball court", "baseball diamond",
    "soccer ball field", "swimming pool", "ground track field",
})


def _prompts_relevant_to_dota(prompts: list[str]) -> bool:
    haystack = " ".join(str(p).lower() for p in prompts)
    return any(term in haystack for term in _DOTA_RELEVANT_TERMS)


def _tag_candidates(layer: str, candidates: list[tuple[Any, Any, Any, Any]]) -> list[tuple[str, tuple[Any, Any, Any, Any]]]:
    return [(layer, candidate) for candidate in candidates]


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
    if name == "yoloe":
        return yoloe.load(device)
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
        "yoloe": None,
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
    # A resident superset (e.g. the "imagery" union on a hot card, or "all")
    # already satisfies any per-modality imagery request — serve without a
    # reload so hot cards never pay swap latency.
    if (
        _current_profile in PROFILE_COMPONENTS
        and _pool
        and profile in PROFILE_COMPONENTS
        and set(PROFILE_COMPONENTS[profile]).issubset(PROFILE_COMPONENTS[_current_profile])
    ):
        return
    _load_profile(profile)


def _profile_for_modality(modality: str) -> str:
    """Map a /detect request modality to its per-modality imagery profile.

    On dynamic-loading cards this keeps only the requested modality's models
    resident (RGB detectors vs Prithvi vs Terramind never co-resident). On hot
    cards the "imagery"/"all" superset short-circuit in `_ensure_profile`
    serves these without a reload."""
    m = (modality or "rgb").lower()
    if m == "multispectral":
        return "imagery_msi"
    if m == "sar":
        return "imagery_sar"
    return "imagery_rgb"


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


def _vram_stats_gib() -> tuple[float | None, float | None]:
    """Aggregate VRAM (used, total) in GiB across every visible CUDA device.

    Surfaced on /health so the backend's inference dashboard can show real
    GPU pressure instead of "sidecar not reporting". Returns (None, None)
    on CPU-only or any cuda error — the caller treats that as unknown."""
    try:
        if not torch.cuda.is_available():
            return None, None
        used_bytes = 0
        total_bytes = 0
        for idx in range(torch.cuda.device_count()):
            free, total = torch.cuda.mem_get_info(idx)
            used_bytes += total - free
            total_bytes += total
        if total_bytes == 0:
            return None, None
        gib = float(1024 ** 3)
        return used_bytes / gib, total_bytes / gib
    except Exception:
        return None, None


def _system_stats() -> dict[str, Any]:
    """Host-level CPU/RAM/disk stats for the inference dashboard.

    Disk usage walks up from $HF_HOME (defaulting to /models/hf) to the first
    real directory — covers both bind-mounted host caches and ephemeral
    container paths. The chosen path is returned as `disk_path` so operators
    aren't confused when the numbers reflect the host volume."""
    gib = float(1024 ** 3)
    try:
        vm = psutil.virtual_memory()
        cpu_pct = psutil.cpu_percent(interval=None)
        disk_root = os.getenv("HF_HOME", "/models/hf")
        probe = disk_root
        while probe and not os.path.isdir(probe):
            probe = os.path.dirname(probe) or "/"
        du = psutil.disk_usage(probe or "/")
        return {
            "cpu_pct": round(cpu_pct, 1),
            "ram_used_gib": round((vm.total - vm.available) / gib, 2),
            "ram_total_gib": round(vm.total / gib, 2),
            "disk_used_gib": round(du.used / gib, 2),
            "disk_total_gib": round(du.total / gib, 2),
            "disk_path": probe,
        }
    except Exception:
        return {
            "cpu_pct": None,
            "ram_used_gib": None,
            "ram_total_gib": None,
            "disk_used_gib": None,
            "disk_total_gib": None,
            "disk_path": None,
        }


def _version_snapshot(bundle: dict[str, Any] | None = None) -> dict[str, Any]:
    versions = dict(sam3_runner.versions())
    versions["dota_obb"] = dota_obb.model_versions((bundle or {}).get("dota_obb"))
    return versions


@app.get("/health")
def health() -> dict[str, Any]:
    vram_used_gib, vram_total_gib = _vram_stats_gib()
    sample_bundle = _pool[0] if _pool else None
    return {
        "status": "ok",
        "model_loaded": bool(_pool),
        "current_profile": _current_profile,
        "available_profiles": list(PROFILE_COMPONENTS.keys()),
        "model_error": _model_error,
        "device": os.getenv("DEVICE", "auto"),
        "pool_size": len(_pool),
        "replicas": [{"device": b["device"], "components": _bundle_components(b)} for b in _pool],
        "model_versions": _version_snapshot(sample_bundle),
        "model_version": MODEL_VERSION,
        "gpu_model": GPU_MODEL,
        "vram_used_gib": vram_used_gib,
        "vram_total_gib": vram_total_gib,
        "active_requests": _active_requests,
        "embed_detections": SAM3_EMBED_DETECTIONS,
        "track_config": {
            "iou_min": sam3_runner.SAM3_TRACK_IOU_MIN,
            "buffer": sam3_runner.SAM3_TRACK_BUFFER,
            "min_consecutive_frames": sam3_runner.SAM3_TRACK_MIN_CONSECUTIVE_FRAMES,
        },
        "load_flags": {
            "dinov3_sat": SAM3_LOAD_DINOV3_SAT,
            "prithvi": SAM3_LOAD_PRITHVI,
            "terramind": SAM3_LOAD_TERRAMIND,
            "dota_obb": SAM3_LOAD_DOTA_OBB,
            "grounding_dino": SAM3_LOAD_GROUNDING_DINO,
            "yoloe": SAM3_LOAD_YOLOE,
        },
        "uptime_s": round(time.time() - BOOT_TS, 1),
        "system": _system_stats(),
        "metrics": _metrics_snapshot(),
        "request_rate_60s": round(_request_rate_60s(), 3),
    }


@app.get("/health/memory")
def memory_health() -> dict[str, Any]:
    """Per-device GPU memory snapshot. Used by benchmark_detect.py.

    Returns allocated, reserved, and peak (since last reset) for every
    visible CUDA device. ``fragmentation_bytes = reserved - allocated``
    is the canonical signal for caching-allocator pressure.
    """
    try:
        import torch
    except ImportError:
        return {"cuda": False, "devices": []}
    if not torch.cuda.is_available():
        return {"cuda": False, "devices": []}
    devices = []
    for idx in range(torch.cuda.device_count()):
        allocated = torch.cuda.memory_allocated(idx)
        reserved = torch.cuda.memory_reserved(idx)
        peak_allocated = torch.cuda.max_memory_allocated(idx)
        peak_reserved = torch.cuda.max_memory_reserved(idx)
        total = torch.cuda.get_device_properties(idx).total_memory
        devices.append({
            "index": idx,
            "name": torch.cuda.get_device_name(idx),
            "allocated_bytes": int(allocated),
            "reserved_bytes": int(reserved),
            "peak_allocated_bytes": int(peak_allocated),
            "peak_reserved_bytes": int(peak_reserved),
            "total_bytes": int(total),
            "fragmentation_bytes": int(reserved - allocated),
            "free_bytes": int(total - reserved),
        })
    return {"cuda": True, "devices": devices}


@app.post("/health/memory/reset")
def memory_reset() -> dict[str, Any]:
    """Reset per-device peak counters; called by benchmark harness before timing."""
    try:
        import torch
    except ImportError:
        return {"cuda": False, "reset": False}
    if not torch.cuda.is_available():
        return {"cuda": False, "reset": False}
    for idx in range(torch.cuda.device_count()):
        torch.cuda.reset_peak_memory_stats(idx)
    return {"cuda": True, "reset": True}


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


@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """Advertise optional protocol features so clients can negotiate.

    Phase 4: returns ``raw_endpoint: true`` to signal that ``/detect_raw``
    accepts pre-decoded numpy bytes + headers and skips the multipart →
    PIL/PNG → multipart round-trip used by ``/detect``. The worker
    fetches this once at startup and switches to the raw path on
    supported modalities.
    """
    return {
        "raw_endpoint": True,
        "raw_endpoint_path": "/detect_raw",
        "supported_modalities": ["rgb"],  # Phase 4 covers RGB; MSI/SAR remain on /detect
        "supported_dtypes": ["uint8"],
        "protocol_version": 1,
    }


async def _detect_pipeline(
    bundle: dict[str, Any],
    meta: dict,
    modality: str,
    chip3: np.ndarray,
    chip6: np.ndarray | None,
    chip2: np.ndarray | None,
    started: float,
    timings: dict[str, float],
    queue_depth: int,
    _peak_dev: int | None,
    _enabled: set,
    _layer_active,
    _unavailable: list,
) -> dict[str, Any]:
    """Shared post-decode inference path used by both /detect and /detect_raw.

    Everything downstream of the chip-bytes → numpy conversion lives here:
    SAM3 image + box/text prompts, DOTA-OBB, Grounding-DINO (auto-gated),
    Prithvi multispectral overlays, DINOv3-SAT embeddings, mask-aware NMS,
    timings rollup, and response construction. ``chip3`` is the RGB array
    SAM3 consumes; ``chip6`` / ``chip2`` are the optional MSI/SAR raw
    arrays carried through to Prithvi and TerraMind respectively.
    """
    def mark(name: str, since: float) -> float:
        now = time.perf_counter()
        timings[name] = round((now - since) * 1000, 3)
        return now

    t0 = time.perf_counter()
    height, width = chip3.shape[:2]
    valid_mask = _decode_valid_mask(meta.get("valid_mask"), (height, width))
    prompt_boxes = meta.get("prompt_boxes")
    prompt_count = 0
    prompts: list[str] = []
    sam3_timings: dict[str, float] = {}
    layer_candidates: list[tuple[str, tuple[Any, Any, Any, Any]]] = []
    candidates_by_layer: dict[str, int] = {}
    if isinstance(prompt_boxes, list) and prompt_boxes:
        prompt_count = len(prompt_boxes)
        with _track("sam3_image"):
            sam3_candidates = await run_in_threadpool(
                sam3_runner.run_box_prompts, bundle, chip3, prompt_boxes, SAM3_BOX_THR,
            )
        layer_candidates.extend(_tag_candidates("sam3", sam3_candidates))
        candidates_by_layer["sam3"] = len(sam3_candidates)
    else:
        try:
            prompts = resolve_prompts(meta)
        except OntologyBackendUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prompt_count = len(prompts)
        with _track("sam3_image"):
            sam3_candidates = await run_in_threadpool(
                sam3_runner.run_text_prompts, bundle, chip3, prompts, SAM3_TEXT_THR, sam3_timings,
            )
        layer_candidates.extend(_tag_candidates("sam3", sam3_candidates))
        candidates_by_layer["sam3"] = len(sam3_candidates)
    for _k, _v in sam3_timings.items():
        timings[f"sam3_{_k}"] = _v
    t0 = mark("sam3_inference", t0)

    force_dota = bool(meta.get("force_dota_obb", False))
    dota_allowed = (
        force_dota
        or (not isinstance(prompt_boxes, list) and _prompts_relevant_to_dota(prompts))
    )
    if bundle.get("dota_obb") and (force_dota or _layer_active("dota_obb")) and dota_allowed:
        with _track("dota_obb"):
            dota_candidates = await run_in_threadpool(
                dota_obb.run, bundle["dota_obb"], chip3, dota_obb.DOTA_OBB_THRESHOLD,
            )
        layer_candidates.extend(_tag_candidates("dota_obb", dota_candidates))
        candidates_by_layer["dota_obb"] = len(dota_candidates)
    else:
        candidates_by_layer.setdefault("dota_obb", 0)

    gd_force = bool(meta.get("force_grounding_dino", False))
    gd_explicit = "grounding_dino" in _enabled
    gd_should_run, gd_gated_reason = grounding_dino_gate.should_run_grounding_dino(
        prompts, force=gd_force,
    )
    if (
        bundle.get("grounding_dino")
        and prompts
        and (gd_force or _layer_active("grounding_dino"))
        and gd_should_run
        and (gd_force or gd_explicit)
    ):
        with _track("grounding_dino"):
            gd_candidates = await run_in_threadpool(
                grounding_dino.run,
                bundle["grounding_dino"],
                chip3,
                prompts,
                grounding_dino.GROUNDING_DINO_THR,
            )
        layer_candidates.extend(_tag_candidates("grounding_dino", gd_candidates))
        candidates_by_layer["grounding_dino"] = len(gd_candidates)
    elif _layer_active("grounding_dino") and gd_gated_reason:
        logger.debug(
            "grounding_dino auto-gated: reason=%s n_prompts=%d", gd_gated_reason, len(prompts),
        )
    candidates_by_layer.setdefault("grounding_dino", 0)
    t0 = mark("specialists", t0)

    overlays: dict[str, np.ndarray] = {}
    if modality == "multispectral":
        if _layer_active("prithvi"):
            with _track("prithvi"):
                overlays = await run_in_threadpool(prithvi_heads.run_all, bundle.get("prithvi"), chip6, (height, width))
        else:
            overlays = {}
    t0 = mark("overlays", t0)

    detections = []
    embedding_ms = 0.0
    dinov3_calls = 0
    for source_layer, (mask, bbox_xyxy, score, label) in layer_candidates:
        det = fusion.candidate_to_detection(
            mask,
            bbox_xyxy,
            score,
            label,
            image_size=(width, height),
            modality=modality,
            valid_mask=valid_mask,
        )
        det["source_layer"] = source_layer
        if meta.get("geo"):
            det["geo"] = {**meta["geo"], "obb_map_crs": None, "obb_map_geojson": None}
        if SAM3_EMBED_DETECTIONS and _layer_active("dinov3_sat") and bundle.get("dinov3_sat"):
            emb_start = time.perf_counter()
            det["embedding"] = embedding.embed_crop(bundle.get("dinov3_sat"), chip3, bbox_xyxy)
            embedding_ms += (time.perf_counter() - emb_start) * 1000
            dinov3_calls += 1
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
    if dinov3_calls > 0:
        _record_metric("dinov3_sat", embedding_ms)
    timings["embedding"] = round(embedding_ms, 3)
    t0 = mark("postprocess", t0)
    _nms_agnostic = os.getenv("SAM3_NMS_AGNOSTIC", "1").strip().lower() in {"1", "true", "yes", "on"}
    _nms_soft = os.getenv("SAM3_NMS_SOFT", "0").strip().lower() in {"1", "true", "yes", "on"}
    pre_nms_count = len(detections)
    # Soft-NMS is an NMS-mode-only knob — WBF averages instead of decaying,
    # so the soft path is meaningless when SAM3_FUSION_MODE=wbf. When the
    # operator has explicitly asked for soft-NMS, honour them by forcing
    # the NMS path; otherwise dispatch via the env-selectable fuser.
    if _nms_soft:
        detections = fusion.mask_aware_nms(
            detections, iou=0.50, agnostic=_nms_agnostic, soft=True,
        )
    else:
        detections = fusion.fuse_detections(
            detections, image_w=width, image_h=height, agnostic=_nms_agnostic,
        )
    suppressed_by_nms = max(0, pre_nms_count - len(detections))
    mark("nms", t0)
    if _peak_dev is not None:
        try:
            import torch as _torch_peak2
            timings["peak_vram_mib"] = round(
                _torch_peak2.cuda.max_memory_allocated(_peak_dev) / (1024 * 1024), 1
            )
        except Exception:
            pass
    timings["total"] = round((time.perf_counter() - started) * 1000, 3)
    logger.info(
        "sam3_detect_timing modality=%s prompts=%s candidates=%s detections=%s queue_depth=%s timings_ms=%s",
        modality,
        prompt_count,
        len(layer_candidates),
        len(detections),
        queue_depth,
        timings,
    )
    debug_counts = {
        "prompt_count": prompt_count,
        "candidates_by_layer": candidates_by_layer,
        "suppressed_by_policy": 0,
        "suppressed_by_nms": suppressed_by_nms,
    }
    return {
        "status": "success",
        "modality": modality,
        "detections": detections,
        "debug_counts": debug_counts,
        "model_version": MODEL_VERSION,
        "model_versions": _version_snapshot(bundle),
        "timings_ms": timings,
        "queue_depth": queue_depth,
        "input_metadata": meta,
        "enabled_layers_unavailable": _unavailable,
        "grounding_dino_gated": gd_gated_reason,
    }


@app.post("/detect")
async def detect(image: UploadFile = File(...), metadata: str = Form("{}")):
    started = time.perf_counter()
    timings: dict[str, float] = {}
    queue_depth = _enter_request()

    # Per-request peak VRAM tracking. Reset peaks now so timings["peak_vram_mib"]
    # below reflects only this request's allocations, not whatever the worker
    # accumulated across prior chips. Two PyTorch calls — negligible cost.
    _peak_dev: int | None = None
    try:
        import torch as _torch_peak
        if _torch_peak.cuda.is_available():
            _peak_dev = _torch_peak.cuda.current_device()
            _torch_peak.cuda.reset_peak_memory_stats(_peak_dev)
    except Exception:
        _peak_dev = None

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
    _reject_image_yoloe_layers(_enabled)
    _layer_active = (lambda layer: (layer in _enabled)) if _enabled else (lambda _: True)

    # Surface layers that were requested but are not loaded in the bundle.
    # We compute this after bundle selection so we can check bundle keys.
    # Note: bundle is resolved below; this list is populated after _next_bundle().

    try:
        raw = await image.read()
        t0 = mark("read_upload", started)
        modality = str(meta.get("modality") or "rgb").lower()
        # Auto-heal: if no profile is loaded (or the wrong one is loaded),
        # swap to the modality's imagery profile before this request runs. On
        # tight cards this loads only the models that modality needs; on hot
        # cards the resident "imagery"/"all" superset is reused without reload.
        _ensure_profile(_profile_for_modality(modality))
        bundle = _next_bundle()
        t0 = mark("model_queue", t0)

        # Guard the profile-swap race: a concurrent FMV /load can swap the pool
        # to a bundle without sam3_image between _ensure_profile() above and here.
        # Fail with an honest 503 (the worker treats it as retryable backpressure)
        # instead of dereferencing None deep in run_text_prompts/run_box_prompts.
        if bundle.get("sam3_image") is None:
            raise HTTPException(
                status_code=503,
                detail=f"sam3_image not resident (profile={_current_profile}); retry",
            )

        # Compute unavailable layers now that we have the bundle.
        _unavailable = [l for l in _enabled if l not in ("sam3",) and not bundle.get(l)]

        try:
            if modality == "multispectral":
                chip6 = await run_in_threadpool(multispectral.decode_hls6, raw)
                chip3 = multispectral.hls_to_rgb_preview(chip6)
                chip2 = None
            elif modality == "sar":
                chip2 = await run_in_threadpool(sar.decode_s1grd, raw)
                with _track("terramind"):
                    chip3 = await run_in_threadpool(terramind.s1_to_s2_rgb, bundle.get("terramind"), chip2, chip2.shape[-2:])
                chip6 = None
            else:
                modality = "rgb"
                chip3 = await run_in_threadpool(_decode_rgb, raw)
                chip6 = chip2 = None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to decode {modality} chip: {exc}") from exc
        t0 = mark("decode", t0)

        return await _detect_pipeline(
            bundle, meta, modality, chip3, chip6, chip2,
            started, timings, queue_depth, _peak_dev,
            _enabled, _layer_active, _unavailable,
        )
    finally:
        _leave_request()


@app.post("/embed")
async def embed_endpoint(image: UploadFile = File(...)):
    """Compute a DINOv3-SAT 1024-d embedding of the supplied image.

    Lightweight alternative to /detect for bake scripts and analyst lookup
    that only need the embedding, not the full detection pipeline. Auto-loads
    the imagery profile on first call.

    NOTE: independent of ``SAM3_EMBED_DETECTIONS``. That flag controls
    whether /detect embeds *its own* detections inline; /embed is the
    standalone path used by the reference-DB baker per
    docs/decisions/why-standalone-embed-endpoint.md and must always work.

    Returns:
        {"model": str, "dim": 1024, "fp16_b64": str}
    """
    # Any imagery profile carries dinov3_sat; imagery_rgb is the lightest.
    _ensure_profile("imagery_rgb")
    bundle = _next_bundle().get("dinov3_sat")
    if bundle is None:
        raise HTTPException(status_code=503, detail="dinov3_sat layer not loaded")
    try:
        img_bytes = await image.read()
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"could not decode image: {e}")
    result = embedding.dinov3_pool(bundle, pil_img)
    if not result.get("fp16_b64"):
        raise HTTPException(status_code=500, detail="embedding computation returned empty result")
    logger.info("/embed ok dim=%s bytes=%d", result.get("dim"), len(img_bytes))
    return result


@app.post("/detect_raw")
async def detect_raw(request: "Request"):  # type: ignore[name-defined]
    """Raw-binary chip endpoint that skips PIL/PNG decoding.

    Body: ``application/octet-stream`` containing a C-contiguous numpy
    buffer. Headers describe the layout so the server can `np.frombuffer`
    directly into the model input array — no PIL, no PNG, no multipart.

    Required headers:
        X-Chip-Modality   : "rgb" (Phase 4 covers RGB only; MSI/SAR stay on /detect)
        X-Chip-Shape      : "H,W,C" e.g. "1008,1008,3"
        X-Chip-Dtype      : "uint8" (Phase 4 supports uint8 only)
        X-Chip-Meta-B64   : base64-encoded JSON; same body as /detect's
                            ``metadata`` form field (modality, geo, valid_mask, …)

    The downstream model pipeline is shared with /detect via
    ``_detect_pipeline``, so detection counts and confidences are bit-for-
    bit identical to the multipart path for the same input pixels.
    """
    started = time.perf_counter()
    timings: dict[str, float] = {}
    queue_depth = _enter_request()
    _peak_dev: int | None = None
    try:
        import torch as _torch_peak
        if _torch_peak.cuda.is_available():
            _peak_dev = _torch_peak.cuda.current_device()
            _torch_peak.cuda.reset_peak_memory_stats(_peak_dev)
    except Exception:
        _peak_dev = None

    try:
        modality_hdr = (request.headers.get("X-Chip-Modality") or "rgb").lower()
        if modality_hdr != "rgb":
            raise HTTPException(
                status_code=400,
                detail=(
                    f"/detect_raw only supports modality=rgb in this version; "
                    f"got {modality_hdr!r}. Send multispectral/SAR chips to /detect."
                ),
            )
        shape_hdr = request.headers.get("X-Chip-Shape") or ""
        try:
            shape_tuple = tuple(int(part) for part in shape_hdr.split(",") if part.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid X-Chip-Shape: {shape_hdr!r}")
        if len(shape_tuple) != 3 or shape_tuple[2] != 3:
            raise HTTPException(
                status_code=400,
                detail=f"X-Chip-Shape must be H,W,3 for rgb; got {shape_tuple}",
            )
        dtype_hdr = (request.headers.get("X-Chip-Dtype") or "uint8").lower()
        if dtype_hdr != "uint8":
            raise HTTPException(status_code=400, detail=f"unsupported dtype {dtype_hdr!r}")

        meta_b64 = request.headers.get("X-Chip-Meta-B64") or ""
        if meta_b64:
            try:
                meta = json.loads(base64.b64decode(meta_b64).decode("utf-8"))
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Invalid X-Chip-Meta-B64: {exc}") from exc
        else:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}

        # Layer-toggle parsing mirrors /detect so behaviour is identical.
        _raw_enabled = meta.get("enabled_layers")
        _enabled = set(_raw_enabled) if isinstance(_raw_enabled, list) else set()
        _reject_image_yoloe_layers(_enabled)
        _layer_active = (lambda layer: (layer in _enabled)) if _enabled else (lambda _: True)

        body = await request.body()
        timings["read_upload"] = round((time.perf_counter() - started) * 1000, 3)
        t0 = time.perf_counter()

        # /detect_raw is the RGB fast path (raw uint8 chips); route to the RGB
        # modality profile. MSI/SAR use the multipart /detect endpoint.
        _ensure_profile("imagery_rgb")
        bundle = _next_bundle()
        timings["model_queue"] = round((time.perf_counter() - t0) * 1000, 3)
        t0 = time.perf_counter()

        _unavailable = [l for l in _enabled if l not in ("sam3",) and not bundle.get(l)]

        expected_bytes = int(np.prod(shape_tuple))  # uint8 → 1 byte per element
        if len(body) != expected_bytes:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"body length {len(body)} != expected {expected_bytes} for shape {shape_tuple} uint8"
                ),
            )
        try:
            # `np.frombuffer` shares memory with the bytes object, which is
            # immutable. We copy so downstream callers that expect a writable
            # array (PIL inside embedding.embed_crop, cv2 ops) don't trip the
            # writable-buffer assertion.
            chip3 = np.frombuffer(body, dtype=np.uint8).reshape(shape_tuple).copy()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Unable to reshape raw chip: {exc}") from exc
        chip6 = None
        chip2 = None
        timings["decode"] = round((time.perf_counter() - t0) * 1000, 3)

        return await _detect_pipeline(
            bundle, meta, "rgb", chip3, chip6, chip2,
            started, timings, queue_depth, _peak_dev,
            _enabled, _layer_active, _unavailable,
        )
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

        prompt_mode = (meta.get("prompt_mode") or "pcs").strip().lower()
        if prompt_mode not in {"pcs", "yoloe"}:
            raise HTTPException(status_code=400, detail=f"unknown prompt_mode {prompt_mode!r}")
        if prompt_mode == "yoloe":
            # YOLOE path: explicit empty text_prompts list → prompt-free
            # (-pf checkpoint). Anything else → resolve from DB ontology
            # like PCS and run -seg. Skipping resolve_prompts for the empty
            # case avoids the 400/503 it raises when the backend has no
            # default prompts.
            explicit = meta.get("text_prompts")
            if isinstance(explicit, list) and len(explicit) == 0:
                prompts = []
            else:
                try:
                    prompts = resolve_prompts({**meta, "modality": "fmv"})
                except OntologyBackendUnavailable as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            try:
                prompts = resolve_prompts({**meta, "modality": "fmv"})
            except OntologyBackendUnavailable as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            # SAM3 video tracking is single-prompt-per-session: the upstream
            # multiplex predictor `add_prompt` unconditionally resets the
            # inference state, so N concepts require N separate sessions.
            # The worker's (window × prompt) ThreadPoolExecutor in
            # backend/worker.py already fans out one prompt per request;
            # reject anything else loudly instead of silently dropping
            # prompts beyond the first.
            if len(prompts) > 1:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"SAM3 video tracking is single-prompt-per-session "
                        f"(received {len(prompts)} resolved prompts: {prompts!r}). "
                        f"Send one /detect_video request per prompt."
                    ),
                )

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

        if prompt_mode == "yoloe":
            yoloe_bundle = reserved.get("yoloe") or {}
            if yoloe_bundle.get("pf") is None and yoloe_bundle.get("seg") is None:
                # Outer `except` runs the bundle.lock.release() + _leave_request()
                # + cleanup_path cleanup on the way out.
                raise HTTPException(
                    status_code=503,
                    detail="YOLOE not loaded (set SAM3_LOAD_YOLOE=1 and reload the fmv profile)",
                )
            _yoloe_prompts = list(prompts) if prompts else None
            # Record latency as wall-clock per detect_video session — slug is
            # yoloe_seg when prompts steer the model, else yoloe_pf (the
            # prompt-free general detector).
            _yoloe_slug = "yoloe_seg" if _yoloe_prompts else "yoloe_pf"
            def stream():
                _stream_t0 = time.perf_counter()
                _stream_err = False
                try:
                    yield from (
                        line + "\n"
                        for line in sam3_runner.run_video_yoloe(
                            reserved,
                            video_path,
                            _yoloe_prompts,
                            frame_stride=frame_stride,
                            start_frame=start_frame,
                            end_frame=end_frame,
                            max_frames=max_frames,
                            score_threshold=SAM3_BOX_THR,
                        )
                    )
                except BaseException:
                    _stream_err = True
                    raise
                finally:
                    _record_metric(_yoloe_slug, (time.perf_counter() - _stream_t0) * 1000, error=_stream_err)
                    if cleanup_path is not None:
                        cleanup_path.unlink(missing_ok=True)
                    reserved["lock"].release()
                    _leave_request()
        else:
            # prompts is guaranteed len == 1 (or 0 → run_video no-ops) by
            # the multi-prompt 400 check above.
            _video_prompt = prompts[0] if prompts else ""
            def stream():
                _stream_t0 = time.perf_counter()
                _stream_err = False
                try:
                    yield from (
                        line + "\n"
                        for line in sam3_runner.run_video(
                            reserved,
                            video_path,
                            _video_prompt,
                            frame_stride=frame_stride,
                            start_frame=start_frame,
                            end_frame=end_frame,
                            max_frames=max_frames,
                            dinov3=None,  # DINOV3_LVD removed (NaN on real video crops)
                            score_threshold=SAM3_TEXT_THR,
                        )
                    )
                except BaseException:
                    _stream_err = True
                    raise
                finally:
                    _record_metric("sam3_video", (time.perf_counter() - _stream_t0) * 1000, error=_stream_err)
                    if cleanup_path is not None:
                        cleanup_path.unlink(missing_ok=True)
                    reserved["lock"].release()
                    _leave_request()

        logger.info(
            "sam3_detect_video_start mode=%s prompts=%s queue_depth=%s path=%s device=%s",
            prompt_mode,
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
        _req_log.append(time.time())
        return _active_requests


def _leave_request() -> None:
    global _active_requests
    with _active_lock:
        _active_requests = max(0, _active_requests - 1)


def _bundle_components(bundle: dict[str, Any]) -> dict[str, Any]:
    prithvi_bundle = bundle.get("prithvi") or {}
    yoloe_bundle = bundle.get("yoloe") or {}
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
        "yoloe": bool(yoloe_bundle.get("pf") or yoloe_bundle.get("seg")),
        "yoloe_pf": yoloe_bundle.get("pf") is not None,
        "yoloe_seg": yoloe_bundle.get("seg") is not None,
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


# ============================================================================
# Training: fine-tune YOLOE on a user-supplied YOLO-format dataset. Runs in a
# background thread and exposes status via GET /train/{job_id}. Output weights
# land at <MODEL_OUT_DIR>/<job_id>/best.pt.
# ============================================================================


_TRAIN_LOCK = threading.Lock()
_TRAIN_JOBS: dict[str, dict[str, Any]] = {}
_TRAIN_BASE_WEIGHTS = os.getenv("YOLOE_BASE_WEIGHTS", "/data/weights/yoloe-11l-seg.pt")
_TRAIN_OUT_ROOT = Path(os.getenv("MODEL_OUT_DIR", "/data/models"))


def _train_worker(job_id: str) -> None:
    """Run an ultralytics YOLO.train() in this thread; mutates _TRAIN_JOBS in place."""
    job = _TRAIN_JOBS[job_id]
    try:
        from ultralytics import YOLO  # local import — large package
    except Exception as exc:  # noqa: BLE001
        with _TRAIN_LOCK:
            job["status"] = "failed"
            job["error"] = f"ultralytics import failed: {exc}"
            job["finished_at"] = time.time()
        return

    base_weights = job.get("base_weights") or _TRAIN_BASE_WEIGHTS
    if not Path(base_weights).exists():
        with _TRAIN_LOCK:
            job["status"] = "failed"
            job["error"] = f"base weights not found: {base_weights}"
            job["finished_at"] = time.time()
        return

    out_dir = _TRAIN_OUT_ROOT / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        model = YOLO(base_weights)
        results = model.train(
            data=job["dataset_path"],
            epochs=int(job["epochs"]),
            project=str(out_dir.parent),
            name=out_dir.name,
            exist_ok=True,
            verbose=False,
        )
        weights_src = Path(results.save_dir) / "weights" / "best.pt"
        weights_dst = out_dir / "best.pt"
        if weights_src.exists() and weights_src != weights_dst:
            weights_dst.write_bytes(weights_src.read_bytes())
        metrics = {}
        if hasattr(results, "results_dict"):
            metrics = {k: float(v) for k, v in results.results_dict.items() if isinstance(v, (int, float))}
        with _TRAIN_LOCK:
            job["status"] = "done"
            job["weights_path"] = str(weights_dst)
            job["metrics"] = metrics
            job["finished_at"] = time.time()
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("inference-sam3").exception("training job %s failed", job_id)
        with _TRAIN_LOCK:
            job["status"] = "failed"
            job["error"] = str(exc)[:2000]
            job["finished_at"] = time.time()


@app.post("/train")
def start_training(payload: dict = None) -> dict:  # type: ignore[assignment]
    """Spawn an ultralytics YOLO fine-tune in the background.

    Body: ``{"name": str, "dataset_path": str, "epochs": int, "base_weights": str?}``.
    Returns ``{"job_id", "status": "running"}`` immediately.
    """
    payload = payload or {}
    dataset_path = str(payload.get("dataset_path") or "").strip()
    if not dataset_path or not Path(dataset_path).exists():
        raise HTTPException(status_code=400, detail=f"dataset_path missing or not found: {dataset_path}")
    try:
        epochs = int(payload.get("epochs") or 1)
    except (TypeError, ValueError):
        epochs = 1
    if epochs <= 0:
        raise HTTPException(status_code=400, detail="epochs must be > 0")
    name = str(payload.get("name") or "yoloe-finetune")[:120]

    job_id = f"train-{int(time.time())}-{name.replace('/', '_')}"
    job = {
        "job_id": job_id,
        "name": name,
        "dataset_path": dataset_path,
        "epochs": epochs,
        "base_weights": payload.get("base_weights") or _TRAIN_BASE_WEIGHTS,
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "metrics": {},
    }
    with _TRAIN_LOCK:
        _TRAIN_JOBS[job_id] = job
    thread = threading.Thread(target=_train_worker, args=(job_id,), name=f"train-{job_id}", daemon=True)
    thread.start()
    return {"job_id": job_id, "status": "running", "name": name}


@app.get("/train/{job_id}")
def training_status(job_id: str) -> dict:
    with _TRAIN_LOCK:
        job = _TRAIN_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="training job not found")
    return dict(job)
