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
