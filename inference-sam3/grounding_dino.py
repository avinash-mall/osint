"""Grounding DINO open-vocabulary text-to-box detector.

A second open-vocabulary detector that complements SAM 3:
- SAM 3 is a strong segmenter but its objectness on small / dense overhead
  targets is weaker than a purpose-built detector.
- Grounding DINO produces text-conditioned boxes with explicit objectness
  scoring; pairing it with SAM 3 gives "tight box from Grounding DINO,
  pixel-perfect mask from SAM 3" which is the published best-practice
  for zero-shot satellite detection.

Returns the same `(mask, bbox_xyxy, score, label)` tuple shape SAM 3 emits, so
detections merge into the existing `fusion.mask_aware_nms` pipeline alongside
DOTA-OBB, YOLOv8m_defence, and SAM 3 itself.

Source: IDEA-Research/grounding-dino-{tiny,base} on Hugging Face,
Apache-2.0, transformers-native (no extra pip dep).
"""
from __future__ import annotations

import os
from typing import Any, Iterable

import numpy as np


GROUNDING_DINO_REPO_ID = os.getenv("GROUNDING_DINO_REPO_ID", "IDEA-Research/grounding-dino-tiny")
# 0.30 box / 0.25 text — stricter than the permissive 0.20/0.15 that let GD's
# short token fragments (e.g. "oil" extracted from "oil or gas facility" at
# conf 0.31) through as false positives on overhead imagery. Open-vocabulary
# detectors run at a 69% FP rate on aerial scenes; a firmer floor is the cheap
# part of the fix (the vocabulary scoping is the rest).
GROUNDING_DINO_THR = float(os.getenv("GROUNDING_DINO_THRESHOLD", "0.30"))
GROUNDING_DINO_TEXT_THR = float(os.getenv("GROUNDING_DINO_TEXT_THRESHOLD", "0.25"))
GROUNDING_DINO_IMGSZ = int(os.getenv("GROUNDING_DINO_IMGSZ", "1024"))
# Max phrases per GD forward pass. Concatenating a long caption makes adjacent
# concepts "bleed" into each other's token spans; chunking the vocabulary into
# short queries keeps each phrase cleanly grounded. Detections from every chunk
# merge in fusion.mask_aware_nms downstream, so chunking is transparent.
GROUNDING_DINO_MAX_PHRASES = int(os.getenv("GROUNDING_DINO_MAX_PHRASES_PER_QUERY", "10"))


