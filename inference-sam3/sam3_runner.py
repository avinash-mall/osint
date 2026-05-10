from __future__ import annotations

import json
import os
import itertools
from typing import Any, Iterable

import numpy as np
from PIL import Image


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
    from sam3.model_builder import build_sam3_multiplex_video_predictor, build_sam3_video_predictor

    if SAM3_USE_MULTIPLEX:
        return build_sam3_multiplex_video_predictor(
            compile=SAM3_COMPILE_VIDEO,
            warm_up=SAM3_COMPILE_VIDEO and SAM3_WARM_UP_VIDEO,
        )
    return build_sam3_video_predictor()


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
        "prithvi_crop": "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
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
    predictor = bundle["sam3_video"]
    session = predictor.handle_request(request={"type": "start_session", "resource_path": video_path})
    session_id = session["session_id"]
    try:
        for prompt in prompts:
            predictor.handle_request(request={"type": "add_prompt", "session_id": session_id, "frame_index": start_frame, "text": prompt})

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
            for track in _iter_sam3_video_tracks(resp.get("outputs") or []):
                score = float(track.get("score") or 0.0)
                if score < score_threshold:
                    continue
                import fusion

                mask = np.asarray(track["mask"], dtype=bool)
                height, width = mask.shape[-2:]
                bbox_xyxy = [float(v) for v in track["bbox_xyxy"]]
                obb = fusion.mask_to_obb_record(mask, bbox_xyxy, width, height)
                entry = {
                    "frame_index": frame_idx,
                    "track_id": int(track["track_id"]),
                    "class": track["prompt_text"],
                    "original_class": track["prompt_text"],
                    "parent_class": "track",
                    "bbox_xyxy": bbox_xyxy,
                    "obb": obb["points"],
                    "obb_format": "yolo_obb_normalized_xyxyxyxy",
                    "obb_source": obb["source"],
                    "obb_angle_deg": obb["angle_deg"],
                    "edge_truncated": obb["edge_truncated"],
                    "score": score,
                    "mask_rle": fusion.coco_rle(mask),
                }
                yield json.dumps(entry, separators=(",", ":"))
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


def _iter_sam3_video_tracks(outputs):
    for item in outputs:
        if not isinstance(item, dict):
            continue
        mask = item.get("mask") or item.get("segmentation")
        if mask is None:
            continue
        bbox = item.get("bbox_xyxy") or item.get("box") or item.get("bbox")
        if bbox is None:
            ys, xs = np.where(np.asarray(mask, dtype=bool))
            if len(xs) == 0:
                continue
            bbox = [xs.min(), ys.min(), xs.max() + 1, ys.max() + 1]
        yield {
            "track_id": item.get("obj_id") or item.get("track_id") or item.get("id") or 0,
            "prompt_text": item.get("prompt_text") or item.get("text") or item.get("label") or "track",
            "score": item.get("score", 1.0),
            "mask": mask,
            "bbox_xyxy": bbox,
        }
