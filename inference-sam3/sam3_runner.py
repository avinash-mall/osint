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
SAM3_NATIVE_BF16 = os.getenv("SAM3_NATIVE_BF16", "0").strip().lower() in {"1", "true", "yes", "on"}
SAM3_SDPA_BACKEND = os.getenv("SAM3_SDPA_BACKEND", "auto").strip().lower()
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

# SegEarth-OV3-inspired presence ratio (arxiv 2512.08730). The existing
# SAM3_CATEGORY_THRESHOLD gate uses the max per-mask score across all
# candidates for a prompt. That catches obvious absence (low max) but
# misses the textbook hallucination pattern where SAM3 emits many
# uniformly mediocre masks that lift the mean close to the max — a
# diffuse "all this background kind of looks like X" response.
#
# Presence ratio = max_score / max(mean_score, EPS). A "real" detection
# is sharp/localized — max is well above mean. A hallucination is diffuse
# — max ≈ mean. Defaults: 1.8 — means max must be at least 80% higher
# than mean to keep the prompt.
SAM3_PRESENCE_RATIO_FLOOR = float(os.getenv("SAM3_PRESENCE_RATIO_FLOOR", "1.8"))
SAM3_PRESENCE_RATIO_EPS = float(os.getenv("SAM3_PRESENCE_RATIO_EPS", "0.05"))

# Mode selector: which gate(s) to apply. Backward-compat default is
# "both" (existing max-score gate AND new ratio gate must both pass).
# "max" = only the existing gate (legacy behaviour).
# "ratio" = only the new ratio gate (skip max-score check).
# An invalid value silently disables BOTH gates because neither branch
# matches; warn and fall back so a typo doesn't quietly turn off filtering.
_VALID_PRESENCE_MODES = ("max", "ratio", "both")
_raw_mode = os.getenv("SAM3_PRESENCE_MODE", "both").strip().lower()
if _raw_mode not in _VALID_PRESENCE_MODES:
    logger.warning(
        "SAM3_PRESENCE_MODE=%r is invalid (expected one of %s); falling back to 'both'",
        _raw_mode, _VALID_PRESENCE_MODES,
    )
    _raw_mode = "both"
SAM3_PRESENCE_MODE = _raw_mode


