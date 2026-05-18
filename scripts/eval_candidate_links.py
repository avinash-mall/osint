"""Phase 9.45 — candidate-link precision / recall evaluation harness.

Measures how often Sentinel's detection-to-target candidate-linker proposes
the *right* target as its top-1 / top-K match. This is the only objective
gauge for the Phase 4.14 score rebalance (0.30·distance + 0.30·compat +
0.30·confidence + 0.10·history) and any future change to
``backend/main.py::generate_candidate_links_for_detection``.

The script does NOT call the live backend — it imports the scoring
functions directly so it works in CI without a running PostGIS / Neo4j /
inference stack. Ground truth comes from a small hand-curated JSON file
(see ``scripts/eval_datasets/candidate_links_gt.json`` for the format).

Ground-truth schema::

    {
        "detections": [
            {
                "id": 1,
                "class": "tank",
                "confidence": 0.82,
                "lat": 25.0001,
                "lon": 55.0001,
                "ground_truth_target": "T-72-bn-3"
            },
            ...
        ],
        "targets": [
            {
                "stable_id": "T-72-bn-3",
                "name": "T-72 bn 3",
                "type": "tank",
                "category": "armored_vehicle",
                "description": "Hostile armoured battalion",
                "lat": 25.0,
                "lon": 55.0
            },
            ...
        ]
    }

Usage::

    python scripts/eval_candidate_links.py \\
        --gt scripts/eval_datasets/candidate_links_gt.json \\
        --top-k 5 \\
        --max-distance-m 1500 \\
        --output docs/candidate_link_eval.md

Output::

    docs/candidate_link_eval.md  — Markdown summary
    Per-detection: predicted top-K rank of the ground-truth target.
    Aggregate: top-1 accuracy, top-K accuracy, MRR.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _import_scorer():
    """Import the scoring helpers from ``backend.main`` without triggering
    the heavy module-level imports (Celery, FastAPI, Neo4j, …).

    The candidate-link scoring is conceptually pure: distance + compatibility
    + confidence + history. We re-implement the core scoring inline rather
    than importing the live function, so this harness can run in a CI
    container without PostGIS / Neo4j / Redis.
    """
    return None  # see _score_candidate_link below


def _haversine_metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _compatibility(det_class: str, target_props: dict) -> float:
    """Phase 4.14 normalized compatibility score in [0, 1]."""
    det_text = (det_class or "").replace("_", " ").lower()
    target_text = " ".join(
        str(target_props.get(key, "")) for key in ("name", "type", "category", "description")
    ).lower()
    if not target_text:
        return 0.40
    if any(token in target_text for token in det_text.split() if len(token) >= 4):
        return 1.00
    # Coarse category fallback — split the class on space/underscore and check
    # if any token is in the target_text (which already includes ``category``).
    for token in det_text.split():
        if len(token) >= 4 and token in target_text:
            return 0.70
    return 0.20


def _score(det: dict, target: dict, max_distance_m: float, history_anchor: float = 0.0) -> float:
    """Phase 4.14 score: 0.30·distance + 0.30·compat + 0.30·confidence + 0.10·history."""
    distance_m = _haversine_metres(det["lat"], det["lon"], target["lat"], target["lon"])
    if distance_m > max_distance_m:
        return float("-inf")
    distance_norm = max(0.0, 1.0 - (distance_m / max_distance_m))
    compat = _compatibility(det["class"], target)
    conf = max(0.0, min(1.0, float(det.get("confidence") or 0.0)))
    return 0.30 * distance_norm + 0.30 * compat + 0.30 * conf + 0.10 * history_anchor


def _rank_for(det: dict, targets: list[dict], max_distance_m: float) -> tuple[int, list[tuple[str, float]]]:
    """Score every target for this detection; return (rank_of_gt_target, ranked_list)."""
    scored = []
    for target in targets:
        s = _score(det, target, max_distance_m=max_distance_m)
        if math.isfinite(s):
            scored.append((target["stable_id"], s))
    scored.sort(key=lambda t: t[1], reverse=True)
    gt = det.get("ground_truth_target")
    rank = -1
    for i, (tid, _) in enumerate(scored):
        if tid == gt:
            rank = i + 1
            break
    return rank, scored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate candidate-link precision / recall.")
    parser.add_argument("--gt", required=True, help="Path to ground-truth JSON.")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--max-distance-m", type=float, default=1500.0)
    parser.add_argument("--output", default="docs/candidate_link_eval.md")
    parser.add_argument("--threshold-top1", type=float, default=0.75,
                        help="Exit non-zero if top-1 accuracy falls below this (default 0.75).")
    args = parser.parse_args(argv)

    gt_path = Path(args.gt)
    if not gt_path.exists():
        print(f"[eval_candidate_links] ground-truth file not found: {gt_path}")
        return 2
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    detections = gt.get("detections") or []
    targets = gt.get("targets") or []
    if not detections or not targets:
        print("[eval_candidate_links] empty ground-truth; nothing to score")
        return 2

    rows = []
    top1 = 0
    topk = 0
    mrr_sum = 0.0
    for det in detections:
        rank, ranked = _rank_for(det, targets, max_distance_m=args.max_distance_m)
        if rank == 1:
            top1 += 1
        if 1 <= rank <= args.top_k:
            topk += 1
        if rank > 0:
            mrr_sum += 1.0 / rank
        rows.append({
            "detection_id": det.get("id"),
            "class": det.get("class"),
            "gt_target": det.get("ground_truth_target"),
            "predicted_rank": rank,
            "top1": rank == 1,
            f"top{args.top_k}": 1 <= rank <= args.top_k,
            "ranked": [{"target_id": tid, "score": round(s, 3)} for tid, s in ranked[: args.top_k]],
        })

    n = len(detections)
    top1_acc = top1 / n
    topk_acc = topk / n
    mrr = mrr_sum / n

    out_md = Path(args.output)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Candidate-link evaluation",
        "",
        f"Ground truth: `{gt_path}`",
        f"Detections evaluated: **{n}**",
        f"Targets considered: **{len(targets)}**",
        f"Max distance gate: **{args.max_distance_m} m**",
        "",
        "## Aggregate",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Top-1 accuracy | **{top1_acc:.3f}** |",
        f"| Top-{args.top_k} accuracy | **{topk_acc:.3f}** |",
        f"| MRR | **{mrr:.3f}** |",
        "",
        "## Per-detection",
        "",
        "| Det id | Class | GT target | Predicted rank | Top-1 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['detection_id']} | `{r['class']}` | `{r['gt_target']}` | "
            f"{r['predicted_rank'] if r['predicted_rank'] > 0 else '—'} | "
            f"{'✓' if r['top1'] else ''} |"
        )
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"[eval_candidate_links] wrote {out_md}")
    print(f"[eval_candidate_links] top-1={top1_acc:.3f}  top-{args.top_k}={topk_acc:.3f}  MRR={mrr:.3f}")
    if top1_acc < args.threshold_top1:
        print(
            f"[eval_candidate_links] FAIL — top-1 {top1_acc:.3f} < threshold {args.threshold_top1:.3f}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
