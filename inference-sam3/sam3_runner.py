from __future__ import annotations

import json
import os
from typing import Any, Iterable

import numpy as np
from PIL import Image


SAM3_IMAGE_MODEL_ID = os.getenv("SAM3_IMAGE_MODEL_ID", "facebook/sam3")
SAM3_USE_MULTIPLEX = os.getenv("SAM3_USE_MULTIPLEX", "1").strip().lower() in {"1", "true", "yes", "on"}
PROMPT_TEMPLATE = os.getenv("SAM3_PROMPT_TEMPLATE", "{label}")


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
    """Load the SAM3 image model.

    Two implementations are supported:

    * **transformers route** (preferred): ``Sam3Model.from_pretrained`` +
      ``Sam3Processor.from_pretrained`` exposes ``get_vision_features`` for
      vision-feature caching across prompts. This requires SAM3 support to
      have landed in a transformers release. As of 4.57 it is still on
      ``main`` only — set ``SAM3_BACKEND=transformers`` to force it.
    * **native route** (default fallback): the ``sam3.model_builder.build_sam3_image_model``
      + ``sam3.model.sam3_image_processor.Sam3Processor`` API installed from
      the upstream source clone in the Dockerfile. Vision features are cached
      inside the per-image ``state`` so the multi-prompt loop is still O(1)
      per chip on the encoder side.
    """
    backend = os.getenv("SAM3_BACKEND", "auto").strip().lower()

    if backend in {"auto", "transformers"}:
        try:
            import torch
            from transformers import Sam3Model, Sam3Processor  # type: ignore[attr-defined]

            model = Sam3Model.from_pretrained(SAM3_IMAGE_MODEL_ID, torch_dtype=torch.float16).to(device).eval()
            processor = Sam3Processor.from_pretrained(SAM3_IMAGE_MODEL_ID)
            return {"backend": "transformers", "model": model, "processor": processor}
        except (ImportError, AttributeError) as exc:
            if backend == "transformers":
                raise
            print(f"[sam3_runner] transformers SAM3 not available ({exc}); falling back to native repo API")

    # Native repo fallback. Always works when the SAM3 source clone is installed.
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor as NativeProcessor

    model = build_sam3_image_model().to(device).eval()
    return {"backend": "native", "model": model, "processor": NativeProcessor(model)}


def build_video(device: str):
    from sam3.model_builder import build_sam3_multiplex_video_predictor, build_sam3_video_predictor

    return build_sam3_multiplex_video_predictor() if SAM3_USE_MULTIPLEX else build_sam3_video_predictor()


def versions() -> dict[str, str]:
    return {
        "sam3_image": SAM3_IMAGE_MODEL_ID,
        "sam3_video": "sam3.1-multiplex" if SAM3_USE_MULTIPLEX else "sam3",
        "dinov3_sat": os.getenv("DINOV3_SAT_MODEL_ID", "facebook/dinov3-vitl16-pretrain-sat493m"),
        "dinov3_lvd": os.getenv("DINOV3_LVD_MODEL_ID", "facebook/dinov3-vitl16-pretrain-lvd1689m"),
        "prithvi_backbone": os.getenv("PRITHVI_BACKBONE_ID", "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL"),
        "prithvi_flood": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-TL-Sen1Floods11",
        "prithvi_burn": "ibm-nasa-geospatial/Prithvi-EO-2.0-300M-BurnScars",
        "prithvi_crop": "ibm-nasa-geospatial/Prithvi-EO-1.0-100M-multi-temporal-crop-classification",
        "terramind": os.getenv("TERRAMIND_MODEL_ID", "terramind_v1_large"),
    }


def run_text_prompts(bundle: dict[str, Any], image_rgb_uint8: np.ndarray, prompts: Iterable[str], score_threshold: float):
    image_bundle = bundle["sam3_image"]
    if image_bundle.get("backend") == "transformers":
        return _run_text_prompts_transformers(bundle, image_rgb_uint8, prompts, score_threshold)
    return _run_text_prompts_native(bundle, image_rgb_uint8, prompts, score_threshold)


def _run_text_prompts_transformers(bundle, image_rgb_uint8, prompts, score_threshold):
    import torch

    model = bundle["sam3_image"]["model"]
    processor = bundle["sam3_image"]["processor"]
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates = []
    with bundle["lock"], torch.inference_mode():
        img_inputs = processor(images=pil_image, return_tensors="pt").to(model.device)
        vision_embeds = model.get_vision_features(pixel_values=img_inputs.pixel_values)
        target_sizes = img_inputs.get("original_sizes").tolist()
        for label in prompts:
            phrase = PROMPT_TEMPLATE.format(label=label)
            text_inputs = processor(text=phrase, return_tensors="pt").to(model.device)
            outputs = model(vision_embeds=vision_embeds, **text_inputs)
            result = processor.post_process_instance_segmentation(
                outputs,
                threshold=score_threshold,
                mask_threshold=0.5,
                target_sizes=target_sizes,
            )[0]
            for mask, box_xyxy, score in zip(result["masks"], result["boxes"], result["scores"]):
                candidates.append((
                    mask.cpu().numpy().astype(bool),
                    [float(v) for v in box_xyxy.cpu().numpy()],
                    float(score),
                    label,
                ))
    return candidates


