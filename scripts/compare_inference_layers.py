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
from eval_metrics.box_metrics import compute_box_metrics  # noqa: E402
from eval_metrics.label_normalizer import normalize  # noqa: E402

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
        "config_name": "sam3+yolo_defence",
        "enabled_layers": ["sam3", "yolo_defence"],
    },
    {
        "config_name": "sam3+grounding_dino",
        "enabled_layers": ["sam3", "grounding_dino"],
    },
    {
        "config_name": "sam3+dota_obb+yolo_defence",
        "enabled_layers": ["sam3", "dota_obb", "yolo_defence"],
    },
    {
        "config_name": "sam3+dota_obb+yolo_defence+grounding_dino",
        "enabled_layers": ["sam3", "dota_obb", "yolo_defence", "grounding_dino"],
    },
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
    timeout: int = 120,
) -> dict:
    """POST chip to /detect and return the parsed JSON response + elapsed_ms."""
    started = time.perf_counter()
    resp = requests.post(
        f"{url.rstrip('/')}/detect",
        files={"image": ("chip.png", io.BytesIO(chip_bytes), "image/png")},
        data={
            "metadata": json.dumps({
                "modality": "rgb",
                "text_prompts": prompts,
                "max_prompts": len(prompts),
                "enabled_layers": enabled_layers,
            })
        },
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


# ---------------------------------------------------------------------------
# Detection parsing
# ---------------------------------------------------------------------------

def _parse_detections(
    response: dict,
    enabled_layers: list[str],
) -> list[dict]:
    """Extract normalised predictions from a /detect response dict.

    Returns a list of {"label": str, "bbox_xyxy": [...], "score": float}.
    """
    raw = response.get("detections", [])

    # Determine normalisation layer key:
    # - Single specialist layer → use its name
    # - Multiple layers or sam3-only → "mixed"
    specialist_layers = [l for l in enabled_layers if l != "sam3"]
    norm_layer = specialist_layers[0] if len(specialist_layers) == 1 else "mixed"

    predictions = []
    for det in raw:
        label = det.get("label", "")
        score = float(det.get("confidence", det.get("score", 0.0)))

        # bbox can be {x, y, w, h} or bbox_xyxy
        if "bbox" in det:
            b = det["bbox"]
            x, y, w, h = b.get("x", 0), b.get("y", 0), b.get("w", 0), b.get("h", 0)
            bbox_xyxy = [x, y, x + w, y + h]
        elif "bbox_xyxy" in det:
            bbox_xyxy = det["bbox_xyxy"]
        else:
            continue  # skip malformed detection

        # Normalise label
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
                payload = _post_detect(url, chip_bytes, prompts, enabled_layers)
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

    predictions = _parse_detections(last_payload, enabled_layers)

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
) -> Iterator[tuple[bytes, str, list[str], list[dict]]]:
    if slice_name == "dota":
        yield from iter_dota(labels_path=layers_path, max_chips=max_chips)
    else:
        raise ValueError(f"Unknown slice: {slice_name!r}. Choices: dota")


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
) -> str:
    lines: list[str] = []

    lines.append("# Inference Layer Comparison")
    lines.append("")
    lines.append(f"Generated: {generated_at}  GPU: {gpu}")
    lines.append("")
    lines.append("## Box Detectors")
    lines.append("")
    lines.append(
        f"Dataset: DOTA-v1.0 ({n_chips} chips, IoU threshold 0.5)"
    )
    lines.append("")

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
        if result["config_name"] not in ("sam3_only", "sam3+dota_obb+yolo_defence+grounding_dino"):
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

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main driver
# ---------------------------------------------------------------------------

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
    # Load chips once
    # ------------------------------------------------------------------
    log.info("Loading chips from slice '%s' ...", slice_name)
    chips: list[tuple[bytes, str, list[str], list[dict]]] = list(
        _iter_slice(slice_name, max_chips, layers_path)
    )
    if not chips:
        log.warning("No chips loaded — check dataset path.")

    log.info("Loaded %d chip(s).", len(chips))

    # ------------------------------------------------------------------
    # Evaluate each layer configuration
    # ------------------------------------------------------------------
    all_results: list[dict] = []

    for cfg in LAYER_CONFIGS:
        config_name = cfg["config_name"]
        enabled_layers = cfg["enabled_layers"]
        log.info("Evaluating config: %s  layers=%s", config_name, enabled_layers)

        chip_results: list[dict] = []

        for chip_idx, (chip_bytes, modality, prompts, ground_truth) in enumerate(chips):
            result = _evaluate_chip(
                url=url,
                chip_bytes=chip_bytes,
                prompts=prompts,
                ground_truth=ground_truth,
                enabled_layers=enabled_layers,
                repeats=repeats,
                dry_run=dry_run,
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
        choices=["dota"],
        default="dota",
        help="Dataset slice to evaluate (default: dota)",
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
        help="Optional override for the DOTA labels.json path",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
