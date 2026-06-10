"""Open-vocabulary text-to-box detector — HTTP client for the LAE-DINO sidecar.

This module keeps the *layer identity* `grounding_dino` (gate, profile slot,
`source_layer` provenance, health field, frontend toggle, calibration all key
off this name) but the model behind it is now **LAE-DINO** ("Locate Anything on
Earth", AAAI'25) — a Grounding-DINO derivative fine-tuned on the LAE-1M
aerial/satellite corpus (DIOR + DOTAv2 + FAIR1M + xView + …) instead of the
natural-image IDEA-Research weights it replaces.

Why a remote call instead of in-process: LAE-DINO ships as a forked mmdetection
(mmcv/mmengine + custom `LAEDINO(DINO)` registry) whose torch/transformers pins
conflict with the SAM 3 / TerraMind / Prithvi stack in this service. It runs in
the separate `inference-lae` container; this client POSTs chips to it and maps
the returned boxes back into the SAM3-shaped `(mask, bbox_xyxy, score, label)`
tuple so detections still merge into `fusion.mask_aware_nms` alongside DOTA-OBB
and SAM 3. The "mask" is a filled bbox-rectangle (LAE-DINO emits boxes only);
SAM 3 supplies pixel-perfect masks downstream.

See docs/decisions/why-lae-dino-replaces-grounding-dino.md and
docs/inference/lae-dino-sidecar.md.
"""
from __future__ import annotations

import io
import os
from typing import Any, Iterable

import numpy as np


# Sidecar endpoint. The layer is gated OFF by default (SAM3_LOAD_GROUNDING_DINO);
# enabling it requires bringing up the inference-lae service.
LAE_DINO_URL = os.getenv("LAE_DINO_URL", "http://inference-lae:8010").rstrip("/")
LAE_DINO_TIMEOUT = float(os.getenv("LAE_DINO_TIMEOUT", "30"))
# 0.30 box / 0.25 text — a firm floor keeps open-vocabulary false positives
# down on overhead imagery (the dominant failure mode). Names kept as
# GROUNDING_DINO_* so the existing env/compose/threshold wiring is untouched.
GROUNDING_DINO_THR = float(os.getenv("GROUNDING_DINO_THRESHOLD", "0.30"))
GROUNDING_DINO_TEXT_THR = float(os.getenv("GROUNDING_DINO_TEXT_THRESHOLD", "0.25"))
GROUNDING_DINO_IMGSZ = int(os.getenv("GROUNDING_DINO_IMGSZ", "1024"))
# Max phrases per request. Concatenating a long caption makes adjacent concepts
# "bleed" into each other's token spans; chunking the vocabulary into short
# queries keeps each phrase cleanly grounded. Detections from every chunk merge
# in fusion.mask_aware_nms downstream, so chunking is transparent.
GROUNDING_DINO_MAX_PHRASES = int(os.getenv("GROUNDING_DINO_MAX_PHRASES_PER_QUERY", "10"))

# Human-readable identity surfaced in /health.
_MODEL_ID = os.getenv("LAE_DINO_MODEL_ID", "LAE-DINO (lae_dino_swint_lae1m)")


def load(device: str) -> dict[str, Any]:
    """Probe the LAE-DINO sidecar and return a bundle.

    No GPU work happens in this process — the sidecar owns its own device. The
    `device` arg is kept for interface parity with the other detector loaders.
    A reachable sidecar yields a truthy `model` sentinel so main.py's
    `bundle.get("grounding_dino")` gate and `_model_loaded` checks behave exactly
    as they did for the in-process detector. An unreachable sidecar returns a
    bundle with `model=None` + an error, so the layer simply does not run
    (graceful degradation, same as a missing dependency previously).
    """
    try:
        import requests
    except ImportError as exc:
        return {"model": None, "device": device, "repo_id": _MODEL_ID, "url": LAE_DINO_URL, "error": str(exc)}
    try:
        resp = requests.get(f"{LAE_DINO_URL}/health", timeout=5)
        resp.raise_for_status()
        info = resp.json()
        loaded = bool(info.get("model_loaded"))
        return {
            "model": True if loaded else None,
            "device": device,
            "repo_id": info.get("model", _MODEL_ID),
            "url": LAE_DINO_URL,
            "error": None if loaded else (info.get("model_error") or "sidecar model not loaded"),
        }
    except Exception as exc:
        print(f"[grounding_dino] LAE-DINO sidecar unreachable at {LAE_DINO_URL}: {exc}")
        return {"model": None, "device": device, "repo_id": _MODEL_ID, "url": LAE_DINO_URL, "error": str(exc)}


