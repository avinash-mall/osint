"""Phase 9.45 — candidate-link precision / recall evaluation harness.

Measures how often Sentinel's detection-to-target candidate-linker proposes
the *right* target as its top-1 / top-K match. This is the only objective
gauge for the Phase 4.14 score rebalance (0.30·distance + 0.30·compat +
0.30·confidence + 0.10·history) and any future change to the shared scorer.

The script does NOT call the live backend — it imports the pure shared scorer
from ``backend/candidate_linking.py`` so it works in CI without a running
PostGIS / Neo4j / inference stack. Ground truth comes from a small hand-curated JSON file
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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))
from candidate_linking import score_candidate_link  # noqa: E402


def _rank_for(det: dict, targets: list[dict], max_distance_m: float) -> tuple[int, list[tuple[str, float]]]:
    """Score every target for this detection; return (rank_of_gt_target, ranked_list)."""
    scored = []
    for target in targets:
        result = score_candidate_link(det, target, max_distance_m=max_distance_m)
        if result is not None:
            scored.append((target["stable_id"], result["score"]))
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
