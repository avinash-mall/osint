"""Phase 9 — measure Expected Calibration Error (ECE) for the detector ensemble.

ECE quantifies how well a detector's reported confidence matches its actual
accuracy. For a well-calibrated detector at the 0.8 confidence bucket we
expect ~80% of those predictions to be correct; ECE measures the absolute
gap between confidence and accuracy, averaged over confidence bins.

Usage::

    python scripts/measure_calibration_ece.py \\
        --inference-url http://localhost:8001 \\
        --slice dota \\
        --max-chips 60 \\
        --bins 15 \\
        --output docs/calibration_ece.md

Writes:
    docs/calibration_ece.md     summary table per detector
    docs/calibration_ece.json   raw bin-by-bin data + suggested temperatures

The suggested temperature for each model is computed by minimising the
binary cross-entropy of ``sigmoid(logit(score) / T)`` against ground truth
on the eval slice. Drop the resulting JSON into the
``MODEL_TEMPERATURES_FILE`` consumed by ``backend/calibration.py`` and the
ensemble's confidence scores will be comparable across detectors at NMS
time, removing the bias where SAM3's loud-but-overconfident scores drown
out DOTA-OBB.

This is an *evaluation* tool — it does not change model weights, only the
post-hoc temperature applied at inference time.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Make ``scripts.eval_metrics`` and ``scripts.eval_datasets`` importable
# whether we run from the repo root or scripts/.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def apply_temperature(score: float, T: float) -> float:
    if T == 1.0 or score <= 0.0 or score >= 1.0:
        return score
    return _sigmoid(_logit(score) / T)


def ece_for_predictions(
    scores: list[float],
    correct: list[bool],
    bins: int = 15,
) -> tuple[float, list[dict]]:
    """Compute ECE + per-bin (confidence, accuracy, count) breakdown.

    ``scores`` are confidence values in ``[0, 1]``.
    ``correct`` is a parallel list of booleans (TP=True, FP=False).
    Bins are equal-width on ``[0, 1]``.
    """
    if not scores:
        return 0.0, []
    edges = [i / bins for i in range(bins + 1)]
    buckets: list[dict] = []
    total = len(scores)
    ece = 0.0
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        in_bin = [(s, c) for s, c in zip(scores, correct) if (lo <= s < hi) or (i == bins - 1 and s == hi)]
        if not in_bin:
            buckets.append({"low": lo, "high": hi, "count": 0, "avg_conf": None, "accuracy": None, "gap": None})
            continue
        avg_conf = sum(s for s, _ in in_bin) / len(in_bin)
        acc = sum(1 for _, c in in_bin if c) / len(in_bin)
        gap = abs(avg_conf - acc)
        ece += (len(in_bin) / total) * gap
        buckets.append({
            "low": round(lo, 4),
            "high": round(hi, 4),
            "count": len(in_bin),
            "avg_conf": round(avg_conf, 4),
            "accuracy": round(acc, 4),
            "gap": round(gap, 4),
        })
    return ece, buckets


def fit_temperature(
    scores: list[float],
    correct: list[bool],
    grid: list[float] | None = None,
) -> float:
    """Coarse grid-search for the temperature that minimises BCE.

    Single-parameter optimisation; a grid is fine and avoids depending on
    SciPy in the eval script.
    """
    if not scores:
        return 1.0
    if grid is None:
        grid = [round(0.25 + 0.05 * i, 2) for i in range(80)]  # 0.25 .. 4.20

    def nll(T: float) -> float:
        total = 0.0
        for s, c in zip(scores, correct):
            p = max(1e-7, min(1.0 - 1e-7, apply_temperature(s, T)))
            total += -(math.log(p) if c else math.log(1.0 - p))
        return total

    best_T = 1.0
    best_loss = nll(1.0)
    for T in grid:
        try:
            loss = nll(T)
        except (ValueError, OverflowError):
            continue
        if loss < best_loss:
            best_loss = loss
            best_T = T
    return best_T


def _collect_predictions(
    inference_url: str,
    slice_name: str,
    max_chips: int,
    enabled_layers: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, list[tuple[float, bool]]]:
    """Phase 9.46: collect ``{model_tag: [(score, correct), ...]}`` from the
    eval slice by reusing ``compare_inference_layers``' chip-evaluation
    primitives. The TP/FP decision is delegated to
    :func:`scripts.eval_metrics.box_metrics.per_prediction_matches`, keeping
    the matching logic consistent with the box-mAP harness so the temperatures
    we fit here directly track the metrics ``compare_inference_layers`` reports.
    """
    try:
        # Local lazy imports to keep the CLI startup cheap when --dry-run.
        from scripts.eval_metrics.box_metrics import per_prediction_matches
        from scripts.eval_metrics.label_normalizer import normalize as _normalize_label
        from scripts.compare_inference_layers import (
            _post_detect,
            _parse_detections,
            _synthetic_response,
        )
    except ImportError as exc:
        raise SystemExit(
            "ECE harness needs scripts.eval_datasets, scripts.eval_metrics, and "
            f"scripts.compare_inference_layers — missing dependency: {exc}"
        )

    layers = enabled_layers or ["sam3", "dota_obb"]
    samples_iter = _iter_eval_slice(slice_name, max_chips)
    by_model: dict[str, list[tuple[float, bool]]] = {}

    # Each chip's normalised GT shape matches what ``compute_box_metrics``
    # expects; we re-derive it the same way as in the comparison harness.
    chip_count = 0
    for chip_bytes, modality, prompts, ground_truth in samples_iter:
        if chip_count >= max_chips:
            break
        chip_count += 1
        try:
            if dry_run:
                payload = _synthetic_response(layers, ground_truth)
            else:
                payload = _post_detect(
                    inference_url, chip_bytes, prompts, layers,
                    modality=modality,
                )
        except Exception as exc:
            print(f"[ece] chip {chip_count} failed: {exc}")
            continue

        # Use the chip's actual size for bbox denormalisation.
        chip_size = (1024, 1024)
        try:
            from PIL import Image
            import io as _io
            with Image.open(_io.BytesIO(chip_bytes)) as _img:
                chip_size = _img.size
        except Exception:
            pass
        predictions = _parse_detections(payload, layers, chip_size=chip_size)
        # The /detect response carries source_layer per detection on the
        # production path; group by that. Fall back to "sam3" if absent.
        raw_dets = payload.get("detections", [])
        tag_for_index = []
        for det in raw_dets:
            tag_for_index.append(
                str(det.get("source_layer") or det.get("model_version") or "sam3").lower()
            )

        norm_gt = [
            {"label": _normalize_label(g["label"], "dota_obb"), "bbox_xyxy": g["bbox_xyxy"]}
            for g in (ground_truth or [])
        ]

        per_pred = per_prediction_matches(predictions, norm_gt, iou_threshold=0.5)
        # per_pred is parallel to predictions in input order; raw_dets is
        # parallel to predictions when _parse_detections didn't drop any.
        # In the rare case lengths differ (a malformed bbox got skipped),
        # iterate the shorter list to stay safe.
        for i, match in enumerate(per_pred):
            tag = tag_for_index[i] if i < len(tag_for_index) else "sam3"
            by_model.setdefault(tag, []).append((float(match["score"]), bool(match["is_tp"])))

    return by_model


def _iter_eval_slice(slice_name: str, max_chips: int):
    """Yield ``(chip_bytes, modality, prompts, ground_truth)`` from the named
    slice, mirroring ``compare_inference_layers``'s loader dispatch.
    """
    slice_name = (slice_name or "dota").strip().lower()
    if slice_name == "dota":
        from scripts.eval_datasets.dota import iter_dota
        return iter_dota()
    if slice_name in {"sar", "sar_synth"}:
        from scripts.eval_datasets.sar_synth import iter_sar_synth
        return iter_sar_synth()
    raise SystemExit(f"unknown slice: {slice_name}")


def _format_markdown(by_model: dict[str, dict], total_ece: float) -> str:
    lines = [
        "# Calibration ECE — per-detector",
        "",
        "Expected Calibration Error (lower is better; 0.0 = perfect).",
        "Suggested ``T`` is the temperature that minimises BCE on this slice.",
        "",
        "| Detector | N | Uncalibrated ECE | Suggested T | Calibrated ECE |",
        "|---|---|---|---|---|",
    ]
    for model, info in sorted(by_model.items()):
        lines.append(
            f"| `{model}` | {info['n']} | {info['ece_raw']:.4f} | "
            f"{info['T']:.2f} | {info['ece_cal']:.4f} |"
        )
    lines.extend([
        "",
        f"**Aggregate uncalibrated ECE**: {total_ece:.4f}",
        "",
        "## How to apply",
        "",
        "Copy the suggested temperatures into ``MODEL_TEMPERATURES`` or write",
        "them to ``MODEL_TEMPERATURES_FILE`` (default ``/data/calibration/model_temperatures.json``).",
        "``backend/calibration.py`` will apply the scaling on the next inference.",
        "",
        "```json",
        json.dumps({m: round(info["T"], 3) for m, info in by_model.items()}, indent=2),
        "```",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure detector calibration (ECE) on an eval slice.")
    parser.add_argument("--inference-url", default="http://localhost:8001",
                        help="Base URL of the inference-sam3 service.")
    parser.add_argument("--slice", default="dota",
                        choices=("dota", "sar"),
                        help="Eval dataset slice.")
    parser.add_argument("--max-chips", type=int, default=60)
    parser.add_argument("--bins", type=int, default=15)
    parser.add_argument("--output", default="docs/calibration_ece.md")
    parser.add_argument("--json-output", default=None,
                        help="Optional path for the raw bin/temperature JSON.")
    parser.add_argument(
        "--enabled-layers",
        default="sam3,dota_obb",
        help="Comma-separated list of layers to enable on /detect (default: sam3,dota_obb).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip HTTP calls and use synthetic responses from compare_inference_layers.",
    )
    args = parser.parse_args()

    enabled_layers = [s.strip() for s in args.enabled_layers.split(",") if s.strip()]

    by_model_raw = _collect_predictions(
        args.inference_url, args.slice, args.max_chips,
        enabled_layers=enabled_layers, dry_run=args.dry_run,
    )
    if not by_model_raw:
        print("[measure_calibration_ece] No predictions collected. Aborting.")
        return 2

    by_model: dict[str, dict] = {}
    aggregate_scores: list[float] = []
    aggregate_correct: list[bool] = []
    for model, samples in by_model_raw.items():
        scores = [s for s, _ in samples]
        correct = [c for _, c in samples]
        T = fit_temperature(scores, correct)
        ece_raw, bins_raw = ece_for_predictions(scores, correct, bins=args.bins)
        cal_scores = [apply_temperature(s, T) for s in scores]
        ece_cal, bins_cal = ece_for_predictions(cal_scores, correct, bins=args.bins)
        by_model[model] = {
            "n": len(scores),
            "T": T,
            "ece_raw": ece_raw,
            "ece_cal": ece_cal,
            "bins_raw": bins_raw,
            "bins_cal": bins_cal,
        }
        aggregate_scores.extend(scores)
        aggregate_correct.extend(correct)

    agg_ece, _ = ece_for_predictions(aggregate_scores, aggregate_correct, bins=args.bins)

    out_md = Path(args.output)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(_format_markdown(by_model, agg_ece), encoding="utf-8")
    print(f"Wrote {out_md}")

    if args.json_output:
        out_json = Path(args.json_output)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps({
            "by_model": by_model,
            "aggregate_ece": agg_ece,
            "slice": args.slice,
            "bins": args.bins,
        }, indent=2), encoding="utf-8")
        print(f"Wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
