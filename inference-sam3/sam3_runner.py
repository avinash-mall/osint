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
# Re-prompt every N emitted frames inside a video session to recover tracks
# that have drifted off-target. 0 disables. Default 12 ≈ every 3 s at 4 fps.
SAM3_REPROMPT_EVERY_N_FRAMES = max(0, int(os.getenv("SAM3_REPROMPT_EVERY_N_FRAMES", "12")))
# SAM3 video's add_prompt calls reset_state every invocation (verified in
# sam3_video_inference.py:864 and sam3_multiplex_tracking.py:1697), so each
# extra prompt wastes a ~3 s session-reset only to be overwritten by the
# next one. Cap how many prompts seed the SAM3 tracker — Grounding-DINO
# keyframes (which run *one* multi-class call) still see the full list.
SAM3_VIDEO_MAX_PROMPTS = max(1, int(os.getenv("SAM3_VIDEO_MAX_PROMPTS", "3")))
# Grounding-DINO's text encoder caps at 256 BERT-like tokens; with ~3 tokens
# per prompt + delimiter that's ~50 prompts per batch. Chunk smaller so
# multi-word prompts (e.g. "aircraft carrier") still fit.
GROUNDING_DINO_PROMPT_CHUNK = max(1, int(os.getenv("GROUNDING_DINO_PROMPT_CHUNK", "30")))
# Hybrid FMV pipeline: every N emitted frames, decode the same source frame
# from the prep clip and run Grounding-DINO (with optional SAHI tile slicing)
# on it. Emits those detections as an independent stream alongside SAM3's
# tracker output. 0 disables the keyframe detector.
FMV_KEYFRAME_EVERY_N_FRAMES = max(0, int(os.getenv("FMV_KEYFRAME_EVERY_N_FRAMES", "8")))
# Number of SAHI tiles per keyframe (2×2 grid with overlap when ≥ 4). 0 or 1
# disables slicing (Grounding-DINO sees the whole frame).
FMV_KEYFRAME_SAHI_TILES = max(0, int(os.getenv("FMV_KEYFRAME_SAHI_TILES", "4")))
FMV_KEYFRAME_SAHI_OVERLAP = float(os.getenv("FMV_KEYFRAME_SAHI_OVERLAP", "0.20"))
SAM3_COMPILE_IMAGE = os.getenv("SAM3_COMPILE_IMAGE", "0").strip().lower() in {"1", "true", "yes", "on"}
SAM3_COMPILE_VIDEO = os.getenv("SAM3_COMPILE_VIDEO", "0").strip().lower() in {"1", "true", "yes", "on"}
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
# central Vienna). Setting this to 0.0 disables the gate.
SAM3_CATEGORY_THR = float(os.getenv("SAM3_CATEGORY_THRESHOLD", "0.40"))
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


def build_video(device: str):
    _patch_pkg_resources_py312()
    _install_flash_attn_fallback()
    from sam3.model_builder import build_sam3_multiplex_video_predictor, build_sam3_video_predictor

    if SAM3_USE_MULTIPLEX:
        # Multiplex needs ≈14 GiB of weights + ≈2-3 GiB of session
        # activations per /detect_video; if the GPU total is too small the
        # weights load successfully but the *first* inference OOMs. Refuse
        # multiplex early when there isn't enough headroom so we can fall
        # back cleanly to the base predictor.
        try:
            import torch
            if torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                free_gib = free / (1024 ** 3)
                total_gib = total / (1024 ** 3)
                if total_gib < 20.0:
                    logger.warning(
                        "GPU total VRAM is %.1f GiB (< 20 GiB needed for SAM3.1 multiplex inference); "
                        "falling back to non-multiplex base predictor",
                        total_gib,
                    )
                    return build_sam3_video_predictor()
                if free_gib < 14.0:
                    logger.warning(
                        "GPU free VRAM is %.1f GiB but multiplex needs ~14 GiB of weights; "
                        "falling back to non-multiplex base predictor",
                        free_gib,
                    )
                    return build_sam3_video_predictor()
        except Exception:
            pass
        try:
            predictor = build_sam3_multiplex_video_predictor(
                compile=SAM3_COMPILE_VIDEO,
                warm_up=SAM3_COMPILE_VIDEO and SAM3_WARM_UP_VIDEO,
            )
            _patch_multiplex_init_state(predictor)
            return predictor
        except Exception as exc:
            logger.warning(
                "SAM3 multiplex video predictor failed to load (%s); falling back to non-multiplex base predictor",
                exc,
            )
    return build_sam3_video_predictor()