def _run_text_prompts_native(bundle, image_rgb_uint8, prompts, score_threshold):
    """Native facebookresearch/sam3 API.

    ``processor.set_image`` returns an inference state that caches vision
    features; ``set_text_prompt`` reuses the state for each prompt.
    """
    import torch

    processor = bundle["sam3_image"]["processor"]
    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    candidates = []

    if device.startswith("cuda"):
        autocast_ctx = torch.autocast(device_type="cuda", dtype=torch.bfloat16)
    else:
        autocast_ctx = torch.autocast(device_type="cpu", enabled=False)

    with bundle["lock"], torch.inference_mode(), autocast_ctx:
        state = processor.set_image(pil_image)
        for label in prompts:
            phrase = PROMPT_TEMPLATE.format(label=label)
            output = processor.set_text_prompt(state=state, prompt=phrase)
            masks = _to_list(output.get("masks"))
            boxes = _to_list(output.get("boxes"))
            scores = _to_list(output.get("scores"))
            for mask, box_xyxy, score in zip(masks, boxes, scores):
                score_f = float(score)
                if score_f < score_threshold:
                    continue
                candidates.append((
                    np.asarray(_to_numpy(mask), dtype=bool).squeeze(),
                    [float(v) for v in _to_numpy(box_xyxy).reshape(-1)[:4]],
                    score_f,
                    label,
                ))
    return candidates


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


def _to_xyxy_pixels(entry: dict[str, Any], width: int, height: int) -> list[float] | None:
    bbox = entry.get("bbox")
    obb = entry.get("obb")
    if obb and len(obb) >= 8:
        xs = [float(obb[i]) for i in range(0, 8, 2)]
        ys = [float(obb[i]) for i in range(1, 8, 2)]
        x1n, y1n, x2n, y2n = min(xs), min(ys), max(xs), max(ys)
    elif bbox and len(bbox) >= 4:
        cx, cy, w, h = (float(v) for v in bbox[:4])
        x1n, y1n, x2n, y2n = cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0
    else:
        return None
    x1 = max(0.0, min(float(width), x1n * width))
    y1 = max(0.0, min(float(height), y1n * height))
    x2 = max(0.0, min(float(width), x2n * width))
    y2 = max(0.0, min(float(height), y2n * height))
    return [x1, y1, x2, y2] if x2 - x1 >= 1.0 and y2 - y1 >= 1.0 else None


def run_box_prompts(bundle: dict[str, Any], image_rgb_uint8: np.ndarray, prompt_boxes: list[dict[str, Any]], score_threshold: float):
    image_bundle = bundle["sam3_image"]
    if image_bundle.get("backend") != "transformers":
        # Native repo API does not expose box-prompt segmentation in a stable
        # public form; fall back to text prompting on the caller's labels.
        labels = [str(entry.get("class") or entry.get("original_class") or "object") for entry in prompt_boxes]
        return run_text_prompts(bundle, image_rgb_uint8, labels, score_threshold)

    import torch

    model = bundle["sam3_image"]["model"]
    processor = bundle["sam3_image"]["processor"]
    pil_image = Image.fromarray(image_rgb_uint8)
    height, width = image_rgb_uint8.shape[:2]
    candidates = []
    with bundle["lock"], torch.inference_mode():
        for entry in prompt_boxes:
            box_xyxy = _to_xyxy_pixels(entry, width, height)
            if box_xyxy is None:
                continue
            label = entry.get("class") or entry.get("original_class") or "segment"
            inputs = processor(
                images=pil_image,
                input_boxes=[[box_xyxy]],
                input_boxes_labels=[[1]],
                return_tensors="pt",
            ).to(model.device)
            outputs = model(**inputs)
            result = processor.post_process_instance_segmentation(
                outputs,
                threshold=score_threshold,
                mask_threshold=0.5,
                target_sizes=inputs.get("original_sizes").tolist(),
            )[0]
            for mask, box, score in zip(result["masks"], result["boxes"], result["scores"]):
                candidates.append((mask.cpu().numpy().astype(bool), [float(v) for v in box.cpu().numpy()], float(score), label))
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