def load(device: str) -> dict[str, Any]:
    """Load Grounding DINO via transformers. Auto-downloads to HF cache."""
    try:
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
    except ImportError as exc:
        return {"model": None, "device": device, "repo_id": GROUNDING_DINO_REPO_ID, "error": str(exc)}
    try:
        processor = AutoProcessor.from_pretrained(GROUNDING_DINO_REPO_ID)
        model = AutoModelForZeroShotObjectDetection.from_pretrained(GROUNDING_DINO_REPO_ID)
        if device and device != "cpu":
            try:
                model = model.to(device)
            except Exception:
                pass
        model.eval()
        return {
            "model": model,
            "processor": processor,
            "device": device,
            "repo_id": GROUNDING_DINO_REPO_ID,
        }
    except Exception as exc:
        print(f"[grounding_dino] failed to load {GROUNDING_DINO_REPO_ID}: {exc}")
        return {"model": None, "device": device, "repo_id": GROUNDING_DINO_REPO_ID, "error": str(exc)}


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    prompts: Iterable[str],
    score_threshold: float = GROUNDING_DINO_THR,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Run Grounding DINO on a chip with the supplied prompts.

    The vocabulary is split into chunks of at most GROUNDING_DINO_MAX_PHRASES
    phrases per forward pass to avoid cross-concept token bleed. Output is the
    SAM3-shaped tuple list so the existing `fusion.mask_aware_nms` step can
    dedupe across chunks and across all detector sources.
    """
    if bundle is None or bundle.get("model") is None:
        return []
    prompts = [p for p in prompts if p and not p.startswith("__")]
    if not prompts:
        return []

    try:
        import torch
        from PIL import Image
    except Exception as exc:
        print(f"[grounding_dino] dependency missing: {exc}")
        return []

    model = bundle["model"]
    processor = bundle["processor"]
    device = bundle.get("device", "cpu")
    height, width = image_rgb_uint8.shape[:2]
    pil = Image.fromarray(image_rgb_uint8)

    from inference_utils import safe_predict, cuda_cleanup, memory_guard, device_ctx

    def _forward_chunk(label_list: list[str]) -> list[tuple[np.ndarray, list[float], float, str]]:
        # Grounding DINO's processor expects a list of phrases joined by ". "
        # in a single text query string. The post-processor uses `text_labels`
        # to map detected token spans back to the original phrase strings.
        text_query = ". ".join(label_list) + "."

        def _do_forward():
            inputs = processor(images=pil, text=text_query, return_tensors="pt")
            # Move every tensor in the BatchEncoding to the target device. The
            # default `.to(device)` only walks the top-level dict and can leave
            # nested ints/longs on CPU, triggering "tensors on different
            # devices" at forward time.
            # Pin the current CUDA device too: this runs in the anyio threadpool
            # (current device defaults to cuda:0), so a forward on cuda:N would
            # issue cross-device kernels and illegal-access under concurrency.
            # See docs/decisions/optical-inference-throughput.md.
            with device_ctx(device):
                inputs_dev = {k: (v.to(device) if hasattr(v, "to") else v) for k, v in inputs.items()}
                with torch.inference_mode():
                    outputs = model(**inputs_dev)
                return processor.post_process_grounded_object_detection(
                    outputs,
                    input_ids=inputs_dev.get("input_ids"),
                    threshold=score_threshold,
                    text_threshold=GROUNDING_DINO_TEXT_THR,
                    target_sizes=[(height, width)],
                    text_labels=[label_list],
                )

        try:
            with memory_guard("grounding_dino"):
                results = safe_predict(
                    _do_forward,
                    on_oom=cuda_cleanup,
                    max_retries=1,
                    fallback=lambda: [],
                    name="grounding_dino.run",
                )
        except Exception as exc:
            print(f"[grounding_dino] inference failed: {exc}")
            return []

        if not results:
            return []
        result = results[0]
        boxes = result.get("boxes")
        scores = result.get("scores")
        labels = result.get("text_labels") or result.get("labels")
        if boxes is None or scores is None or labels is None:
            return []

        chunk_out: list[tuple[np.ndarray, list[float], float, str]] = []
        boxes_np = boxes.detach().cpu().numpy() if hasattr(boxes, "detach") else np.asarray(boxes)
        scores_np = scores.detach().cpu().numpy() if hasattr(scores, "detach") else np.asarray(scores)
        for box, score, label in zip(boxes_np, scores_np, labels):
            score_f = float(score)
            if score_f < score_threshold:
                continue
            # Grounding DINO's post-processor returns short token-span strings
            # (e.g. "oil" from "oil or gas facility", or "fixed - wing
            # aircraft" with mangled punctuation). Map back to the canonical
            # prompt so the detection routes to the right ontology branch.
            canonical = _map_to_original_prompt(str(label), label_list)
            if canonical is None:
                continue
            x1, y1, x2, y2 = (float(v) for v in box[:4])
            mask = _bbox_mask(x1, y1, x2, y2, height, width)
            chunk_out.append((mask, [x1, y1, x2, y2], score_f, canonical))
        return chunk_out

    all_prompts = list(prompts)
    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for i in range(0, len(all_prompts), GROUNDING_DINO_MAX_PHRASES):
        out.extend(_forward_chunk(all_prompts[i:i + GROUNDING_DINO_MAX_PHRASES]))
    return out


def _map_to_original_prompt(returned_label: str, prompts: list[str]) -> str | None:
    """Map Grounding DINO's parsed label back to the original prompt string.

    Returns the original prompt that best contains the returned tokens. If
    nothing matches, returns ``None`` so the candidate is dropped — better to
    drop than to pollute downstream classification with an opaque label.
    """
    norm_ret = returned_label.lower().replace("-", " ").replace("_", " ")
    norm_ret = " ".join(norm_ret.split())
    if not norm_ret:
        return None
    # Prefer exact-prompt match
    for p in prompts:
        if p.lower() == returned_label.lower():
            return p
    # Then prefer the prompt whose lowercased form contains all returned tokens
    ret_tokens = set(norm_ret.split())
    best = None
    best_overlap = 0
    for p in prompts:
        norm_p = p.lower().replace("-", " ").replace("_", " ")
        norm_p = " ".join(norm_p.split())
        p_tokens = set(norm_p.split())
        if not p_tokens or not ret_tokens:
            continue
        if ret_tokens.issubset(p_tokens):
            overlap = len(ret_tokens & p_tokens)
            if overlap > best_overlap:
                best = p
                best_overlap = overlap
    return best


def _bbox_mask(x1: float, y1: float, x2: float, y2: float, height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=bool)
    xi1 = max(0, int(round(x1))); xi2 = min(width, int(round(x2)))
    yi1 = max(0, int(round(y1))); yi2 = min(height, int(round(y2)))
    if xi2 > xi1 and yi2 > yi1:
        mask[yi1:yi2, xi1:xi2] = True
    return mask


def model_versions(bundle: dict[str, Any] | None) -> dict[str, Any]:
    if bundle is None:
        return {"loaded": False}
    return {
        "loaded": bundle.get("model") is not None,
        "repo_id": bundle.get("repo_id"),
        "threshold": GROUNDING_DINO_THR,
        "text_threshold": GROUNDING_DINO_TEXT_THR,
        "imgsz": GROUNDING_DINO_IMGSZ,
        "error": bundle.get("error"),
    }
