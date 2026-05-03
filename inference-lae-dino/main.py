import os
import io
import time
import json
import asyncio
import threading
import contextlib
import importlib.metadata
from typing import Any, Optional, List, Tuple

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from starlette.concurrency import run_in_threadpool
import numpy as np
from PIL import Image

from detection_policy import active_detection_policy, detection_decision
from lae_vocabulary import (
    COCO_CLASSES,
    LAE_80C_CLASSES,
    chunk_classes,
    load_vocabulary_file,
    prompt_from_classes,
    split_prompt_tokens,
)

app = FastAPI(title="Magritte AIP Node - Grounding DINO Open-Vocabulary Inference")


GROUNDING_DINO_MODEL_ID = os.getenv(
    "GROUNDING_DINO_MODEL_ID",
    "IDEA-Research/grounding-dino-base",
)
GROUNDING_DINO_BOX_THRESHOLD = float(os.getenv("GROUNDING_DINO_BOX_THRESHOLD", "0.25"))
GROUNDING_DINO_TEXT_THRESHOLD = float(os.getenv("GROUNDING_DINO_TEXT_THRESHOLD", "0.25"))

LAE_PROMPT_PROFILE = os.getenv("LAE_PROMPT_PROFILE", "official_lae80c").strip() or "official_lae80c"
LAE_PROMPT_CHUNK_SIZE = max(1, int(os.getenv("LAE_PROMPT_CHUNK_SIZE", "20")))
LAE_VOCAB_FILE = os.getenv("LAE_VOCAB_FILE", "").strip()
DEFAULT_TEXT_PROMPT = os.getenv("DEFAULT_TEXT_PROMPT", prompt_from_classes(LAE_80C_CLASSES))

DETECTION_POLICY = active_detection_policy()
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.25"))
MAX_DETECTIONS_PER_CHIP = int(os.getenv("MAX_DETECTIONS_PER_CHIP", "300"))
MODEL_VERSION = os.getenv("MODEL_VERSION", "grounding-dino-base")

# Mixed precision auto-tune. "auto" probes CUDA dtypes in preference order
# and keeps the first one that works for the installed GPU/PyTorch stack.
# Override with "bf16", "fp16", "fp32", or "off" to skip autocast entirely.
LAE_AUTOCAST_DTYPE = os.getenv("LAE_AUTOCAST_DTYPE", "auto").strip().lower()

# Startup sanity probe: after each model loads at the chosen dtype, run one
# inference on a known-good chip and assert detection count >= threshold.
# On failure, try the next candidate for that device. Without this guard, a
# mixed-precision regression could silently break or halve recall in production.
# LAE_MIN_PROBE_DETECTIONS=0 disables enforcement (probe still runs and is
# logged, but error fall-back remains enabled).
LAE_PROBE_IMAGE_PATH = os.getenv("LAE_PROBE_IMAGE_PATH", "/app/probes/probe_chip.png")
LAE_MIN_PROBE_DETECTIONS = int(os.getenv("LAE_MIN_PROBE_DETECTIONS", "0"))
LAE_BATCH_MAX_SIZE = max(1, int(os.getenv("LAE_BATCH_MAX_SIZE", "4")))
LAE_BATCH_TIMEOUT_MS = max(0.0, float(os.getenv("LAE_BATCH_TIMEOUT_MS", "25")))


def available_cpu_count() -> int:
    if hasattr(os, "sched_getaffinity"):
        try:
            return len(os.sched_getaffinity(0))
        except OSError:
            pass
    return os.cpu_count() or 1


def normalize_device_list(value: str) -> List[str]:
    devices = []
    for item in value.split(","):
        device = item.strip()
        if not device:
            continue
        if device.isdigit():
            device = f"cuda:{device}"
        devices.append(device)
    return devices or ["cpu"]


def resolve_devices() -> List[str]:
    requested = os.getenv("DEVICE", "auto").strip()
    if requested and requested.lower() != "auto":
        devices = normalize_device_list(requested)
        print(f"[INFERENCE-LAE] Using requested devices: {', '.join(devices)}")
        return devices
    try:
        import torch
    except ImportError:
        print("[INFERENCE-LAE] WARNING: torch is unavailable; falling back to CPU.")
        return ["cpu"]

    cuda_version = getattr(torch.version, "cuda", None)
    if torch.cuda.is_available():
        devices = [f"cuda:{i}" for i in range(torch.cuda.device_count())]
        names = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        print(
            f"[INFERENCE-LAE] Using CUDA devices {', '.join(devices)}: "
            f"{', '.join(names)} (torch CUDA {cuda_version})"
        )
        return devices

    print(
        "[INFERENCE-LAE] WARNING: CUDA unavailable; using CPU. "
        f"torch={torch.__version__}, torch CUDA={cuda_version}"
    )
    return ["cpu"]


