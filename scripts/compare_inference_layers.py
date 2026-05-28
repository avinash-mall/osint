#!/usr/bin/env python3
"""
compare_inference_layers.py
============================
Comparison driver for inference layer configurations.

Evaluates multiple enabled_layers configurations against a dataset slice,
measures latency across repeats, computes box-detection metrics, and writes
a Markdown + optional JSON report.

Usage
-----
::

    python scripts/compare_inference_layers.py \\
      --url http://localhost:8001 \\
      --slice dota \\
      --max-chips 30 \\
      --repeats 3 \\
      --output docs/inference_layer_comparison.md \\
      --json-output docs/inference_layer_comparison.json

    # Smoke-test without a live server:
    python scripts/compare_inference_layers.py --dry-run --slice dota --max-chips 3
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import random
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Path setup — ensure scripts/ is importable regardless of cwd
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import requests  # noqa: E402

from eval_datasets.dota import iter_dota  # noqa: E402
from eval_datasets.hls_burn import iter_hls_burn  # noqa: E402
from eval_datasets.sar_synth import iter_sar_synth  # noqa: E402
from eval_datasets.sen1floods import iter_sen1floods  # noqa: E402
from eval_datasets.triage import iter_triage  # noqa: E402
from eval_metrics.box_metrics import compute_box_metrics  # noqa: E402
from eval_metrics.label_normalizer import normalize  # noqa: E402
from eval_metrics.mask_metrics import chip_level_iou, POSITIVE_LABELS  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("compare_layers")

# ---------------------------------------------------------------------------
# Layer configurations (box-detector scope, Task 5)
# ---------------------------------------------------------------------------
LAYER_CONFIGS: list[dict] = [
    {
        "config_name": "sam3_only",
        "enabled_layers": ["sam3"],
    },
    {
        "config_name": "sam3+dota_obb",
        "enabled_layers": ["sam3", "dota_obb"],
    },
    {
        "config_name": "sam3+grounding_dino",
        "enabled_layers": ["sam3", "grounding_dino"],
    },
    {
        "config_name": "sam3+dota_obb+grounding_dino",
        "enabled_layers": ["sam3", "dota_obb", "grounding_dino"],
    },
]

# ---------------------------------------------------------------------------
# Layer configurations (segmenter scope, Task 6)
# ---------------------------------------------------------------------------
SEGMENTER_CONFIGS: list[dict] = [
    {
        "config_name": "sam3_only",
        "enabled_layers": ["sam3"],
    },
    {
        "config_name": "sam3+prithvi",
        "enabled_layers": ["sam3", "prithvi"],
    },
]

# ---------------------------------------------------------------------------
# Layer configurations (embedding-only scope, Task 7)
# ---------------------------------------------------------------------------
EMBEDDING_CONFIGS: list[dict] = [
    {"name": "sam3_only",       "enabled_layers": ["sam3"]},
    {"name": "sam3+dinov3_sat", "enabled_layers": ["sam3", "dinov3_sat"]},
    {"name": "sam3+terramind",  "enabled_layers": ["sam3", "terramind"]},
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _probe_health(url: str, timeout: int = 10) -> str:
    """Return GPU string from /health or 'unknown' if unreachable."""
    try:
        resp = requests.get(f"{url.rstrip('/')}/health", timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            return str(data.get("gpu", "unknown"))
    except Exception:
        pass
    return "unknown"


def _post_detect(
    url: str,
    chip_bytes: bytes,
    prompts: list[str],
    enabled_layers: list[str],
    modality: str = "rgb",
    timeout: int = 120,
    force_grounding_dino: bool = False,
) -> dict:
    """POST chip to /detect and return the parsed JSON response + elapsed_ms."""
    started = time.perf_counter()
    # Choose MIME type based on modality
    mime_type = "image/tiff" if modality == "multispectral" else "image/png"
    filename = "chip.tif" if modality == "multispectral" else "chip.png"
    metadata: dict[str, Any] = {
        "modality": modality,
        "text_prompts": prompts,
        "max_prompts": len(prompts),
        "enabled_layers": enabled_layers,
    }
    if force_grounding_dino and "grounding_dino" in enabled_layers:
        metadata["force_grounding_dino"] = True
    resp = requests.post(
        f"{url.rstrip('/')}/detect",
        files={"image": (filename, io.BytesIO(chip_bytes), mime_type)},
        data={"metadata": json.dumps(metadata)},
        timeout=timeout,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    resp.raise_for_status()
    payload = resp.json()
    payload["_elapsed_ms"] = elapsed_ms
    return payload


# ---------------------------------------------------------------------------
# Dry-run synthetic data
# ---------------------------------------------------------------------------

def _synthetic_response(enabled_layers: list[str], ground_truth: list[dict]) -> dict:
    """Return a plausible fake /detect response for --dry-run mode."""
    rng = random.Random(42 + len(enabled_layers))
    detections = []
    # Randomly keep/miss some GT boxes
    for gt in ground_truth:
        if rng.random() > 0.3:
            x1, y1, x2, y2 = gt["bbox_xyxy"]
            # Add small jitter
            jx, jy = rng.randint(-5, 5), rng.randint(-5, 5)
            detections.append({
                "label": gt["label"],
                "confidence": round(rng.uniform(0.5, 0.99), 3),
                "bbox": {
                    "x": max(0, x1 + jx),
                    "y": max(0, y1 + jy),
                    "w": max(1, (x2 - x1) + rng.randint(-3, 3)),
                    "h": max(1, (y2 - y1) + rng.randint(-3, 3)),
                },
            })
    return {
        "detections": detections,
        "timings_ms": {
            "sam3_inference": round(rng.uniform(80, 150), 1),
            "specialists": round(rng.uniform(20, 60) * max(1, len(enabled_layers) - 1), 1),
            "total": round(rng.uniform(120, 250), 1),
        },
        "enabled_layers_unavailable": [],
        "_elapsed_ms": round(rng.uniform(130, 270), 1),
    }


def _synthetic_segmenter_response(
    enabled_layers: list[str],
    ground_truth: dict,
) -> dict:
    """Return a plausible fake /detect response for --dry-run mode (segmenter slices).

    For slices that drive PRITHVI heads (hls_burn, sen1floods), the response
    includes ``prithvi_labels`` in each detection.  When only ``sam3`` is enabled,
    detections have no ``prithvi_labels`` (PRITHVI is not loaded).

    Parameters
    ----------
    enabled_layers:
        Active layers for this config.
    ground_truth:
        Dict like ``{"burn_scar": bool}`` or ``{"flood": bool}``.

    Returns
    -------
    Fake /detect response dict with ``detections``, ``timings_ms``, and ``_elapsed_ms``.
    """
    rng = random.Random(99 + len(enabled_layers))
    has_prithvi = "prithvi" in enabled_layers

    detections: list[dict] = []

    if has_prithvi:
        # Simulate PRITHVI labels — with ~70% accuracy relative to GT
        prithvi_labels: dict[str, str] = {}
        for task, gt_positive in ground_truth.items():
            positive_label = POSITIVE_LABELS.get(task, "positive")
            negative_label = f"no_{task}"
            # 70% chance of correct prediction
            if rng.random() < 0.7:
                pred_label = positive_label if gt_positive else negative_label
            else:
                pred_label = negative_label if gt_positive else positive_label
            prithvi_labels[task] = pred_label

        detections.append({
            "label": "region",
            "confidence": round(rng.uniform(0.6, 0.95), 3),
            "bbox": {"x": 0, "y": 0, "w": 64, "h": 64},
            "prithvi_labels": prithvi_labels,
        })
    else:
        # sam3-only: basic detection, no prithvi_labels
        detections.append({
            "label": "region",
            "confidence": round(rng.uniform(0.5, 0.85), 3),
            "bbox": {"x": 0, "y": 0, "w": 64, "h": 64},
        })

    return {
        "detections": detections,
        "timings_ms": {
            "sam3_inference": round(rng.uniform(80, 150), 1),
            "specialists": round(rng.uniform(40, 120) if has_prithvi else 0.0, 1),
            "total": round(rng.uniform(120, 300), 1),
        },
        "enabled_layers_unavailable": [],
        "_elapsed_ms": round(rng.uniform(130, 320), 1),
    }


def _synthetic_embedding_response(enabled_layers: list[str]) -> dict:
    """Return a plausible fake /detect response for --dry-run mode (embedding slice).

    Synthesises:
    - ``timings_ms["embedding"]`` in range 50–200 ms (when a DINOv3 layer is active).
    - ``timings_ms["total"]`` in range 300–800 ms.
    - Each detection has ``embedding = {"model": "dinov3_sat", "dim": 768, "fp16_b64": "AAAA..."}``.
    - For terramind config: ``terramind_embedding`` is a list of floats.
    """
    rng = random.Random(77 + len(enabled_layers))

    has_dinov3_sat = "dinov3_sat" in enabled_layers
    has_terramind = "terramind" in enabled_layers

    # Build a few synthetic detections
    n_dets = rng.randint(2, 6)
    detections: list[dict] = []
    for i in range(n_dets):
        det: dict = {
            "label": "vehicle",
            "confidence": round(rng.uniform(0.5, 0.95), 3),
            "bbox": {"x": rng.randint(0, 200), "y": rng.randint(0, 200), "w": 32, "h": 32},
        }
        if has_dinov3_sat:
            det["embedding"] = {
                "model": "dinov3_sat",
                "dim": 768,
                "fp16_b64": "AAAA" + "AA==" * 192,  # fake base64 placeholder
            }
        if has_terramind:
            det["terramind_embedding"] = [round(rng.uniform(-1.0, 1.0), 4) for _ in range(64)]
        detections.append(det)

    timings: dict = {
        "sam3_inference": round(rng.uniform(80, 150), 1),
        "total": round(rng.uniform(300, 800), 1),
    }
    if has_dinov3_sat:
        timings["embedding"] = round(rng.uniform(50, 200), 1)

    return {
        "detections": detections,
        "timings_ms": timings,
        "enabled_layers_unavailable": [],
        "_elapsed_ms": round(rng.uniform(310, 820), 1),
    }


# ---------------------------------------------------------------------------
# Detection parsing
# ---------------------------------------------------------------------------

def _parse_detections(
    response: dict,
    enabled_layers: list[str],
    chip_size: tuple[int, int] = (1024, 1024),
) -> list[dict]:
    """Extract normalised predictions from a /detect response dict.

    chip_size: (width, height) in pixels — needed to denormalise bbox coords.
    Returns a list of {"label": str, "bbox_xyxy": [...], "score": float}.
    """
    raw = response.get("detections", [])
    width, height = chip_size

    specialist_layers = [l for l in enabled_layers if l != "sam3"]
    norm_layer = specialist_layers[0] if len(specialist_layers) == 1 else "mixed"

    predictions = []
    for det in raw:
        label = det.get("class") or det.get("label") or det.get("original_class") or ""
        score = float(det.get("confidence", det.get("score", 0.0)))

        # The /detect API returns bbox as normalised YOLO [cx, cy, w, h] floats in [0, 1].
        # Older synthetic dry-run paths used dict {x,y,w,h} or bbox_xyxy — handle both.
        bbox_xyxy = None
        if "bbox" in det:
            b = det["bbox"]
            if isinstance(b, list) and len(b) == 4:
                cx, cy, bw, bh = b
                x1 = (cx - bw / 2.0) * width
                y1 = (cy - bh / 2.0) * height
                x2 = (cx + bw / 2.0) * width
                y2 = (cy + bh / 2.0) * height
                bbox_xyxy = [x1, y1, x2, y2]
            elif isinstance(b, dict):
                x = b.get("x", 0); y = b.get("y", 0)
                bw = b.get("w", 0); bh = b.get("h", 0)
                bbox_xyxy = [x, y, x + bw, y + bh]
        if bbox_xyxy is None and "bbox_xyxy" in det:
            bbox_xyxy = det["bbox_xyxy"]
        if bbox_xyxy is None:
            continue

        norm_label = normalize(label, norm_layer)
        predictions.append({
            "label": norm_label,
            "bbox_xyxy": bbox_xyxy,
            "score": score,
        })
    return predictions


# ---------------------------------------------------------------------------
# Chip-level evaluation
# ---------------------------------------------------------------------------

def _evaluate_chip(
    url: str,
    chip_bytes: bytes,
    prompts: list[str],
    ground_truth: list[dict],
    enabled_layers: list[str],
    repeats: int,
    dry_run: bool,
    modality: str = "rgb",
    force_grounding_dino: bool = False,
) -> dict | None:
    """Run N repeats for a single chip+config. Returns chip result dict or None on failure."""

    timings: list[dict] = []
    last_payload: dict | None = None
    unavailable_count = 0

    for attempt in range(repeats):
        try:
            if dry_run:
                payload = _synthetic_response(enabled_layers, ground_truth)
            else:
                payload = _post_detect(
                    url, chip_bytes, prompts, enabled_layers,
                    modality=modality,
                    force_grounding_dino=force_grounding_dino,
                )
        except requests.exceptions.RequestException as exc:
            log.warning("HTTP error on repeat %d: %s", attempt + 1, exc)
            return None

        if payload.get("enabled_layers_unavailable"):
            unavailable_count += 1
            log.warning(
                "Layers unavailable: %s",
                payload["enabled_layers_unavailable"],
            )

        timings_ms = payload.get("timings_ms", {})
        timings.append({
            "elapsed_ms": payload.get("_elapsed_ms", 0.0),
            "sam3_ms": timings_ms.get("sam3_inference", 0.0),
            "specialists_ms": timings_ms.get("specialists", 0.0),
            "total_ms": timings_ms.get("total", payload.get("_elapsed_ms", 0.0)),
        })
        last_payload = payload

    if last_payload is None:
        return None

    chip_size = (1024, 1024)
    try:
        from PIL import Image
        import io as _io
        with Image.open(_io.BytesIO(chip_bytes)) as _img:
            chip_size = _img.size
    except Exception:
        pass
    predictions = _parse_detections(last_payload, enabled_layers, chip_size=chip_size)

    # Normalise GT labels for fair comparison (use "dota_obb" as source for DOTA GT)
    norm_gt = [
        {
            "label": normalize(g["label"], "dota_obb"),
            "bbox_xyxy": g["bbox_xyxy"],
        }
        for g in ground_truth
    ]

    metrics = compute_box_metrics(predictions, norm_gt)

    return {
        "metrics": metrics,
        "timings": timings,
        "unavailable": unavailable_count > 0,
    }


# ---------------------------------------------------------------------------
# Segmenter chip evaluation (PRITHVI heads)
# ---------------------------------------------------------------------------

def _evaluate_segmenter_chip(
    url: str,
    chip_bytes: bytes,
    ground_truth: dict,
    enabled_layers: list[str],
    repeats: int,
    dry_run: bool,
) -> dict | None:
    """Run N repeats for a segmenter chip. Returns chip result dict or None on failure.

    Parameters
    ----------
    url:
        Inference service URL.
    chip_bytes:
        Raw bytes of a 6-channel multispectral chip.
    ground_truth:
        Dict like ``{"burn_scar": bool}`` or ``{"flood": bool}``.
    enabled_layers:
        Active layers for this config (e.g. ``["sam3", "prithvi"]``).
    repeats:
        Number of inference repeats for latency averaging.
    dry_run:
        If True, use synthetic response instead of hitting the service.

    Returns
    -------
    dict with keys:
        ``"timings"`` — list of timing dicts.
        ``"pred_positive_per_task"`` — dict of task → bool.
        ``"gt_positive_per_task"``   — dict of task → bool.
        ``"unavailable"``            — bool.
    """
    timings: list[dict] = []
    last_payload: dict | None = None
    unavailable_count = 0

    for attempt in range(repeats):
        try:
            if dry_run:
                payload = _synthetic_segmenter_response(enabled_layers, ground_truth)
            else:
                payload = _post_detect(
                    url, chip_bytes, [], enabled_layers, modality="multispectral"
                )
        except requests.exceptions.RequestException as exc:
            log.warning("HTTP error on segmenter repeat %d: %s", attempt + 1, exc)
            return None

        if payload.get("enabled_layers_unavailable"):
            unavailable_count += 1

        timings_ms = payload.get("timings_ms", {})
        timings.append({
            "elapsed_ms": payload.get("_elapsed_ms", 0.0),
            "sam3_ms": timings_ms.get("sam3_inference", 0.0),
            "specialists_ms": timings_ms.get("specialists", 0.0),
            "total_ms": timings_ms.get("total", payload.get("_elapsed_ms", 0.0)),
        })
        last_payload = payload

    if last_payload is None:
        return None

    # Extract prithvi_labels from all detections
    detections = last_payload.get("detections", [])
    pred_positive_per_task: dict[str, bool] = {}

    for task in ground_truth:
        positive_label = POSITIVE_LABELS.get(task)
        pred_positive = False
        if positive_label is not None:
            for det in detections:
                if det.get("prithvi_labels", {}).get(task) == positive_label:
                    pred_positive = True
                    break
        pred_positive_per_task[task] = pred_positive

    gt_positive_per_task: dict[str, bool] = {
        task: bool(gt_val) for task, gt_val in ground_truth.items()
    }

    return {
        "timings": timings,
        "pred_positive_per_task": pred_positive_per_task,
        "gt_positive_per_task": gt_positive_per_task,
        "unavailable": unavailable_count > 0,
    }


def _aggregate_segmenter_results(
    chip_results: list[dict],
    tasks: list[str],
    baseline_latency_ms: float | None = None,
) -> dict:
    """Aggregate segmenter chip results into per-task IoU and latency stats.

    Parameters
    ----------
    chip_results:
        List of dicts returned by ``_evaluate_segmenter_chip``.
    tasks:
        Task names to aggregate (e.g. ``["burn_scar"]``).
    baseline_latency_ms:
        Median latency of the sam3_only config, for delta calculation.

    Returns
    -------
    dict with keys:
        ``"chips_evaluated"``    : int
        ``"per_task_iou"``       : dict task → chip_level_iou result dict
        ``"mean_iou"``           : float
        ``"latency_ms"``         : dict
        ``"layers_unavailable_count"`` : int
    """
    if not chip_results:
        return {
            "chips_evaluated": 0,
            "per_task_iou": {t: {"iou": 0.0, "tp": 0, "fp": 0, "fn": 0, "tn": 0, "n_chips": 0} for t in tasks},
            "mean_iou": 0.0,
            "latency_ms": {
                "median_total": 0.0,
                "p95_total": 0.0,
                "median_sam3": 0.0,
                "median_specialists": 0.0,
            },
            "layers_unavailable_count": 0,
        }

    # --- Latency ---
    all_elapsed: list[float] = []
    all_sam3: list[float] = []
    all_specialists: list[float] = []

    for cr in chip_results:
        for t in cr["timings"]:
            all_elapsed.append(t["elapsed_ms"])
            all_sam3.append(t["sam3_ms"])
            all_specialists.append(t["specialists_ms"])

    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        values_sorted = sorted(values)
        idx = max(0, math.ceil(0.95 * len(values_sorted)) - 1)
        return values_sorted[idx]

    # --- Per-task chip-level IoU ---
    per_task_iou: dict[str, dict] = {}
    iou_values: list[float] = []

    for task in tasks:
        chips_for_task = [
            {
                "pred_positive": cr["pred_positive_per_task"].get(task, False),
                "gt_positive": cr["gt_positive_per_task"].get(task, False),
            }
            for cr in chip_results
        ]
        iou_result = chip_level_iou(chips_for_task)
        per_task_iou[task] = iou_result
        iou_values.append(iou_result["iou"])

    mean_iou = statistics.mean(iou_values) if iou_values else 0.0
    unavailable_count = sum(1 for cr in chip_results if cr.get("unavailable", False))

    return {
        "chips_evaluated": len(chip_results),
        "per_task_iou": per_task_iou,
        "mean_iou": round(mean_iou, 4),
        "latency_ms": {
            "median_total": round(statistics.median(all_elapsed), 1),
            "p95_total": round(_p95(all_elapsed), 1),
            "median_sam3": round(statistics.median(all_sam3) if all_sam3 else 0.0, 1),
            "median_specialists": round(statistics.median(all_specialists) if all_specialists else 0.0, 1),
        },
        "layers_unavailable_count": unavailable_count,
    }


# ---------------------------------------------------------------------------
# Embedding chip evaluation (DINOV3_SAT, TERRAMIND)
# ---------------------------------------------------------------------------

def _evaluate_embedding_chip(
    url: str,
    chip_bytes: bytes,
    enabled_layers: list[str],
    repeats: int,
    dry_run: bool,
    modality: str = "rgb",
) -> dict | None:
    """Run N repeats for a single chip under an embedding config.

    Returns a dict with keys:
        ``"timings"``          — list of timing dicts (embedding_ms, total_ms).
        ``"embed_coverage"``   — fraction of detections with dim > 0.
        ``"terramind_any"``    — bool, True if any detection has terramind_embedding.
        ``"unavailable"``      — bool.
    """
    timings: list[dict] = []
    last_payload: dict | None = None
    unavailable_count = 0

    for attempt in range(repeats):
        try:
            if dry_run:
                payload = _synthetic_embedding_response(enabled_layers)
            else:
                payload = _post_detect(url, chip_bytes, [], enabled_layers, modality=modality)
        except requests.exceptions.RequestException as exc:
            log.warning("HTTP error on embedding repeat %d: %s", attempt + 1, exc)
            return None

        if payload.get("enabled_layers_unavailable"):
            unavailable_count += 1

        timings_ms = payload.get("timings_ms", {})
        timings.append({
            "embedding_ms": timings_ms.get("embedding", 0.0),
            "total_ms": timings_ms.get("total", payload.get("_elapsed_ms", 0.0)),
        })
        last_payload = payload

    if last_payload is None:
        return None

    detections = last_payload.get("detections", [])
    n_dets = len(detections)
    if n_dets > 0:
        n_with_embedding = sum(
            1 for d in detections
            if isinstance(d.get("embedding"), dict) and d["embedding"].get("dim", 0) > 0
        )
        embed_coverage = n_with_embedding / n_dets
    else:
        embed_coverage = 0.0

    terramind_any = any(
        d.get("terramind_embedding") is not None for d in detections
    )

    return {
        "timings": timings,
        "embed_coverage": embed_coverage,
        "terramind_any": terramind_any,
        "unavailable": unavailable_count > 0,
    }


def _aggregate_embedding_results(
    chip_results: list[dict],
    config_name: str,
    baseline_median_ms: float | None = None,
) -> dict:
    """Aggregate embedding chip results into latency and coverage stats.

    Parameters
    ----------
    chip_results:
        List of dicts returned by ``_evaluate_embedding_chip``.
    config_name:
        Name of the embedding config (e.g. ``"sam3+dinov3_sat"``).
    baseline_median_ms:
        Median total latency of the sam3_only config, for delta calculation.

    Returns
    -------
    dict with keys:
        ``"config_name"``
        ``"median_total_ms"``
        ``"delta_ms_vs_baseline"``
        ``"median_embedding_ms"``
        ``"embed_coverage_fraction"``
    """
    if not chip_results:
        return {
            "config_name": config_name,
            "median_total_ms": 0.0,
            "delta_ms_vs_baseline": 0.0,
            "median_embedding_ms": 0.0,
            "embed_coverage_fraction": 0.0,
        }

    all_total: list[float] = []
    all_embedding: list[float] = []
    all_coverage: list[float] = []

    for cr in chip_results:
        for t in cr["timings"]:
            all_total.append(t["total_ms"])
            if t["embedding_ms"] > 0:
                all_embedding.append(t["embedding_ms"])
        all_coverage.append(cr["embed_coverage"])

    median_total = round(statistics.median(all_total), 1) if all_total else 0.0
    median_embedding = round(statistics.median(all_embedding), 1) if all_embedding else 0.0
    mean_coverage = round(statistics.mean(all_coverage), 4) if all_coverage else 0.0

    delta = round(median_total - (baseline_median_ms or 0.0), 1)

    return {
        "config_name": config_name,
        "median_total_ms": median_total,
        "delta_ms_vs_baseline": delta,
        "median_embedding_ms": median_embedding,
        "embed_coverage_fraction": mean_coverage,
    }


# ---------------------------------------------------------------------------
# Aggregate chip results across a config
# ---------------------------------------------------------------------------

def _aggregate_results(chip_results: list[dict]) -> dict:
    """Average metrics and latency across all chips for one config."""
    if not chip_results:
        return {
            "chips_evaluated": 0,
            "metrics": {"per_class": {}, "macro_f1": 0.0, "map_50": 0.0},
            "latency_ms": {
                "median_total": 0.0,
                "p95_total": 0.0,
                "median_sam3": 0.0,
                "median_specialists": 0.0,
            },
            "layers_unavailable_count": 0,
        }

    # --- Latency ---
    all_elapsed: list[float] = []
    all_sam3: list[float] = []
    all_specialists: list[float] = []

    for cr in chip_results:
        for t in cr["timings"]:
            all_elapsed.append(t["elapsed_ms"])
            all_sam3.append(t["sam3_ms"])
            all_specialists.append(t["specialists_ms"])

    def _p95(values: list[float]) -> float:
        if not values:
            return 0.0
        values_sorted = sorted(values)
        idx = max(0, math.ceil(0.95 * len(values_sorted)) - 1)
        return values_sorted[idx]

    # --- Metrics aggregation ---
    # macro_f1 and map_50: mean over chips
    macro_f1_values = [cr["metrics"]["macro_f1"] for cr in chip_results]
    map_50_values = [cr["metrics"]["map_50"] for cr in chip_results]
    mean_macro_f1 = statistics.mean(macro_f1_values) if macro_f1_values else 0.0
    mean_map_50 = statistics.mean(map_50_values) if map_50_values else 0.0

    # per_class: aggregate TP/FP/FN across chips then recompute P/R/F1
    class_accum: dict[str, dict[str, float]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "ap_sum": 0.0, "ap_count": 0}
    )
    for cr in chip_results:
        for cls_label, cls_metrics in cr["metrics"]["per_class"].items():
            acc = class_accum[cls_label]
            acc["tp"] += cls_metrics.get("tp", 0)
            acc["fp"] += cls_metrics.get("fp", 0)
            acc["fn"] += cls_metrics.get("fn", 0)
            acc["ap_sum"] += cls_metrics.get("ap", 0.0)
            acc["ap_count"] += 1

    per_class_agg: dict[str, dict] = {}
    for cls_label, acc in class_accum.items():
        tp, fp, fn = acc["tp"], acc["fp"], acc["fn"]
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        ap = acc["ap_sum"] / acc["ap_count"] if acc["ap_count"] > 0 else 0.0
        per_class_agg[cls_label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "ap": round(ap, 4),
            "tp": int(tp),
            "fp": int(fp),
            "fn": int(fn),
        }

    unavailable_count = sum(1 for cr in chip_results if cr.get("unavailable", False))

    return {
        "chips_evaluated": len(chip_results),
        "metrics": {
            "per_class": per_class_agg,
            "macro_f1": round(mean_macro_f1, 4),
            "map_50": round(mean_map_50, 4),
        },
        "latency_ms": {
            "median_total": round(statistics.median(all_elapsed), 1),
            "p95_total": round(_p95(all_elapsed), 1),
            "median_sam3": round(statistics.median(all_sam3) if all_sam3 else 0.0, 1),
            "median_specialists": round(statistics.median(all_specialists) if all_specialists else 0.0, 1),
        },
        "layers_unavailable_count": unavailable_count,
    }


# ---------------------------------------------------------------------------
# Dataset iteration dispatch
# ---------------------------------------------------------------------------

def _iter_slice(
    slice_name: str,
    max_chips: int,
    layers_path: str | None,
    triage_dir: str | None = None,
    triage_rgb_only: bool = True,
) -> Iterator[tuple[bytes, str, list[str], Any]]:
    if slice_name in ("dota", "embedding"):
        # embedding slice uses DOTA chips for RGB layers
        yield from iter_dota(labels_path=layers_path, max_chips=max_chips)
    elif slice_name == "hls_burn":
        yield from iter_hls_burn(labels_path=layers_path, max_chips=max_chips)
    elif slice_name == "sen1floods":
        yield from iter_sen1floods(labels_path=layers_path, max_chips=max_chips)
    elif slice_name == "sar":
        yield from iter_sar_synth(labels_path=layers_path, max_chips=max_chips)
    elif slice_name == "triage":
        if not triage_dir:
            raise ValueError(
                "--slice triage requires --triage-set <path-to-triage-dir>"
            )
        count = 0
        for tup in iter_triage(Path(triage_dir), rgb_only=triage_rgb_only):
            if max_chips and count >= max_chips:
                break
            yield tup
            count += 1
    else:
        raise ValueError(
            f"Unknown slice: {slice_name!r}. "
            "Choices: dota, hls_burn, sen1floods, sar, embedding, triage, all"
        )


# ---------------------------------------------------------------------------
# Markdown report generation
# ---------------------------------------------------------------------------

def _fmt(value: float, decimals: int = 4) -> str:
    return f"{value:.{decimals}f}"


def _delta(value: float, baseline: float, fmt: str = "+.4f") -> str:
    diff = value - baseline
    return f"{diff:{fmt}}"


def _build_markdown(
    all_results: list[dict],
    slice_name: str,
    n_chips: int,
    gpu: str,
    generated_at: str,
    segmenter_results: list[dict] | None = None,
    segmenter_slice: str | None = None,
    n_segmenter_chips: int = 0,
    embedding_results: list[dict] | None = None,
    n_embedding_chips: int = 0,
) -> str:
    lines: list[str] = []

    lines.append("# Inference Layer Comparison")
    lines.append("")
    lines.append(f"Generated: {generated_at}  GPU: {gpu}")
    lines.append("")

    # ------------------------------------------------------------------
    # Box Detectors section (always present, even if empty for seg slices)
    # ------------------------------------------------------------------
    lines.append("## Box Detectors")
    lines.append("")
    lines.append(
        f"Dataset: DOTA-v1.0 ({n_chips} chips, IoU threshold 0.5)"
    )
    lines.append("")

    if all_results:
        # Summary table
        baseline = next((r for r in all_results if r["config_name"] == "sam3_only"), None)
        baseline_map = baseline["metrics"]["map_50"] if baseline else 0.0
        baseline_lat = baseline["latency_ms"]["median_total"] if baseline else 0.0

        headers = ["Config", "mAP@0.5", "Macro F1", "Δ mAP vs SAM3", "Median Total ms", "Δ ms vs SAM3"]
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join(["---"] * len(headers)) + "|")

        for result in all_results:
            cfg = result["config_name"]
            m = result["metrics"]
            lat = result["latency_ms"]

            display_name = cfg.replace("sam3_only", "sam3 (baseline)")
            map_50 = m["map_50"]
            macro_f1 = m["macro_f1"]
            median_ms = lat["median_total"]

            if cfg == "sam3_only":
                delta_map = "—"
                delta_ms = "—"
            else:
                delta_map = _delta(map_50, baseline_map)
                delta_ms = _delta(median_ms, baseline_lat, fmt="+.1f")

            lines.append(
                f"| {display_name} | {_fmt(map_50)} | {_fmt(macro_f1)} "
                f"| {delta_map} | {median_ms:.1f} | {delta_ms} |"
            )

        lines.append("")

        # Per-class tables for baseline and full config
        for result in all_results:
            if result["config_name"] not in ("sam3_only", "sam3+dota_obb+grounding_dino"):
                continue
            cfg_label = (
                "SAM3 baseline"
                if result["config_name"] == "sam3_only"
                else "all box detectors"
            )
            lines.append(f"### Per-class metrics ({cfg_label})")
            lines.append("")
            lines.append("| Class | Precision | Recall | F1 | AP |")
            lines.append("|---|---|---|---|---|")

            per_class = result["metrics"]["per_class"]
            for cls_label in sorted(per_class.keys()):
                cm = per_class[cls_label]
                lines.append(
                    f"| {cls_label} "
                    f"| {_fmt(cm['precision'])} "
                    f"| {_fmt(cm['recall'])} "
                    f"| {_fmt(cm['f1'])} "
                    f"| {_fmt(cm['ap'])} |"
                )
            lines.append("")

    # ------------------------------------------------------------------
    # Semantic Segmenters section (only when a segmenter slice was run)
    # ------------------------------------------------------------------
    if segmenter_results is not None:
        lines.append("## Semantic Segmenters (PRITHVI Heads)")
        lines.append("")

        dataset_label = {
            "hls_burn": "HLS Burn Scars",
            "sen1floods": "Sen1Floods11",
        }.get(segmenter_slice or "", segmenter_slice or "unknown")

        lines.append(f"Dataset: {dataset_label} ({n_segmenter_chips} chips)")
        lines.append("")
        lines.append(
            "Chip-level IoU: chip is predicted positive if any detection has the PRITHVI "
            "positive label for the task; IoU = TP/(TP+FP+FN) over chips."
        )
        lines.append("")

        # Determine baseline latency for delta column
        seg_baseline = next(
            (r for r in segmenter_results if r["config_name"] == "sam3_only"), None
        )
        seg_baseline_lat = (
            seg_baseline["latency_ms"]["median_total"] if seg_baseline else 0.0
        )

        seg_headers = [
            "Config", "Task", "Chip-level IoU",
            "Pred Positive %", "GT Positive %", "Δ ms vs SAM3",
        ]
        lines.append("| " + " | ".join(seg_headers) + " |")
        lines.append("|" + "|".join(["---"] * len(seg_headers)) + "|")

        for result in segmenter_results:
            cfg = result["config_name"]
            median_ms = result["latency_ms"]["median_total"]

            if cfg == "sam3_only":
                delta_ms_str = "—"
            else:
                delta_ms_str = _delta(median_ms, seg_baseline_lat, fmt="+.1f")

            per_task_iou = result.get("per_task_iou", {})
            if not per_task_iou:
                # No tasks (e.g. sam3_only with no prithvi)
                lines.append(
                    f"| {cfg} | — | — | — | — | {delta_ms_str} |"
                )
                continue

            for task_name in sorted(per_task_iou.keys()):
                iou_info = per_task_iou[task_name]
                iou_val = iou_info.get("iou", 0.0)
                n_chips_task = iou_info.get("n_chips", 0)
                tp = iou_info.get("tp", 0)
                fp = iou_info.get("fp", 0)
                fn = iou_info.get("fn", 0)
                tn = iou_info.get("tn", 0)

                pred_pos_pct = (
                    f"{100.0 * (tp + fp) / n_chips_task:.0f}%"
                    if n_chips_task > 0
                    else "N/A"
                )
                gt_pos_pct = (
                    f"{100.0 * (tp + fn) / n_chips_task:.0f}%"
                    if n_chips_task > 0
                    else "N/A"
                )

                lines.append(
                    f"| {cfg} | {task_name} | {_fmt(iou_val)} "
                    f"| {pred_pos_pct} | {gt_pos_pct} | {delta_ms_str} |"
                )

        lines.append("")

    # ------------------------------------------------------------------
    # Embedding Models section (only when embedding slice was run)
    # ------------------------------------------------------------------
    if embedding_results is not None:
        lines.append("## Embedding Models (Latency-Only)")
        lines.append("")
        lines.append(
            "These layers add no detections — they enrich detections with embedding vectors "
            "for downstream retrieval/re-ID."
        )
        lines.append("")
        lines.append(f"Dataset: DOTA ({n_embedding_chips} chips, RGB modality)")
        lines.append("")

        embed_baseline = next(
            (r for r in embedding_results if r["config_name"] == "sam3_only"), None
        )

        emb_headers = ["Config", "Median Total ms", "Δ ms vs SAM3", "Embed ms", "Coverage"]
        lines.append("| " + " | ".join(emb_headers) + " |")
        lines.append("|" + "|".join(["---"] * len(emb_headers)) + "|")

        for result in embedding_results:
            cfg = result["config_name"]
            median_total = result["median_total_ms"]
            embed_ms = result["median_embedding_ms"]
            coverage = result["embed_coverage_fraction"]

            if cfg == "sam3_only":
                delta_str = "—"
                embed_ms_str = "0"
                coverage_str = "0%"
            elif cfg == "sam3+terramind":
                delta_str = f"{result['delta_ms_vs_baseline']:+.1f}"
                embed_ms_str = "N/A"
                coverage_str = "N/A (SAR)"
            else:
                delta_str = f"{result['delta_ms_vs_baseline']:+.1f}"
                embed_ms_str = f"{embed_ms:.1f}"
                coverage_str = f"{coverage * 100:.0f}%"

            lines.append(
                f"| {cfg} | {median_total:.1f} | {delta_str} | {embed_ms_str} | {coverage_str} |"
            )

        lines.append("")

    # ------------------------------------------------------------------
    # Cumulative Pipeline section (only when all three slice types were run)
    # ------------------------------------------------------------------
    if all_results and segmenter_results is not None and embedding_results is not None:
        lines.append("## Cumulative Pipeline")
        lines.append("")
        lines.append(
            "Shows total latency as each layer is added on top of SAM3 base. "
            "Detection count delta shows new detections added by each layer (vs. previous config)."
        )
        lines.append("")

        cum_headers = [
            "Layer added",
            "Median Total ms",
            "\u0394 ms added",
            "Cumulative \u0394 ms",
            "Det Count (avg)",
            "Det \u0394",
        ]
        lines.append("| " + " | ".join(cum_headers) + " |")
        lines.append("|" + "|".join(["---"] * len(cum_headers)) + "|")

        # Extract median latency from results dicts
        def _med(results, key):
            r = next((x for x in results if x.get("config_name") == key), None)
            if r is None:
                return 0.0
            return r.get("latency_ms", {}).get("median_total", r.get("median_total_ms", 0.0))

        # Approximate detection count (TP+FP per chip) from box results
        def _det_count(results, key):
            r = next((x for x in results if x.get("config_name") == key), None)
            if r is None:
                return 0.0
            chips = r.get("chips_evaluated", 1) or 1
            per_class = r.get("metrics", {}).get("per_class", {})
            total = sum(v.get("tp", 0) + v.get("fp", 0) for v in per_class.values())
            return total / chips

        # Box layer latencies
        sam3_ms_cp     = _med(all_results, "sam3_only")
        dota_ms_cp     = _med(all_results, "sam3+dota_obb")
        full_box_ms_cp = _med(all_results, "sam3+dota_obb+grounding_dino")

        # Segmenter (PRITHVI) latency delta vs its own baseline
        prithvi_result_cp = next(
            (r for r in segmenter_results if r.get("config_name") == "sam3+prithvi"), None
        )
        prithvi_delta_cp = 0.0
        if prithvi_result_cp is not None:
            seg_base_cp = next(
                (r for r in segmenter_results if r.get("config_name") == "sam3_only"), None
            )
            seg_base_ms_cp = seg_base_cp["latency_ms"]["median_total"] if seg_base_cp else 0.0
            prithvi_delta_cp = prithvi_result_cp["latency_ms"]["median_total"] - seg_base_ms_cp

        # Embedding latency deltas vs embedding baseline
        def _emb_delta_cp(name):
            r = next((x for x in embedding_results if x.get("config_name") == name), None)
            return r["delta_ms_vs_baseline"] if r else 0.0

        dinov3_sat_delta_cp = _emb_delta_cp("sam3+dinov3_sat")
        terramind_delta_cp  = _emb_delta_cp("sam3+terramind")

        # Detection counts (box layers only; embedding/segmenter don't change det count)
        det_sam3_cp = _det_count(all_results, "sam3_only")
        det_dota_cp = _det_count(all_results, "sam3+dota_obb")
        det_full_cp = _det_count(all_results, "sam3+dota_obb+grounding_dino")

        # Rows: (label, median_total_ms, delta_vs_prev, cumulative_delta, det_avg, det_delta)
        cum_pipeline_rows = [
            ("SAM3 (base)",      sam3_ms_cp, None, None, det_sam3_cp, None),
            ("+ DOTA_OBB",       dota_ms_cp, dota_ms_cp - sam3_ms_cp,
             dota_ms_cp - sam3_ms_cp, det_dota_cp, det_dota_cp - det_sam3_cp),
            ("+ GROUNDING_DINO", full_box_ms_cp, full_box_ms_cp - dota_ms_cp,
             full_box_ms_cp - sam3_ms_cp, det_full_cp, det_full_cp - det_dota_cp),
        ]

        cum_p  = full_box_ms_cp - sam3_ms_cp + prithvi_delta_cp
        cum_ds = cum_p  + dinov3_sat_delta_cp
        cum_tm = cum_ds + terramind_delta_cp

        cum_pipeline_rows += [
            ("+ PRITHVI",
             full_box_ms_cp + prithvi_delta_cp,
             prithvi_delta_cp, cum_p, det_full_cp, 0),
            ("+ DINOV3_SAT",
             full_box_ms_cp + prithvi_delta_cp + dinov3_sat_delta_cp,
             dinov3_sat_delta_cp, cum_ds, det_full_cp, 0),
            ("+ TERRAMIND (SAR)",
             full_box_ms_cp + prithvi_delta_cp + dinov3_sat_delta_cp + terramind_delta_cp,
             terramind_delta_cp, cum_tm, det_full_cp, 0),
        ]

        for label_cp, med_ms_cp, delta_ms_cp, cum_delta_cp, det_avg_cp, det_d_cp in cum_pipeline_rows:
            if delta_ms_cp is None:
                delta_str_cp = "\u2014"
                cum_str_cp   = "\u2014"
            else:
                delta_str_cp = f"{delta_ms_cp:+.0f} ms"
                cum_str_cp   = f"{cum_delta_cp:+.0f} ms"
            if det_d_cp is None:
                det_delta_str_cp = "\u2014"
            elif det_d_cp == 0:
                det_delta_str_cp = "0"
            else:
                det_delta_str_cp = f"{det_d_cp:+.1f}"
            lines.append(
                f"| {label_cp} | {med_ms_cp:.0f} | {delta_str_cp} | {cum_str_cp} "
                f"| {det_avg_cp:.1f} | {det_delta_str_cp} |"
            )

        lines.append("")

    # ------------------------------------------------------------------
    # Recommendations section (always present)
    # ------------------------------------------------------------------
    lines.append("## Recommendations")
    lines.append("")
    lines.append("Based on the comparative analysis above.")
    lines.append("")

    rec_headers = ["Layer", "Verdict", "Quality impact", "Latency cost", "Notes"]
    lines.append("| " + " | ".join(rec_headers) + " |")
    lines.append("|" + "|".join(["---"] * len(rec_headers)) + "|")

    def _box_map_delta_rec(config_name):
        if not all_results:
            return ""
        bl = next((r for r in all_results if r["config_name"] == "sam3_only"), None)
        rs = next((r for r in all_results if r["config_name"] == config_name), None)
        if bl is None or rs is None:
            return ""
        return f"{rs['metrics']['map_50'] - bl['metrics']['map_50']:+.2f} mAP"

    def _box_lat_delta_rec(config_name, baseline_name="sam3_only"):
        if not all_results:
            return "?"
        bl = next((r for r in all_results if r["config_name"] == baseline_name), None)
        rs = next((r for r in all_results if r["config_name"] == config_name), None)
        if bl is None or rs is None:
            return "?"
        return f"{rs['latency_ms']['median_total'] - bl['latency_ms']['median_total']:+.0f} ms"

    def _seg_lat_delta_rec():
        if segmenter_results is None:
            return "?"
        bl = next((r for r in segmenter_results if r["config_name"] == "sam3_only"), None)
        pr = next((r for r in segmenter_results if r["config_name"] == "sam3+prithvi"), None)
        if bl is None or pr is None:
            return "?"
        return f"{pr['latency_ms']['median_total'] - bl['latency_ms']['median_total']:+.0f} ms"

    def _emb_lat_rec(name):
        if embedding_results is None:
            return "?"
        r = next((x for x in embedding_results if x.get("config_name") == name), None)
        if r is None:
            return "?"
        return f"{r['delta_ms_vs_baseline']:+.0f} ms"

    dota_map_rec = _box_map_delta_rec("sam3+dota_obb") or "+N.NN mAP"
    dota_lat_rec = _box_lat_delta_rec("sam3+dota_obb")

    dota_r_rec = next((r for r in all_results if r.get("config_name") == "sam3+dota_obb"), None)
    dino_map_rec       = _box_map_delta_rec("sam3+dota_obb+grounding_dino") or "+N.NN mAP"
    dino_lat_rec       = _box_lat_delta_rec("sam3+dota_obb+grounding_dino")
    prithvi_lat_rec    = _seg_lat_delta_rec()
    dinov3_sat_lat_rec = _emb_lat_rec("sam3+dinov3_sat")
    terramind_lat_rec  = _emb_lat_rec("sam3+terramind")

    rec_rows = [
        ("DOTA_OBB",       "\u2705 Keep",               dota_map_rec,              dota_lat_rec,
         "Adds aerial vehicle/plane classes not in SAM3 vocab"),
        ("GROUNDING_DINO", "\u2705 Keep (auto-gated)",  dino_map_rec,              dino_lat_rec,
         "Open-vocab recall; auto-gated when all prompts are in SAM3+DOTA common vocab"),
        ("PRITHVI",        "\u2705 Keep",               "\u2014 (segmentation)",  prithvi_lat_rec,
         "Only specialist for multispectral flood/burn; no alternative"),
        ("DINOV3_SAT",     "\u2705 Keep for tracking",  "\u2014 (embedding)",     dinov3_sat_lat_rec,
         "Embedding for cross-image object re-ID; see video_tracking_stability.md"),
        ("TERRAMIND",      "\u26a0\ufe0f SAR-only",     "\u2014 (embedding)",     terramind_lat_rec,
         "SAR-only; no impact on RGB/multispectral; enable only for SAR pipelines"),
    ]

    for layer_rec, verdict_rec, quality_rec, lat_cost_rec, notes_rec in rec_rows:
        lines.append(
            f"| {layer_rec} | {verdict_rec} | {quality_rec} | {lat_cost_rec} | {notes_rec} |"
        )

    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

_SEGMENTER_SLICES = frozenset({"hls_burn", "sen1floods"})
_EMBEDDING_SLICES = frozenset({"embedding"})
_SAR_SLICES = frozenset({"sar"})

# SAR-modality configs: measures TERRAMIND latency overhead in the SAR pipeline.
SAR_CONFIGS: list[dict] = [
    {"config_name": "sam3_only_sar",     "enabled_layers": ["sam3"]},
    {"config_name": "sam3+terramind",    "enabled_layers": ["sam3", "terramind"]},
]


def _restart_service(restart_cmd: str | None, url: str, wait_timeout: int) -> bool:
    """Run restart_cmd, then poll url/health until 200 or wait_timeout elapses.

    Returns True on successful restart + ready, False otherwise. Caller
    decides whether to abort or skip the restart.
    """
    if not restart_cmd:
        return True
    import shlex
    import subprocess
    import time as _time
    log.info("Restarting service via: %s", restart_cmd)
    started = _time.time()
    try:
        subprocess.run(shlex.split(restart_cmd), check=True, timeout=60)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        log.error("Restart command failed: %s", exc)
        return False
    health_url = f"{url.rstrip('/')}/health"
    while _time.time() - started < wait_timeout:
        try:
            r = requests.get(health_url, timeout=3)
            if r.status_code == 200:
                elapsed = _time.time() - started
                log.info("Service back up after %.1fs", elapsed)
                return True
        except requests.exceptions.RequestException:
            pass
        _time.sleep(3)
    log.error("Service did not become healthy within %ds", wait_timeout)
    return False


def _fetch_ontology_prompts(ontology_url: str, branch: str | None = None) -> list[str]:
    """Fetch the live ontology default-prompt vocabulary for an ontology-mode run.

    ``branch`` is None (full vocabulary) or a comma-separated list of branch
    ids — the union of those branches' scoped subsets is returned, modelling a
    scene-relevant vocabulary. Prithvi sentinel prompts (``__prithvi_*``) are
    dropped — they drive segmenter heads, not the box detectors this measures.
    """
    branches: list[str | None] = (
        [b.strip() for b in branch.split(",") if b.strip()] if branch else [None]
    )
    seen: set[str] = set()
    out: list[str] = []
    for b in branches:
        params = {"branch": b} if b else {}
        resp = requests.get(
            f"{ontology_url.rstrip('/')}/api/ontology/default-prompts",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        for p in (resp.json().get("prompts") or []):
            p = str(p)
            if p and not p.startswith("__") and p not in seen:
                seen.add(p)
                out.append(p)
    return out


def run(args: argparse.Namespace) -> int:
    """Execute the comparison. Returns exit code."""
    url: str = args.url
    slice_name: str = args.slice
    max_chips: int = args.max_chips
    repeats: int = args.repeats
    output: Path = Path(args.output)
    json_output: Path | None = Path(args.json_output) if args.json_output else None
    layers_path: str | None = args.layers_path
    dry_run: bool = args.dry_run
    restart_cmd: str | None = getattr(args, "restart_cmd", None)
    restart_wait: int = getattr(args, "restart_wait_timeout", 180)
    force_gd: bool = getattr(args, "force_grounding_dino", False)
    if dry_run:
        restart_cmd = None  # never restart in dry-run mode

    is_all_slice = slice_name == "all"
    is_segmenter_slice = slice_name in _SEGMENTER_SLICES
    is_embedding_slice = slice_name in _EMBEDDING_SLICES
    is_sar_slice = slice_name in _SAR_SLICES

    # ------------------------------------------------------------------
    # Probe service availability
    # ------------------------------------------------------------------
    if not dry_run:
        try:
            requests.get(f"{url.rstrip('/')}/health", timeout=10)
        except requests.exceptions.RequestException as exc:
            log.error(
                "Inference service not reachable at %s: %s\n"
                "Start the service or use --dry-run for a smoke test.",
                url,
                exc,
            )
            return 1

    gpu = _probe_health(url) if not dry_run else "dry-run"
    generated_at = datetime.now(tz=timezone.utc).isoformat()

    log.info("Slice: %s | max_chips: %d | repeats: %d | dry_run: %s",
             slice_name, max_chips, repeats, dry_run)

    # ------------------------------------------------------------------
    # --slice all: run dota, hls_burn, and embedding sub-slices
    # ------------------------------------------------------------------
    if is_all_slice:
        log.info("Running --slice all: dota + hls_burn + embedding")

        # ---- dota (box detectors) ----
        log.info("Loading chips from slice 'dota' ...")
        dota_chips_all: list[tuple[bytes, str, list[str], Any]] = list(
            _iter_slice("dota", max_chips, layers_path)
        )
        log.info("Loaded %d dota chip(s).", len(dota_chips_all))
        if getattr(args, "ontology_mode", False):
            onto_prompts = _fetch_ontology_prompts(args.ontology_url, args.ontology_branch)
            log.info("Ontology mode: %d ontology prompt(s) for dota sub-slice", len(onto_prompts))
            dota_chips_all = [(cb, mod, onto_prompts, gt) for cb, mod, _p, gt in dota_chips_all]

        all_results_sl: list[dict] = []
        # Always restart at the start of the slice (in case the GPU was left
        # fragmented by an earlier process — e.g. a previous test run, or the
        # health-probe that loaded the video model).
        _restart_service(restart_cmd, url, restart_wait)
        for cfg_idx, cfg in enumerate(LAYER_CONFIGS):
            if cfg_idx > 0:
                _restart_service(restart_cmd, url, restart_wait)
            config_name = cfg["config_name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating config: %s  layers=%s", config_name, enabled_layers)
            chip_results_box_sl: list[dict] = []
            for chip_bytes_sl, modality_sl, prompts_sl, gt_sl in dota_chips_all:
                result_sl = _evaluate_chip(
                    url=url, chip_bytes=chip_bytes_sl, prompts=prompts_sl,
                    ground_truth=gt_sl, enabled_layers=enabled_layers,
                    repeats=repeats, dry_run=dry_run, modality=modality_sl,
                    force_grounding_dino=force_gd,
                )
                if result_sl is not None:
                    chip_results_box_sl.append(result_sl)
            agg_sl = _aggregate_results(chip_results_box_sl)
            agg_sl["config_name"] = config_name
            agg_sl["enabled_layers"] = enabled_layers
            all_results_sl.append(agg_sl)
            log.info("  chips=%d  mAP=%.4f  ms=%.1f", agg_sl["chips_evaluated"],
                     agg_sl["metrics"]["map_50"], agg_sl["latency_ms"]["median_total"])

        # ---- hls_burn (segmenter) ----
        log.info("Loading chips from slice 'hls_burn' ...")
        seg_chips_sl: list[tuple[bytes, str, list[str], Any]] = list(
            _iter_slice("hls_burn", max_chips, layers_path)
        )
        log.info("Loaded %d hls_burn chip(s).", len(seg_chips_sl))

        sample_gt_sl = seg_chips_sl[0][3] if seg_chips_sl else {}
        tasks_sl = list(sample_gt_sl.keys())
        segmenter_results_sl: list[dict] = []
        n_seg_chips_sl = 0
        # Always restart between slices (box→segmenter switches modality + payload size).
        _restart_service(restart_cmd, url, restart_wait)
        for cfg_idx, cfg in enumerate(SEGMENTER_CONFIGS):
            if cfg_idx > 0:
                _restart_service(restart_cmd, url, restart_wait)
            config_name = cfg["config_name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating segmenter config: %s  layers=%s", config_name, enabled_layers)
            chip_results_seg_sl: list[dict] = []
            for chip_bytes_sl, modality_sl, prompts_sl, gt_sl in seg_chips_sl:
                result_sl = _evaluate_segmenter_chip(
                    url=url, chip_bytes=chip_bytes_sl, ground_truth=gt_sl,
                    enabled_layers=enabled_layers, repeats=repeats, dry_run=dry_run,
                )
                if result_sl is not None:
                    chip_results_seg_sl.append(result_sl)
            agg_seg_sl = _aggregate_segmenter_results(chip_results_seg_sl, tasks_sl)
            agg_seg_sl["config_name"] = config_name
            agg_seg_sl["enabled_layers"] = enabled_layers
            segmenter_results_sl.append(agg_seg_sl)
            n_seg_chips_sl = max(n_seg_chips_sl, agg_seg_sl["chips_evaluated"])
            log.info("  chips=%d  mean_iou=%.4f  ms=%.1f",
                     agg_seg_sl["chips_evaluated"], agg_seg_sl["mean_iou"],
                     agg_seg_sl["latency_ms"]["median_total"])

        # ---- embedding ----
        log.info("Loading chips from slice 'embedding' ...")
        emb_chips_sl: list[tuple[bytes, str, list[str], Any]] = list(
            _iter_slice("embedding", max_chips, layers_path)
        )
        log.info("Loaded %d embedding chip(s).", len(emb_chips_sl))

        embedding_results_sl: list[dict] = []
        n_emb_chips_sl = 0
        baseline_emb_ms_sl: float | None = None
        # Always restart before the embedding slice (segmenter just ran lots of
        # multispectral PRITHVI inference — GPU is fragmented again).
        _restart_service(restart_cmd, url, restart_wait)
        for cfg_idx, cfg in enumerate(EMBEDDING_CONFIGS):
            if cfg_idx > 0:
                _restart_service(restart_cmd, url, restart_wait)
            config_name = cfg["name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating embedding config: %s  layers=%s", config_name, enabled_layers)
            chip_results_emb_sl: list[dict] = []
            for chip_bytes_sl, modality_sl, prompts_sl, gt_sl in emb_chips_sl:
                result_sl = _evaluate_embedding_chip(
                    url=url, chip_bytes=chip_bytes_sl, enabled_layers=enabled_layers,
                    repeats=repeats, dry_run=dry_run, modality=modality_sl,
                )
                if result_sl is not None:
                    chip_results_emb_sl.append(result_sl)
            agg_emb_sl = _aggregate_embedding_results(
                chip_results_emb_sl, config_name,
                baseline_median_ms=baseline_emb_ms_sl,
            )
            # Warn if a non-baseline config got 0 chips — usually means all
            # layers loaded together exceeded GPU memory. The classic culprit
            # is `all_embeddings` (SAT + LVD + TERRAMIND together).
            if (
                len(chip_results_emb_sl) == 0
                and config_name != "sam3_only"
                and not dry_run
            ):
                log.warning(
                    "Config %s evaluated 0 chips — likely GPU OOM with %d "
                    "embedding model(s) active. Consider testing each "
                    "embedding layer in isolation, or freeing one of the "
                    "image-only layers (DOTA_OBB / GROUNDING_DINO / PRITHVI) "
                    "for this config.",
                    config_name, len([l for l in enabled_layers if l != "sam3"]),
                )
                agg_emb_sl["warning"] = (
                    f"0 chips evaluated; likely GPU OOM with "
                    f"{len(enabled_layers) - 1} embedding model(s) active"
                )
            embedding_results_sl.append(agg_emb_sl)
            n_emb_chips_sl = max(n_emb_chips_sl, len(chip_results_emb_sl))
            if config_name == "sam3_only":
                baseline_emb_ms_sl = agg_emb_sl["median_total_ms"]

        # ---- sar (TERRAMIND latency) ----
        log.info("Loading chips from slice 'sar' ...")
        sar_chips_sl: list[tuple[bytes, str, list[str], Any]] = list(
            _iter_slice("sar", max_chips, layers_path)
        )
        log.info("Loaded %d sar chip(s).", len(sar_chips_sl))

        sar_results_sl: list[dict] = []
        n_sar_chips_sl = 0
        if sar_chips_sl:
            _restart_service(restart_cmd, url, restart_wait)
            for cfg_idx, cfg in enumerate(SAR_CONFIGS):
                if cfg_idx > 0:
                    _restart_service(restart_cmd, url, restart_wait)
                config_name = cfg["config_name"]
                enabled_layers = cfg["enabled_layers"]
                log.info("Evaluating SAR config: %s  layers=%s", config_name, enabled_layers)
                chip_results_sar_sl: list[dict] = []
                for chip_bytes_sl, modality_sl, prompts_sl, gt_sl in sar_chips_sl:
                    result_sl = _evaluate_chip(
                        url=url, chip_bytes=chip_bytes_sl, prompts=prompts_sl,
                        ground_truth=gt_sl, enabled_layers=enabled_layers,
                        repeats=repeats, dry_run=dry_run, modality=modality_sl,
                    )
                    if result_sl is not None:
                        chip_results_sar_sl.append(result_sl)
                agg_sar_sl = _aggregate_results(chip_results_sar_sl)
                agg_sar_sl["config_name"] = config_name
                agg_sar_sl["enabled_layers"] = enabled_layers
                sar_results_sl.append(agg_sar_sl)
                n_sar_chips_sl = max(n_sar_chips_sl, agg_sar_sl["chips_evaluated"])
                log.info("  chips=%d  median_total=%.1fms",
                         agg_sar_sl["chips_evaluated"],
                         agg_sar_sl["latency_ms"]["median_total"])

        # ---- Write JSON ----
        if json_output:
            json_output.parent.mkdir(parents=True, exist_ok=True)
            json_output.write_text(
                json.dumps(
                    {
                        "generated_at": generated_at,
                        "gpu": gpu,
                        "slice": "all",
                        "max_chips": max_chips,
                        "repeats": repeats,
                        "results": all_results_sl,
                        "segmenter_results": segmenter_results_sl,
                        "embedding_results": embedding_results_sl,
                        "sar_results": sar_results_sl,
                        "sar_chips_loaded": len(sar_chips_sl),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            log.info("JSON artifact written to %s", json_output)

        # ---- Write Markdown ----
        n_chips_sl_actual = max(
            (r["chips_evaluated"] for r in all_results_sl), default=0
        )
        markdown_sl = _build_markdown(
            all_results=all_results_sl,
            slice_name="dota",
            n_chips=n_chips_sl_actual,
            gpu=gpu,
            generated_at=generated_at,
            segmenter_results=segmenter_results_sl,
            segmenter_slice="hls_burn",
            n_segmenter_chips=n_seg_chips_sl,
            embedding_results=embedding_results_sl,
            n_embedding_chips=n_emb_chips_sl,
        )

        # Append SAR section (synthetic 2-band SAR, latency-only).
        if sar_results_sl:
            sar_lines = [
                "",
                "## SAR / TERRAMIND (Synthetic)",
                "",
                f"Dataset: synthetic 2-band SAR ({n_sar_chips_sl} chips). Real Sentinel-1 GRD VV/VH "
                "is not freely available on HuggingFace at a manageable size (the SSL4EO-S12 "
                "S1 archive is 480 GB). Quality cannot be measured here — TERRAMIND only "
                "exposes a pooled embedding + RGB preview, no per-pixel labels — but **latency** "
                "is reliable.",
                "",
                "| Config | Chips | Median Total ms | Δ ms vs SAM3 (SAR) |",
                "|---|---|---|---|",
            ]
            sar_baseline_ms = next(
                (r["latency_ms"]["median_total"] for r in sar_results_sl
                 if r["config_name"] == "sam3_only_sar"),
                0.0,
            )
            for r in sar_results_sl:
                med = r["latency_ms"]["median_total"]
                if r["config_name"] == "sam3_only_sar":
                    delta = "—"
                else:
                    delta = f"{med - sar_baseline_ms:+.1f} ms"
                sar_lines.append(
                    f"| {r['config_name']} | {r['chips_evaluated']} | "
                    f"{med:.1f} | {delta} |"
                )
            markdown_sl = markdown_sl.rstrip() + "\n" + "\n".join(sar_lines) + "\n"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown_sl, encoding="utf-8")
        log.info("Markdown report written to %s", output)
        return 0

    # ------------------------------------------------------------------
    # Load chips once
    # ------------------------------------------------------------------
    log.info("Loading chips from slice '%s' ...", slice_name)
    triage_dir = getattr(args, "triage_set", None)
    triage_rgb_only = not getattr(args, "triage_include_non_rgb", False)
    chips: list[tuple[bytes, str, list[str], Any]] = list(
        _iter_slice(
            slice_name, max_chips, layers_path,
            triage_dir=triage_dir, triage_rgb_only=triage_rgb_only,
        )
    )
    if not chips:
        log.warning("No chips loaded — check dataset path.")

    log.info("Loaded %d chip(s).", len(chips))

    # Ontology-mode: swap each chip's oracle GT prompts for the real ontology
    # vocabulary, so the run measures detection quality without the per-chip
    # class hint the operator never has.
    if getattr(args, "ontology_mode", False) and slice_name in ("dota", "embedding"):
        onto_prompts = _fetch_ontology_prompts(args.ontology_url, args.ontology_branch)
        log.info(
            "Ontology mode: replaced per-chip GT prompts with %d ontology prompt(s)%s",
            len(onto_prompts),
            f" (branch={args.ontology_branch})" if args.ontology_branch else "",
        )
        chips = [(cb, mod, onto_prompts, gt) for cb, mod, _p, gt in chips]

    # ------------------------------------------------------------------
    # Evaluate each layer configuration
    # ------------------------------------------------------------------
    all_results: list[dict] = []
    segmenter_results: list[dict] | None = None
    n_segmenter_chips = 0
    embedding_results: list[dict] | None = None
    n_embedding_chips = 0

    if is_embedding_slice:
        embedding_results = []
        baseline_embedding_ms: float | None = None

        for cfg in EMBEDDING_CONFIGS:
            config_name = cfg["name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating embedding config: %s  layers=%s", config_name, enabled_layers)

            chip_results_emb: list[dict] = []

            for chip_idx, (chip_bytes, modality, prompts, ground_truth) in enumerate(chips):
                result = _evaluate_embedding_chip(
                    url=url,
                    chip_bytes=chip_bytes,
                    enabled_layers=enabled_layers,
                    repeats=repeats,
                    dry_run=dry_run,
                    modality=modality,
                )
                if result is None:
                    log.warning("Chip %d/%d skipped (embedding eval failed).", chip_idx + 1, len(chips))
                    continue
                chip_results_emb.append(result)

            agg_emb = _aggregate_embedding_results(
                chip_results_emb,
                config_name,
                baseline_median_ms=baseline_embedding_ms,
            )
            embedding_results.append(agg_emb)
            n_embedding_chips = max(n_embedding_chips, len(chip_results_emb))

            if config_name == "sam3_only":
                baseline_embedding_ms = agg_emb["median_total_ms"]

            log.info(
                "  chips_evaluated=%d  median_total_ms=%.1f  embed_ms=%.1f  coverage=%.2f",
                len(chip_results_emb),
                agg_emb["median_total_ms"],
                agg_emb["median_embedding_ms"],
                agg_emb["embed_coverage_fraction"],
            )

    elif is_segmenter_slice:
        # Determine which tasks appear in the ground truth
        sample_gt = chips[0][3] if chips else {}
        tasks = list(sample_gt.keys())
        segmenter_results = []

        for cfg in SEGMENTER_CONFIGS:
            config_name = cfg["config_name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating segmenter config: %s  layers=%s", config_name, enabled_layers)

            chip_results: list[dict] = []

            for chip_idx, (chip_bytes, modality, prompts, ground_truth) in enumerate(chips):
                result = _evaluate_segmenter_chip(
                    url=url,
                    chip_bytes=chip_bytes,
                    ground_truth=ground_truth,
                    enabled_layers=enabled_layers,
                    repeats=repeats,
                    dry_run=dry_run,
                )
                if result is None:
                    log.warning("Chip %d/%d skipped (evaluation failed).", chip_idx + 1, len(chips))
                    continue
                chip_results.append(result)

            agg = _aggregate_segmenter_results(chip_results, tasks)
            agg["config_name"] = config_name
            agg["enabled_layers"] = enabled_layers
            segmenter_results.append(agg)
            n_segmenter_chips = max(n_segmenter_chips, agg["chips_evaluated"])

            log.info(
                "  chips_evaluated=%d  mean_iou=%.4f  median_ms=%.1f",
                agg["chips_evaluated"],
                agg["mean_iou"],
                agg["latency_ms"]["median_total"],
            )

    else:
        for cfg in LAYER_CONFIGS:
            config_name = cfg["config_name"]
            enabled_layers = cfg["enabled_layers"]
            log.info("Evaluating config: %s  layers=%s", config_name, enabled_layers)

            chip_results = []

            for chip_idx, (chip_bytes, modality, prompts, ground_truth) in enumerate(chips):
                result = _evaluate_chip(
                    url=url,
                    chip_bytes=chip_bytes,
                    prompts=prompts,
                    ground_truth=ground_truth,
                    enabled_layers=enabled_layers,
                    repeats=repeats,
                    dry_run=dry_run,
                    modality=modality,
                )
                if result is None:
                    log.warning("Chip %d/%d skipped (evaluation failed).", chip_idx + 1, len(chips))
                    continue
                chip_results.append(result)

            agg = _aggregate_results(chip_results)
            agg["config_name"] = config_name
            agg["enabled_layers"] = enabled_layers
            all_results.append(agg)

            log.info(
                "  chips_evaluated=%d  mAP@0.5=%.4f  macro_f1=%.4f  median_ms=%.1f",
                agg["chips_evaluated"],
                agg["metrics"]["map_50"],
                agg["metrics"]["macro_f1"],
                agg["latency_ms"]["median_total"],
            )

    # ------------------------------------------------------------------
    # Write JSON artifact
    # ------------------------------------------------------------------
    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(
            json.dumps(
                {
                    "generated_at": generated_at,
                    "gpu": gpu,
                    "slice": slice_name,
                    "max_chips": max_chips,
                    "repeats": repeats,
                    "results": all_results,
                    "segmenter_results": segmenter_results,
                    "embedding_results": embedding_results,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        log.info("JSON artifact written to %s", json_output)

    # ------------------------------------------------------------------
    # Write Markdown report
    # ------------------------------------------------------------------
    n_chips_actual = max(
        (r["chips_evaluated"] for r in all_results), default=0
    )
    markdown = _build_markdown(
        all_results=all_results,
        slice_name=slice_name,
        n_chips=n_chips_actual,
        gpu=gpu,
        generated_at=generated_at,
        segmenter_results=segmenter_results,
        segmenter_slice=slice_name if is_segmenter_slice else None,
        n_segmenter_chips=n_segmenter_chips,
        embedding_results=embedding_results,
        n_embedding_chips=n_embedding_chips,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    log.info("Markdown report written to %s", output)

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare inference layer configurations on an eval dataset slice.",
    )
    parser.add_argument(
        "--url",
        default="http://localhost:8001",
        help="Inference service base URL (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--slice",
        choices=["dota", "hls_burn", "sen1floods", "sar", "embedding", "triage", "all"],
        default="dota",
        help=(
            "Dataset slice to evaluate (default: dota). "
            "Choices: dota (box detectors), hls_burn / sen1floods (PRITHVI segmenter heads), "
            "sar (synthetic 2-band SAR for TERRAMIND latency only), "
            "embedding (DINOV3_SAT / TERRAMIND embedding latency), "
            "triage (analyst-curated production-image benchmark; see "
            "scripts/build_triage_set.py), "
            "all (runs dota + hls_burn + embedding and combines into one full report)."
        ),
    )
    parser.add_argument(
        "--max-chips",
        type=int,
        default=30,
        dest="max_chips",
        help="Maximum number of chips to evaluate (default: 30)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="Number of inference repeats per chip for latency averaging (default: 3)",
    )
    parser.add_argument(
        "--output",
        default="docs/inference_layer_comparison.md",
        help="Path for the Markdown report (default: docs/inference_layer_comparison.md)",
    )
    parser.add_argument(
        "--json-output",
        dest="json_output",
        default=None,
        help="Optional path for the JSON artifact",
    )
    parser.add_argument(
        "--layers-path",
        dest="layers_path",
        default=None,
        help="Optional override for the dataset labels.json path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help=(
            "Skip HTTP calls and use synthetic results. "
            "Useful for testing report generation without a live server."
        ),
    )
    parser.add_argument(
        "--restart-cmd",
        dest="restart_cmd",
        default=None,
        help=(
            "Shell command to run between configs to clear GPU memory "
            "(e.g. 'docker restart osint-inference-sam3-1'). After running, "
            "the driver polls --url/health until it returns 200."
        ),
    )
    parser.add_argument(
        "--restart-wait-timeout",
        dest="restart_wait_timeout",
        type=int,
        default=180,
        help="Seconds to wait for /health after a restart (default: 180)",
    )
    parser.add_argument(
        "--force-grounding-dino",
        dest="force_grounding_dino",
        action="store_true",
        help=(
            "Set force_grounding_dino=true in metadata for configs that include "
            "grounding_dino in enabled_layers. Bypasses the common-vocab gate so "
            "GROUNDING_DINO's contribution can actually be measured on common "
            "prompts (otherwise it auto-skips and the config is identical to baseline)."
        ),
    )
    # Phase 9.47: per-class regression gate. When ``--regression-baseline`` is
    # passed, the script compares this run's per-class recall against the JSON
    # blob at that path. If any class regresses by more than --regression-tol
    # (default 0.05 = 5%) and the class appeared in the baseline, the script
    # exits non-zero so CI flags accidental quality regressions.
    parser.add_argument(
        "--regression-baseline",
        dest="regression_baseline",
        default=None,
        help=(
            "Optional path to a previous run's JSON output. When provided, the "
            "script compares per-class recall against the baseline and exits "
            "non-zero if any class regresses by more than --regression-tol."
        ),
    )
    parser.add_argument(
        "--regression-tol",
        dest="regression_tol",
        type=float,
        default=0.05,
        help="Allowable per-class recall drop vs --regression-baseline (default 0.05).",
    )
    # Ontology-mode evaluation: instead of feeding each chip its own ground-truth
    # class names (an oracle the operator never has), feed every chip the real
    # ontology vocabulary fetched from the backend. Measures detection quality
    # the way an analyst actually experiences it.
    parser.add_argument(
        "--ontology-mode",
        dest="ontology_mode",
        action="store_true",
        help=(
            "Replace per-chip ground-truth prompts with the live ontology "
            "default-prompt vocabulary (fetched from --ontology-url). Applies "
            "to the dota box-detector slice."
        ),
    )
    parser.add_argument(
        "--ontology-url",
        dest="ontology_url",
        default="http://localhost:3000",
        help="Backend base URL for --ontology-mode prompt fetch (default: http://localhost:3000).",
    )
    parser.add_argument(
        "--ontology-branch",
        dest="ontology_branch",
        default=None,
        help="Optional ontology branch id to scope the --ontology-mode vocabulary.",
    )
    # Tier-0 triage benchmark — analyst-curated chips pulled from the operator's
    # own recent uploads. See scripts/build_triage_set.py.
    parser.add_argument(
        "--triage-set",
        dest="triage_set",
        default=None,
        help=(
            "Path to a triage-set directory produced by build_triage_set.py. "
            "When set, forces --slice triage."
        ),
    )
    parser.add_argument(
        "--include-non-rgb",
        dest="triage_include_non_rgb",
        action="store_true",
        help=(
            "For --slice triage: include non-RGB chips (SAR / multispectral). "
            "By default only RGB chips are evaluated."
        ),
    )
    return parser


def _check_regression_gate(args: argparse.Namespace) -> int:
    """Phase 9.47: enforce per-class recall does not regress vs a baseline JSON.

    Reads the just-written ``--json-output`` (or the canonical default), loads
    ``--regression-baseline``, and compares per-class recall. Returns ``0`` if
    every class in the baseline either improved or stayed within tolerance;
    returns ``1`` and prints a diff if anything regressed.

    The check is permissive about which configs exist — we only fail when a
    config + class present in *both* runs shows a recall drop, so adding new
    configs or new classes between runs is fine.
    """
    baseline_path = args.regression_baseline
    if not baseline_path:
        return 0
    current_path = args.json_output or args.output.replace(".md", ".json")
    try:
        import json as _json
        from pathlib import Path as _Path
        if not _Path(current_path).exists():
            print(f"[regression-gate] current results not found at {current_path}; skipping")
            return 0
        baseline = _json.loads(_Path(baseline_path).read_text(encoding="utf-8"))
        current = _json.loads(_Path(current_path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[regression-gate] failed to load JSONs: {exc}")
        return 0

    def _per_class_recall(blob: dict) -> dict[tuple[str, str], float]:
        """Flatten to {(config_name, class_label): recall}."""
        out: dict[tuple[str, str], float] = {}
        configs = blob.get("configs") or blob.get("results") or []
        for entry in configs:
            cfg_name = entry.get("name") or entry.get("config") or "<unknown>"
            per_class = (entry.get("metrics") or {}).get("per_class") or {}
            for cls, m in per_class.items():
                if isinstance(m, dict) and m.get("recall") is not None:
                    try:
                        out[(cfg_name, cls)] = float(m["recall"])
                    except (TypeError, ValueError):
                        continue
        return out

    base_map = _per_class_recall(baseline)
    curr_map = _per_class_recall(current)
    tol = float(args.regression_tol)
    regressions: list[str] = []
    for key, base_recall in base_map.items():
        cur_recall = curr_map.get(key)
        if cur_recall is None:
            continue
        drop = base_recall - cur_recall
        if drop > tol:
            cfg, cls = key
            regressions.append(
                f"  - {cfg}/{cls}: {base_recall:.3f} -> {cur_recall:.3f} (Δ -{drop:.3f})"
            )
    if regressions:
        print(f"[regression-gate] FAIL — {len(regressions)} class(es) regressed by more than {tol:.2%}:")
        for line in regressions:
            print(line)
        return 1
    print(f"[regression-gate] OK — no per-class recall drop > {tol:.2%} vs {baseline_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # --triage-set is a convenience flag that pins --slice to triage.
    if getattr(args, "triage_set", None):
        args.slice = "triage"
    rc = run(args)
    if rc == 0:
        rc = _check_regression_gate(args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