_flash_attn_patched = False


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
        _flash_attn_patched = True
        return  # real flash-attn-3 is installed; nothing to do
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


def _sahi_tile_rects(width: int, height: int, n_tiles: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """Compute (x1, y1, x2, y2) rectangles for an n-tile SAHI grid with the
    requested overlap. For n_tiles <= 1 returns a single full-frame rect."""
    if n_tiles <= 1:
        return [(0, 0, width, height)]
    # Use 2×2 (4 tiles) or 3×3 (9 tiles) layouts; fall through to nearest grid.
    grid = 2 if n_tiles <= 4 else 3 if n_tiles <= 9 else 4
    tile_w = int(width / grid * (1.0 + overlap))
    tile_h = int(height / grid * (1.0 + overlap))
    step_x = max(1, int((width - tile_w) / max(1, grid - 1))) if grid > 1 else width
    step_y = max(1, int((height - tile_h) / max(1, grid - 1))) if grid > 1 else height
    rects: list[tuple[int, int, int, int]] = []
    for gy in range(grid):
        for gx in range(grid):
            x1 = min(width - tile_w, gx * step_x) if grid > 1 else 0
            y1 = min(height - tile_h, gy * step_y) if grid > 1 else 0
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(width, x1 + tile_w)
            y2 = min(height, y1 + tile_h)
            rects.append((x1, y1, x2, y2))
    return rects


def _grounding_dino_keyframe(
    bundle: dict[str, Any],
    frame_rgb: np.ndarray,
    prompts: list[str],
    score_threshold: float,
    *,
    n_tiles: int,
    overlap: float,
    iou_merge: float = 0.5,
) -> list[dict[str, Any]]:
    """Run Grounding-DINO on the frame (and optionally on SAHI tiles), merge
    per-class via simple greedy IoU NMS, and return detections shaped like
    SAM3's tracker output (normalised bbox, class, score, axis-aligned OBB
    points derived from the bbox).

    Each detection is independent (no track_id) — the worker stores them
    alongside SAM3 tracks; the FmvPlayer renders both."""
    gd_bundle = bundle.get("grounding_dino") if isinstance(bundle, dict) else None
    if not gd_bundle or gd_bundle.get("model") is None:
        return []
    import grounding_dino  # local import: optional dependency
    H, W = frame_rgb.shape[:2]
    tile_rects = _sahi_tile_rects(W, H, max(1, n_tiles), overlap)

    all_dets: list[tuple[str, float, float, float, float, float]] = []  # (label, x1n, y1n, x2n, y2n, score)
    for (tx1, ty1, tx2, ty2) in tile_rects:
        if tx2 <= tx1 or ty2 <= ty1:
            continue
        crop = frame_rgb[ty1:ty2, tx1:tx2]
        if crop.size == 0:
            continue
        # Grounding-DINO's text encoder caps at 256 tokens; chunk the
        # prompt list so a 130-entry admin ontology fits across multiple
        # encoder passes.
        results: list = []
        for chunk_start in range(0, len(prompts), GROUNDING_DINO_PROMPT_CHUNK):
            chunk = prompts[chunk_start:chunk_start + GROUNDING_DINO_PROMPT_CHUNK]
            if not chunk:
                continue
            try:
                chunk_results = grounding_dino.run(gd_bundle, crop, chunk, score_threshold=score_threshold)
            except Exception as exc:
                logger.debug(
                    "grounding_dino tile (%d,%d,%d,%d) chunk %d failed: %s",
                    tx1, ty1, tx2, ty2, chunk_start, exc,
                )
                continue
            results.extend(chunk_results)
        # Each result is (mask, [x1,y1,x2,y2], score, canonical_label) in tile-local pixels.
        for (_mask, box_px, score, label) in results:
            x1t, y1t, x2t, y2t = box_px
            # Map back to global pixel coords, then normalise.
            x1g = (tx1 + x1t) / W
            y1g = (ty1 + y1t) / H
            x2g = (tx1 + x2t) / W
            y2g = (ty1 + y2t) / H
            all_dets.append((label, float(x1g), float(y1g), float(x2g), float(y2g), float(score)))

    if not all_dets:
        return []

    # Per-class greedy IoU NMS to dedupe across tile boundaries.
    by_class: dict[str, list[tuple[float, float, float, float, float]]] = {}
    for label, x1, y1, x2, y2, sc in all_dets:
        by_class.setdefault(label, []).append((x1, y1, x2, y2, sc))

    def _iou(a, b) -> float:
        ax1, ay1, ax2, ay2, _ = a
        bx1, by1, bx2, by2, _ = b
        ix1 = max(ax1, bx1); iy1 = max(ay1, by1)
        ix2 = min(ax2, bx2); iy2 = min(ay2, by2)
        iw = max(0.0, ix2 - ix1); ih = max(0.0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0.0:
            return 0.0
        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    merged: list[dict[str, Any]] = []
    for label, boxes in by_class.items():
        boxes.sort(key=lambda b: b[4], reverse=True)
        keep: list[tuple[float, float, float, float, float]] = []
        for cand in boxes:
            if any(_iou(cand, k) >= iou_merge for k in keep):
                continue
            keep.append(cand)
        for (x1n, y1n, x2n, y2n, sc) in keep:
            # Axis-aligned 4-corner polygon as the "OBB" so the frontend's
            # canvas overlay can draw it (Grounding-DINO doesn't give rotated
            # boxes; the UI's normalizeBbox accepts the flat 8-number form).
            obb_norm = [
                x1n, y1n,
                x2n, y1n,
                x2n, y2n,
                x1n, y2n,
            ]
            merged.append({
                "bbox_xyxy_norm": [x1n, y1n, x2n, y2n],
                "class": label,
                "score": sc,
                "obb": obb_norm,
                "obb_format": "yolo_obb_normalized_xyxyxyxy",
                "obb_source": "grounding_dino",
                "edge_truncated": False,
            })
    return merged


def _decode_prep_frame(video_path: str, frame_idx: int) -> np.ndarray | None:
    """Decode a single frame from the prep clip via OpenCV. Returns an
    RGB uint8 ndarray (H, W, 3), or None on failure."""
    import cv2 as _cv2
    cap = _cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            return None
        cap.set(_cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ok, frame_bgr = cap.read()
        if not ok or frame_bgr is None:
            return None
        return _cv2.cvtColor(frame_bgr, _cv2.COLOR_BGR2RGB)
    finally:
        cap.release()


def run_video(bundle, video_path, prompts, *, frame_stride, start_frame, end_frame, max_frames, dinov3, score_threshold):
    predictor = bundle["sam3_video"]
    # Full prompt list (e.g. 130 admin-managed prompts) drives the
    # Grounding-DINO keyframe pass; SAM3's video tracker only uses a small
    # subset because each add_prompt resets state and dominates wall time.
    # Bias the SAM3 subset toward generic FMV-friendly tokens (vehicle /
    # person / building) when present in the admin list so the tracker
    # latches onto things it can actually see, regardless of admin order.
    _clean_prompts = [p for p in prompts if p and not p.startswith("__")]
    _preferred = ("vehicle", "person", "building", "car", "truck", "aircraft")
    sam3_prompts: list[str] = []
    _seen: set[str] = set()
    for pref in _preferred:
        for p in _clean_prompts:
            pl = p.lower()
            if pref in pl and pl not in _seen:
                sam3_prompts.append(p); _seen.add(pl)
                break
        if len(sam3_prompts) >= SAM3_VIDEO_MAX_PROMPTS:
            break
    for p in _clean_prompts:
        if len(sam3_prompts) >= SAM3_VIDEO_MAX_PROMPTS:
            break
        if p.lower() not in _seen:
            sam3_prompts.append(p); _seen.add(p.lower())
    # Autocast: the multiplex predictor casts decoded frames to bfloat16
    # internally but ships its weights as float32. Without the ambient cuda
    # autocast context the vitdet linear layers raise
    # "mat1 BFloat16 / mat2 Float". The image detection paths in this file
    # already wrap forward passes in `_autocast_ctx`; mirror that here.
    with _autocast_ctx(bundle["device"]):
        session = predictor.handle_request(request={"type": "start_session", "resource_path": video_path})
        session_id = session["session_id"]
        try:
            # Track which obj_id maps to which prompt so the non-multiplex
            # predictor (which returns only obj_ids/masks) can still label
            # detections back to the prompting text. We pass an explicit
            # obj_id per add_prompt so the base predictor accepts multiple
            # prompts in one session (the auto-allocated obj_id=0 path was
            # making each call overwrite the previous one).
            obj_id_to_prompt: dict[int, str] = {}
            next_obj_id = 0

            def _add_prompts_at(frame_idx: int) -> None:
                """Issue one add_prompt per text prompt against the given
                frame, with a unique obj_id per call. Updates
                obj_id_to_prompt for the worker to map detections back."""
                nonlocal next_obj_id
                for prompt in sam3_prompts:
                    obj_id = next_obj_id
                    next_obj_id += 1
                    obj_id_to_prompt[obj_id] = prompt
                    try:
                        add_resp = predictor.handle_request(request={
                            "type": "add_prompt",
                            "session_id": session_id,
                            "frame_index": frame_idx,
                            "text": prompt,
                            "obj_id": obj_id,
                        })
                    except TypeError:
                        # Older multiplex builds don't accept obj_id; fall
                        # back to auto-assigned ids and learn them from the
                        # response.
                        add_resp = predictor.handle_request(request={
                            "type": "add_prompt",
                            "session_id": session_id,
                            "frame_index": frame_idx,
                            "text": prompt,
                        })
                    outs = add_resp.get("outputs") if isinstance(add_resp, dict) else None
                    if isinstance(outs, dict):
                        obj_ids = outs.get("out_obj_ids")
                        if obj_ids is not None:
                            for oid in obj_ids:
                                try:
                                    obj_id_to_prompt[int(oid)] = prompt
                                except (TypeError, ValueError):
                                    pass

            _add_prompts_at(start_frame)

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
                for track in _iter_sam3_video_tracks(_resp_outs, obj_id_to_prompt=obj_id_to_prompt):
                    score = float(track.get("score") or 0.0)
                    if score < score_threshold:
                        continue
                    import fusion

                    mask = track.get("mask")
                    bbox_xyxy_norm = track.get("bbox_xyxy_norm")
                    entry: dict[str, Any] = {
                        "frame_index": frame_idx,
                        "track_id": int(track["track_id"]),
                        "class": track["prompt_text"],
                        "original_class": track["prompt_text"],
                        "parent_class": "track",
                        "score": score,
                    }
                    if mask is not None and bbox_xyxy_norm is not None:
                        mask_arr = np.asarray(mask, dtype=bool)
                        height, width = mask_arr.shape[-2:]
                        # _hbb_fallback wants pixel xyxy; reconstruct from normalised.
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
                    yield json.dumps(entry, separators=(",", ":"))

                # Hybrid keyframe pass: every N emitted frames, decode the
                # raw frame and run Grounding-DINO (with optional SAHI
                # tiles) on it. Emits each detection as an independent
                # entry — no track_id, mask_rle=None, axis-aligned OBB
                # synthesised from the bbox. Drone-FMV survey paper
                # prescribes this as the canonical recall booster when
                # SAM3's text-grounded tracker loses targets after camera
                # motion.
                if (
                    FMV_KEYFRAME_EVERY_N_FRAMES
                    and bundle is not None
                    and bundle.get("grounding_dino") is not None
                    and (emitted_frames == 1 or emitted_frames % FMV_KEYFRAME_EVERY_N_FRAMES == 0)
                ):
                    try:
                        gd_frame = _decode_prep_frame(video_path, frame_idx)
                        if gd_frame is not None:
                            gd_dets = _grounding_dino_keyframe(
                                bundle, gd_frame, list(prompts), score_threshold,
                                n_tiles=FMV_KEYFRAME_SAHI_TILES,
                                overlap=FMV_KEYFRAME_SAHI_OVERLAP,
                            )
                            for det in gd_dets:
                                entry = {
                                    "frame_index": frame_idx,
                                    "track_id": None,
                                    "class": det["class"],
                                    "original_class": det["class"],
                                    "parent_class": "track",
                                    "score": det["score"],
                                    "bbox_xyxy_norm": det["bbox_xyxy_norm"],
                                    "obb": det["obb"],
                                    "obb_format": det["obb_format"],
                                    "obb_source": det["obb_source"],
                                    "edge_truncated": det["edge_truncated"],
                                    "mask_rle": None,
                                    "prompt_text": det["class"],
                                }
                                yield json.dumps(entry, separators=(",", ":"))
                    except Exception as exc:
                        logger.warning("Grounding-DINO keyframe at frame %s failed: %s", frame_idx, exc)

                # Periodic re-prompt: every N emitted frames, re-issue the
                # prompts on the current frame as new obj_ids. SAM3
                # propagates each set forward; the worker dedupes via the
                # per-prompt class label so the timeline doesn't fork.
                if (
                    SAM3_REPROMPT_EVERY_N_FRAMES
                    and emitted_frames > 0
                    and emitted_frames % SAM3_REPROMPT_EVERY_N_FRAMES == 0
                ):
                    try:
                        _add_prompts_at(frame_idx)
                    except Exception as exc:
                        logger.warning("re-prompt at frame %s failed: %s", frame_idx, exc)
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


def _iter_sam3_video_tracks(outputs, obj_id_to_prompt: dict[int, str] | None = None):
    # Multiplex tracker yields a list of per-track dicts; the non-multiplex
    # base predictor yields a single dict with parallel ``out_obj_ids`` /
    # ``out_boxes_xywh`` / ``out_binary_masks`` arrays. Handle both shapes.
    #
    # Bbox normalization: always derive from the mask's own pixel space.
    # SAM3's ``out_boxes_xywh`` is already divided by W_video/H_video
    # (sam3/model/sam3_multiplex_tracking.py:736-738), so re-treating it
    # as pixel xywh produced the off-target rectangles the user reported.
    # We always emit ``bbox_xyxy_norm`` in [0, 1] now.
    if isinstance(outputs, dict):
        obj_ids = outputs.get("out_obj_ids")
        masks = outputs.get("out_binary_masks")
        # The non-multiplex predictor uses ``out_probs``; some forks emit
        # ``out_scores``. Prefer probs since that's what the upstream model
        # actually returns for SAM3 video.
        scores = outputs.get("out_probs")
        if scores is None:
            scores = outputs.get("out_scores")
        if obj_ids is None or masks is None:
            return
        for idx in range(len(obj_ids)):
            mask_arr = np.asarray(masks[idx], dtype=bool)
            track_id = int(obj_ids[idx])
            prompt = (obj_id_to_prompt or {}).get(track_id, "track")
            score = float(scores[idx]) if scores is not None and idx < len(scores) else 1.0
            if not mask_arr.any():
                # Heartbeat: the tracker still owns this track but it has
                # no visible mask this frame. Emit so the per-track timeline
                # stays continuous; the worker stores it with empty bbox.
                yield {
                    "track_id": track_id,
                    "prompt_text": prompt,
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
                "prompt_text": prompt,
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
        prompt = (obj_id_to_prompt or {}).get(int(track_id) if isinstance(track_id, int) else 0)
        prompt_text = item.get("prompt_text") or item.get("text") or item.get("label") or prompt or "track"
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