DEVICES = resolve_devices()
DEVICE = ",".join(DEVICES)


def configure_cpu_threads() -> int:
    requested = os.getenv("CPU_THREADS", "auto").strip().lower()
    if requested not in {"", "auto"}:
        threads = max(1, int(requested))
    elif all(device == "cpu" for device in DEVICES):
        workers = max(1, int(os.getenv("WEB_CONCURRENCY", "1")))
        threads = max(1, available_cpu_count() // workers)
    else:
        threads = max(1, min(8, available_cpu_count() // max(1, len(DEVICES))))

    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    try:
        import torch
        torch.set_num_threads(threads)
        torch.set_num_interop_threads(max(1, min(4, threads // 2)))
    except (ImportError, RuntimeError):
        pass
    print(f"[INFERENCE-LAE] Using {threads} CPU compute threads per process.")
    return threads


CPU_THREADS = configure_cpu_threads()


def configure_torch_runtime() -> None:
    """Apply runtime knobs that benefit any GPU inference workload — chosen
    once at startup, no per-request overhead. Safe on CPU too (the cudnn line
    is a no-op without CUDA)."""
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        # cudnn benchmark picks the fastest convolution algo per input shape
        # after a one-time autotune. Inputs in our pipeline are stable
        # First request pays the autotune; subsequent ones are fast.
        torch.backends.cudnn.benchmark = True
    # 'high' enables TF32 on Ampere+/cuDNN heuristics; falls back gracefully
    # on older cards. No effect when autocast handles dtype explicitly.
    try:
        torch.set_float32_matmul_precision("high")
    except AttributeError:
        pass


configure_torch_runtime()


detection_models: List[dict] = []
model_pool_lock = threading.Lock()
model_pool_index = 0


def _build_model(device: str):
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    processor = AutoProcessor.from_pretrained(GROUNDING_DINO_MODEL_ID)
    model = (
        AutoModelForZeroShotObjectDetection
        .from_pretrained(GROUNDING_DINO_MODEL_ID)
        .to(device)
        .eval()
    )
    return {"model": model, "processor": processor}


def load_model() -> None:
    global detection_models
    if detection_models:
        return

    loaded = []
    for device in DEVICES:
        try:
            bundle = _build_model(device)
            ac_candidates = _autocast_candidates(device)
            ac_dtype, ac_label = ac_candidates[0]
            loaded.append({
                "model": bundle["model"],
                "processor": bundle["processor"],
                "device": device,
                "lock": threading.Lock(),
                "autocast_dtype": ac_dtype,
                "autocast_label": ac_label,
                "autocast_candidates": ac_candidates,
                "last_probe_detections": None,
                "last_probe_dtype": None,
                "last_probe_ms": None,
            })
            print(
                f"[INFERENCE-LAE] Grounding DINO loaded from {GROUNDING_DINO_MODEL_ID} "
                f"on {device}, autocast={ac_label}"
            )
        except Exception as exc:
            print(f"[INFERENCE-LAE] Load failed on {device}: {exc}")

    detection_models = loaded
    if not detection_models:
        print("[INFERENCE-LAE] WARNING: No model replicas loaded. /detect will return 503.")
        return

    _run_startup_probe()


def _run_startup_probe() -> None:
    """Sanity-check each loaded model against its autocast candidates. Logs the
    probe outcome on every entry and commits the first dtype that runs and
    satisfies LAE_MIN_PROBE_DETECTIONS. Probe failure is non-fatal — the entry
    stays in the pool with whatever state it ended in, and /health surfaces the
    result for ops to inspect."""
    if not os.path.exists(LAE_PROBE_IMAGE_PATH):
        print(
            f"[INFERENCE-LAE] Probe image missing at {LAE_PROBE_IMAGE_PATH}; "
            "sanity check skipped."
        )
        return
    try:
        probe_img = Image.open(LAE_PROBE_IMAGE_PATH).convert("RGB")
    except Exception as exc:
        print(f"[INFERENCE-LAE] Probe image load failed: {exc}; sanity check skipped.")
        return
    probe_arr = np.array(probe_img)
    probe_plan = resolve_prompt_plan({"prompt_profile": "official_lae80c"})
    probe_prompt = probe_plan["chunks"][0] if probe_plan["chunks"] else DEFAULT_TEXT_PROMPT

    for entry in detection_models:
        device = entry["device"]
        attempts = _autocast_attempts(entry)

        for dtype, label in attempts:
            try:
                with entry["lock"]:
                    t0 = time.time()
                    result = _run_detector(
                        entry, probe_arr, probe_prompt,
                        autocast_dtype=dtype,
                    )
                    elapsed_ms = round((time.time() - t0) * 1000, 1)
            except Exception as exc:
                print(f"[INFERENCE-LAE] Probe failed on {device} ({label}): {exc}")
                entry["last_probe_detections"] = -1
                entry["last_probe_dtype"] = label
                entry["last_probe_ms"] = -1
                continue

            scores = result.get("scores")
            n_above = 0
            if scores is not None:
                try:
                    n_above = int((scores >= CONFIDENCE_THRESHOLD).sum().item())
                except Exception:
                    n_above = 0
            entry["last_probe_detections"] = n_above
            entry["last_probe_dtype"] = label
            entry["last_probe_ms"] = elapsed_ms
            print(
                f"[INFERENCE-LAE] Probe ({device}, {label}): "
                f"{n_above} detections >= {CONFIDENCE_THRESHOLD} in {elapsed_ms}ms"
            )

            if (
                LAE_MIN_PROBE_DETECTIONS <= 0
                or n_above >= LAE_MIN_PROBE_DETECTIONS
            ):
                # Probe satisfies threshold (or enforcement is off); commit this dtype.
                entry["autocast_dtype"] = dtype
                entry["autocast_label"] = label
                break

            print(
                f"[INFERENCE-LAE] Probe below LAE_MIN_PROBE_DETECTIONS={LAE_MIN_PROBE_DETECTIONS} "
                f"on {device} at {label}; trying next attempt."
            )
        else:
            print(
                f"[INFERENCE-LAE] WARNING: All probe attempts on {device} fell short. "
                "Detection quality may be degraded; check probe image and prompt."
            )


def next_model_entry() -> Optional[dict]:
    global model_pool_index
    if not detection_models:
        load_model()
    if not detection_models:
        return None
    with model_pool_lock:
        entry = detection_models[model_pool_index % len(detection_models)]
        model_pool_index += 1
    return entry


def _load_profile_classes(profile: str) -> tuple[str, ...]:
    if profile == "coco":
        return COCO_CLASSES
    if profile == "lae1m_file":
        if not LAE_VOCAB_FILE:
            print("[INFERENCE-LAE] LAE_PROMPT_PROFILE=lae1m_file but LAE_VOCAB_FILE is unset; using LAE-80C.")
            return LAE_80C_CLASSES
        try:
            loaded = load_vocabulary_file(LAE_VOCAB_FILE)
        except Exception as exc:
            print(f"[INFERENCE-LAE] Failed to load LAE_VOCAB_FILE={LAE_VOCAB_FILE}: {exc}; using LAE-80C.")
            return LAE_80C_CLASSES
        return loaded or LAE_80C_CLASSES
    return LAE_80C_CLASSES


def _metadata_threshold(metadata: Optional[dict]) -> float:
    if isinstance(metadata, dict):
        value = metadata.get("confidence_threshold")
        if value is not None:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                pass
    return CONFIDENCE_THRESHOLD


def resolve_prompt_plan(metadata: Optional[dict]) -> dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    custom_prompt = metadata.get("text_prompt")
    threshold = _metadata_threshold(metadata)
    if isinstance(custom_prompt, str) and custom_prompt.strip():
        prompt = custom_prompt.strip()
        return {
            "profile": "custom",
            "chunks": [prompt],
            "classes": split_prompt_tokens(prompt),
            "confidence_threshold": threshold,
        }

    profile = str(metadata.get("prompt_profile") or LAE_PROMPT_PROFILE or "official_lae80c").strip()
    if profile not in {"official_lae80c", "lae1m_file", "coco"}:
        profile = "official_lae80c"
    classes = _load_profile_classes(profile)
    chunks = [prompt_from_classes(group) for group in chunk_classes(classes, LAE_PROMPT_CHUNK_SIZE)]
    return {
        "profile": profile,
        "chunks": chunks,
        "classes": list(classes),
        "confidence_threshold": threshold,
    }


def _append_fp32_once(candidates: List[Tuple[Optional["object"], str]]) -> None:
    if not any(label == "fp32" for _, label in candidates):
        candidates.append((None, "fp32"))


def _autocast_candidates(device: str) -> List[Tuple[Optional["object"], str]]:
    """Resolve autocast candidates for a device in preference order.

    None means run in fp32 with no autocast. Even explicit mixed-precision
    requests include fp32 as an automatic safety fallback when the CUDA op stack
    rejects a dtype at runtime.
    """
    if device == "cpu":
        return [(None, "fp32")]
    requested = LAE_AUTOCAST_DTYPE
    try:
        import torch
    except ImportError:
        return [(None, "fp32")]

    candidates: List[Tuple[Optional["object"], str]] = []
    if requested in {"fp32", "off", "none"}:
        return [(None, "fp32")]
    elif requested == "bf16":
        candidates.append((torch.bfloat16, "bf16"))
    elif requested == "fp16":
        candidates.append((torch.float16, "fp16"))
    else:
        try:
            idx = int(device.split(":")[1]) if ":" in device else 0
            cap = torch.cuda.get_device_capability(idx)
        except Exception:
            cap = (0, 0)
        if cap >= (8, 0):
            candidates.append((torch.bfloat16, "bf16"))
        if cap >= (7, 0):
            candidates.append((torch.float16, "fp16"))

    _append_fp32_once(candidates)
    return candidates


def _autocast_attempts(entry: dict) -> List[Tuple[Optional["object"], str]]:
    attempts = [(entry.get("autocast_dtype"), entry.get("autocast_label", "fp32"))]
    attempts.extend(entry.get("autocast_candidates") or [])

    seen = set()
    unique = []
    for dtype, label in attempts:
        if label in seen:
            continue
        seen.add(label)
        unique.append((dtype, label))
    return unique


def _to_grounding_dino_prompt(prompt: str) -> str:
    tokens = [token.strip().lower() for token in prompt.split(".") if token.strip()]
    return ". ".join(tokens) + "." if tokens else ""


def _run_detector(entry: dict, image_array: np.ndarray, prompt: str, autocast_dtype=None):
    return _run_detector_batch(entry, [image_array], prompt, autocast_dtype=autocast_dtype)[0]


def _matched_prompt_class(label: str, prompt_tokens: list[str]) -> Optional[str]:
    label_norm = label.strip().lower()
    if not label_norm:
        return None
    for token in prompt_tokens:
        token_norm = token.strip().lower()
        if not token_norm:
            continue
        if label_norm == token_norm or label_norm in token_norm or token_norm in label_norm:
            return token
    return None


def _run_detector_batch(entry: dict, image_arrays: List[np.ndarray], prompt: str, autocast_dtype=None) -> List[object]:
    import torch

    model = entry["model"]
    processor = entry["processor"]
    device = entry["device"]
    pil_images = [Image.fromarray(image_array).convert("RGB") for image_array in image_arrays]
    text = _to_grounding_dino_prompt(prompt)
    inputs = processor(
        images=pil_images,
        text=[text] * len(pil_images),
        return_tensors="pt",
    ).to(device)
    if autocast_dtype is not None and torch.cuda.is_available():
        autocast_ctx = torch.autocast(device_type="cuda", dtype=autocast_dtype)
    else:
        autocast_ctx = contextlib.nullcontext()
    with torch.inference_mode(), autocast_ctx:
        outputs = model(**inputs)
    target_sizes = [(image.height, image.width) for image in pil_images]
    try:
        return processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            box_threshold=GROUNDING_DINO_BOX_THRESHOLD,
            text_threshold=GROUNDING_DINO_TEXT_THRESHOLD,
            target_sizes=target_sizes,
        )
    except TypeError as exc:
        if "box_threshold" not in str(exc):
            raise
        return processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=GROUNDING_DINO_BOX_THRESHOLD,
            text_threshold=GROUNDING_DINO_TEXT_THRESHOLD,
            target_sizes=target_sizes,
        )


def _response_from_result(
    start_time: float,
    result,
    device: str,
    prompt: str,
    img_w: int,
    img_h: int,
    prompt_profile: str,
    prompt_chunk_index: int,
    prompt_total_chunks: int,
    confidence_threshold: float,
) -> dict:
    detections: List[dict] = []
    prompt_tokens = split_prompt_tokens(prompt)

    if not isinstance(result, dict):
        return _empty_response(start_time, device, prompt, prompt_profile, confidence_threshold)

    scores_t = result.get("scores")
    bboxes_t = result.get("boxes")
    labels = result.get("text_labels") or result.get("labels")
    if scores_t is None or bboxes_t is None or labels is None:
        return _empty_response(start_time, device, prompt, prompt_profile, confidence_threshold)

    try:
        scores_np = scores_t.detach().cpu().numpy()
        bboxes_np = bboxes_t.detach().cpu().numpy()
    except AttributeError as exc:
        print(f"[INFERENCE-LAE] Unexpected Grounding DINO result shape: {exc}")
        return _empty_response(start_time, device, prompt, prompt_profile, confidence_threshold)

    raw_candidate_count = int(scores_np.shape[0])
    candidate_indices = [
        index for index, score in enumerate(scores_np)
        if float(score) >= confidence_threshold
    ]
    threshold_candidate_count = len(candidate_indices)
    candidate_indices.sort(key=lambda index: float(scores_np[index]), reverse=True)
    if MAX_DETECTIONS_PER_CHIP > 0:
        candidate_indices = candidate_indices[:MAX_DETECTIONS_PER_CHIP]

    policy_suppressed_count = 0
    official_profile = prompt_profile in {"official_lae80c", "lae1m_file", "coco"}
    for ai in candidate_indices:
        score = float(scores_np[ai])
        cls_name = str(labels[ai])
        matched_class = _matched_prompt_class(cls_name, prompt_tokens) if official_profile else None
        policy_label = matched_class or cls_name

        decision = detection_decision(policy_label, score, DETECTION_POLICY)
        official_class = matched_class is not None
        policy_review_status = decision["review_status"]
        if official_profile and official_class:
            if decision["review_status"] in {"disabled_distractor", "below_class_threshold"}:
                decision = {**decision, "review_status": "review_candidate"}
        elif not decision["class_enabled"] or decision["review_status"] == "below_class_threshold":
            policy_suppressed_count += 1
            continue

        x1, y1, x2, y2 = (float(v) for v in bboxes_np[ai][:4])
        cx = (x1 + x2) / 2.0 / img_w
        cy = (y1 + y2) / 2.0 / img_h
        bw = (x2 - x1) / img_w
        bh = (y2 - y1) / img_h

        detections.append({
            "class": decision["original_class"] if official_profile and official_class else decision["parent_class"],
            "original_class": decision["original_class"],
            "parent_class": decision["parent_class"],
            "bbox": [cx, cy, bw, bh],
            "confidence": score,
            "policy_review_status": policy_review_status,
            "prompt_profile": prompt_profile,
            "prompt_chunk_index": prompt_chunk_index,
            "prompt_total_chunks": prompt_total_chunks,
            "prompt_text": prompt,
            **decision,
        })

    processing_time = time.time() - start_time
    return {
        "status": "success",
        "detections": detections,
        "processing_time_ms": round(processing_time * 1000, 2),
        "model": GROUNDING_DINO_MODEL_ID,
        "task": "open_vocab_detect",
        "device": device,
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": confidence_threshold,
        "confidence_threshold": confidence_threshold,
        "text_prompt": prompt,
        "prompt_profile": prompt_profile,
        "prompt_chunk_index": prompt_chunk_index,
        "prompt_total_chunks": prompt_total_chunks,
        "raw_candidate_count": raw_candidate_count,
        "threshold_candidate_count": threshold_candidate_count,
        "emitted_count": len(detections),
        "policy_suppressed_count": policy_suppressed_count,
    }


def _run_inference_batch_for_prompt(
    items: List[dict],
    prompt: str,
    prompt_profile: str,
    prompt_chunk_index: int,
    prompt_total_chunks: int,
    confidence_threshold: float,
) -> List[dict]:
    entry = next_model_entry()
    if entry is None:
        raise HTTPException(
            status_code=503,
            detail="No Grounding DINO model is loaded; refusing to fabricate detections.",
        )

    device = entry["device"]
    image_arrays = [item["image_array"] for item in items]

    last_exc: Optional[Exception] = None
    results = None
    for dtype, label in _autocast_attempts(entry):
        try:
            with entry["lock"]:
                results = _run_detector_batch(
                    entry,
                    image_arrays,
                    prompt,
                    autocast_dtype=dtype,
                )
            if label != entry.get("autocast_label"):
                print(f"[INFERENCE-LAE] Autocast fallback selected {label} on {device}.")
                entry["autocast_dtype"] = dtype
                entry["autocast_label"] = label
            break
        except Exception as exc:
            last_exc = exc
            print(f"[INFERENCE-LAE] Inference error on {device} ({label}): {exc}")
            if "out of memory" in str(exc).lower():
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass
            continue
    if results is None:
        raise HTTPException(
            status_code=500,
            detail=f"Grounding DINO inference failed: {last_exc}",
        )

    return [
        _response_from_result(
            item["start_time"],
            result,
            device,
            prompt,
            item["image_size"][0],
            item["image_size"][1],
            prompt_profile,
            prompt_chunk_index,
            prompt_total_chunks,
            confidence_threshold,
        )
        for item, result in zip(items, results)
    ]


def _bbox_iou(a: list[float], b: list[float]) -> float:
    if len(a) != 4 or len(b) != 4:
        return 0.0
    acx, acy, aw, ah = a
    bcx, bcy, bw, bh = b
    ax1, ay1, ax2, ay2 = acx - aw / 2, acy - ah / 2, acx + aw / 2, acy + ah / 2
    bx1, by1, bx2, by2 = bcx - bw / 2, bcy - bh / 2, bcx + bw / 2, bcy + bh / 2
    inter_x1 = max(ax1, bx1)
    inter_y1 = max(ay1, by1)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_area = max(0.0, inter_x2 - inter_x1) * max(0.0, inter_y2 - inter_y1)
    if inter_area <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter_area
    return float(inter_area / union) if union else 0.0


def _dedupe_chunked_detections(detections: list[dict], iou_threshold: float = 0.45) -> list[dict]:
    kept: list[dict] = []
    for det in sorted(detections, key=lambda item: float(item.get("confidence") or 0.0), reverse=True):
        det_key = det.get("class") or det.get("original_class")
        duplicate = next(
            (
                existing for existing in kept
                if (existing.get("class") or existing.get("original_class")) == det_key
                and _bbox_iou(existing.get("bbox", []), det.get("bbox", [])) >= iou_threshold
            ),
            None,
        )
        if duplicate is None:
            kept.append(det)
    return kept


def _run_inference_plan_batch(items: List[dict], prompt_plan: dict[str, Any]) -> List[dict]:
    chunks = list(prompt_plan.get("chunks") or [])
    if not chunks:
        chunks = [DEFAULT_TEXT_PROMPT]
    prompt_profile = str(prompt_plan.get("profile") or "custom")
    confidence_threshold = float(prompt_plan.get("confidence_threshold", CONFIDENCE_THRESHOLD))

    collected = [[] for _ in items]
    aggregate_counts = [
        {
            "raw_candidate_count": 0,
            "threshold_candidate_count": 0,
            "policy_suppressed_count": 0,
        }
        for _ in items
    ]
    last_responses: list[dict] | None = None
    total_chunks = len(chunks)

    for chunk_index, prompt in enumerate(chunks):
        responses = _run_inference_batch_for_prompt(
            items,
            prompt,
            prompt_profile,
            chunk_index,
            total_chunks,
            confidence_threshold,
        )
        last_responses = responses
        for item_index, response in enumerate(responses):
            collected[item_index].extend(response.get("detections") or [])
            aggregate_counts[item_index]["raw_candidate_count"] += int(response.get("raw_candidate_count") or 0)
            aggregate_counts[item_index]["threshold_candidate_count"] += int(response.get("threshold_candidate_count") or 0)
            aggregate_counts[item_index]["policy_suppressed_count"] += int(response.get("policy_suppressed_count") or 0)

    combined: list[dict] = []
    for item_index, item in enumerate(items):
        base = (last_responses or [{}])[item_index] if last_responses else {}
        detections = _dedupe_chunked_detections(collected[item_index])
        combined.append({
            **base,
            "status": "success",
            "detections": detections,
            "processing_time_ms": round((time.time() - item["start_time"]) * 1000, 2),
            "model": GROUNDING_DINO_MODEL_ID,
            "task": "open_vocab_detect",
            "model_version": MODEL_VERSION,
            "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
            "threshold_profile": DETECTION_POLICY["threshold_profile"],
            "global_confidence_floor": confidence_threshold,
            "confidence_threshold": confidence_threshold,
            "text_prompt": " | ".join(chunks),
            "prompt_profile": prompt_profile,
            "prompt_chunks": chunks,
            "prompt_total_chunks": total_chunks,
            "official_vocabulary_size": len(LAE_80C_CLASSES) if prompt_profile == "official_lae80c" else None,
            "raw_candidate_count": aggregate_counts[item_index]["raw_candidate_count"],
            "threshold_candidate_count": aggregate_counts[item_index]["threshold_candidate_count"],
            "emitted_count": len(detections),
            "policy_suppressed_count": aggregate_counts[item_index]["policy_suppressed_count"],
            "chunk_deduped_count": max(0, len(collected[item_index]) - len(detections)),
        })
    return combined


def run_inference(image: Image.Image, metadata: Optional[dict] = None) -> dict:
    start_time = time.time()
    prompt_plan = resolve_prompt_plan(metadata)
    item = {
        "image_array": np.array(image),
        "image_size": image.size,
        "start_time": start_time,
    }
    return _run_inference_plan_batch([item], prompt_plan)[0]


def _empty_response(
    start_time: float,
    device: str,
    prompt: str,
    prompt_profile: str,
    confidence_threshold: float,
) -> dict:
    return {
        "status": "success",
        "detections": [],
        "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        "model": GROUNDING_DINO_MODEL_ID,
        "task": "open_vocab_detect",
        "device": device,
        "model_version": MODEL_VERSION,
        "taxonomy_version": DETECTION_POLICY["taxonomy_version"],
        "threshold_profile": DETECTION_POLICY["threshold_profile"],
        "global_confidence_floor": confidence_threshold,
        "confidence_threshold": confidence_threshold,
        "text_prompt": prompt,
        "prompt_profile": prompt_profile,
        "raw_candidate_count": 0,
        "threshold_candidate_count": 0,
        "emitted_count": 0,
        "policy_suppressed_count": 0,
    }


class LaeInferenceBatcher:
    def __init__(self, max_size: int, timeout_ms: float):
        self.max_size = max(1, max_size)
        self.timeout_s = max(0.0, timeout_ms / 1000.0)
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self.total_batches = 0
        self.total_items = 0
        self.last_batch_size = 0
        self.last_batch_ms = None

    async def submit(self, image: Image.Image, metadata: Optional[dict]) -> dict:
        prompt_plan = resolve_prompt_plan(metadata)
        queue_key = json.dumps({
            "profile": prompt_plan["profile"],
            "chunks": prompt_plan["chunks"],
            "confidence_threshold": prompt_plan["confidence_threshold"],
        }, sort_keys=True)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        item = {
            "image_array": np.array(image),
            "image_size": image.size,
            "start_time": time.time(),
            "future": future,
        }
        queue = await self._queue_for_prompt(queue_key, prompt_plan)
        await queue.put(item)
        return await future

    async def _queue_for_prompt(self, queue_key: str, prompt_plan: dict[str, Any]) -> asyncio.Queue:
        async with self._lock:
            queue = self._queues.get(queue_key)
            if queue is None:
                queue = asyncio.Queue()
                self._queues[queue_key] = queue
                self._workers[queue_key] = asyncio.create_task(
                    self._worker(prompt_plan, queue),
                    name=f"lae-batcher-{len(self._workers) + 1}",
                )
            return queue

    async def _worker(self, prompt_plan: dict[str, Any], queue: asyncio.Queue) -> None:
        while True:
            first = await queue.get()
            batch = [first]
            deadline = asyncio.get_running_loop().time() + self.timeout_s
            while len(batch) < self.max_size:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    batch.append(await asyncio.wait_for(queue.get(), timeout=remaining))
                except asyncio.TimeoutError:
                    break

            batch_start = time.time()
            payload = [
                {
                    "image_array": item["image_array"],
                    "image_size": item["image_size"],
                    "start_time": item["start_time"],
                }
                for item in batch
            ]
            try:
                responses = await run_in_threadpool(_run_inference_plan_batch, payload, prompt_plan)
            except Exception as exc:
                for item in batch:
                    future = item["future"]
                    if not future.done():
                        future.set_exception(exc)
            else:
                for item, response in zip(batch, responses):
                    future = item["future"]
                    if not future.done():
                        future.set_result(response)
                self.total_batches += 1
                self.total_items += len(batch)
                self.last_batch_size = len(batch)
                self.last_batch_ms = round((time.time() - batch_start) * 1000, 2)
            finally:
                for _ in batch:
                    queue.task_done()

    def stats(self) -> dict:
        queued_items = sum(queue.qsize() for queue in self._queues.values())
        avg_batch_size = (
            round(self.total_items / self.total_batches, 2)
            if self.total_batches
            else 0
        )
        return {
            "enabled": self.max_size > 1,
            "max_size": self.max_size,
            "timeout_ms": LAE_BATCH_TIMEOUT_MS,
            "prompt_queues": len(self._queues),
            "queued_items": queued_items,
            "total_batches": self.total_batches,
            "total_items": self.total_items,
            "avg_batch_size": avg_batch_size,
            "last_batch_size": self.last_batch_size,
            "last_batch_ms": self.last_batch_ms,
        }


lae_batcher = LaeInferenceBatcher(LAE_BATCH_MAX_SIZE, LAE_BATCH_TIMEOUT_MS)


@app.on_event("startup")
def startup_event():
    load_model()


@app.post("/detect")
async def detect_objects(
    image: UploadFile = File(...),
    metadata: str = Form("{}"),
):
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError:
        meta = {}

    contents = await image.read()
    try:
        pil_image = Image.open(io.BytesIO(contents))
        if pil_image.mode != "RGB":
            pil_image = pil_image.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image file: {exc}")

    if LAE_BATCH_MAX_SIZE > 1:
        result = await lae_batcher.submit(pil_image, meta)
    else:
        result = await run_in_threadpool(run_inference, pil_image, meta)
    result["input_metadata"] = meta
    return result


@app.get("/health")
def health():
    replicas = []
    for entry in detection_models:
        replicas.append({
            "device": entry["device"],
            "autocast_dtype": entry.get("autocast_label"),
            "autocast_candidates": [
                label for _, label in entry.get("autocast_candidates", [])
            ],
            "last_probe_detections": entry.get("last_probe_detections"),
            "last_probe_dtype": entry.get("last_probe_dtype"),
            "last_probe_ms": entry.get("last_probe_ms"),
        })
    torch_info = {"torch": None, "torch_cuda": None, "cuda_runtime": None}
    try:
        import torch
        torch_info["torch"] = torch.__version__
        torch_info["torch_cuda"] = getattr(torch.version, "cuda", None)
        if torch.cuda.is_available():
            torch_info["cuda_runtime"] = {
                "device_count": torch.cuda.device_count(),
                "names": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
                "capabilities": [
                    list(torch.cuda.get_device_capability(i))
                    for i in range(torch.cuda.device_count())
                ],
            }
    except Exception as exc:
        torch_info["error"] = str(exc)
    try:
        transformers_version = importlib.metadata.version("transformers")
    except importlib.metadata.PackageNotFoundError:
        transformers_version = None
    return {
        "status": "ok",
        "model_loaded": bool(detection_models),
        "model_id": GROUNDING_DINO_MODEL_ID,
        "model_task": "open_vocab_detect",
        "processor_loaded": any(entry.get("processor") is not None for entry in detection_models),
        "transformers_version": transformers_version,
        "device": DEVICE,
        "devices": DEVICES,
        "cpu_threads": CPU_THREADS,
        "model_replicas": len(detection_models),
        "replicas": replicas,
        "default_text_prompt": DEFAULT_TEXT_PROMPT,
        "prompt_profile": LAE_PROMPT_PROFILE,
        "prompt_chunk_size": LAE_PROMPT_CHUNK_SIZE,
        "official_vocabulary_size": len(LAE_80C_CLASSES),
        "lae_vocab_file": LAE_VOCAB_FILE or None,
        "model_version": MODEL_VERSION,
        "detection_policy": DETECTION_POLICY,
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "grounding_dino_box_threshold": GROUNDING_DINO_BOX_THRESHOLD,
        "grounding_dino_text_threshold": GROUNDING_DINO_TEXT_THRESHOLD,
        "max_detections_per_chip": MAX_DETECTIONS_PER_CHIP,
        "autocast_setting": LAE_AUTOCAST_DTYPE,
        "batching": lae_batcher.stats(),
        "probe_image_path": LAE_PROBE_IMAGE_PATH,
        "probe_image_exists": os.path.exists(LAE_PROBE_IMAGE_PATH),
        "min_probe_detections": LAE_MIN_PROBE_DETECTIONS,
        "torch_info": torch_info,
    }
