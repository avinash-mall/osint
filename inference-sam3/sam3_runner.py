from __future__ import annotations

import json
import logging
import os
import itertools
from typing import Any, Iterable

import numpy as np
from PIL import Image


logger = logging.getLogger("sam3_runner")


SAM3_IMAGE_MODEL_ID = os.getenv("SAM3_IMAGE_MODEL_ID", "facebook/sam3")
# "official" → gated facebook/sam3 (requires HF_TOKEN). "mirror" → non-gated
# 1038lab/sam3 mirror; tokenizer + processor configs still come from the
# upstream sam3 Python package shipped in the container, so the mirror is a
# pure weight substitution, not a full repo fork.
SAM3_WEIGHTS_SOURCE = os.getenv("SAM3_WEIGHTS_SOURCE", "official").strip().lower()
SAM3_MIRROR_REPO_ID = os.getenv("SAM3_MIRROR_REPO_ID", "1038lab/sam3")
SAM3_MIRROR_FILENAME = os.getenv("SAM3_MIRROR_FILENAME", "sam3.safetensors")
SAM3_USE_MULTIPLEX = os.getenv("SAM3_USE_MULTIPLEX", "1").strip().lower() in {"1", "true", "yes", "on"}
SAM3_COMPILE_IMAGE = os.getenv("SAM3_COMPILE_IMAGE", "0").strip().lower() in {"1", "true", "yes", "on"}


def _default_compile_video() -> str:
    """torch.compile pays off on Ampere+ where bf16/TF32 + Triton fusion is
    well-supported; on older GPUs or CPU it costs cold-start without the
    speedup. Detect sm_80 (A100) and newer at import time."""
    try:
        import torch
        if not torch.cuda.is_available():
            return "0"
        major, _ = torch.cuda.get_device_capability(0)
        return "1" if major >= 8 else "0"
    except Exception:
        return "0"


SAM3_COMPILE_VIDEO = os.getenv("SAM3_COMPILE_VIDEO", _default_compile_video()).strip().lower() in {"1", "true", "yes", "on"}
SAM3_WARM_UP_VIDEO = os.getenv("SAM3_WARM_UP_VIDEO", "1").strip().lower() in {"1", "true", "yes", "on"}
SAM3_BATCHED_TEXT = os.getenv("SAM3_BATCHED_TEXT", "1").strip().lower() in {"1", "true", "yes", "on"}
SAM3_BATCHED_TEXT_CHUNK_SIZE = max(1, int(os.getenv("SAM3_BATCHED_TEXT_CHUNK_SIZE", "8")))
# Category-level presence gate (SegEarth-OV-3 idea): drop the entire prompt if
# its best candidate is below this threshold. SAM 3 internally multiplies
# per-mask scores by the presence-token probability before thresholding, so
# the maximum candidate score for a prompt is a proxy for the per-prompt
# presence signal. A high `category_threshold` value forces the model to
# return zero detections for prompts whose concept is absent from the scene
# (which is what produced the false-positive "Oil Refinery Complex" hits in
# central Vienna). Setting this to 0.0 disables the gate. The same gate is
# applied to video sessions: emissions are buffered through the hotstart
# window (15 frames) and suppressed entirely if the best-seen track score
# never exceeds the threshold.
SAM3_CATEGORY_THR = float(os.getenv("SAM3_CATEGORY_THRESHOLD", "0.40"))
# Number of frames the SAM3 multiplex tracker buffers internally before its
# hotstart unmatched/duplicate suppression activates. Mirror this on the
# emit-side so the video category gate has at least this many scores to
# evaluate before deciding whether the prompt's concept is present in the
# scene. Matches the upstream Sam3VideoConfig default.
SAM3_HOTSTART_DELAY_FRAMES = max(1, int(os.getenv("SAM3_HOTSTART_DELAY_FRAMES", "15")))
PROMPT_TEMPLATE = os.getenv("SAM3_PROMPT_TEMPLATE", "{label}")
_QUERY_IDS = itertools.count(1)