def _load_per_class_category_thresholds() -> dict[str, float]:
    """Per-class overrides of ``SAM3_CATEGORY_THRESHOLD``.

    Format: JSON dict mapping ``label`` → ``threshold``. Lookup is
    case/whitespace-insensitive via :func:`_canonical_prompt_key`. Classes
    absent from the map fall back to ``SAM3_CATEGORY_THR``.

    Why: the global 0.40 gate kills rare/small military classes
    (``self-propelled howitzer``, ``transporter erector launcher``,
    ``armoured personnel carrier``, …) whose best-chip score on DOTA-v1.0
    routinely sits at 0.15–0.30. Operators can drop just those prompts'
    floors without lowering the gate for civilian noise prompts.
    """
    raw = (os.getenv("SAM3_CATEGORY_THRESHOLDS_PER_CLASS") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SAM3_CATEGORY_THRESHOLDS_PER_CLASS is not valid JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in parsed.items():
        try:
            out[_canonical_prompt_key(key)] = float(value)
        except (TypeError, ValueError):
            continue
    if out:
        logger.info("SAM3 per-class category thresholds loaded: %d entries", len(out))
    return out


def _canonical_prompt_key(text: Any) -> str:
    """Canonicalise a prompt/class label for per-class threshold lookup."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = " ".join(s.split())  # collapse whitespace
    return s


_PER_CLASS_CATEGORY_THR: dict[str, float] = _load_per_class_category_thresholds()


_LOGGED_THRESHOLD_MISS: set[str] = set()


def _category_threshold_for(label: Any) -> float:
    """Return the category-presence threshold for a given prompt label.

    Falls back to the global ``SAM3_CATEGORY_THR`` when no per-class override
    is configured for the label. Logs the canonical key on the first miss
    per label so operators can see why an override appeared to be ignored.
    """
    if not _PER_CLASS_CATEGORY_THR:
        return SAM3_CATEGORY_THR
    key = _canonical_prompt_key(label)
    if key in _PER_CLASS_CATEGORY_THR:
        return _PER_CLASS_CATEGORY_THR[key]
    if key and key not in _LOGGED_THRESHOLD_MISS:
        _LOGGED_THRESHOLD_MISS.add(key)
        print(
            f"[sam3_runner] no per-class category threshold for canonical key "
            f"{key!r}; falling back to SAM3_CATEGORY_THR={SAM3_CATEGORY_THR}"
        )
    return SAM3_CATEGORY_THR


# Number of frames the SAM3 multiplex tracker buffers internally before its
# hotstart unmatched/duplicate suppression activates. Mirror this on the
# emit-side so the video category gate has at least this many scores to
# evaluate before deciding whether the prompt's concept is present in the
# scene. Matches the upstream Sam3VideoConfig default.
SAM3_HOTSTART_DELAY_FRAMES = max(1, int(os.getenv("SAM3_HOTSTART_DELAY_FRAMES", "15")))
PROMPT_TEMPLATE = os.getenv("SAM3_PROMPT_TEMPLATE", "{label}")
_QUERY_IDS = itertools.count(1)

# ---------------------------------------------------------------------------
# Cross-frame tracker knobs — shared by the YOLOE FMV tracker. AMG mode was
# removed (SAM 3 cannot emit labels without a text prompt; YOLO 26 covers the
# promptless workflow), but the Hungarian-IoU linking + masklet-confirmation
# pattern is generic. ``SAM3_AMG_*`` env-var names are accepted as fallbacks
# for back-compat with existing ``.env`` files written by configure_host.
# ---------------------------------------------------------------------------
SAM3_TRACK_IOU_MIN = float(
    os.getenv("SAM3_TRACK_IOU_MIN", os.getenv("SAM3_AMG_TRACK_IOU_MIN", "0.30"))
)
SAM3_TRACK_BUFFER = max(1, int(
    os.getenv("SAM3_TRACK_BUFFER", os.getenv("SAM3_AMG_TRACK_BUFFER", "12"))
))
SAM3_TRACK_MIN_CONSECUTIVE_FRAMES = max(1, int(
    os.getenv(
        "SAM3_TRACK_MIN_CONSECUTIVE_FRAMES",
        os.getenv("SAM3_AMG_MIN_CONSECUTIVE_FRAMES", "2"),
    )
))


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

    # Build under the replica's CUDA device context. build_sam3_image_model
    # honours `device=` for parameters/buffers, but the model also creates
    # non-param tensors — notably the vision positional encodings that
    # _get_img_feats indexes (`vis_pos_enc`) — on the *current* CUDA device,
    # which defaults to cuda:0. Without this context every replica's pos-enc
    # lands on cuda:0 while `.to(device)` moves the weights to the replica's
    # GPU, so on multi-GPU hosts _get_img_feats indexes a cuda:0 vis_pos_enc
    # with cuda:N img_ids and dies ("indices ... same device"). Mirrors the
    # build_video device-context fix. See
    # docs/decisions/cached-forward-device-normalise.md.
    with _device_context(device):
        model = build_sam3_image_model(
            device=device,
            compile=SAM3_COMPILE_IMAGE,
            checkpoint_path=checkpoint_path,
            load_from_HF=load_from_hf,
        ).to(device).eval()
        if SAM3_NATIVE_BF16 and device.startswith("cuda"):
            # Cast vision + text encoders + decoder to bf16. The legacy fp32-
            # text-encoder pin was only needed because Flash-Attention dislikes
            # fp32; we're on SDPA so this is safe. mlx-community/sam3-bf16 ships
            # a fully-bf16 SAM3 checkpoint as precedent for quality.
            import torch as _torch_bf16
            try:
                model = model.to(_torch_bf16.bfloat16)
                logger.info("SAM3 image model cast to native bfloat16")
            except Exception as exc:
                logger.warning("SAM3 native bf16 cast failed (%s); staying fp32", exc)
    _install_sam3_perf_patches()
    return {"model": model, "processor": Sam3Processor(model, device=device)}


def _install_sam3_perf_patches() -> None:
    """Install runtime patches that enable the cached-encoder fast path.

    Idempotent. Safe across replica builds. Failures are logged and ignored
    so the service starts even if upstream changed the patched class.
    """
    try:
        from patches.sam3_cached_forward import install as _install
        _install()
    except Exception as exc:
        logger.warning("sam3_cached_forward patch failed to install: %s", exc)


def _cached_batched_supported(bundle: dict[str, Any]) -> bool:
    """True iff the Sam3Image cached-forward monkey-patch is live."""
    try:
        from patches.sam3_cached_forward import is_installed
        return bool(is_installed())
    except Exception:
        return False


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

    Functionally equivalent to `_device_ctx` defined below. They differ
    only in docstring emphasis: this one is for predictor build / video
    session lifecycle; `_device_ctx` is for per-forward-pass thread-local
    pinning under anyio threadpool reuse. A future refactor should
    collapse them to a single helper.
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
            is_oom = isinstance(exc, RuntimeError) and (
                "CUDA out of memory" in exc_text
                or "out of memory" in exc_text.lower()
            )
            cuda_poisoned = isinstance(exc, RuntimeError) and not is_oom and (
                "CUBLAS_STATUS" in exc_text
                or "CUDA error" in exc_text
                or "cuDNN error" in exc_text
            )
            if is_oom:
                # OOM during multiplex warmup is recoverable: the cuBLAS
                # handle is still valid, just out of VRAM headroom. Clear
                # caches and fall through to the non-multiplex predictor,
                # which has a smaller activation footprint and routinely
                # fits where multiplex does not. Avoids the spurious
                # process restart that previously happened whenever a
                # /detect ran during multiplex warmup.
                try:
                    from inference_utils import cuda_cleanup
                    cuda_cleanup()
                except Exception:
                    pass
                logger.warning(
                    "SAM3 multiplex video predictor hit OOM (%s); falling back to non-multiplex base predictor",
                    exc,
                )
            elif cuda_poisoned:
                logger.error(
                    "SAM3 multiplex video predictor crashed CUDA context (%s); "
                    "process state is unrecoverable, exiting so docker-compose "
                    "respawns the container",
                    exc,
                )
                os._exit(1)
            else:
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
        "sam3_category_threshold_overrides": str(len(_PER_CLASS_CATEGORY_THR)),
        "flash_attn_3": _flash_attn_3_status(),
        "dinov3_sat": os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m"),
        "prithvi_backbone": os.getenv("PRITHVI_BACKBONE_ID", "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL"),
        "prithvi_flood": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
        "prithvi_burn": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
        "terramind": os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large"),
        "yoloe_pf": os.getenv("YOLOE_PF_MODEL_ID", "yoloe-26x-seg-pf.pt"),
        "yoloe_seg": os.getenv("YOLOE_SEG_MODEL_ID", "yoloe-26x-seg.pt"),
    }


def _autocast_ctx(device: str):
    import torch
    from contextlib import nullcontext

    if SAM3_NATIVE_BF16 and device.startswith("cuda"):
        # Weights + activations are already bf16; autocast would only add
        # cast ops that re-promote then re-demote tensors at op boundaries.
        return nullcontext()
    if device.startswith("cuda"):
        return torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    return torch.autocast(device_type="cpu", enabled=False)


def _sdpa_ctx():
    """SDPA backend context for SAM3 forward calls.

    "flash" prefers FLASH_ATTENTION (PyTorch picks the fastest available
    accelerated kernel; falls through to EFFICIENT_ATTENTION on sm_120
    until FA4 lands), "efficient" pins EFFICIENT_ATTENTION only, "auto"
    leaves the backend choice to PyTorch.
    """
    from contextlib import nullcontext
    if SAM3_SDPA_BACKEND == "flash":
        from sam3_perf import pin_sdpa_backend
        return pin_sdpa_backend(prefer_flash=True)
    if SAM3_SDPA_BACKEND == "efficient":
        from sam3_perf import pin_sdpa_backend
        return pin_sdpa_backend(prefer_flash=False)
    return nullcontext()


def _device_ctx(device: str):
    """Pin PyTorch's thread-local current CUDA device for a forward.

    The current CUDA device is thread-local; the service runs inference in an
    anyio worker threadpool whose threads are reused across replicas pinned to
    different GPUs, so the ambient current device can drift from this replica's
    device. The SAM3 collator builds index tensors (notably find_input.img_ids)
    on the *current* device — under drift that is a wrong GPU and _get_img_feats
    then dies indexing the cached vis_pos_enc. Pinning makes the collator build
    those tensors on the replica's GPU. No-op for CPU devices.

    Functionally equivalent to `_device_context` defined above. Both wrap
    `torch.cuda.device(device)`. Kept as a separate name for now because
    text/box forward paths reach for `_device_ctx` while video lifecycle
    code reaches for `_device_context`; a future refactor should collapse
    them.
    """
    import torch
    from contextlib import nullcontext

    if device.startswith("cuda"):
        return torch.cuda.device(device)
    return nullcontext()


def run_text_prompts(bundle: dict[str, Any], image_rgb_uint8: np.ndarray, prompts: Iterable[str], score_threshold: float, timings: dict[str, float] | None = None):
    """Native facebookresearch/sam3 API.

    ``processor.set_image`` returns an inference state that caches vision
    features; ``set_text_prompt`` reuses the state for each prompt.

    Optional ``timings`` dict is populated with per-stage ms keys when
    provided (encode_image, decode_loop / decode_batched, etc.).
    """
    from sam3_perf import stage_timer

    if bundle.get("sam3_image") is None:
        raise RuntimeError("sam3_image model not resident in bundle (profile not loaded)")
    if timings is None:
        timings = {}
    prompts = list(prompts)
    # Dispatch — see _run_text_prompts_cached_batched docstring for rationale.
    # When the cached-encoder batched path is available (post-A3 patch), it
    # collapses N×encoder runs to 1 while keeping the per-chunk decoder batch.
    if SAM3_BATCHED_TEXT and len(prompts) > 1 and _cached_batched_supported(bundle):
        return _run_text_prompts_cached_batched(
            bundle, image_rgb_uint8, prompts, score_threshold, timings=timings,
        )
    if SAM3_BATCHED_TEXT and len(prompts) > 1:
        candidates: list[tuple[np.ndarray, list[float], float, str]] = []
        for offset in range(0, len(prompts), SAM3_BATCHED_TEXT_CHUNK_SIZE):
            candidates.extend(
                _run_text_prompts_batched(
                    bundle,
                    image_rgb_uint8,
                    prompts[offset:offset + SAM3_BATCHED_TEXT_CHUNK_SIZE],
                    score_threshold,
                    timings=timings,
                )
            )
        return candidates

    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates: list[tuple[np.ndarray, list[float], float, str]] = []

    with (bundle.get("forward_lock") or bundle["lock"]), _device_ctx(device), _inference_mode(), _autocast_ctx(device), _sdpa_ctx():
        with stage_timer(timings, "encode_image"):
            state = processor.set_image(pil_image)
        with stage_timer(timings, "decode_loop"):
            for label in prompts:
                phrase = PROMPT_TEMPLATE.format(label=label)
                output = processor.set_text_prompt(state=state, prompt=phrase)
                if not _prompt_passes_category_gate(output, label):
                    continue
                candidates.extend(_collect_candidates(output, score_threshold, label))
    return candidates


def _presence_signals(scores: Iterable[float]) -> dict[str, float]:
    """Summarise a per-prompt score distribution for the presence gate.

    Returns ``{"max": float, "mean": float, "ratio": float, "n": int}``.
    ``ratio = max / max(mean, SAM3_PRESENCE_RATIO_EPS)``. Pure function;
    safe to call from tests and diagnostics. Empty inputs return zeros.
    """
    vals = [float(s) for s in scores]
    n = len(vals)
    if n == 0:
        return {"max": 0.0, "mean": 0.0, "ratio": 0.0, "n": 0}
    max_v = max(vals)
    mean_v = sum(vals) / n
    ratio = max_v / max(mean_v, SAM3_PRESENCE_RATIO_EPS)
    return {"max": max_v, "mean": mean_v, "ratio": ratio, "n": n}


def _prompt_passes_category_gate(output, label: Any = None) -> bool:
    """Category-level presence gate (legacy max-score + SegEarth-OV3 ratio).

    Returns True iff the active gates (controlled by ``SAM3_PRESENCE_MODE``)
    all pass for this prompt. Default mode ``"both"`` requires the existing
    max-score gate AND the new presence-ratio gate to pass.

    Modes:
      ``max``   — legacy: ``max_score >= threshold`` (per-class or global).
      ``ratio`` — SegEarth-OV3-inspired: ``max_score / mean_score >= ratio_floor``.
      ``both``  — DEFAULT: both gates must pass.

    See [docs/decisions/why-segearth-presence-filter.md] for the rationale
    behind the more-restrictive default; operators wanting strict legacy
    behaviour can set ``SAM3_PRESENCE_MODE=max``.
    """
    scores = _to_list(output.get("scores"))
    if not scores:
        # No candidates at all — preserve existing behaviour (drop the prompt).
        return False
    try:
        scores_f = [float(s) for s in scores]
    except Exception:
        return True
    max_score = max(scores_f)

    if SAM3_PRESENCE_MODE in ("max", "both"):
        threshold = _category_threshold_for(label) if label is not None else SAM3_CATEGORY_THR
        if threshold > 0.0 and max_score < threshold:
            if label is not None:
                logger.debug(
                    "presence gate dropped prompt %r: signals=%s mode=%s floor=%.2f",
                    label, _presence_signals(scores_f), SAM3_PRESENCE_MODE, SAM3_PRESENCE_RATIO_FLOOR,
                )
            return False
        if SAM3_PRESENCE_MODE == "max":
            return True

    if SAM3_PRESENCE_MODE in ("ratio", "both") and SAM3_PRESENCE_RATIO_FLOOR > 0.0:
        mean_score = sum(scores_f) / len(scores_f)
        ratio = max_score / max(mean_score, SAM3_PRESENCE_RATIO_EPS)
        if ratio < SAM3_PRESENCE_RATIO_FLOOR:
            if label is not None:
                logger.debug(
                    "presence gate dropped prompt %r: signals=%s mode=%s floor=%.2f",
                    label, _presence_signals(scores_f), SAM3_PRESENCE_MODE, SAM3_PRESENCE_RATIO_FLOOR,
                )
            return False

    return True


def _run_text_prompts_batched(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    prompts: list[str],
    score_threshold: float,
    timings: dict[str, float] | None = None,
):
    """Run the upstream SAM3 batched image API for multiple text queries.

    This mirrors ``examples/sam3_image_batched_inference.ipynb`` from
    facebookresearch/sam3: build one datapoint containing many text queries,
    collate it, move it to the target device, then call the image model once.
    """
    from sam3_perf import stage_timer

    if timings is None:
        timings = {}
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
    if SAM3_NATIVE_BF16 and device.startswith("cuda"):
        batch.img_batch = batch.img_batch.to(torch.bfloat16)
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

    with (bundle.get("forward_lock") or bundle["lock"]), _device_ctx(device), _inference_mode(), _autocast_ctx(device), _sdpa_ctx():
        with stage_timer(timings, "batched_forward"):
            output = bundle["sam3_image"]["model"](batch)
        with stage_timer(timings, "batched_postproc"):
            processed = postprocessor.process_results(output, batch.find_metadatas)
    return _collect_batched_candidates(processed, query_labels)


def _cuda_context_poisoned(exc: Exception) -> bool:
    """True when ``exc`` corresponds to an unrecoverable CUDA fault.

    A ``cudaErrorIllegalAddress`` ("illegal memory access"), device-side
    assert, cuBLAS/cuDNN init failure, or any other "CUDA error" sticks to the
    process's CUDA context: once raised, every subsequent kernel launch in this
    process — including the next request's image encode — fails identically.
    There is no in-process recovery, mirroring the multiplex-warmup path above
    (see _build_*_predictor). OOM is explicitly excluded: it leaves the cuBLAS
    handle valid and is recoverable by clearing caches, so it stays on the
    graceful-degrade path (skip the chunk, keep the others).
    """
    if not isinstance(exc, RuntimeError):
        return False
    text = str(exc)
    if "CUDA out of memory" in text or "out of memory" in text.lower():
        return False
    return (
        "CUDA error" in text
        or "illegal memory access" in text
        or "device-side assert" in text
        or "CUBLAS_STATUS" in text
        or "cuDNN error" in text
    )


def _run_text_prompts_cached_batched(
    bundle: dict[str, Any],
    image_rgb_uint8: np.ndarray,
    prompts: list[str],
    score_threshold: float,
    timings: dict[str, float] | None = None,
):
    """Cached-encoder variant of `_run_text_prompts_batched`.

    Runs the SAM3 vision encoder ONCE for the whole image, then iterates the
    text prompts in chunks (size SAM3_BATCHED_TEXT_CHUNK_SIZE) doing only
    text-encode + DETR-decode + mask-postproc per chunk. The encoder savings
    dominate when many ontology-resolved prompts hit one chip (typical
    worker request: ~146 prompts → 18 chunks → was 18× encoder, now 1×).

    Requires the runtime patch in patches.sam3_cached_forward (idempotent).
    Caller is responsible for checking `_cached_batched_supported(bundle)`.
    """
    from sam3_perf import stage_timer
    if timings is None:
        timings = {}

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
    from sam3.train.transforms.basic_for_api import (
        ComposeAPI,
        NormalizeAPI,
        RandomResizeAPI,
        ToTensorAPI,
    )

    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    width, height = pil_image.size

    transform = ComposeAPI(
        transforms=[
            RandomResizeAPI(sizes=1008, max_size=1008, square=True, consistent_transform=False),
            ToTensorAPI(),
            NormalizeAPI(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )

    candidates: list[tuple[np.ndarray, list[float], float, str]] = []
    chunk_size = max(1, SAM3_BATCHED_TEXT_CHUNK_SIZE)
    total_chunks = 0
    failed_chunks = 0
    last_exc: Exception | None = None

    with (bundle.get("forward_lock") or bundle["lock"]), _device_ctx(device), _inference_mode(), _autocast_ctx(device), _sdpa_ctx():
        # ---- Encode the image ONCE for the whole request ----
        # We bypass the collator/Datapoint path here because the SAM3 collator
        # requires at least one find_query, but at seed time we have zero —
        # we only need the image tensor on device. The first chunk's
        # Datapoint below will go through the normal collator with its
        # queries; we just splice the cached backbone_out onto it.
        with stage_timer(timings, "encode_image"):
            from torchvision import tv_tensors
            import torchvision.transforms.v2 as v2_transforms
            tv_image = tv_tensors.Image(np.array(pil_image).transpose(2, 0, 1))
            seed_tensor = v2_transforms.functional.resize(tv_image, [1008, 1008])
            seed_tensor = (seed_tensor.float() / 255.0 - 0.5) / 0.5
            seed_tensor = seed_tensor.unsqueeze(0).to(device, non_blocking=device.startswith("cuda"))
            if SAM3_NATIVE_BF16 and device.startswith("cuda"):
                seed_tensor = seed_tensor.to(torch.bfloat16)
            model = bundle["sam3_image"]["model"]
            cached_backbone_out: dict[str, Any] = {}
            cached_backbone_out.update(model.backbone.forward_image(seed_tensor))
            # Guarantee every cached vision tensor (incl. vis_pos_enc, the
            # operand _get_img_feats indexes) sits on this replica's device so
            # it co-locates with the collator-built img_ids. Cheap no-op when
            # the model is fully resident on `device` (the build is now wrapped
            # in _device_context); a real backstop if any backbone tensor was
            # created on a stray (cuda:0) device.
            cached_backbone_out = copy_data_to_device(
                cached_backbone_out, torch.device(device),
                non_blocking=device.startswith("cuda"),
            )
            cached_img_batch = seed_tensor

        # ---- Iterate chunks: build a tiny batch (no image work), reuse cache ----
        for offset in range(0, len(prompts), chunk_size):
            chunk = prompts[offset:offset + chunk_size]
            total_chunks += 1
            # Degrade gracefully: a single failing chunk (e.g. GPU OOM on a
            # content-heavy tile) is logged and skipped so the tile still
            # returns the detections from the chunks that did succeed, rather
            # than 500-ing the whole /detect_raw request and blanking the tile.
            # But if EVERY chunk fails (e.g. the GPU profile over-committed VRAM
            # so no SAM3 forward fits), we must NOT report a clean empty result
            # — that masked a real misconfig as "no objects found" and let the
            # upload finalize as `ready` with zero detections. Raise below.
            try:
                query_labels: dict[int, str] = {}
                chunk_dp = Datapoint(
                    find_queries=[],
                    images=[SAMImage(data=pil_image, objects=[], size=[height, width])],
                )
                for label in chunk:
                    qid = next(_QUERY_IDS)
                    phrase = PROMPT_TEMPLATE.format(label=label)
                    chunk_dp.find_queries.append(
                        FindQueryLoaded(
                            query_text=phrase,
                            image_id=0,
                            object_ids_output=[],
                            is_exhaustive=True,
                            query_processing_order=0,
                            inference_metadata=InferenceMetadata(
                                coco_image_id=qid,
                                original_image_id=qid,
                                original_category_id=1,
                                original_size=(height, width),
                                object_id=0,
                                frame_index=0,
                            ),
                        )
                    )
                    query_labels[qid] = label
                chunk_dp = transform(chunk_dp)
                chunk_batch = collate([chunk_dp], dict_key="sam3")["sam3"]
                chunk_batch = copy_data_to_device(
                    chunk_batch, torch.device(device),
                    non_blocking=device.startswith("cuda"),
                )
                # Carry cached image features onto this batch — Sam3Image.forward
                # patched in patches.sam3_cached_forward picks this up and skips
                # the vision encoder.
                chunk_batch.img_batch = cached_img_batch
                chunk_batch._cached_backbone_out = cached_backbone_out

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
                with stage_timer(timings, "batched_forward"):
                    output = model(chunk_batch)
                with stage_timer(timings, "batched_postproc"):
                    processed = postprocessor.process_results(output, chunk_batch.find_metadatas)
                candidates.extend(_collect_batched_candidates(processed, query_labels))
            except Exception as exc:
                # A poisoned CUDA context (illegal memory access / device-side
                # assert / cuBLAS-cuDNN init failure) is NOT a per-chunk OOM:
                # it corrupts the whole process, so retrying the next chunk —
                # or the next request's image encode — keeps failing forever
                # while /health still reports ok. Self-heal by exiting; the
                # `restart: unless-stopped` policy respawns the container with
                # a clean context. Mirrors the multiplex-warmup path above.
                if _cuda_context_poisoned(exc):
                    logger.critical(
                        "sam3 cached-batched chunk poisoned the CUDA context "
                        "(offset=%d, labels=%s): %s — process state is "
                        "unrecoverable, exiting so docker-compose respawns the "
                        "container",
                        offset, chunk, exc,
                    )
                    os._exit(1)
                failed_chunks += 1
                last_exc = exc
                logger.warning(
                    "sam3 cached-batched chunk failed (offset=%d, labels=%s): %s",
                    offset, chunk, exc,
                )
                continue

    # Every chunk failed → this was not "no objects found", it was a failed
    # inference (typically GPU OOM from an over-committed model set). Raise so
    # /detect(_raw) returns a non-200 and the worker marks the upload failed
    # instead of finalizing it `ready` with zero detections.
    if total_chunks > 0 and failed_chunks == total_chunks:
        raise RuntimeError(
            f"SAM3 batched inference failed on all {total_chunks} text chunks "
            f"(last error: {last_exc})"
        ) from last_exc

    return candidates


def _collect_batched_candidates(processed: dict[int, dict[str, Any]], query_labels: dict[int, str]):
    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for query_id, result in processed.items():
        label = query_labels.get(int(query_id), "object")
        masks = _to_list(result.get("masks"))
        boxes = _to_list(result.get("boxes"))
        scores = _to_list(result.get("scores"))
        # Delegate to the canonical gate so the batched path applies the
        # same max-gate + SegEarth-OV3 ratio composition as the single-prompt
        # path (SAM3_PRESENCE_MODE). Production runs with SAM3_BATCHED_TEXT=1
        # so this is the dominant code path; an inline max-only gate here
        # would defeat the ratio default.
        if not _prompt_passes_category_gate({"scores": scores}, label):
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
    if bundle.get("sam3_image") is None:
        raise RuntimeError("sam3_image model not resident in bundle (profile not loaded)")
    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates: list[tuple[np.ndarray, list[float], float, str]] = []

    with (bundle.get("forward_lock") or bundle["lock"]), _device_ctx(device), _inference_mode(), _autocast_ctx(device), _sdpa_ctx():
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


def run_video(bundle, video_path, prompt: str, *, frame_stride, start_frame, end_frame, max_frames, dinov3, score_threshold):
    """Run a SAM3 video tracking session for a single text concept.

    The upstream API (`Sam3VideoInference.add_prompt` and
    `Sam3MultiplexTrackingWithInteractivity.add_prompt`) unconditionally
    calls `self.reset_state(inference_state)` for any text prompt, so the
    tracker can only persist one text concept per session. Callers that
    need N concepts must run N sequential sessions (one per prompt) —
    the worker's (window × prompt) ThreadPoolExecutor is the supported
    pattern. Anything that re-prompts mid-session would reset the
    inference state and destroy SAM3's built-in hotstart unmatched/
    duplicate suppression (`hotstart_delay=15`,
    `hotstart_unmatch_thresh=8`, `hotstart_dup_thresh=8`), so
    re-prompting is intentionally absent.
    """
    predictor = bundle["sam3_video"]
    prompt_text = prompt if prompt and not prompt.startswith("__") else None
    if not prompt_text:
        return

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
        # entirely (gate fails) and continue streaming live. Uses the
        # per-class override for this prompt when one is configured.
        video_category_threshold = _category_threshold_for(prompt_text)
        gate_active = video_category_threshold > 0.0
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
                        "source_layer": "sam3",
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
                            if gate_max_score >= video_category_threshold:
                                gate_passed = True
                                for buffered in gate_buffer:
                                    yield buffered
                                gate_buffer.clear()
                            else:
                                # Concept absent from the scene — drop this
                                # session's emissions entirely and close.
                                logger.info(
                                    "video category gate dropped prompt %r (max score %.3f < %.2f)",
                                    prompt_text, gate_max_score, video_category_threshold,
                                )
                                return
                    else:
                        yield serialised
            # Stream ended before the hotstart window closed; flush the
            # buffer only if the gate would have passed.
            if gate_active and not gate_passed:
                if gate_max_score >= video_category_threshold:
                    for buffered in gate_buffer:
                        yield buffered
                else:
                    logger.info(
                        "video category gate dropped prompt %r (max score %.3f < %.2f, partial window)",
                        prompt_text, gate_max_score, video_category_threshold,
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
# Mask-IoU + tracker helpers — used by the YOLOE FMV tracker (run_video_yoloe).
# ---------------------------------------------------------------------------


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
_MASK_NMS_RESOLUTION = max(16, int(
    os.getenv("SAM3_TRACK_MASK_RES", os.getenv("SAM3_AMG_NMS_MASK_RES", "64"))
))


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


def run_video_yoloe(
    bundle,
    video_path: str,
    prompts: list[str] | None,
    *,
    frame_stride: int,
    start_frame: int,
    end_frame: int | None,
    max_frames: int | None,
    score_threshold: float,
):
    """Standalone YOLOE FMV tracker — bypasses SAM 3.1 multiplex.

    YOLOE-26x-seg(-pf) emits per-frame instance masks directly. Cross-frame
    association uses ``_hungarian_iou_link`` with the same track buffer +
    masklet confirmation that the (removed) SAM-3 AMG path used to apply.
    NDJSON entries match the ``run_video`` shape so the worker's drain /
    insert code is unchanged (``parent_class="yoloe_track"`` distinguishes
    YOLOE rows in the DB).

    ``prompts`` non-empty → uses yoloe-26x-seg with text prompts (PCS).
    ``prompts`` empty/None → uses yoloe-26x-seg-pf prompt-free (AMG).
    """
    import cv2
    import time as _time

    import yoloe

    yoloe_bundle = bundle.get("yoloe")
    if yoloe_bundle is None or (yoloe_bundle.get("pf") is None and yoloe_bundle.get("seg") is None):
        logger.warning("run_video_yoloe called but yoloe bundle is unavailable")
        return

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")
    try:
        frame_idx = 0
        emitted_frames = 0
        tracks: dict[int, dict[str, Any]] = {}
        next_track_id = 1
        prev_masks: list[np.ndarray] = []
        prev_track_ids: list[int] = []
        confirm_n = max(1, SAM3_TRACK_MIN_CONSECUTIVE_FRAMES)
        emitted_count = 0
        dropped_unconfirmed = 0
        yoloe_start = _time.monotonic()

        clean_prompts = [p for p in (prompts or []) if p and not str(p).startswith("__")]
        variant = "seg" if clean_prompts else "pf"

        logger.info(
            "run_video_yoloe start: video=%s variant=%s prompts=%d max_frames=%s confirm_n=%d",
            video_path, variant, len(clean_prompts), max_frames, confirm_n,
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
            try:
                candidates = yoloe.run(yoloe_bundle, rgb, clean_prompts or None, score_threshold)
            except Exception as exc:
                logger.warning("yoloe.run failed on frame %d: %s", frame_idx, exc)
                candidates = []

            if not candidates:
                prev_masks = []
                prev_track_ids = []
                frame_idx += 1
                emitted_frames += 1
                continue

            curr_masks = [c[0] for c in candidates]
            curr_boxes = [c[1] for c in candidates]
            curr_scores = [c[2] for c in candidates]
            curr_labels = [c[3] for c in candidates]

            assignment = _hungarian_iou_link(prev_masks, curr_masks, SAM3_TRACK_IOU_MIN)
            this_track_ids: list[int] = []
            this_frame_tids: set[int] = set()
            for i, prev_j in enumerate(assignment):
                if prev_j >= 0 and prev_j < len(prev_track_ids):
                    tid = prev_track_ids[prev_j]
                else:
                    tid = next_track_id
                    next_track_id += 1
                    tracks[tid] = {
                        "first_seen_frame": frame_idx,
                        "label": None,
                        "consecutive": 0,
                        "pending": [],
                        "confirmed": False,
                    }
                tracks[tid]["last_seen_frame"] = frame_idx
                tracks[tid]["score"] = curr_scores[i]
                tracks[tid]["label"] = curr_labels[i]
                this_track_ids.append(tid)
                this_frame_tids.add(tid)

            for tid in list(tracks.keys()):
                if tid not in this_frame_tids and not tracks[tid].get("confirmed"):
                    tracks[tid]["consecutive"] = 0
                    if tracks[tid]["pending"]:
                        dropped_unconfirmed += len(tracks[tid]["pending"])
                        tracks[tid]["pending"] = []

            stale = [tid for tid, info in tracks.items()
                     if frame_idx - int(info.get("last_seen_frame", frame_idx)) > SAM3_TRACK_BUFFER]
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
                cls = tracks[tid].get("label") or "object"
                entry: dict[str, Any] = {
                    "frame_index": frame_idx,
                    "track_id": int(tid),
                    "class": cls,
                    "original_class": cls,
                    "parent_class": "yoloe_track",
                    "source_layer": "yoloe",
                    "score": score,
                    "bbox_xyxy_norm": bbox_xyxy_norm,
                    "obb": obb["points"],
                    "obb_format": "yolo_obb_normalized_xyxyxyxy",
                    "obb_source": obb["source"],
                    "obb_angle_deg": obb["angle_deg"],
                    "edge_truncated": obb["edge_truncated"],
                    "mask_rle": fusion.coco_rle(mask_arr),
                }
                serialised = json.dumps(entry, separators=(",", ":"))
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

        for tid, info in tracks.items():
            if not info.get("confirmed"):
                dropped_unconfirmed += len(info.get("pending", []))
        logger.info(
            "run_video_yoloe done: video=%s emitted_frames=%d tracks_seen=%d "
            "emitted=%d dropped_unconfirmed=%d elapsed=%.2fs",
            video_path, emitted_frames, next_track_id - 1,
            emitted_count, dropped_unconfirmed,
            _time.monotonic() - yoloe_start,
        )
    finally:
        cap.release()
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