def run(
    bundle: dict[str, Any] | None,
    image_rgb_uint8: np.ndarray,
    prompts: Iterable[str],
    score_threshold: float = GROUNDING_DINO_THR,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """POST a chip to the LAE-DINO sidecar and return SAM3-shaped tuples.

    The vocabulary is split into chunks of at most GROUNDING_DINO_MAX_PHRASES
    phrases per request to avoid cross-concept token bleed; detections from
    every chunk are concatenated and deduped later by fusion.mask_aware_nms.
    """
    if bundle is None or bundle.get("model") is None:
        return []
    prompts = [p for p in prompts if p and not p.startswith("__")]
    if not prompts:
        return []

    try:
        import requests
        from PIL import Image
    except Exception as exc:
        print(f"[grounding_dino] dependency missing: {exc}")
        return []

    url = bundle.get("url", LAE_DINO_URL)
    height, width = image_rgb_uint8.shape[:2]

    # Encode the chip once (PNG, lossless) and reuse the bytes across chunks.
    buf = io.BytesIO()
    Image.fromarray(image_rgb_uint8).save(buf, format="PNG")
    png_bytes = buf.getvalue()

    def _forward_chunk(label_list: list[str]) -> list[tuple[np.ndarray, list[float], float, str]]:
        import json as _json
        try:
            resp = requests.post(
                f"{url}/detect",
                files={"file": ("chip.png", png_bytes, "image/png")},
                data={
                    "prompts": _json.dumps(label_list),
                    "threshold": str(score_threshold),
                    "text_threshold": str(GROUNDING_DINO_TEXT_THR),
                },
                timeout=LAE_DINO_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            print(f"[grounding_dino] LAE-DINO request failed: {exc}")
            return []
        if payload.get("error"):
            print(f"[grounding_dino] LAE-DINO error: {payload['error']}")
        return _dets_to_tuples(payload.get("detections", []), label_list, score_threshold, height, width)

    all_prompts = list(prompts)
    out: list[tuple[np.ndarray, list[float], float, str]] = []
    for i in range(0, len(all_prompts), GROUNDING_DINO_MAX_PHRASES):
        out.extend(_forward_chunk(all_prompts[i:i + GROUNDING_DINO_MAX_PHRASES]))
    return out


def run_batch(
    bundle: dict[str, Any] | None,
    images_rgb_uint8: list[np.ndarray],
    prompts: Iterable[str],
    score_threshold: float = GROUNDING_DINO_THR,
) -> list[list[tuple[np.ndarray, list[float], float, str]]]:
    """Batched variant of run(): POST N chips that share one prompt set to the
    sidecar's /detect_batch (one mmdet forward) and return per-chip SAM3-shaped
    tuples, in input order. Used by the inference-sam3 /detect_batch_raw path
    when SENTINEL_ENABLE_BATCHING is on. Degrades to empty lists on any error.
    """
    n = len(images_rgb_uint8)
    empty: list[list[tuple[np.ndarray, list[float], float, str]]] = [[] for _ in range(n)]
    if bundle is None or bundle.get("model") is None or n == 0:
        return empty
    prompts = [p for p in prompts if p and not p.startswith("__")]
    if not prompts:
        return empty

    try:
        import json as _json
        import requests
        from PIL import Image
    except Exception as exc:
        print(f"[grounding_dino] dependency missing: {exc}")
        return empty

    url = bundle.get("url", LAE_DINO_URL)
    dims = [(img.shape[0], img.shape[1]) for img in images_rgb_uint8]
    png_list: list[bytes] = []
    for img in images_rgb_uint8:
        buf = io.BytesIO()
        Image.fromarray(img).save(buf, format="PNG")
        png_list.append(buf.getvalue())

    out: list[list[tuple[np.ndarray, list[float], float, str]]] = [[] for _ in range(n)]
    all_prompts = list(prompts)
    for i in range(0, len(all_prompts), GROUNDING_DINO_MAX_PHRASES):
        label_list = all_prompts[i:i + GROUNDING_DINO_MAX_PHRASES]
        files = [("files", (f"chip{j}.png", png_list[j], "image/png")) for j in range(n)]
        try:
            resp = requests.post(
                f"{url}/detect_batch",
                files=files,
                data={
                    "prompts": _json.dumps(label_list),
                    "threshold": str(score_threshold),
                    "text_threshold": str(GROUNDING_DINO_TEXT_THR),
                },
                timeout=LAE_DINO_TIMEOUT * max(1, n),
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            print(f"[grounding_dino] LAE-DINO batch request failed: {exc}")
            continue
        if payload.get("error"):
            print(f"[grounding_dino] LAE-DINO batch error: {payload['error']}")
        results = payload.get("results") or []
        for j in range(n):
            dets = results[j] if j < len(results) else []
            h, w = dims[j]
            out[j].extend(_dets_to_tuples(dets, label_list, score_threshold, h, w))
    return out


def _dets_to_tuples(
    detections: list[dict[str, Any]],
    label_list: list[str],
    score_threshold: float,
    height: int,
    width: int,
) -> list[tuple[np.ndarray, list[float], float, str]]:
    """Convert the sidecar's JSON detections into SAM3-shaped tuples. LAE-DINO
    returns the matched entity string; map it back to the operator's canonical
    prompt so the detection routes to the right ontology branch (drops
    opaque/unmappable labels)."""
    tuples: list[tuple[np.ndarray, list[float], float, str]] = []
    for det in detections:
        score_f = float(det.get("score", 0.0))
        if score_f < score_threshold:
            continue
        canonical = _map_to_original_prompt(str(det.get("label", "")), label_list)
        if canonical is None:
            continue
        box = det.get("bbox") or []
        if len(box) < 4:
            continue
        x1, y1, x2, y2 = (float(v) for v in box[:4])
        mask = _bbox_mask(x1, y1, x2, y2, height, width)
        tuples.append((mask, [x1, y1, x2, y2], score_f, canonical))
    return tuples


def _map_to_original_prompt(returned_label: str, prompts: list[str]) -> str | None:
    """Map LAE-DINO's parsed entity string back to the original prompt string.

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
        "url": bundle.get("url", LAE_DINO_URL),
        "threshold": GROUNDING_DINO_THR,
        "text_threshold": GROUNDING_DINO_TEXT_THR,
        "imgsz": GROUNDING_DINO_IMGSZ,
        "error": bundle.get("error"),
    }