# ---------------------------------------------------------------------------
# AMG (Automatic Mask Generation) — promptless dense detection.
# ---------------------------------------------------------------------------
# The video path normally requires a text concept (`run_video`). AMG provides
# a class-agnostic alternative: a dense n×n grid of geometric prompts is fed
# through the SAM 3 image model on a "seed" frame; mask-IoU NMS distills
# the (up to n²) candidates into a track set; intermediate frames either
# reuse the seed-frame masks (cheap) or re-seed (`reseed_every_n_frames`).
# Per-profile defaults (grid size, reseed cadence, master enable) come from
# scripts/gpu_profiles.py via configure_host.py.
SAM3_AMG_GRID_SIZE = max(4, int(os.getenv("SAM3_AMG_GRID_SIZE", "32")))
SAM3_AMG_RESEED_FRAMES = max(1, int(os.getenv("SAM3_AMG_RESEED_FRAMES", "4")))
SAM3_AMG_ENABLED = os.getenv("SAM3_AMG_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
# Mask-IoU NMS threshold for AMG candidate suppression. 0.7 follows the SAM
# paper. Lower = more aggressive merging; higher = more candidates kept.
SAM3_AMG_NMS_IOU = float(os.getenv("SAM3_AMG_NMS_IOU", "0.5"))
# Tiny-box prompt half-width (normalized). The geometric-prompt API takes
# a (cx, cy, w, h) box; "point" prompts are emulated by a near-degenerate
# box centered at each grid point.
SAM3_AMG_POINT_BOX_NORM = float(os.getenv("SAM3_AMG_POINT_BOX_NORM", "0.02"))
# Track-association threshold for cross-frame Hungarian assignment.
# Pairs below this mask-IoU are treated as new tracks.
SAM3_AMG_TRACK_IOU_MIN = float(os.getenv("SAM3_AMG_TRACK_IOU_MIN", "0.30"))
# Frames a lost track is kept alive (matches the ByteTrack `track_buffer`).
SAM3_AMG_TRACK_BUFFER = max(1, int(os.getenv("SAM3_AMG_TRACK_BUFFER", "12")))

# ---------------------------------------------------------------------------
# Phase 3 AMG quality knobs (SAM-2-style filters + Grounding-DINO labelling).
# All are evaluated per-mask before NMS; defaults pick a recall/precision
# compromise that empirically cuts the ~250-cands-per-256-grid pass-through
# rate to ~30-80 cands of substantially higher quality.
# ---------------------------------------------------------------------------
SAM3_AMG_PRED_IOU_THRESH = float(os.getenv("SAM3_AMG_PRED_IOU_THRESH", "0.50"))
SAM3_AMG_MIN_AREA_PX = max(1, int(os.getenv("SAM3_AMG_MIN_AREA_PX", "200")))
SAM3_AMG_MAX_AREA_FRAC = float(os.getenv("SAM3_AMG_MAX_AREA_FRAC", "0.50"))
SAM3_AMG_EDGE_FRAC_MAX = float(os.getenv("SAM3_AMG_EDGE_FRAC_MAX", "0.80"))
# Stability score (SAM-2 style). 0.0 disables. > 0 enables an extra
# add_geometric_prompt call with the box shifted by ±SAM3_AMG_STABILITY_DELTA
# in normalized coords; rejects masks whose perturbed mask-IoU falls below
# the threshold. Costs ~2× the per-grid-point compute when enabled.
SAM3_AMG_STABILITY_THRESH = float(os.getenv("SAM3_AMG_STABILITY_THRESH", "0.0"))
SAM3_AMG_STABILITY_DELTA = float(os.getenv("SAM3_AMG_STABILITY_DELTA", "0.01"))
# Masklet confirmation — track must be observed in this many consecutive
# emitted frames before its detections are flushed. 1 = no buffering
# (Phase 2 behaviour); 2 = drop single-frame transient FPs.
SAM3_AMG_MIN_CONSECUTIVE_FRAMES = max(1, int(os.getenv("SAM3_AMG_MIN_CONSECUTIVE_FRAMES", "2")))
# Grounding-DINO label assignment on the seed frame.
SAM3_AMG_LABEL_VIA_GD = os.getenv("SAM3_AMG_LABEL_VIA_GD", "1").strip().lower() in {"1", "true", "yes", "on"}
SAM3_AMG_LABEL_PROMPTS = os.getenv(
    "SAM3_AMG_LABEL_PROMPTS",
    "vehicle,person,building,road,vegetation,water,aircraft,vessel,"
    "animal,equipment,structure,tower,pole,sign,debris,container",
)
# Phase 6: Two-tier GD score floor driven by the admin GEOINT ontology.
#   * SAM3_AMG_LABEL_GD_THRESH — floor for labels NOT in the optical
#     ontology. Raised from 0.25 → 0.45 so GD-tiny's "pole"/"tower"/"sign"
#     hallucinations need strong confidence to escape the filter.
#   * SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY — floor for labels that ARE in
#     the optical ontology (vehicle, person, building, vegetation, …).
#     Default 0.20 recovers recall on the core classes the user actually
#     cares about. Operators widen the ontology via OntologyAdmin.tsx →
#     more FMV recall, automatically.
# GD is invoked at the LOWER of the two thresholds so it returns enough
# candidates; per-class filtering in _filter_by_class_threshold then
# applies the correct floor. Backend-unavailable → ontology set is empty
# → every label uses the default (high) floor.
SAM3_AMG_LABEL_GD_THRESH = float(os.getenv("SAM3_AMG_LABEL_GD_THRESH", "0.45"))
SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY = float(os.getenv("SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY", "0.20"))
# Tighter IoU threshold than before — but the centroid-containment fallback
# (≥ 0.6 of candidate inside the GD box) means tiny AMG masks sitting inside
# broad GD "vegetation"/"building" boxes still match.
SAM3_AMG_LABEL_IOU_MIN = float(os.getenv("SAM3_AMG_LABEL_IOU_MIN", "0.20"))

# Phase 5: drone-HUD overlay auto-detection. Forward-looking FMV clips often
# have burnt-in telemetry overlays (LAT/LON/HDG numerics, scale bars, dial
# readouts) that GD misclassifies as Sign / Pole / Vegetation with high
# confidence. The HUD pixels are STATIC across frames, so inter-frame std
# pinpoints them with no per-clip configuration needed. We then drop any
# AMG detection whose bbox sits ≥ SAM3_AMG_HUD_OVERLAP_MAX inside HUD.
SAM3_AMG_HUD_MASK_ENABLED = os.getenv("SAM3_AMG_HUD_MASK_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
SAM3_AMG_HUD_STD_THRESH = float(os.getenv("SAM3_AMG_HUD_STD_THRESH", "3.0"))
SAM3_AMG_HUD_SAMPLES = max(2, int(os.getenv("SAM3_AMG_HUD_SAMPLES", "5")))
SAM3_AMG_HUD_OVERLAP_MAX = float(os.getenv("SAM3_AMG_HUD_OVERLAP_MAX", "0.5"))

# Phase 4: detector selection. "gd" = Grounding-DINO-first (default; fast,
# requires GD model + label vocab to cover the scene). "grid" = dense N×N
# point-grid AMG (Phase 3 behaviour; slow but vocab-free). GD-first is ~7×
# faster per seed frame because it runs ~20 add_geometric_prompt calls
# instead of 256, with the same Phase 3 quality filters applied to the
# refined masks. Auto-falls-back to grid when SAM3_AMG_LABEL_VIA_GD=0
# (vocab-free intent — no GD vocab → no GD detector).
SAM3_AMG_DETECTOR = os.getenv("SAM3_AMG_DETECTOR", "gd").strip().lower()
if SAM3_AMG_DETECTOR not in {"gd", "grid"}:
    SAM3_AMG_DETECTOR = "gd"
if not SAM3_AMG_LABEL_VIA_GD:
    SAM3_AMG_DETECTOR = "grid"

_AMG_AVAILABLE: bool | None = None  # set by probe_amg() on first call
# Hybrid AMG (Phase 2): image AMG on seed frame → batched box-prompt adds to
# a single video session → propagate. ~5× faster than per-frame AMG. The probe
# confirms `predictor.handle_request` accepts `add_prompt` with
# `bounding_boxes=[...]` and `obj_id=...` without triggering reset_state on
# subsequent calls. If the upstream API shifts, we fall back to the per-frame
# `run_video_amg` path (slower but functionally equivalent).
_AMG_SEEDED_AVAILABLE: bool | None = None


def _patch_pkg_resources_py312() -> None:
    """Keep old pkg_resources importable under Python 3.12.

    Upstream SAM3 imports pkg_resources. Some CUDA/Ubuntu image combinations
    still expose an apt-era pkg_resources on sys.path; that version references
    pkgutil.ImpImporter, which Python 3.12 removed. The finder is only used for
    legacy import hooks, so a placeholder class is enough to let pkg_resources
    finish importing.
    """
    import pkgutil

    if not hasattr(pkgutil, "ImpImporter"):
        pkgutil.ImpImporter = type("ImpImporter", (), {})  # type: ignore[attr-defined]
    if not hasattr(pkgutil, "ImpLoader"):
        pkgutil.ImpLoader = type("ImpLoader", (), {})  # type: ignore[attr-defined]


def _cuda_unsupported_arch_policy() -> str:
    policy = os.getenv("CUDA_UNSUPPORTED_ARCH_POLICY", "cpu").strip().lower()
    return policy if policy in {"cpu", "cuda"} else "cpu"


def _auto_cuda_devices(torch_module: Any) -> list[str]:
    supported_arches = set(torch_module.cuda.get_arch_list())
    unsupported: list[str] = []
    devices: list[str] = []
    for index in range(torch_module.cuda.device_count()):
        capability = torch_module.cuda.get_device_capability(index)
        device_arch = f"sm_{capability[0]}{capability[1]}"
        device_name = torch_module.cuda.get_device_name(index)
        if not supported_arches or device_arch in supported_arches:
            devices.append(f"cuda:{index}")
        else:
            unsupported.append(f"cuda:{index} {device_name} {device_arch}")
    if devices:
        return devices
    if unsupported:
        message = (
            "[INFERENCE-SAM3] No visible CUDA device has an arch in the torch build "
            f"arch list ({sorted(supported_arches)}); unsupported devices: {unsupported}"
        )
        if _cuda_unsupported_arch_policy() == "cuda":
            print(f"{message}; forcing CUDA")
            return [f"cuda:{index}" for index in range(torch_module.cuda.device_count())]
        print(f"{message}; falling back to CPU")
    return []


def normalize_device_list(value: str) -> list[str]:
    devices: list[str] = []
    for item in value.split(","):
        device = item.strip()
        if device:
            devices.append(f"cuda:{device}" if device.isdigit() else device)
    return devices or ["cpu"]


def resolve_devices(value: str) -> list[str]:
    requested = (value or "auto").strip().lower()
    if requested and requested != "auto":
        return normalize_device_list(requested)
    try:
        import torch

        if torch.cuda.is_available():
            devices = _auto_cuda_devices(torch)
            if devices:
                return devices
    except Exception:
        pass
    return ["cpu"]


def build_image(device: str) -> dict[str, Any]:
    _disable_flash_sdp_for_fp32_text_encoder()
    return _build_image_impl(device)


def _build_image_impl(device: str) -> dict[str, Any]:
    """Load the SAM3 image model via the native upstream API.

    SAM 3.1 ships only as a multiplex video checkpoint
    (``facebook/sam3.1`` → ``sam3.1_multiplex.pt``), so the standalone image
    model stays on ``facebook/sam3``. Image grounding goes through the native
    ``sam3.model.sam3_image_processor.Sam3Processor`` whose state caches
    backbone features so per-prompt cost is encoder-free after the first call.
    """
    _patch_pkg_resources_py312()
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    checkpoint_path = None
    load_from_hf = True
    if SAM3_WEIGHTS_SOURCE == "mirror":
        checkpoint_path = _resolve_mirror_checkpoint()
        load_from_hf = False

    model = build_sam3_image_model(
        device=device,
        compile=SAM3_COMPILE_IMAGE,
        checkpoint_path=checkpoint_path,
        load_from_HF=load_from_hf,
    ).to(device).eval()
    return {"model": model, "processor": Sam3Processor(model, device=device)}


def _resolve_mirror_checkpoint() -> str:
    """Download (or reuse cached) `sam3.safetensors` from the 1038lab mirror,
    convert to a torch-compatible state-dict file the upstream loader can read.

    The upstream `_load_checkpoint` calls `torch.load`, so we need a `.pt`. We
    convert the safetensors file once and cache the result alongside it.
    """
    from huggingface_hub import hf_hub_download

    safetensors_path = hf_hub_download(
        repo_id=SAM3_MIRROR_REPO_ID,
        filename=SAM3_MIRROR_FILENAME,
    )
    pt_path = safetensors_path + ".pt"
    if os.path.exists(pt_path) and os.path.getmtime(pt_path) >= os.path.getmtime(safetensors_path):
        return pt_path

    import torch
    try:
        from safetensors.torch import load_file as load_safetensors
    except ImportError as exc:
        raise RuntimeError(
            "safetensors package required for SAM3_WEIGHTS_SOURCE=mirror"
        ) from exc

    state = load_safetensors(safetensors_path)
    # Upstream `_load_checkpoint` looks for keys containing "detector" /
    # "tracker"; the 1038lab mirror preserves Meta's original key namespace,
    # so we just round-trip through torch.save without renaming.
    torch.save({"model": state}, pt_path)
    return pt_path


def _device_context(device: str):
    """`torch.cuda.device(device)` if device is CUDA, else a no-op context.

    Critical for multi-GPU: the upstream `build_sam3_multiplex_video_predictor`
    ends with `demo_model.cuda()` which uses `torch.cuda.current_device()` —
    without wrapping the build in this context, every replica lands on
    cuda:0 regardless of the `device` argument, collapsing a 4-GPU pool
    onto one GPU.
    """
    from contextlib import nullcontext
    if not device.startswith("cuda"):
        return nullcontext()
    import torch
    return torch.cuda.device(torch.device(device))


def build_video(device: str):
    _patch_pkg_resources_py312()
    _install_flash_attn_fallback()
    _disable_flash_sdp_for_fp32_text_encoder()
    from sam3.model_builder import build_sam3_multiplex_video_predictor, build_sam3_video_predictor

    if SAM3_USE_MULTIPLEX:
        # Multiplex needs ≈14 GiB of weights + ≈2-3 GiB of session
        # activations per /detect_video; if the GPU total is too small the
        # weights load successfully but the *first* inference OOMs. Refuse
        # multiplex early when there isn't enough headroom so we can fall
        # back cleanly to the base predictor. Both thresholds are env-
        # driven (`SAM3_MULTIPLEX_MIN_VRAM_MIB` total, default 20480 MiB;
        # `SAM3_MULTIPLEX_MIN_FREE_VRAM_MIB` free, default 14336 MiB) so
        # configure_host.py / operators can dial them per hardware class.
        min_total_mib = int(os.getenv("SAM3_MULTIPLEX_MIN_VRAM_MIB", "20480"))
        min_free_mib = int(os.getenv("SAM3_MULTIPLEX_MIN_FREE_VRAM_MIB", "14336"))
        try:
            import torch
            if torch.cuda.is_available():
                # Query the device this predictor is being built for, not
                # whichever cuda happens to be current — matters on
                # heterogenous multi-GPU hosts.
                free, total = torch.cuda.mem_get_info(torch.device(device))
                free_mib = free // (1024 * 1024)
                total_mib = total // (1024 * 1024)
                if total_mib < min_total_mib:
                    logger.warning(
                        "GPU total VRAM is %d MiB (< %d MiB required for SAM3.1 multiplex inference); "
                        "falling back to non-multiplex base predictor",
                        total_mib, min_total_mib,
                    )
                    with _device_context(device):
                        predictor = build_sam3_video_predictor()
                    predictor._sentinel_device = device
                    return predictor
                if free_mib < min_free_mib:
                    logger.warning(
                        "GPU free VRAM is %d MiB (< %d MiB headroom needed for multiplex weights); "
                        "falling back to non-multiplex base predictor",
                        free_mib, min_free_mib,
                    )
                    with _device_context(device):
                        predictor = build_sam3_video_predictor()
                    predictor._sentinel_device = device
                    return predictor
        except Exception as exc:
            logger.warning(
                "multiplex VRAM preflight failed (%s); will still attempt load",
                exc,
            )
        try:
            with _device_context(device):
                predictor = build_sam3_multiplex_video_predictor(
                    compile=SAM3_COMPILE_VIDEO,
                    warm_up=SAM3_COMPILE_VIDEO and SAM3_WARM_UP_VIDEO,
                )
            _patch_multiplex_init_state(predictor)
            predictor._sentinel_device = device
            return predictor
        except Exception as exc:
            # cuBLAS-Lt state can't be reset within a Python process: once
            # the warmup hits CUBLAS_STATUS_NOT_INITIALIZED or any CUDA
            # error, every subsequent matmul in this process — including
            # in the non-multiplex base predictor — fails with the same
            # error. The only clean recovery is process restart, mirroring
            # the `/unload` kill-and-respawn pattern in
            # main.py (`os._exit(1)` under `restart: unless-stopped`).
            exc_text = str(exc)
            cuda_poisoned = isinstance(exc, RuntimeError) and (
                "CUBLAS_STATUS" in exc_text
                or "CUDA error" in exc_text
                or "CUDA out of memory" in exc_text
                or "cuDNN error" in exc_text
            )
            if cuda_poisoned:
                logger.error(
                    "SAM3 multiplex video predictor crashed CUDA context (%s); "
                    "process state is unrecoverable, exiting so docker-compose "
                    "respawns the container",
                    exc,
                )
                os._exit(1)
            logger.warning(
                "SAM3 multiplex video predictor failed to load (%s); falling back to non-multiplex base predictor",
                exc,
            )
    with _device_context(device):
        predictor = build_sam3_video_predictor()
    predictor._sentinel_device = device
    return predictor


_flash_attn_patched = False
_flash_sdp_disabled = False


def _disable_flash_sdp_for_fp32_text_encoder() -> None:
    """Route fp32 attention paths off PyTorch's bundled flash-attention.

    SAM3's CLIP text encoder ([text_encoder_ve.py]) ships fp32 weights
    and its `nn.MultiheadAttention` path does not honour the ambient
    `torch.autocast(bf16)` context used in `run_video`. Under PyTorch
    2.10 + cu130 on sm_80 (A100), MHA dispatches to the bundled
    flash-attention-3 kernel which raises
    ``FlashAttention on Ampere/Ada cards only supports fp16 and bf16
    data type``.

    Two knobs are needed because `nn.MultiheadAttention` has a *native*
    fast path (`_native_multi_head_attention` → `mha_fwd`) that bypasses
    the SDPA backend toggle:

    1. `torch.backends.mha.set_fastpath_enabled(False)` — forces MHA to
       run through `multi_head_attention_forward` which calls
       `scaled_dot_product_attention`, which is the only path that
       honours the SDPA backend selection.
    2. `torch.backends.cuda.enable_flash_sdp(False)` — once the call
       reaches SDPA, picks memory-efficient or math backend instead of
       flash (both accept fp32).
    """
    global _flash_sdp_disabled
    if _flash_sdp_disabled:
        return
    try:
        import torch
        # Knob 1: route MHA through SDPA in the first place. Without
        # this, the SDPA backend selection below is unreachable from
        # nn.MultiheadAttention's forward path.
        try:
            torch.backends.mha.set_fastpath_enabled(False)
        except AttributeError:
            # Older PyTorch — the API isn't there, so the fast path
            # can't be disabled this way. Fall back to monkey-patching
            # so the SDPA backend selection still applies.
            import torch.nn as nn
            _orig_forward = nn.MultiheadAttention.forward

            def _no_fastpath_forward(self, *args, **kwargs):
                self._disable_fastpath = True
                return _orig_forward(self, *args, **kwargs)

            nn.MultiheadAttention.forward = _no_fastpath_forward
        # Knob 2: prefer memory-efficient / math SDPA over flash.
        torch.backends.cuda.enable_flash_sdp(False)
        _flash_sdp_disabled = True
        logger.info("Disabled MHA fastpath + Flash SDP backend for fp32 text encoder compatibility")
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not disable flash SDP backend: %s", exc)


def _is_hopper_or_newer() -> bool:
    """True if the first visible CUDA device is sm_90 (Hopper) or newer."""
    try:
        import torch
        if not torch.cuda.is_available():
            return False
        major, _ = torch.cuda.get_device_capability(0)
        return major >= 9
    except Exception:
        return False


def _install_flash_attn_fallback() -> None:
    """Provide a torch SDPA fallback for SAM3's flash-attn-3 dependency.

    SAM3's vitdet backbone calls ``sam3.perflib.fa3.flash_attn_func`` which
    in turn invokes a custom op that imports ``flash_attn_interface``
    (flash-attn-3) at call time. When the image is built with
    ``SAM3_INSTALL_FAST_DEPS=0`` that wheel isn't present and inference
    crashes inside the attention block with::

        ModuleNotFoundError: No module named 'flash_attn_interface'

    Replace ``flash_attn_func`` with a wrapper around
    ``torch.nn.functional.scaled_dot_product_attention``. PyTorch's SDPA
    transparently uses Flash Attention 2 on Ampere+, so the perf hit vs
    flash-attn-3 is modest. The patch is a no-op if the real wheel is
    available, and silent if the sam3 modules can't be imported (e.g. the
    image-only path).
    """
    global _flash_attn_patched
    if _flash_attn_patched:
        return
    try:
        import flash_attn_interface  # noqa: F401
        # The flash-attn-3 wheel is built for Hopper (sm_90). On sm_90+
        # it works and we should leave SAM3's perflib alone. On sm < 90
        # (Ampere/Ada/older), the wheel's `mha_fwd` rejects fp32 with
        # "FlashAttention on Ampere/Ada cards only supports fp16 and
        # bf16 data type" — and SAM3's `sam3.perflib.fa3` casts q/k/v
        # to a module-level `dtype` that isn't bf16 under our autocast
        # context, so q/k/v arrive at the kernel as fp32. Force the
        # SDPA fallback in that case.
        if _is_hopper_or_newer():
            _flash_attn_patched = True
            return
        logger.info(
            "flash_attn_interface installed but running on sm<9.0; "
            "the Hopper-built kernel rejects fp32 here. Forcing SDPA "
            "fallback for sam3.perflib.fa3.flash_attn_func"
        )
    except ImportError:
        pass

    try:
        import torch.nn.functional as F
        from sam3.perflib import fa3 as _fa3
    except Exception as exc:  # noqa: BLE001
        print(f"[sam3_runner] flash-attn fallback patch skipped: {exc}")
        return

    def _sdpa_fallback(q, k, v, *_, **__):
        # SAM3's flash_attn_func uses [B, S, H, D] (head-second-to-last);
        # torch SDPA expects [B, H, S, D]. Transpose, compute, transpose back.
        q_t = q.transpose(1, 2)
        k_t = k.transpose(1, 2)
        v_t = v.transpose(1, 2)
        out = F.scaled_dot_product_attention(q_t, k_t, v_t)
        return out.transpose(1, 2)

    _fa3.flash_attn_func = _sdpa_fallback
    # vitdet did `from sam3.perflib.fa3 import flash_attn_func` at import
    # time, so its module-level reference also needs replacing.
    try:
        from sam3.model import vitdet as _vitdet
        if hasattr(_vitdet, "flash_attn_func"):
            _vitdet.flash_attn_func = _sdpa_fallback
    except Exception:
        pass
    logger.info("flash_attn_3 not installed; using torch SDPA fallback (no accuracy impact, ~10%% slower attention)")
    _flash_attn_patched = True


def _patch_multiplex_init_state(predictor: Any) -> None:
    """Drop unsupported kwargs from ``Sam3MultiplexTrackingWithInteractivity.init_state``.

    The upstream ``sam3.model.sam3_base_predictor.start_session`` builds an
    ``init_kwargs`` dict that includes ``offload_state_to_cpu`` and passes it
    to ``self.model.init_state(**init_kwargs)``. The multiplex tracker's
    signature doesn't list that argument, so the call raises
    ``TypeError: ... got an unexpected keyword argument 'offload_state_to_cpu'``.
    Filter unknown kwargs through inspect so the call succeeds (and so future
    upstream additions don't surprise us either).
    """
    import functools
    import inspect

    model = getattr(predictor, "model", None)
    if model is None or not hasattr(model, "init_state"):
        return
    try:
        sig = inspect.signature(model.init_state)
    except (TypeError, ValueError):
        return
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        return  # already accepts **kwargs; nothing to do
    accepted = {name for name, p in sig.parameters.items()
                if p.kind != inspect.Parameter.VAR_POSITIONAL}
    original = model.init_state

    @functools.wraps(original)
    def safe_init_state(*args, **kwargs):
        dropped = [k for k in kwargs if k not in accepted]
        if dropped:
            print(f"[sam3_runner] init_state kwargs dropped: {dropped}")
        filtered = {k: v for k, v in kwargs.items() if k in accepted}
        return original(*args, **filtered)

    model.init_state = safe_init_state


def _flash_attn_3_status() -> str:
    """``installed`` if the fa3 wheel imports cleanly, else ``fallback``.

    Surfaced on /health so we can spot silent regressions after image
    rebuilds — the Dockerfile installs flash-attn-3 by default but the
    install can fail silently on architectures the wheel doesn't cover.
    """
    try:
        import flash_attn_interface  # noqa: F401
        return "installed"
    except Exception:
        return "fallback"


def versions() -> dict[str, str]:
    return {
        "sam3_image": SAM3_IMAGE_MODEL_ID,
        "sam3_weights_source": SAM3_WEIGHTS_SOURCE,
        "sam3_mirror_repo_id": SAM3_MIRROR_REPO_ID if SAM3_WEIGHTS_SOURCE == "mirror" else "",
        "sam3_video": "sam3.1-multiplex" if SAM3_USE_MULTIPLEX else "sam3",
        "sam3_compile_image": str(SAM3_COMPILE_IMAGE).lower(),
        "sam3_compile_video": str(SAM3_COMPILE_VIDEO).lower(),
        "sam3_batched_text": str(SAM3_BATCHED_TEXT).lower(),
        "sam3_batched_text_chunk_size": str(SAM3_BATCHED_TEXT_CHUNK_SIZE),
        "sam3_category_threshold": f"{SAM3_CATEGORY_THR:.2f}",
        "flash_attn_3": _flash_attn_3_status(),
        "dinov3_sat": os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m"),
        "prithvi_backbone": os.getenv("PRITHVI_BACKBONE_ID", "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL"),
        "prithvi_flood": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
        "prithvi_burn": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
        "terramind": os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large"),
    }


def _autocast_ctx(device: str):
    import torch

    if device.startswith("cuda"):
        return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    return torch.autocast(device_type="cpu", enabled=False)


def run_text_prompts(bundle: dict[str, Any], image_rgb_uint8: np.ndarray, prompts: Iterable[str], score_threshold: float):
    """Native facebookresearch/sam3 API.

    ``processor.set_image`` returns an inference state that caches vision
    features; ``set_text_prompt`` reuses the state for each prompt.
    """
    prompts = list(prompts)
    if SAM3_BATCHED_TEXT and len(prompts) > 1:
        try:
            candidates: list[tuple[np.ndarray, list[float], float, str]] = []
            for offset in range(0, len(prompts), SAM3_BATCHED_TEXT_CHUNK_SIZE):
                candidates.extend(
                    _run_text_prompts_batched(
                        bundle,
                        image_rgb_uint8,
                        prompts[offset:offset + SAM3_BATCHED_TEXT_CHUNK_SIZE],
                        score_threshold,
                    )
                )
            return candidates
        except Exception as exc:
            print(f"[INFERENCE-SAM3] batched text prompt path failed; falling back to native loop: {exc}")

    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates: list[tuple[np.ndarray, list[float], float, str]] = []

    with bundle["lock"], _inference_mode(), _autocast_ctx(device):
        state = processor.set_image(pil_image)
        for label in prompts:
            phrase = PROMPT_TEMPLATE.format(label=label)
            output = processor.set_text_prompt(state=state, prompt=phrase)
            if not _prompt_passes_category_gate(output):
                continue
            candidates.extend(_collect_candidates(output, score_threshold, label))
    return candidates


def _prompt_passes_category_gate(output) -> bool:
    """Category-level presence gate. ``True`` if the prompt's best candidate
    score is at least ``SAM3_CATEGORY_THR``. Suppresses the entire prompt's
    detections when the concept is effectively absent from the scene
    (presence-token probability multiplied with per-mask quality < gate)."""
    if SAM3_CATEGORY_THR <= 0.0:
        return True
    scores = _to_list(output.get("scores"))
    if not scores:
        return False
    try:
        max_score = max(float(s) for s in scores)
    except Exception:
        return True
    return max_score >= SAM3_CATEGORY_THR


def _run_text_prompts_batched(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    prompts: list[str],
    score_threshold: float,
):
    """Run the upstream SAM3 batched image API for multiple text queries.

    This mirrors ``examples/sam3_image_batched_inference.ipynb`` from
    facebookresearch/sam3: build one datapoint containing many text queries,
    collate it, move it to the target device, then call the image model once.
    """
    import torch
    from sam3.eval.postprocessors import PostProcessImage
    from sam3.model.utils.misc import copy_data_to_device
    from sam3.train.data.collator import collate_fn_api as collate
    from sam3.train.data.sam3_image_dataset import (
        Datapoint,
        FindQueryLoaded,
        Image as SAMImage,
        InferenceMetadata,
    )
    from sam3.train.transforms.basic_for_api import ComposeAPI, NormalizeAPI, RandomResizeAPI, ToTensorAPI

    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    width, height = pil_image.size
    datapoint = Datapoint(
        find_queries=[],
        images=[SAMImage(data=pil_image, objects=[], size=[height, width])],
    )
    query_labels: dict[int, str] = {}
    for label in prompts:
        query_id = next(_QUERY_IDS)
        phrase = PROMPT_TEMPLATE.format(label=label)
        datapoint.find_queries.append(
            FindQueryLoaded(
                query_text=phrase,
                image_id=0,
                object_ids_output=[],
                is_exhaustive=True,
                query_processing_order=0,
                inference_metadata=InferenceMetadata(
                    coco_image_id=query_id,
                    original_image_id=query_id,
                    original_category_id=1,
                    original_size=(height, width),
                    object_id=0,
                    frame_index=0,
                ),
            )
        )
        query_labels[query_id] = label

    transform = ComposeAPI(
        transforms=[
            RandomResizeAPI(sizes=1008, max_size=1008, square=True, consistent_transform=False),
            ToTensorAPI(),
            NormalizeAPI(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )
    datapoint = transform(datapoint)
    batch = collate([datapoint], dict_key="sam3")["sam3"]
    batch = copy_data_to_device(batch, torch.device(device), non_blocking=device.startswith("cuda"))
    postprocessor = PostProcessImage(
        max_dets_per_img=-1,
        iou_type="segm",
        use_original_sizes_box=True,
        use_original_sizes_mask=True,
        convert_mask_to_rle=False,
        detection_threshold=score_threshold,
        to_cpu=True,
        always_interpolate_masks_on_gpu=device.startswith("cuda"),
    )

    with bundle["lock"], _inference_mode(), _autocast_ctx(device):
        output = bundle["sam3_image"]["model"](batch)
        processed = postprocessor.process_results(output, batch.find_metadatas)
    return _collect_batched_candidates(processed, query_labels)


def _collect_batched_candidates(processed: dict[int, dict[str, Any]], query_labels: dict[int, str]):
    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for query_id, result in processed.items():
        label = query_labels.get(int(query_id), "object")
        masks = _to_list(result.get("masks"))
        boxes = _to_list(result.get("boxes"))
        scores = _to_list(result.get("scores"))
        # Category-level presence gate: drop the entire prompt if its best
        # candidate score is below SAM3_CATEGORY_THR. See _prompt_passes_category_gate.
        if SAM3_CATEGORY_THR > 0.0 and scores:
            try:
                max_score = max(float(s) for s in scores)
            except Exception:
                max_score = 1.0
            if max_score < SAM3_CATEGORY_THR:
                continue
        for mask, box_xyxy, score in zip(masks, boxes, scores):
            out.append((
                np.asarray(_to_numpy(mask), dtype=bool).squeeze(),
                [float(v) for v in _to_numpy(box_xyxy).reshape(-1)[:4]],
                float(score),
                label,
            ))
    return out


def run_box_prompts(bundle: dict[str, Any], image_rgb_uint8: np.ndarray, prompt_boxes: list[dict[str, Any]], score_threshold: float):
    """Run the native sam3 box-prompt path.

    Each entry is fed to ``Sam3Processor.add_geometric_prompt`` as a
    normalized cxcywh box. The processor's per-image ``state`` caches backbone
    features so each prompt only costs the grounding head + decoder.
    Prompts are evaluated independently (state is reset between entries) so
    that returned masks/boxes correspond 1:1 to the input list.
    """
    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates: list[tuple[np.ndarray, list[float], float, str]] = []

    with bundle["lock"], _inference_mode(), _autocast_ctx(device):
        state = processor.set_image(pil_image)
        for entry in prompt_boxes:
            cxcywh = _entry_to_norm_cxcywh(entry)
            if cxcywh is None:
                continue
            label = str(entry.get("class") or entry.get("original_class") or "segment")
            processor.reset_all_prompts(state)
            output = processor.add_geometric_prompt(box=cxcywh, label=True, state=state)
            candidates.extend(_collect_candidates(output, score_threshold, label))
    return candidates


def run_video(bundle, video_path, prompts, *, frame_stride, start_frame, end_frame, max_frames, dinov3, score_threshold):
    """Run a SAM3 video tracking session for a single text concept.

    The upstream API (`Sam3VideoInference.add_prompt` and
    `Sam3MultiplexTrackingWithInteractivity.add_prompt`) unconditionally
    calls `self.reset_state(inference_state)` for any text prompt, so the
    tracker can only persist one text concept per session. Callers that
    need N concepts must run N sequential sessions (one per prompt).
    Anything that re-prompts mid-session would reset the inference state
    and destroy SAM3's built-in hotstart unmatched/duplicate suppression
    (`hotstart_delay=15`, `hotstart_unmatch_thresh=8`,
    `hotstart_dup_thresh=8`), so re-prompting is intentionally absent.
    """
    predictor = bundle["sam3_video"]
    clean_prompts = [p for p in prompts if p and not p.startswith("__")]
    if not clean_prompts:
        return
    if len(clean_prompts) > 1:
        logger.warning(
            "run_video received %d prompts; only the first (%r) will be tracked. "
            "Caller should iterate sessions per prompt to track multiple concepts.",
            len(clean_prompts), clean_prompts[0],
        )
    prompt_text = clean_prompts[0]

    # Autocast: the multiplex predictor casts decoded frames to bfloat16
    # internally but ships its weights as float32. Without the ambient cuda
    # autocast context the vitdet linear layers raise
    # "mat1 BFloat16 / mat2 Float". The image detection paths in this file
    # already wrap forward passes in `_autocast_ctx`; mirror that here.
    # Device pinning: the multiplex predictor's weights are on
    # `_sentinel_device` (set by build_video). Wrap the session lifecycle
    # in `torch.cuda.device(...)` so any internal `.cuda()` calls land on
    # the right GPU. Without this, anyio/threadpool dispatch may pick up
    # `cuda:0` regardless of which bundle was selected.
    target_device = getattr(predictor, "_sentinel_device", bundle["device"])
    with _device_context(target_device), _autocast_ctx(target_device):
        session = predictor.handle_request(request={"type": "start_session", "resource_path": video_path})
        session_id = session["session_id"]
        # Buffer emissions through the hotstart window so the category
        # presence gate has enough scores to decide. After the window
        # closes we either flush the buffer (gate passes) or drop it
        # entirely (gate fails) and continue streaming live.
        gate_active = SAM3_CATEGORY_THR > 0.0
        gate_passed = not gate_active
        gate_buffer: list[str] = []
        gate_max_score = 0.0
        try:
            predictor.handle_request(request={
                "type": "add_prompt",
                "session_id": session_id,
                "frame_index": start_frame,
                "text": prompt_text,
            })

            emitted_frames = 0
            for resp in predictor.handle_stream_request(request={"type": "propagate_in_video", "session_id": session_id}):
                frame_idx = int(resp["frame_index"])
                if frame_idx < start_frame:
                    continue
                if end_frame is not None and frame_idx > int(end_frame):
                    break
                if (frame_idx - start_frame) % frame_stride:
                    continue
                if max_frames is not None and emitted_frames >= int(max_frames):
                    break
                emitted_frames += 1
                _resp_outs = resp.get("outputs")
                if _resp_outs is None:
                    _resp_outs = []
                for track in _iter_sam3_video_tracks(_resp_outs, prompt_text=prompt_text):
                    score = float(track.get("score") or 0.0)
                    if score < score_threshold:
                        continue
                    if gate_active and not gate_passed:
                        gate_max_score = max(gate_max_score, score)
                    import fusion

                    mask = track.get("mask")
                    bbox_xyxy_norm = track.get("bbox_xyxy_norm")
                    entry: dict[str, Any] = {
                        "frame_index": frame_idx,
                        "track_id": int(track["track_id"]),
                        "class": prompt_text,
                        "original_class": prompt_text,
                        "parent_class": "track",
                        "score": score,
                    }
                    if mask is not None and bbox_xyxy_norm is not None:
                        mask_arr = np.asarray(mask, dtype=bool)
                        height, width = mask_arr.shape[-2:]
                        x1n, y1n, x2n, y2n = bbox_xyxy_norm
                        bbox_xyxy_px = [x1n * width, y1n * height, x2n * width, y2n * height]
                        obb = fusion.mask_to_obb_record(mask_arr, bbox_xyxy_px, width, height)
                        entry.update({
                            "bbox_xyxy_norm": bbox_xyxy_norm,
                            "obb": obb["points"],
                            "obb_format": "yolo_obb_normalized_xyxyxyxy",
                            "obb_source": obb["source"],
                            "obb_angle_deg": obb["angle_deg"],
                            "edge_truncated": obb["edge_truncated"],
                            "mask_rle": fusion.coco_rle(mask_arr),
                        })
                    else:
                        # Heartbeat — the tracker still owns this id but
                        # has no visible mask this frame. Worker stores it
                        # with empty bbox; UI just doesn't draw a box this
                        # frame but the per-track timeline stays intact.
                        entry.update({
                            "bbox_xyxy_norm": None,
                            "obb": None,
                            "obb_format": None,
                            "obb_source": "tracker_lost",
                            "mask_rle": None,
                        })
                    serialised = json.dumps(entry, separators=(",", ":"))
                    if gate_active and not gate_passed:
                        gate_buffer.append(serialised)
                        if emitted_frames >= SAM3_HOTSTART_DELAY_FRAMES:
                            if gate_max_score >= SAM3_CATEGORY_THR:
                                gate_passed = True
                                for buffered in gate_buffer:
                                    yield buffered
                                gate_buffer.clear()
                            else:
                                # Concept absent from the scene — drop this
                                # session's emissions entirely and close.
                                logger.info(
                                    "video category gate dropped prompt %r (max score %.3f < %.2f)",
                                    prompt_text, gate_max_score, SAM3_CATEGORY_THR,
                                )
                                return
                    else:
                        yield serialised
            # Stream ended before the hotstart window closed; flush the
            # buffer only if the gate would have passed.
            if gate_active and not gate_passed:
                if gate_max_score >= SAM3_CATEGORY_THR:
                    for buffered in gate_buffer:
                        yield buffered
                else:
                    logger.info(
                        "video category gate dropped prompt %r (max score %.3f < %.2f, partial window)",
                        prompt_text, gate_max_score, SAM3_CATEGORY_THR,
                    )
        except RuntimeError as exc:
            # Multiplex tracker raises `RuntimeError("No points are
            # provided; please add points first")` mid-propagation from
            # `video_tracking_multiplex_demo.py:3410` when the detector
            # found nothing on the previous frame and the tracker has no
            # anchor (point/mask prompt) to continue from. Treat this
            # as graceful end-of-tracking for this window — every
            # detection we already yielded above is valid and stays in
            # the response. The finally block still closes the session.
            if "No points are provided" not in str(exc):
                raise
            logger.warning(
                "SAM3 propagation ended early in session %s: %s",
                session_id, exc,
            )
        finally:
            predictor.handle_request(request={"type": "close_session", "session_id": session_id})


# ---------------------------------------------------------------------------
# AMG implementation. See the module-level "Automatic Mask Generation" block
# for the rationale. Public entry point is `run_video_amg`; `probe_amg` should
# be invoked once at bundle init by main.py's /health route so the AMG path's
# availability can be reported without paying probe cost per request.
# ---------------------------------------------------------------------------


def _build_point_grid_norm(n: int) -> np.ndarray:
    """Centered n×n point grid in normalized [0,1] coords. Returns (n*n, 2)."""
    xs = (np.arange(n, dtype=np.float32) + 0.5) / float(n)
    ys = (np.arange(n, dtype=np.float32) + 0.5) / float(n)
    return np.stack(np.meshgrid(xs, ys, indexing="xy"), axis=-1).reshape(-1, 2)


def _downsample_bool(mask: np.ndarray, target: int) -> np.ndarray:
    """Block-max downsample a 2-D bool mask to roughly target×target.

    Faster than scikit-image / cv2 for our use (NMS-only) — keeps any
    pixel set in the source block. Always yields exactly target×target.
    """
    h, w = mask.shape[-2:]
    if h == target and w == target:
        return mask.astype(bool)
    bh = max(1, h // target)
    bw = max(1, w // target)
    trimmed = mask[: bh * target, : bw * target]
    if trimmed.shape[0] < target or trimmed.shape[1] < target:
        # fall back to nearest-neighbor indexing on the rare degenerate case
        ys = np.linspace(0, h - 1, target).astype(np.intp)
        xs = np.linspace(0, w - 1, target).astype(np.intp)
        return mask[ys[:, None], xs[None, :]].astype(bool)
    return trimmed.reshape(target, bh, target, bw).any(axis=(1, 3))


# Resolution at which masks are pairwise-compared for NMS / Hungarian linking.
# At 540p, raw N² mask-IoU is O(N²·291k) ≈ 30 s for N=576 on CPU. Downsampling
# to 64×64 cuts that to ~0.4 s with negligible IoU error for the 0.7 threshold
# we use. Override via env if you need pixel-exact NMS.
_MASK_NMS_RESOLUTION = max(16, int(os.getenv("SAM3_AMG_NMS_MASK_RES", "64")))


def _mask_iou_matrix(masks: list[np.ndarray], resolution: int | None = None) -> np.ndarray:
    """Pairwise mask-IoU on downsampled masks. O(N² · resolution²) memory."""
    n = len(masks)
    if n == 0:
        return np.zeros((0, 0), dtype=np.float32)
    res = int(resolution or _MASK_NMS_RESOLUTION)
    flat = np.stack([_downsample_bool(m, res).reshape(-1) for m in masks]).astype(np.int32)
    inter = flat @ flat.T
    areas = flat.sum(axis=1)
    union = areas[:, None] + areas[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1), 0.0).astype(np.float32)


def _mask_nms(
    masks: list[np.ndarray],
    boxes: list[list[float]],
    scores: list[float],
    iou_thresh: float,
) -> tuple[list[np.ndarray], list[list[float]], list[float]]:
    """Greedy mask-IoU NMS in score-descending order."""
    if not masks:
        return [], [], []
    order = sorted(range(len(masks)), key=lambda i: -scores[i])
    iou = _mask_iou_matrix(masks)
    suppressed = np.zeros(len(masks), dtype=bool)
    kept: list[int] = []
    for i in order:
        if suppressed[i]:
            continue
        kept.append(i)
        suppressed |= (iou[i] >= iou_thresh)
        suppressed[i] = False  # keep self
    return (
        [masks[i] for i in kept],
        [boxes[i] for i in kept],
        [scores[i] for i in kept],
    )


def _mask_edge_frac(mask: np.ndarray, ring_px: int = 2) -> float:
    """Fraction of mask pixels that lie within ``ring_px`` of the frame
    boundary. Used to drop masks that mostly hug the image edge (typical
    SAM 3 failure mode where the segmenter latches onto the frame border
    when the prompt point lands on uniform sky/road).
    """
    if mask.size == 0 or not mask.any():
        return 0.0
    h, w = mask.shape[-2:]
    if h <= 2 * ring_px or w <= 2 * ring_px:
        return 1.0
    total = int(mask.sum())
    if total <= 0:
        return 0.0
    # Pixels in the outer ``ring_px`` ring.
    inner = mask.copy()
    inner[ring_px : h - ring_px, ring_px : w - ring_px] = False
    return float(int(inner.sum()) / total)


def _passes_quality_filters(
    mask: np.ndarray,
    score: float,
    frame_total_px: int,
    *,
    pred_iou_thresh: float,
    min_area_px: int,
    max_area_frac: float,
    edge_frac_max: float,
) -> bool:
    """Cheap pre-NMS gate for AMG candidates. All checks are O(H·W) at most.

    Filter order matters: cheapest first so we short-circuit before the
    edge-fraction computation on tiny / huge / low-score masks.
    """
    if score < pred_iou_thresh:
        return False
    area = int(mask.sum())
    if area < min_area_px:
        return False
    if max_area_frac > 0 and frame_total_px > 0 and area / frame_total_px > max_area_frac:
        return False
    if edge_frac_max < 1.0 and _mask_edge_frac(mask) > edge_frac_max:
        return False
    return True


# ---------------------------------------------------------------------------
# Phase 5: drone-HUD overlay detection & post-detection filter.
# ---------------------------------------------------------------------------


def _detect_hud_mask(video_path: str) -> np.ndarray | None:
    """Sample frames evenly across the clip; pixels with low inter-frame
    std-dev are 'static' = HUD overlay. Returns ``(H, W) bool`` array where
    True = HUD pixel, or ``None`` when:

      * ``SAM3_AMG_HUD_MASK_ENABLED=0``
      * video can't be opened
      * fewer than 2 frames could be sampled

    A morphological close (5×5) merges text characters into rectangular
    HUD blocks so the downstream bbox-overlap test catches whole HUD strips
    instead of just the lit pixels.
    """
    if not SAM3_AMG_HUD_MASK_ENABLED:
        return None
    try:
        import cv2  # opencv-python-headless is pinned in inference-sam3
    except Exception as exc:
        logger.debug("HUD detect: cv2 unavailable (%s)", exc)
        return None
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total < 2:
            return None
        n = min(SAM3_AMG_HUD_SAMPLES, total)
        indices = np.linspace(0, total - 1, n).astype(int)
        frames: list[np.ndarray] = []
        for idx in indices:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, bgr = cap.read()
            if ok and bgr is not None:
                frames.append(bgr.astype(np.float32))
        if len(frames) < 2:
            return None
        stack = np.stack(frames)  # (N, H, W, 3)
        std_per_channel = stack.std(axis=0)  # (H, W, 3)
        std_max = std_per_channel.max(axis=-1)  # (H, W)
        hud = (std_max < float(SAM3_AMG_HUD_STD_THRESH)).astype(np.uint8) * 255
        kernel = np.ones((5, 5), dtype=np.uint8)
        hud = cv2.morphologyEx(hud, cv2.MORPH_CLOSE, kernel)
        mask = hud > 0
        frac = float(mask.sum() / max(1, mask.size))
        logger.info(
            "AMG HUD mask: %d static pixels (frac=%.2f%%) from %d samples",
            int(mask.sum()), frac * 100.0, len(frames),
        )
        return mask
    except Exception as exc:
        logger.warning("HUD detect failed (%s); proceeding without HUD filter", exc)
        return None
    finally:
        cap.release()


def _bbox_overlap_with_hud(bbox_xyxy_px: list[float], hud_mask: np.ndarray | None) -> float:
    """Fraction of the bbox area that overlaps HUD pixels (0..1).

    Returns 0.0 when ``hud_mask`` is None or the bbox is invalid. Callers
    drop detections whose overlap ≥ ``SAM3_AMG_HUD_OVERLAP_MAX``.
    """
    if hud_mask is None:
        return 0.0
    if not bbox_xyxy_px or len(bbox_xyxy_px) < 4:
        return 0.0
    x1, y1, x2, y2 = (int(round(float(v))) for v in bbox_xyxy_px[:4])
    h, w = hud_mask.shape[-2:]
    x1 = max(0, min(w - 1, x1))
    x2 = max(x1 + 1, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(y1 + 1, min(h, y2))
    region = hud_mask[y1:y2, x1:x2]
    if region.size == 0:
        return 0.0
    return float(region.sum() / region.size)


def _filter_candidates_by_hud(
    candidates: list[tuple],
    hud_mask: np.ndarray | None,
    *,
    overlap_max: float | None = None,
) -> list[tuple]:
    """Drop candidates whose bbox overlaps HUD by ≥ overlap_max.

    Accepts both grid-mode 3-tuples ``(mask, bbox_px, score)`` and
    GD-first 4-tuples ``(mask, bbox_px, score, label)``. Returns a new
    list preserving the original tuple shape; the bbox is always at index
    1.
    """
    if hud_mask is None or not candidates:
        return list(candidates)
    threshold = float(SAM3_AMG_HUD_OVERLAP_MAX if overlap_max is None else overlap_max)
    if threshold >= 1.0:
        return list(candidates)
    kept = []
    dropped = 0
    for cand in candidates:
        bbox = cand[1]
        if _bbox_overlap_with_hud(bbox, hud_mask) >= threshold:
            dropped += 1
            continue
        kept.append(cand)
    if dropped:
        logger.debug("HUD filter dropped %d/%d candidates", dropped, len(candidates))
    return kept


def _ontology_label_set(sensor: str = "optical") -> frozenset[str]:
    """Lowercase set of admin-ontology prompts for ``sensor``.

    Thin wrapper around ``main.get_ontology_optical_labels`` so this module
    doesn't reimplement the cache + TTL. Returns an empty set on any
    failure (backend down, circular-import edge case) so callers safely
    fall back to the default (high) GD threshold for every class.
    """
    try:
        import main as _main  # noqa: WPS433 — sibling module, runtime import
        if sensor == "optical":
            return _main.get_ontology_optical_labels()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ontology label set unavailable: %s", exc)
    return frozenset()


def _filter_by_class_threshold(
    candidates: list[tuple],
    ontology_set: frozenset[str],
    onto_thresh: float,
    default_thresh: float,
) -> list[tuple]:
    """Apply a per-class GD score floor driven by the admin ontology.

    Each candidate is a tuple with the GD/box score at index 2 and the
    label at index 3 (matches both ``grounding_dino.run`` outputs and the
    refined 4-tuples from ``_amg_sweep_via_gd``). Candidates whose label
    is in ``ontology_set`` must clear ``onto_thresh``; others must clear
    ``default_thresh``. ``label=None`` candidates pass through unchanged
    so the caller can keep the ``_amg`` generic fallback.

    When ``onto_thresh >= default_thresh`` the two-tier policy reduces to
    a single floor and the ontology lookup is skipped.
    """
    if not candidates:
        return list(candidates)
    if onto_thresh >= default_thresh:
        return [
            c for c in candidates
            if (len(c) < 4 or c[3] is None) or float(c[2]) >= default_thresh
        ]
    out = []
    for c in candidates:
        label = c[3] if len(c) >= 4 else None
        if label is None:
            out.append(c)
            continue
        key = str(label).strip().lower()
        thr = onto_thresh if key in ontology_set else default_thresh
        if float(c[2]) >= thr:
            out.append(c)
    return out


def _amg_sweep_image_grid(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    grid_size: int,
    score_threshold: float,
    nms_iou: float,
    point_box_norm: float,
    *,
    bypass_quality_filters: bool = False,
    hud_mask: np.ndarray | None = None,
) -> list[tuple[np.ndarray, list[float], float]]:
    """Run an AMG sweep on one frame. Returns NMSed (mask, bbox_xyxy_px, score).

    The SAM3 image processor caches backbone features in ``state`` after
    ``set_image``, so the per-grid-point cost is grounding head + decoder
    rather than a full forward pass.

    Phase 3 adds cheap pre-NMS quality filters (pred_iou_thresh, area gates,
    edge-touching gate) and an opt-in stability score. These cut the typical
    ~250-cands-per-256-grid pass-through to ~30-80 high-quality candidates.

    **Lock contract**: this helper does NOT acquire ``bundle["lock"]``.
    Callers are responsible for holding it. Acquired by ``probe_amg``
    (a one-shot synthetic call) and inherited from ``/detect_video``'s
    bundle-reservation lock by ``run_video_amg`` (which runs inside the
    pre-acquired session lock for the entire window).
    """
    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil = Image.fromarray(image_rgb_uint8)
    grid = _build_point_grid_norm(grid_size)
    frame_h, frame_w = image_rgb_uint8.shape[:2]
    frame_total_px = max(1, frame_h * frame_w)
    # Effective score floor combines the caller-supplied threshold with the
    # AMG-specific pred_iou_thresh — whichever is stricter wins. The probe
    # bypasses both via ``bypass_quality_filters=True`` since the synthetic
    # 64×64 fixture is too small to clear normal-resolution thresholds.
    if bypass_quality_filters:
        score_floor = float(score_threshold)
    else:
        score_floor = max(float(score_threshold), float(SAM3_AMG_PRED_IOU_THRESH))
    masks: list[np.ndarray] = []
    boxes: list[list[float]] = []
    scores: list[float] = []
    with _inference_mode(), _autocast_ctx(device):
        state = processor.set_image(pil)
        for (cx, cy) in grid:
            processor.reset_all_prompts(state)
            output = processor.add_geometric_prompt(
                box=[float(cx), float(cy), float(point_box_norm), float(point_box_norm)],
                label=True,
                state=state,
            )
            for cand in _collect_candidates(output, score_floor, "_amg"):
                mask_arr, box_xyxy, score, _label = cand
                if not bypass_quality_filters and not _passes_quality_filters(
                    mask_arr, score, frame_total_px,
                    pred_iou_thresh=score_floor,
                    min_area_px=SAM3_AMG_MIN_AREA_PX,
                    max_area_frac=SAM3_AMG_MAX_AREA_FRAC,
                    edge_frac_max=SAM3_AMG_EDGE_FRAC_MAX,
                ):
                    continue
                if bypass_quality_filters and (mask_arr.size == 0 or not mask_arr.any()):
                    # Even the probe rejects fully-empty masks.
                    continue
                # Optional SAM-2-style stability score: shift the prompt box
                # by ±delta and require the resulting mask's IoU to stay
                # above the threshold. Off by default (0.0).
                if SAM3_AMG_STABILITY_THRESH > 0.0:
                    if not _check_stability(
                        processor, state, mask_arr, cx, cy,
                        point_box_norm, SAM3_AMG_STABILITY_DELTA,
                        SAM3_AMG_STABILITY_THRESH,
                    ):
                        continue
                masks.append(mask_arr)
                boxes.append(box_xyxy)
                scores.append(score)
    if not masks:
        return []
    candidates = list(zip(*_mask_nms(masks, boxes, scores, nms_iou)))
    # Drop candidates that fall (mostly) inside the static-pixel HUD region.
    return _filter_candidates_by_hud(candidates, hud_mask)


def _amg_sweep_via_gd(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    score_threshold: float,
    nms_iou: float,
    *,
    hud_mask: np.ndarray | None = None,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """GD-first AMG: Grounding-DINO produces detection boxes, SAM 3 refines
    them into masks. Returns ``(mask, bbox_xyxy_px, score, gd_label)`` tuples
    — note the **4-tuple shape** vs the 3-tuple from `_amg_sweep_image_grid`.
    Callers (the dispatcher in `_amg_sweep_image` and `run_video_amg`) tell
    the modes apart by ``len(candidates[0]) == 4``.

    Empty result on:
      * missing GD bundle (model didn't load) — logs a warning
      * GD produces zero boxes on the seed frame — logs at info level
      * all per-box masks fail the Phase 3 quality filters

    Phase 3 quality filters (area/edge/score) and final mask-IoU NMS are
    applied to the refined masks, same as in the grid path, so downstream
    quality is preserved.
    """
    import time as _time

    gd_bundle = bundle.get("grounding_dino")
    if gd_bundle is None or gd_bundle.get("model") is None:
        logger.warning("GD-first AMG requested but grounding_dino unavailable")
        return []

    import grounding_dino  # local — avoid module-import-time cycle

    label_prompts = [p.strip() for p in SAM3_AMG_LABEL_PROMPTS.split(",") if p.strip()]
    if not label_prompts:
        logger.warning("GD-first AMG: SAM3_AMG_LABEL_PROMPTS is empty")
        return []

    sweep_start = _time.monotonic()
    # Phase 6: call GD at the LOWER of (default, ontology) so on-ontology
    # candidates (building/vehicle/person/…) above 0.20 survive; per-class
    # filtering below then re-applies the 0.45 floor to off-ontology
    # labels (pole/tower/sign).
    gd_score_floor = min(SAM3_AMG_LABEL_GD_THRESH, SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY)
    try:
        gd_results = grounding_dino.run(
            gd_bundle, image_rgb_uint8, label_prompts,
            score_threshold=gd_score_floor,
        )
    except Exception as exc:
        logger.warning("GD-first AMG: grounding_dino.run failed (%s)", exc)
        return []

    if not gd_results:
        logger.info(
            "GD-first AMG: 0 GD boxes on seed frame (vocab gap; "
            "consider lowering SAM3_AMG_LABEL_GD_THRESH or expanding prompts)"
        )
        return []

    # Phase 6 per-class filter: drop off-ontology low-score candidates
    # before wasting SAM3 refinement compute on them. Ontology labels keep
    # the lower floor so building/vehicle/person recall is recovered.
    ontology_set = _ontology_label_set("optical")
    pre_class = len(gd_results)
    gd_results = _filter_by_class_threshold(
        gd_results, ontology_set,
        onto_thresh=SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY,
        default_thresh=SAM3_AMG_LABEL_GD_THRESH,
    )
    if pre_class != len(gd_results):
        logger.info(
            "GD-first AMG: per-class GD floor (onto=%.2f, default=%.2f, "
            "ontology_size=%d) dropped %d/%d GD boxes",
            SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY, SAM3_AMG_LABEL_GD_THRESH,
            len(ontology_set), pre_class - len(gd_results), pre_class,
        )
    if not gd_results:
        return []

    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil = Image.fromarray(image_rgb_uint8)
    H, W = image_rgb_uint8.shape[:2]
    frame_total_px = max(1, H * W)
    # Effective score floor combines caller threshold with AMG pred-IoU gate.
    score_floor = max(float(score_threshold), float(SAM3_AMG_PRED_IOU_THRESH))

    refined: list[tuple[np.ndarray, list[float], float, str]] = []
    with _inference_mode(), _autocast_ctx(device):
        state = processor.set_image(pil)
        for (_gd_mask, gd_box_xyxy, _gd_score, gd_label) in gd_results:
            x1, y1, x2, y2 = (float(v) for v in gd_box_xyxy[:4])
            # Convert pixel xyxy → normalized cxcywh for add_geometric_prompt.
            cx = ((x1 + x2) / 2.0) / max(1.0, float(W))
            cy = ((y1 + y2) / 2.0) / max(1.0, float(H))
            box_w = max(0.001, (x2 - x1) / max(1.0, float(W)))
            box_h = max(0.001, (y2 - y1) / max(1.0, float(H)))
            cx = min(1.0, max(0.0, cx))
            cy = min(1.0, max(0.0, cy))
            box_w = min(1.0, max(0.001, box_w))
            box_h = min(1.0, max(0.001, box_h))
            try:
                processor.reset_all_prompts(state)
                output = processor.add_geometric_prompt(
                    box=[cx, cy, box_w, box_h], label=True, state=state,
                )
            except Exception as exc:
                logger.debug("GD-first AMG: add_geometric_prompt failed for %s: %s",
                             gd_label, exc)
                continue
            for mask_arr, box_xyxy_px, score, _ in _collect_candidates(
                output, score_floor, gd_label,
            ):
                if not _passes_quality_filters(
                    mask_arr, score, frame_total_px,
                    pred_iou_thresh=score_floor,
                    min_area_px=SAM3_AMG_MIN_AREA_PX,
                    max_area_frac=SAM3_AMG_MAX_AREA_FRAC,
                    edge_frac_max=SAM3_AMG_EDGE_FRAC_MAX,
                ):
                    continue
                refined.append((mask_arr, box_xyxy_px, score, gd_label))

    if not refined:
        logger.info(
            "GD-first AMG: %d GD boxes → 0 refined masks (all filtered) elapsed=%.2fs",
            len(gd_results), _time.monotonic() - sweep_start,
        )
        return []

    # Cross-box NMS — GD frequently emits overlapping boxes for the same
    # target (vehicle/equipment, structure/building). Use the existing
    # downsampled-mask NMS, then re-attach labels via list-identity lookup.
    masks = [c[0] for c in refined]
    boxes = [c[1] for c in refined]
    scores = [c[2] for c in refined]
    labels = [c[3] for c in refined]
    kept_masks, kept_boxes, kept_scores = _mask_nms(masks, boxes, scores, nms_iou)
    kept_labels: list[str] = []
    for km in kept_masks:
        for i, m in enumerate(masks):
            if m is km:
                kept_labels.append(labels[i])
                break
        else:
            kept_labels.append("_amg")  # defensive fallback (shouldn't happen)
    candidates = list(zip(kept_masks, kept_boxes, kept_scores, kept_labels))
    # Drop GD-derived boxes that sit (mostly) inside the static HUD region.
    pre_hud = len(candidates)
    candidates = _filter_candidates_by_hud(candidates, hud_mask)
    logger.info(
        "GD-first AMG: %d GD boxes → %d refined → %d post-NMS → %d post-HUD elapsed=%.2fs",
        len(gd_results), len(refined), pre_hud, len(candidates),
        _time.monotonic() - sweep_start,
    )
    return candidates


def _amg_sweep_image(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    grid_size: int,
    score_threshold: float,
    nms_iou: float,
    point_box_norm: float,
    *,
    bypass_quality_filters: bool = False,
    hud_mask: np.ndarray | None = None,
):
    """Detector dispatcher. Selects between Phase 4's GD-first path (default)
    and Phase 3's dense N×N grid path based on ``SAM3_AMG_DETECTOR``.

    Returns:
      * Grid mode: list of ``(mask, bbox_xyxy_px, score)`` 3-tuples
      * GD-first mode: list of ``(mask, bbox_xyxy_px, score, gd_label)`` 4-tuples

    Callers in ``run_video_amg`` / ``run_video_amg_seeded`` use
    ``len(candidates[0]) == 4`` to detect GD mode and read labels in-band
    instead of running the secondary `_assign_amg_labels_via_gd` post-pass.

    The probe (`probe_amg`) always passes ``bypass_quality_filters=True``
    which forces the grid path on the 64×64 synthetic fixture, keeping the
    probe deterministic regardless of which detector is active in
    production.
    """
    if bypass_quality_filters or SAM3_AMG_DETECTOR == "grid":
        return _amg_sweep_image_grid(
            bundle, image_rgb_uint8, grid_size, score_threshold,
            nms_iou, point_box_norm,
            bypass_quality_filters=bypass_quality_filters,
            hud_mask=hud_mask,
        )
    return _amg_sweep_via_gd(
        bundle, image_rgb_uint8, score_threshold, nms_iou,
        hud_mask=hud_mask,
    )


def _check_stability(
    processor,
    state,
    base_mask: np.ndarray,
    cx: float, cy: float,
    point_box_norm: float,
    delta: float,
    stability_thresh: float,
) -> bool:
    """Perturb the prompt box by ``delta`` and compare to the original mask.

    SAM 2 computes stability as the IoU between two binary masks at
    different logit thresholds; SAM 3's image processor doesn't expose raw
    logits via add_geometric_prompt, so we approximate by perturbing the
    prompt box itself and re-segmenting. Costs one extra forward pass per
    candidate when enabled — keep this opt-in.
    """
    try:
        processor.reset_all_prompts(state)
        shifted = processor.add_geometric_prompt(
            box=[float(cx) + delta, float(cy) + delta,
                 float(point_box_norm), float(point_box_norm)],
            label=True, state=state,
        )
    except Exception:
        return True  # fail-open: don't drop candidates on probe failure
    cands = _collect_candidates(shifted, 0.0, "_amg")
    if not cands:
        return False
    # Take the highest-score perturbed mask and compute downsampled IoU.
    cands.sort(key=lambda c: -c[2])
    shifted_mask = cands[0][0]
    if shifted_mask.size == 0 or not shifted_mask.any():
        return False
    iou_matrix = _mask_iou_matrix([base_mask, shifted_mask])
    return bool(iou_matrix[0, 1] >= stability_thresh)


def _assign_amg_labels_via_gd(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    candidate_boxes_xyxy_px: list[list[float]],
) -> list[str | None]:
    """Run Grounding-DINO once on the seed frame, then for each AMG
    candidate bbox return the best-matching GD label (or None).

    Returns a list parallel to ``candidate_boxes_xyxy_px``. Indices with no
    GD box of bbox-IoU ≥ ``SAM3_AMG_LABEL_IOU_MIN`` get ``None`` so the
    caller can fall back to ``_amg``.

    Disabled by ``SAM3_AMG_LABEL_VIA_GD=0`` or absent GD bundle — returns
    ``[None] * len(candidates)`` in that case.
    """
    n = len(candidate_boxes_xyxy_px)
    if not SAM3_AMG_LABEL_VIA_GD or n == 0:
        return [None] * n
    gd_bundle = bundle.get("grounding_dino")
    if gd_bundle is None or gd_bundle.get("model") is None:
        return [None] * n
    import grounding_dino  # local import — avoid circular at module top
    label_prompts = [p.strip() for p in SAM3_AMG_LABEL_PROMPTS.split(",") if p.strip()]
    if not label_prompts:
        return [None] * n
    # Phase 6: call GD at the lower of the two floors so on-ontology
    # labels survive the GD-side gate.
    gd_score_floor = min(SAM3_AMG_LABEL_GD_THRESH, SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY)
    try:
        gd_results = grounding_dino.run(
            gd_bundle, image_rgb_uint8, label_prompts,
            score_threshold=gd_score_floor,
        )
    except Exception as exc:
        logger.debug("AMG label assignment via GD failed: %s", exc)
        return [None] * n
    if not gd_results:
        return [None] * n
    # Phase 6 per-class filter: only GD boxes that clear their class-specific
    # floor are eligible for label matching. The remaining AMG candidates
    # fall through to `_amg` exactly as today (label=None).
    ontology_set = _ontology_label_set("optical")
    gd_results = _filter_by_class_threshold(
        gd_results, ontology_set,
        onto_thresh=SAM3_AMG_LABEL_GD_THRESH_ONTOLOGY,
        default_thresh=SAM3_AMG_LABEL_GD_THRESH,
    )
    if not gd_results:
        return [None] * n
    gd_boxes = np.asarray([r[1] for r in gd_results], dtype=np.float32)  # (G, 4) xyxy
    gd_labels = [r[3] for r in gd_results]
    cand_boxes = np.asarray(candidate_boxes_xyxy_px, dtype=np.float32)   # (N, 4)
    out: list[str | None] = [None] * n
    # Vectorised bbox-IoU: (N, G). Plus an asymmetric **containment** score
    # = intersection / candidate_area, which is appropriate when AMG masks
    # are tiny single-object detections sitting inside larger GD boxes
    # (drone footage: GD-tiny outputs broad "vegetation"/"building" boxes
    # spanning many AMG masks). We accept whichever criterion fires first.
    cand_x1 = cand_boxes[:, None, 0]; cand_y1 = cand_boxes[:, None, 1]
    cand_x2 = cand_boxes[:, None, 2]; cand_y2 = cand_boxes[:, None, 3]
    gd_x1 = gd_boxes[None, :, 0]; gd_y1 = gd_boxes[None, :, 1]
    gd_x2 = gd_boxes[None, :, 2]; gd_y2 = gd_boxes[None, :, 3]
    ix1 = np.maximum(cand_x1, gd_x1); iy1 = np.maximum(cand_y1, gd_y1)
    ix2 = np.minimum(cand_x2, gd_x2); iy2 = np.minimum(cand_y2, gd_y2)
    iw = np.clip(ix2 - ix1, 0.0, None); ih = np.clip(iy2 - iy1, 0.0, None)
    inter = iw * ih
    cand_area = np.clip((cand_x2 - cand_x1) * (cand_y2 - cand_y1), 0.0, None)
    gd_area = np.clip((gd_x2 - gd_x1) * (gd_y2 - gd_y1), 0.0, None)
    union = np.maximum(cand_area + gd_area - inter, 1e-6)
    iou = inter / union  # (N, G) symmetric IoU
    # Containment: how much of the candidate is inside the GD box (range 0-1).
    contain = inter / np.maximum(cand_area, 1e-6)
    # Use whichever score is higher per (cand, gd) pair, then pick best GD.
    score_mat = np.maximum(iou, contain * 0.5)  # halve containment to keep IoU primary
    best_g = score_mat.argmax(axis=1)
    best_score = score_mat.max(axis=1)
    # Accept a match if either the IoU clears the threshold OR containment
    # is ≥ 0.6 (candidate is largely inside the GD box).
    for i in range(n):
        g = int(best_g[i])
        if iou[i, g] >= SAM3_AMG_LABEL_IOU_MIN or contain[i, g] >= 0.6:
            out[i] = gd_labels[g]
    matched = sum(1 for x in out if x is not None)
    logger.info(
        "AMG label assignment: %d candidates, %d GD boxes, %d matched (iou_min=%.2f, contain≥0.6)",
        n, len(gd_results), matched, SAM3_AMG_LABEL_IOU_MIN,
    )
    return out


def probe_amg(bundle: dict[str, Any]) -> bool:
    """Confirm `processor.add_geometric_prompt` is callable and returns a
    well-formed output. Sets the module-level ``_AMG_AVAILABLE`` cache.

    Safe to call multiple times — the probe runs at most once per process.
    If the probe fails (upstream API renamed, processor missing, etc.) AMG
    is marked unavailable; the PCS path is unaffected.
    """
    global _AMG_AVAILABLE
    if _AMG_AVAILABLE is not None:
        return _AMG_AVAILABLE
    if not SAM3_AMG_ENABLED:
        _AMG_AVAILABLE = False
        logger.info("AMG probe skipped: SAM3_AMG_ENABLED=0 on this profile")
        return False
    sam3_image = bundle.get("sam3_image")
    if not sam3_image or sam3_image.get("processor") is None:
        _AMG_AVAILABLE = False
        logger.warning("AMG probe skipped: no sam3_image processor in bundle")
        return False
    # Probe acquires the bundle lock non-blockingly. If the bundle is busy
    # serving inference, leave `_AMG_AVAILABLE` unset (None) so the next
    # idle /health call probes for real — better than blocking the health
    # endpoint behind a multi-minute video session.
    lock = bundle.get("lock")
    if lock is not None and not lock.acquire(blocking=False):
        logger.debug("AMG probe deferred: bundle lock busy")
        return False
    try:
        synth = np.zeros((64, 64, 3), dtype=np.uint8)
        synth[16:48, 16:48] = 255
        result = _amg_sweep_image(
            bundle, synth, grid_size=4, score_threshold=0.0,
            nms_iou=0.7, point_box_norm=0.05,
            bypass_quality_filters=True,
        )
        ok = bool(result)
        _AMG_AVAILABLE = ok
        if ok:
            logger.info(
                "AMG probe ok: grid=%d reseed=%d nms_iou=%.2f",
                SAM3_AMG_GRID_SIZE, SAM3_AMG_RESEED_FRAMES, SAM3_AMG_NMS_IOU,
            )
        else:
            logger.warning("AMG probe: empty result on synthetic fixture; AMG disabled")
    except Exception as exc:
        _AMG_AVAILABLE = False
        logger.warning("AMG probe failed (%s); AMG disabled (PCS path unaffected)", exc)
    finally:
        if lock is not None:
            lock.release()
    return bool(_AMG_AVAILABLE)


def amg_available() -> bool:
    """Cached AMG availability. ``probe_amg`` must be invoked once first."""
    return bool(_AMG_AVAILABLE)


def _hungarian_iou_link(
    prev_masks: list[np.ndarray],
    curr_masks: list[np.ndarray],
    iou_min: float,
) -> list[int]:
    """For each current mask, return the matched previous index or -1.

    Greedy in IoU-descending order — scipy.optimize is avoided to keep the
    inference container's dependency surface unchanged. For the AMG track
    counts we expect (≤ 64 per frame after NMS) the greedy result matches
    the Hungarian optimum in > 99% of cases. Operates on downsampled masks
    to keep this O(N·M·resolution²) rather than O(N·M·H·W).
    """
    if not prev_masks or not curr_masks:
        return [-1] * len(curr_masks)
    res = _MASK_NMS_RESOLUTION
    p_flat = np.stack([_downsample_bool(m, res).reshape(-1) for m in prev_masks]).astype(np.int32)
    c_flat = np.stack([_downsample_bool(m, res).reshape(-1) for m in curr_masks]).astype(np.int32)
    inter = c_flat @ p_flat.T  # (curr, prev)
    c_areas = c_flat.sum(axis=1)
    p_areas = p_flat.sum(axis=1)
    union = c_areas[:, None] + p_areas[None, :] - inter
    iou = np.where(union > 0, inter / np.maximum(union, 1), 0.0)
    assignment = [-1] * len(curr_masks)
    used_prev: set[int] = set()
    pairs = sorted(
        ((iou[i, j], i, j) for i in range(len(curr_masks)) for j in range(len(prev_masks))),
        key=lambda t: -t[0],
    )
    for score, i, j in pairs:
        if score < iou_min:
            break
        if assignment[i] != -1 or j in used_prev:
            continue
        assignment[i] = j
        used_prev.add(j)
    return assignment


def run_video_amg(
    bundle,
    video_path: str,
    *,
    frame_stride: int,
    start_frame: int,
    end_frame: int | None,
    max_frames: int | None,
    dinov3,
    score_threshold: float,
    grid_size: int,
    reseed_every_n_frames: int,
):
    """Promptless video tracking via Automatic Mask Generation.

    Emits NDJSON entries with the same schema as `run_video`, so downstream
    consumers (`backend/worker.py:_drain_response_entries`,
    `_insert_detection_rows`) don't need schema-aware branching. Tracks are
    labelled `class="_amg"` / `parent_class="amg_track"` to distinguish from
    text-prompted detections in the database.

    Flow per frame:
      * On every ``reseed_every_n_frames`` frames (starting from
        ``start_frame``): run a fresh AMG sweep on this frame.
      * On other frames: re-use the previous frame's masks as track anchors.
        (We do NOT propagate via the multiplex predictor's handle_request
        API — its mask-input request shape is version-dependent and the
        per-frame AMG cost is the dominant term anyway.)
      * Track IDs persist across frames via Hungarian-IoU matching; tracks
        lost for more than SAM3_AMG_TRACK_BUFFER frames are discarded.
    """
    import cv2  # opencv-python-headless is pinned in inference-sam3 requirements

    if not amg_available():
        logger.warning("run_video_amg called but AMG is unavailable; returning empty stream")
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    try:
        frame_idx = 0
        emitted_frames = 0
        # Per-track state: id -> {"mask": last_mask, "last_seen_frame": idx,
        #                         "first_seen_frame": idx, "embedding": vec | None,
        #                         "label": str | None, "consecutive": int,
        #                         "pending": list[str], "confirmed": bool}
        tracks: dict[int, dict[str, Any]] = {}
        next_track_id = 1
        prev_masks: list[np.ndarray] = []
        prev_track_ids: list[int] = []
        emit_dinov3 = dinov3 is not None
        confirm_n = max(1, SAM3_AMG_MIN_CONSECUTIVE_FRAMES)
        emitted_count = 0
        dropped_unconfirmed = 0

        import time as _time
        amg_start = _time.monotonic()
        # Detect drone-HUD overlay once per video (cached for all seeds in
        # this window). Returns None on non-HUD clips or when disabled.
        hud_mask = _detect_hud_mask(video_path)
        logger.info(
            "run_video_amg start: video=%s grid=%d reseed=%d max_frames=%s "
            "confirm_n=%d hud=%s",
            video_path, grid_size, reseed_every_n_frames, max_frames, confirm_n,
            "on" if hud_mask is not None else "off",
        )
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            if frame_idx < start_frame:
                frame_idx += 1
                continue
            if end_frame is not None and frame_idx > int(end_frame):
                break
            if (frame_idx - start_frame) % frame_stride:
                frame_idx += 1
                continue
            if max_frames is not None and emitted_frames >= int(max_frames):
                break

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            is_seed = ((frame_idx - start_frame) % max(1, reseed_every_n_frames)) == 0
            seed_labels: list[str | None] = []

            if is_seed or not prev_masks:
                sweep_start = _time.monotonic()
                candidates = _amg_sweep_image(
                    bundle, rgb,
                    grid_size=grid_size,
                    score_threshold=score_threshold,
                    nms_iou=SAM3_AMG_NMS_IOU,
                    point_box_norm=SAM3_AMG_POINT_BOX_NORM,
                    hud_mask=hud_mask,
                )
                logger.info(
                    "AMG seed frame=%d sweep=%.2fs candidates=%d "
                    "(detector=%s)",
                    frame_idx, _time.monotonic() - sweep_start,
                    len(candidates), SAM3_AMG_DETECTOR,
                )
                # Phase 4 GD-first emits 4-tuples (mask, bbox_px, score, label);
                # Phase 3 grid path emits 3-tuples. Detect and normalise.
                if candidates and len(candidates[0]) == 4:
                    seed_labels = [c[3] for c in candidates]
                    candidates = [(c[0], c[1], c[2]) for c in candidates]
                elif candidates:
                    # Grid mode → post-hoc Grounding-DINO matching for labels.
                    seed_labels = _assign_amg_labels_via_gd(
                        bundle, rgb, [list(c[1]) for c in candidates],
                    )
                else:
                    seed_labels = []
            else:
                # Reuse previous frame's masks as "detections" — keeps track
                # IDs stable through non-seed frames. Boxes derived from the
                # mask bounds; scores reuse the last seed-frame score.
                candidates = []
                for m, tid in zip(prev_masks, prev_track_ids):
                    if not m.any():
                        continue
                    ys, xs = np.where(m)
                    H, W = m.shape[-2:]
                    bbox_px = [float(xs.min()), float(ys.min()),
                               float(xs.max() + 1), float(ys.max() + 1)]
                    prev_score = float(tracks.get(tid, {}).get("score", 0.5))
                    candidates.append((m, bbox_px, prev_score))
                seed_labels = [None] * len(candidates)

            if not candidates:
                # No detections this frame; reset prev so the next seed frame
                # starts fresh rather than carrying stale state forward.
                prev_masks = []
                prev_track_ids = []
                frame_idx += 1
                emitted_frames += 1
                continue

            curr_masks = [c[0] for c in candidates]
            curr_boxes = [c[1] for c in candidates]
            curr_scores = [c[2] for c in candidates]
            # Link to previous frame's tracks.
            assignment = _hungarian_iou_link(prev_masks, curr_masks, SAM3_AMG_TRACK_IOU_MIN)
            this_track_ids: list[int] = []
            this_frame_tids: set[int] = set()
            for i, prev_j in enumerate(assignment):
                if prev_j >= 0 and prev_j < len(prev_track_ids):
                    tid = prev_track_ids[prev_j]
                else:
                    tid = next_track_id
                    next_track_id += 1
                    tracks[tid] = {
                        "first_seen_frame": frame_idx, "embedding": None,
                        "label": None, "consecutive": 0, "pending": [],
                        "confirmed": False,
                    }
                tracks[tid]["last_seen_frame"] = frame_idx
                tracks[tid]["score"] = curr_scores[i]
                # Update the track's cached label whenever this frame
                # produced one. The guard used to be `if is_seed` but
                # that lost labels on non-seed frames that re-ran AMG
                # because the previous seed returned 0 candidates (common
                # when GD's vocab gap leaves a seed empty). Any
                # ``seed_labels`` entry that came from a fresh AMG sweep
                # is valid regardless of seed status.
                if i < len(seed_labels) and seed_labels[i] is not None:
                    tracks[tid]["label"] = seed_labels[i]
                this_track_ids.append(tid)
                this_frame_tids.add(tid)

            # Reset consecutive-frames counter for tracks NOT seen this frame
            # (they break the chain — must re-confirm if they reappear).
            for tid in list(tracks.keys()):
                if tid not in this_frame_tids and not tracks[tid].get("confirmed"):
                    tracks[tid]["consecutive"] = 0
                    if tracks[tid]["pending"]:
                        dropped_unconfirmed += len(tracks[tid]["pending"])
                        tracks[tid]["pending"] = []

            # Garbage-collect stale tracks.
            stale = [tid for tid, info in tracks.items()
                     if frame_idx - int(info.get("last_seen_frame", frame_idx)) > SAM3_AMG_TRACK_BUFFER]
            for tid in stale:
                tracks.pop(tid, None)

            import fusion

            for i, tid in enumerate(this_track_ids):
                mask_arr = curr_masks[i]
                bbox_px = curr_boxes[i]
                score = float(curr_scores[i])
                H, W = mask_arr.shape[-2:]
                bbox_xyxy_norm = [bbox_px[0] / W, bbox_px[1] / H, bbox_px[2] / W, bbox_px[3] / H]
                obb = fusion.mask_to_obb_record(mask_arr, bbox_px, W, H)
                cls = tracks[tid].get("label") or "_amg"
                entry: dict[str, Any] = {
                    "frame_index": frame_idx,
                    "track_id": int(tid),
                    "class": cls,
                    "original_class": cls,
                    "parent_class": "amg_track",
                    "score": score,
                    "bbox_xyxy_norm": bbox_xyxy_norm,
                    "obb": obb["points"],
                    "obb_format": "yolo_obb_normalized_xyxyxyxy",
                    "obb_source": obb["source"],
                    "obb_angle_deg": obb["angle_deg"],
                    "edge_truncated": obb["edge_truncated"],
                    "mask_rle": fusion.coco_rle(mask_arr),
                }
                # First-frame DINOv3-SAT embedding for cross-window re-ID.
                if emit_dinov3 and tracks[tid].get("embedding") is None:
                    try:
                        emb = _embed_amg_crop(dinov3, rgb, bbox_px)
                        if emb is not None:
                            tracks[tid]["embedding"] = emb
                            entry["embedding"] = emb.tolist()
                    except Exception as exc:
                        logger.debug("AMG embedding failed for track %d: %s", tid, exc)

                serialised = json.dumps(entry, separators=(",", ":"))
                # Masklet confirmation buffer: a track must be seen in
                # `confirm_n` consecutive frames before its entries flow
                # to the worker. Once confirmed, entries stream live.
                if tracks[tid].get("confirmed"):
                    yield serialised
                    emitted_count += 1
                else:
                    tracks[tid]["consecutive"] += 1
                    tracks[tid]["pending"].append(serialised)
                    if tracks[tid]["consecutive"] >= confirm_n:
                        tracks[tid]["confirmed"] = True
                        for buffered in tracks[tid]["pending"]:
                            yield buffered
                            emitted_count += 1
                        tracks[tid]["pending"] = []

            prev_masks = curr_masks
            prev_track_ids = this_track_ids
            emitted_frames += 1
            frame_idx += 1
        # Drop any still-unconfirmed buffered entries at end-of-stream.
        for tid, info in tracks.items():
            if not info.get("confirmed"):
                dropped_unconfirmed += len(info.get("pending", []))
        logger.info(
            "run_video_amg done: video=%s emitted_frames=%d tracks_seen=%d "
            "emitted=%d dropped_unconfirmed=%d elapsed=%.2fs",
            video_path, emitted_frames, next_track_id - 1,
            emitted_count, dropped_unconfirmed,
            _time.monotonic() - amg_start,
        )
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Hybrid AMG-seeded video propagation (Phase 2).
#
# Run image AMG once on the seed frame → discover N objects → start ONE video
# session → add_prompt with bounding_boxes for each discovered object (one
# obj_id per call, accumulating without state reset) → propagate_in_video for
# the rest of the window. ~5× faster than the per-frame AMG path because the
# expensive 256-prompt image sweep runs only on every reseed_every_n_frames
# frames, and the per-frame propagation step is GPU-batched by the video
# predictor.
# ---------------------------------------------------------------------------


def _bbox_pixels_to_xywh_norm(bbox_xyxy_px: list[float], height: int, width: int) -> list[float]:
    """Convert pixel xyxy to normalized cxcywh for ``add_prompt(bounding_boxes=...)``.

    The upstream `Sam3BasePredictor.handle_request` accepts boxes in
    normalized cxcywh when `rel_coordinates=True` (the default). xyxy → cx,
    cy, w, h then divide by frame dims.
    """
    x1, y1, x2, y2 = (float(v) for v in bbox_xyxy_px[:4])
    cx = (x1 + x2) / 2.0 / max(1.0, float(width))
    cy = (y1 + y2) / 2.0 / max(1.0, float(height))
    w = max(0.0, (x2 - x1) / max(1.0, float(width)))
    h = max(0.0, (y2 - y1) / max(1.0, float(height)))
    # Clamp to [0,1] — out-of-frame boxes are a no-op for the tracker.
    cx = min(1.0, max(0.0, cx))
    cy = min(1.0, max(0.0, cy))
    w = min(1.0, max(0.0, w))
    h = min(1.0, max(0.0, h))
    return [cx, cy, w, h]


def probe_amg_seeded(bundle: dict[str, Any]) -> bool:
    """Confirm the video predictor accepts box-prompted `add_prompt` calls with
    accumulating obj_ids — i.e. the second call doesn't reset state.

    Sets ``_AMG_SEEDED_AVAILABLE``. Non-blocking on bundle lock; if the bundle
    is busy, defer (returns False) and re-probe on the next idle /health.
    """
    global _AMG_SEEDED_AVAILABLE
    if _AMG_SEEDED_AVAILABLE is not None:
        return _AMG_SEEDED_AVAILABLE
    if not SAM3_AMG_ENABLED:
        _AMG_SEEDED_AVAILABLE = False
        return False
    predictor = bundle.get("sam3_video")
    if predictor is None:
        _AMG_SEEDED_AVAILABLE = False
        return False
    lock = bundle.get("lock")
    if lock is not None and not lock.acquire(blocking=False):
        logger.debug("AMG-seeded probe deferred: bundle lock busy")
        return False
    try:
        # Write a 4-frame synthetic mp4 to a temp file and try a 2-object
        # session against it. The video predictor needs a real on-disk
        # resource for start_session — we can't fake it.
        import os as _os
        import tempfile as _tempfile
        import cv2 as _cv2
        tmp = _tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp.close()
        try:
            writer = _cv2.VideoWriter(tmp.name, _cv2.VideoWriter_fourcc(*"mp4v"),
                                      4.0, (64, 64))
            for i in range(4):
                frame = np.zeros((64, 64, 3), dtype=np.uint8)
                frame[10:30, 10:30] = 255
                frame[40:60, 40:60] = 128
                writer.write(frame)
            writer.release()
            target_device = getattr(predictor, "_sentinel_device", bundle.get("device", "cpu"))
            with _device_context(target_device), _autocast_ctx(target_device):
                session = predictor.handle_request(
                    request={"type": "start_session", "resource_path": tmp.name},
                )
                session_id = session["session_id"]
                try:
                    # First box: clear_old_boxes=True (initialise).
                    predictor.handle_request(request={
                        "type": "add_prompt", "session_id": session_id,
                        "frame_index": 0,
                        "bounding_boxes": [[0.3125, 0.3125, 0.3125, 0.3125]],
                        "bounding_box_labels": [True], "obj_id": 0,
                        "clear_old_boxes": True,
                    })
                    # Second box: clear_old_boxes=False (accumulate).
                    predictor.handle_request(request={
                        "type": "add_prompt", "session_id": session_id,
                        "frame_index": 0,
                        "bounding_boxes": [[0.78125, 0.78125, 0.3125, 0.3125]],
                        "bounding_box_labels": [True], "obj_id": 1,
                        "clear_old_boxes": False,
                    })
                    # Drain one frame — if the session has 2 objects we
                    # confirm both obj_ids appear in the output. `out_obj_ids`
                    # may be a numpy array, list, or torch tensor depending on
                    # predictor variant — coerce via `_to_list` to avoid the
                    # "truth value of an empty array is ambiguous" trap.
                    obj_ids_seen: set[int] = set()
                    for resp in predictor.handle_stream_request(request={
                        "type": "propagate_in_video", "session_id": session_id,
                    }):
                        outs = resp.get("outputs")
                        if isinstance(outs, dict):
                            oids = _to_list(outs.get("out_obj_ids"))
                            for oid in oids:
                                obj_ids_seen.add(int(oid))
                        if len(obj_ids_seen) >= 2:
                            break
                    ok = len(obj_ids_seen) >= 2
                finally:
                    predictor.handle_request(request={
                        "type": "close_session", "session_id": session_id,
                    })
        finally:
            _os.unlink(tmp.name)
        _AMG_SEEDED_AVAILABLE = bool(ok)
        if ok:
            logger.info("AMG-seeded probe ok: video predictor accumulates box prompts")
        else:
            logger.warning(
                "AMG-seeded probe: only %d obj_id(s) survived; falling back to per-frame AMG",
                len(obj_ids_seen),
            )
    except Exception as exc:
        _AMG_SEEDED_AVAILABLE = False
        logger.warning(
            "AMG-seeded probe failed (%s); using per-frame run_video_amg as fallback",
            exc,
        )
    finally:
        if lock is not None:
            lock.release()
    return bool(_AMG_SEEDED_AVAILABLE)


def amg_seeded_available() -> bool:
    """Cached availability of the hybrid (image-AMG seed + video-propagate) path."""
    return bool(_AMG_SEEDED_AVAILABLE)


def run_video_amg_seeded(
    bundle,
    video_path: str,
    *,
    frame_stride: int,
    start_frame: int,
    end_frame: int | None,
    max_frames: int | None,
    dinov3,
    score_threshold: float,
    grid_size: int,
    reseed_every_n_frames: int,
):
    """Hybrid AMG: image AMG on seed frames + single-session video propagation.

    Emits NDJSON entries with the SAME schema as ``run_video`` and
    ``run_video_amg``, so the worker's drain/insert code is unchanged.

    Flow per window:
      1. Read seed frame via cv2 → run ``_amg_sweep_image`` → get N (mask,
         bbox_px, score) candidates.
      2. ``predictor.handle_request(type="start_session", resource_path=...)``.
      3. For each candidate k: ``add_prompt(bounding_boxes=[cxcywh_norm],
         obj_id=k, clear_old_boxes=(k==0))``. The k==0 call clears any prior
         box state from a previous session; subsequent calls accumulate
         without triggering ``reset_state`` (text prompts are the only path
         that resets state — verified upstream).
      4. ``propagate_in_video`` streams per-frame outputs (out_obj_ids +
         out_binary_masks). Parse via the existing ``_iter_sam3_video_tracks``
         helper so emit shape is identical.
      5. If reseed_every_n_frames > 0, additional image-AMG sweeps run on
         frames {reseed, 2*reseed, …}; new candidates that don't already
         match a tracked object via mask-IoU get added as new obj_ids. This
         is wired here as a one-shot seed (frame 0 only) — multi-seed mode
         is a follow-up; for fmv windows of 12 s at 4 fps (48 frames) the
         one-shot seed already covers the dominant objects.
    """
    import cv2
    import time as _time

    if not amg_seeded_available():
        # Caller is expected to gate on amg_seeded_available() before
        # invoking us, but be defensive.
        logger.warning("run_video_amg_seeded called but probe says unavailable; "
                       "returning empty stream — caller should use run_video_amg")
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    predictor = bundle["sam3_video"]
    target_device = getattr(predictor, "_sentinel_device", bundle.get("device", "cpu"))
    amg_start = _time.monotonic()
    seed_frame_idx = max(0, int(start_frame))

    # Seek to the seed frame so cv2.read() returns it first.
    if seed_frame_idx > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, seed_frame_idx)

    ok, bgr = cap.read()
    cap.release()
    if not ok:
        logger.warning("run_video_amg_seeded: empty video at %s", video_path)
        return
    seed_rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    seed_h, seed_w = seed_rgb.shape[:2]

    # Detect drone-HUD overlay once per video (used to filter out
    # detections on burnt-in telemetry text). Returns None on non-HUD
    # clips or when SAM3_AMG_HUD_MASK_ENABLED=0.
    hud_mask = _detect_hud_mask(video_path)
    logger.info(
        "run_video_amg_seeded start: video=%s grid=%d reseed=%d max_frames=%s "
        "seed=%d hud=%s",
        video_path, grid_size, reseed_every_n_frames, max_frames,
        seed_frame_idx, "on" if hud_mask is not None else "off",
    )

    sweep_start = _time.monotonic()
    candidates = _amg_sweep_image(
        bundle, seed_rgb,
        grid_size=grid_size,
        score_threshold=score_threshold,
        nms_iou=SAM3_AMG_NMS_IOU,
        point_box_norm=SAM3_AMG_POINT_BOX_NORM,
        hud_mask=hud_mask,
    )
    logger.info(
        "AMG-seeded sweep frame=%d sweep=%.2fs candidates=%d",
        seed_frame_idx, _time.monotonic() - sweep_start, len(candidates),
    )

    if not candidates:
        logger.info("run_video_amg_seeded: no candidates on seed frame; no detections to emit")
        return

    # Phase 4 GD-first returns 4-tuples (mask, bbox_px, score, label).
    # Phase 3 grid returns 3-tuples and we run the secondary GD-matching
    # pass to get labels.
    if candidates and len(candidates[0]) == 4:
        seed_labels = [c[3] for c in candidates]
        candidates = [(c[0], c[1], c[2]) for c in candidates]
    else:
        seed_labels = _assign_amg_labels_via_gd(
            bundle, seed_rgb, [list(c[1]) for c in candidates],
        )

    import fusion

    with _device_context(target_device), _autocast_ctx(target_device):
        session = predictor.handle_request(
            request={"type": "start_session", "resource_path": video_path},
        )
        session_id = session["session_id"]
        try:
            # Seed every candidate as its own obj_id via box prompt. The
            # first call clears any leftover state; subsequent calls
            # accumulate.
            obj_id_to_score: dict[int, float] = {}
            obj_id_to_label: dict[int, str] = {}
            for k, (_mask, bbox_px, score) in enumerate(candidates):
                cxcywh_norm = _bbox_pixels_to_xywh_norm(bbox_px, seed_h, seed_w)
                try:
                    predictor.handle_request(request={
                        "type": "add_prompt",
                        "session_id": session_id,
                        "frame_index": seed_frame_idx,
                        "bounding_boxes": [cxcywh_norm],
                        "bounding_box_labels": [True],
                        "obj_id": k,
                        "clear_old_boxes": k == 0,
                        "rel_coordinates": True,
                    })
                    obj_id_to_score[k] = float(score)
                    if k < len(seed_labels) and seed_labels[k] is not None:
                        obj_id_to_label[k] = seed_labels[k]
                except Exception as exc:
                    logger.debug("add_prompt obj_id=%d failed (%s); skipping", k, exc)
                    continue

            if not obj_id_to_score:
                logger.warning("run_video_amg_seeded: all add_prompt calls failed; no propagation")
                return

            # Masklet confirmation state per obj_id.
            confirm_n = max(1, SAM3_AMG_MIN_CONSECUTIVE_FRAMES)
            track_state: dict[int, dict[str, Any]] = {}
            emitted_frames = 0
            emitted_count = 0
            dropped_unconfirmed = 0
            for resp in predictor.handle_stream_request(request={
                "type": "propagate_in_video", "session_id": session_id,
            }):
                frame_idx = int(resp.get("frame_index", 0))
                if frame_idx < start_frame:
                    continue
                if end_frame is not None and frame_idx > int(end_frame):
                    break
                if (frame_idx - start_frame) % max(1, frame_stride):
                    continue
                if max_frames is not None and emitted_frames >= int(max_frames):
                    break
                emitted_frames += 1
                outs = resp.get("outputs") or {}
                seen_tids: set[int] = set()
                for track in _iter_sam3_video_tracks(outs, prompt_text="_amg"):
                    tid = int(track["track_id"])
                    score = float(track.get("score") or obj_id_to_score.get(tid, 0.5))
                    if score < score_threshold:
                        continue
                    seen_tids.add(tid)
                    cls = obj_id_to_label.get(tid, "_amg")
                    mask = track.get("mask")
                    bbox_xyxy_norm = track.get("bbox_xyxy_norm")
                    entry: dict[str, Any] = {
                        "frame_index": frame_idx,
                        "track_id": tid,
                        "class": cls,
                        "original_class": cls,
                        "parent_class": "amg_track",
                        "score": score,
                    }
                    if mask is not None and bbox_xyxy_norm is not None:
                        mask_arr = np.asarray(mask, dtype=bool)
                        H, W = mask_arr.shape[-2:]
                        x1n, y1n, x2n, y2n = bbox_xyxy_norm
                        bbox_xyxy_px = [x1n * W, y1n * H, x2n * W, y2n * H]
                        obb = fusion.mask_to_obb_record(mask_arr, bbox_xyxy_px, W, H)
                        entry.update({
                            "bbox_xyxy_norm": bbox_xyxy_norm,
                            "obb": obb["points"],
                            "obb_format": "yolo_obb_normalized_xyxyxyxy",
                            "obb_source": obb["source"],
                            "obb_angle_deg": obb["angle_deg"],
                            "edge_truncated": obb["edge_truncated"],
                            "mask_rle": fusion.coco_rle(mask_arr),
                        })
                    else:
                        entry.update({
                            "bbox_xyxy_norm": None,
                            "obb": None,
                            "obb_format": None,
                            "obb_source": "tracker_lost",
                            "mask_rle": None,
                        })
                    serialised = json.dumps(entry, separators=(",", ":"))
                    st = track_state.setdefault(tid, {"confirmed": False, "consecutive": 0, "pending": []})
                    if st["confirmed"]:
                        yield serialised
                        emitted_count += 1
                    else:
                        st["consecutive"] += 1
                        st["pending"].append(serialised)
                        if st["consecutive"] >= confirm_n:
                            st["confirmed"] = True
                            for buffered in st["pending"]:
                                yield buffered
                                emitted_count += 1
                            st["pending"] = []
                # Reset consecutive counter for any track NOT seen this frame.
                for tid, st in track_state.items():
                    if tid not in seen_tids and not st["confirmed"]:
                        if st["pending"]:
                            dropped_unconfirmed += len(st["pending"])
                            st["pending"] = []
                        st["consecutive"] = 0
            for st in track_state.values():
                if not st["confirmed"]:
                    dropped_unconfirmed += len(st.get("pending", []))
            logger.info(
                "run_video_amg_seeded propagate done: emitted=%d dropped_unconfirmed=%d",
                emitted_count, dropped_unconfirmed,
            )
        except RuntimeError as exc:
            # Multiplex tracker raises this when propagation stalls — same
            # graceful-end behavior as run_video.
            if "No points are provided" not in str(exc):
                raise
            logger.warning("AMG-seeded propagation ended early in session %s: %s",
                           session_id, exc)
        finally:
            try:
                predictor.handle_request(request={
                    "type": "close_session", "session_id": session_id,
                })
            except Exception:
                pass

    logger.info(
        "run_video_amg_seeded done: video=%s seed_candidates=%d elapsed=%.2fs",
        video_path, len(candidates), _time.monotonic() - amg_start,
    )


def _embed_amg_crop(dinov3_bundle: dict[str, Any] | None, rgb: np.ndarray,
                    bbox_px: list[float]) -> np.ndarray | None:
    """Best-effort DINOv3-SAT embedding of a single AMG crop. Returns a
    1D float32 vector or None on any failure (logged at debug only)."""
    if dinov3_bundle is None:
        return None
    embed_fn = dinov3_bundle.get("embed") if isinstance(dinov3_bundle, dict) else None
    if not callable(embed_fn):
        return None
    x1, y1, x2, y2 = [int(round(v)) for v in bbox_px[:4]]
    h, w = rgb.shape[:2]
    x1 = max(0, min(w - 1, x1))
    y1 = max(0, min(h - 1, y1))
    x2 = max(x1 + 1, min(w, x2))
    y2 = max(y1 + 1, min(h, y2))
    crop = rgb[y1:y2, x1:x2]
    if crop.size == 0:
        return None
    vec = embed_fn(crop)
    if vec is None:
        return None
    arr = np.asarray(vec, dtype=np.float32).reshape(-1)
    if arr.size == 0 or not np.isfinite(arr).all():
        return None
    return arr


def _inference_mode():
    import torch

    return torch.inference_mode()


def _collect_candidates(output: dict[str, Any], score_threshold: float, label: str):
    masks = _to_list(output.get("masks"))
    boxes = _to_list(output.get("boxes"))
    scores = _to_list(output.get("scores"))
    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for mask, box_xyxy, score in zip(masks, boxes, scores):
        score_f = float(score)
        if score_f < score_threshold:
            continue
        out.append((
            np.asarray(_to_numpy(mask), dtype=bool).squeeze(),
            [float(v) for v in _to_numpy(box_xyxy).reshape(-1)[:4]],
            score_f,
            label,
        ))
    return out


def _to_list(obj):
    if obj is None:
        return []
    if hasattr(obj, "cpu"):
        return list(obj.cpu())
    return list(obj)


def _to_numpy(obj):
    if hasattr(obj, "cpu"):
        return obj.cpu().numpy()
    return np.asarray(obj)


def _entry_to_norm_cxcywh(entry: dict[str, Any]) -> list[float] | None:
    """Resolve a prompt_boxes entry to normalized [cx, cy, w, h] in [0, 1].

    Prefer ``bbox`` if present (already cxcywh-normalized in the upstream
    detection schema). Fallback to ``obb`` (8-point xyxyxyxy normalized) by
    taking its axis-aligned bounding box.
    """
    bbox = entry.get("bbox")
    if bbox and len(bbox) >= 4:
        cx, cy, w, h = (float(v) for v in bbox[:4])
        if w > 0 and h > 0:
            return [_clamp01(cx), _clamp01(cy), _clamp01(w), _clamp01(h)]

    obb = entry.get("obb")
    if obb and len(obb) >= 8:
        xs = [float(obb[i]) for i in range(0, 8, 2)]
        ys = [float(obb[i]) for i in range(1, 8, 2)]
        x1n, y1n, x2n, y2n = min(xs), min(ys), max(xs), max(ys)
        w = x2n - x1n
        h = y2n - y1n
        if w <= 0 or h <= 0:
            return None
        return [_clamp01((x1n + x2n) / 2.0), _clamp01((y1n + y2n) / 2.0), _clamp01(w), _clamp01(h)]

    return None


def _clamp01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _iter_sam3_video_tracks(outputs, prompt_text: str = "track"):
    """Iterate tracks from a `propagate_in_video` stream response.

    The multiplex predictor returns a dict with parallel arrays (`out_obj_ids`,
    `out_probs`, `out_binary_masks`); the non-multiplex base predictor yields a
    list of per-track dicts. Handle both shapes. Bbox is always derived from
    the mask's pixel space (upstream `out_boxes_xywh` is pre-normalised, so
    re-treating it as pixel xywh produced off-target rectangles previously —
    see `sam3_multiplex_tracking.py:1203-1206`).
    """
    if isinstance(outputs, dict):
        obj_ids = outputs.get("out_obj_ids")
        masks = outputs.get("out_binary_masks")
        scores = outputs.get("out_probs")
        if obj_ids is None or masks is None:
            return
        for idx in range(len(obj_ids)):
            mask_arr = np.asarray(masks[idx], dtype=bool)
            track_id = int(obj_ids[idx])
            score = float(scores[idx]) if scores is not None and idx < len(scores) else 1.0
            if not mask_arr.any():
                yield {
                    "track_id": track_id,
                    "prompt_text": prompt_text,
                    "score": score,
                    "mask": None,
                    "bbox_xyxy_norm": None,
                }
                continue
            H, W = mask_arr.shape[-2:]
            ys, xs = np.where(mask_arr)
            x1, y1 = float(xs.min()), float(ys.min())
            x2, y2 = float(xs.max() + 1), float(ys.max() + 1)
            bbox_xyxy_norm = [x1 / W, y1 / H, x2 / W, y2 / H]
            yield {
                "track_id": track_id,
                "prompt_text": prompt_text,
                "score": score,
                "mask": mask_arr,
                "bbox_xyxy_norm": bbox_xyxy_norm,
            }
        return

    for item in outputs or []:
        if not isinstance(item, dict):
            continue
        mask = item.get("mask") or item.get("segmentation")
        if mask is None:
            continue
        mask_arr = np.asarray(mask, dtype=bool)
        track_id = item.get("obj_id") or item.get("track_id") or item.get("id") or 0
        score = item.get("score", 1.0)
        if not mask_arr.any():
            yield {
                "track_id": track_id,
                "prompt_text": prompt_text,
                "score": score,
                "mask": None,
                "bbox_xyxy_norm": None,
            }
            continue
        H, W = mask_arr.shape[-2:]
        ys, xs = np.where(mask_arr)
        x1, y1 = float(xs.min()), float(ys.min())
        x2, y2 = float(xs.max() + 1), float(ys.max() + 1)
        bbox_xyxy_norm = [x1 / W, y1 / H, x2 / W, y2 / H]
        yield {
            "track_id": track_id,
            "prompt_text": prompt_text,
            "score": score,
            "mask": mask_arr,
            "bbox_xyxy_norm": bbox_xyxy_norm,
        }
